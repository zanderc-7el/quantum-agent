"""纯标准库复核已保存的物理扫描，不导入adapter或其验收器。

QCAL_TWIN_OUTPUT 指向本次序列化输出目录。没有指定且默认目录不存在时，
明确跳过；这不构成物理输出验收通过。控制平面单元测试不依赖物理依赖包。
"""
from __future__ import annotations

import cmath
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "twin-final"
COUNTS = {"ro_resonator_survey": 121, "sq_spectroscopy": 121, "sq_rabi_time": 161, "sq_t1": 81, "sq_ramsey": 322}


def load(path):
    def invalid(value):
        raise AssertionError("nonfinite JSON: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def finite_tree(value):
    if isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, dict):
        for child in value.values():
            finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            finite_tree(child)


def as_complex(value):
    if isinstance(value, dict):
        assert set(value) == {"real", "imag"}
        return complex(value["real"], value["imag"])
    return complex(value)


def close(actual, expected, tol=1e-10):
    assert abs(actual - expected) <= tol * max(1.0, abs(expected)), (actual, expected)


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def check_scan(scan, run, phase):
    """从原始联合计数/IQ恢复观测，从拟合参数重算曲线和残差。"""
    finite_tree(scan)
    base = run["step_id"].split(":")[0]
    q = int(run["step_id"].split(":")[1][1:]) if ":" in run["step_id"] else 0
    records = scan["records"]
    result = scan["result"]
    request = scan["request"]
    assert request["run_id"] == run["run_id"]
    assert request["step_id"] == run["step_id"]
    assert request["parameter_versions"] == run["parameter_versions"]
    assert request["operation_id"] == run["operation_id"] + f"/{run['attempt']}/{phase}"
    assert result["data_kind"] == "simulated"
    expected_count = COUNTS[base] + (4 if base == "sq_rabi_time" and phase == "verify" else 0)
    assert len(records) == expected_count == result["acquisition_count"]
    assert result["shots"] == request["shots"]
    assert result["total_shots"] == sum(record["measurement"]["shots"] for record in records)
    assert result["total_shots"] == len(records) * request["shots"]
    populations = []
    for index, record in enumerate(records):
        acq = record["request"]
        measurement = record["measurement"]
        assert acq["shots"] == measurement["shots"] == request["shots"]
        assert measurement["job_id"] == "virtual-" + format(index, "06d")
        counts = measurement["counts"]
        if base == "ro_resonator_survey":
            assert counts == {} and acq["kind"] == "resonator"
            assert len(measurement["iq_mean"]) == 1
            populations.append(complex(*measurement["iq_mean"][0]))
            close(acq["probe_hz"], record["x"])
        else:
            assert acq["kind"] == "qubit"
            assert set(counts) == {format(i, f"0{q+1}b") for i in range(2 ** (q + 1))}
            assert all(type(v) is int and v >= 0 for v in counts.values())
            assert sum(counts.values()) == request["shots"]
            populations.append(sum(v for bits, v in counts.items() if bits[q] == "1") / request["shots"])
            assert all(segment["coupling_scale"] == 0 for segment in acq["sequence"]["segments"])
    anchor_records = []
    if base == "sq_rabi_time" and phase == "verify":
        anchor_records = records[-4:]
        anchor_populations = populations[-4:]
        records = records[:-4]
        populations = populations[:-4]
        pi_key = f"q{q}.pi_" + ("seed_" if run["step_id"].endswith(":seed") else "") + "duration_s"
        for record, population, (name, factor) in zip(anchor_records, anchor_populations,
            (("zero_pi_population",0.),("half_pi_population",.5),("pi_population",1.),("two_pi_population",2.))):
            assert record["quadrature"] == "anchor_" + name
            close(record["x"], run["candidate"][pi_key] * factor, tol=1e-15)
            close(record["request"]["sequence"]["segments"][0]["duration_s"], run["candidate"][pi_key] * factor, tol=1e-15)
            close(result["metrics"][name], population, tol=1e-15)
        assert result["metrics"]["zero_pi_population"] <= .1
        assert .4 <= result["metrics"]["half_pi_population"] <= .6
        assert result["metrics"]["pi_population"] >= .9
        assert result["metrics"]["two_pi_population"] <= .1
    axis = scan["axis"]
    assert len(axis) >= 8 and all(b > a for a, b in zip(axis, axis[1:]))
    if base == "sq_ramsey":
        assert len(records) == 2 * len(axis)
        reconstructed = []
        for i, t in enumerate(axis):
            left, right = records[2*i:2*i+2]
            assert left["quadrature"] == "x" and right["quadrature"] == "y"
            close(left["x"], t); close(right["x"], t)
            for record, expected_phase in ((left, 0.), (right, math.pi/2)):
                segments = record["request"]["sequence"]["segments"]
                assert len(segments) == 3
                close(segments[1]["duration_s"], t, tol=1e-15)
                close(segments[2]["phases_rad"][q], expected_phase)
                pi = request["parameter_values"][f"q{q}.pi_seed_duration_s"]
                close(segments[0]["duration_s"], pi/2, tol=1e-15)
                close(segments[2]["duration_s"], pi/2, tol=1e-15)
            reconstructed.append((2*populations[2*i]-1)-1j*(2*populations[2*i+1]-1))
    else:
        assert len(axis) == len(records)
        reconstructed = populations
        for t, record in zip(axis, records):
            close(record["x"], t)
            if base in ("sq_rabi_time", "sq_t1"):
                segments = record["request"]["sequence"]["segments"]
                close(segments[-1]["duration_s"], t, tol=1e-15)
    observed = list(map(as_complex, scan["observed"]))
    assert len(observed) == len(reconstructed)
    for actual, expected in zip(observed, reconstructed):
        close(actual, expected, tol=1e-13)
    p = result["fit"]["parameters"]
    predicted = []
    for x in axis:
        if base == "ro_resonator_survey":
            value = 1 - p["depth"] / (1 + 2j * (x - p["frequency_hz"]) / p["linewidth_hz"])
        elif base == "sq_spectroscopy":
            value = p["offset"] + p["contrast"] / (1 + ((x - p["frequency_hz"]) / p["hwhm_hz"])**2)
        elif base == "sq_rabi_time":
            value = p["offset"] + p["amplitude"] * math.exp(-x/p["decay_s"]) * math.cos(2*math.pi*p["frequency_hz"]*x+p["phase_rad"])
        elif base == "sq_t1":
            value = p["offset"] + p["amplitude"] * math.exp(-x/p["t1_s"])
        else:
            value = complex(p["offset_i"],p["offset_q"]) + complex(p["amplitude_i"],p["amplitude_q"]) * cmath.exp(-x/p["t2_s"] + 2j*math.pi*p["detuning_hz"]*x)
        predicted.append(value)
    assert len(scan["predicted"]) == len(predicted) == len(observed)
    assert result["metrics"]["fit_success"] == 1.0
    for actual, expected in zip(map(as_complex, scan["predicted"]), predicted):
        close(actual, expected, tol=5e-10)
    rmse = math.sqrt(sum(abs(a-b)**2 for a,b in zip(observed,predicted)) / len(observed))
    close(result["metrics"]["fit_rmse"], rmse, tol=1e-10)
    close(result["fit"]["rmse"], rmse, tol=1e-10)
    assert result["fit"]["success"] is True
    assert all(value >= 0 for value in result["fit"]["stderr"].values())
    candidate = result["candidate"]
    if base == "sq_ramsey":
        frame = records[0]["request"]["sequence"]["frame_hz"][q]
        close(candidate[f"q{q}.frequency_hz"], frame + p["detuning_hz"])
        close(candidate[f"q{q}.t2_s"], p["t2_s"], tol=1e-15)
        center = request["parameter_values"][f"q{q}.frequency_coarse_hz"] if phase == "measure" else run["candidate"][f"q{q}.frequency_hz"]
        close(frame, center + 4e5)
    elif base == "sq_rabi_time":
        suffix = "seed_" if run["step_id"].endswith(":seed") else ""
        close(candidate[f"q{q}.pi_{suffix}duration_s"], 1/(2*p["frequency_hz"]), tol=1e-15)
        close(candidate[f"q{q}.rabi_{suffix}hz_per_amp"], p["frequency_hz"]/.2)
    if phase == "verify":
        assert result["candidate_hash"] == canonical_hash(run["candidate"])
        assert request["candidate_hash"] == result["candidate_hash"]
        assert result["passed"] is True
        assert result["metrics"]["fit_rmse"] <= .05
        assert result["metrics"]["agreement_ratio"] <= 1
        proposed = run["candidate"]
        if base == "ro_resonator_survey":
            tolerances = {"readout.frequency_hz": 50000.}
        elif base == "sq_spectroscopy":
            tolerances = {f"q{q}.frequency_coarse_hz": 50000.}
        elif base == "sq_rabi_time":
            tolerances = {k: abs(v)*.03 for k,v in proposed.items()}
        elif base == "sq_t1":
            tolerances = {f"q{q}.t1_s": proposed[f"q{q}.t1_s"]*.15}
        else:
            tolerances = {f"q{q}.frequency_hz": 30000., f"q{q}.t2_s": proposed[f"q{q}.t2_s"]*.20}
        assert result["verification_tolerances"] == tolerances
        ratio = max(abs(candidate[k]-proposed[k])/tolerances[k] for k in proposed)
        close(result["metrics"]["agreement_ratio"], ratio, tol=1e-12)
    return result["total_shots"]


def verify_bundle(directory):
    directory = Path(directory)
    summary = load(directory / "twin_summary.json")
    assert summary["data_kind"] == "simulated"
    assert summary["runs"] and all(summary["valid_evidence"].values())
    backend = directory / "backend"
    recipe = load(backend / "virtual_control_recipe.json")
    evidence_ids, acquisition_ids, paths, kinds = set(), set(), set(), set()
    total_shots = 0
    database = sqlite3.connect(f"file:{directory / 'runtime.sqlite'}?mode=ro", uri=True)
    try:
        assert database.execute("SELECT count(*) FROM locks").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM operations WHERE state!='completed'").fetchone()[0] == 0
        for run in summary["runs"]:
            assert run["state"] == "completed" and run["phase"] == "committed"
            assert run["trial_active"] is False and run["error"] is None
            assert run["candidate_hash"] == canonical_hash(run["candidate"])
            step = json.loads(database.execute("SELECT spec FROM steps WHERE id=?", (run["step_id"],)).fetchone()[0])
            kinds.add(run["step_id"].split(":")[0])
            run_shots = 0
            for phase, key in (("measure","measurement"),("verify","verification")):
                value = run[key]
                assert value["evidence_id"] not in evidence_ids
                assert value["acquisition_id"] not in acquisition_ids
                evidence_ids.add(value["evidence_id"]); acquisition_ids.add(value["acquisition_id"])
                path = (backend / value["artifact_path"]).resolve()
                assert path.is_relative_to(backend.resolve()) and path not in paths
                paths.add(path)
                assert hashlib.sha256(path.read_bytes()).hexdigest() == value["artifact_sha256"]
                scan = load(path)
                assert scan["result"] == {k:v for k,v in value.items() if k not in ("artifact_path","artifact_sha256")}
                run_shots += check_scan(scan, run, phase)
                receipt_path = backend / "receipts" / (hashlib.sha256(scan["request"]["operation_id"].encode()).hexdigest()+".json")
                receipt = load(receipt_path)
                assert receipt["kind"] == phase and receipt["result"] == value
            assert run_shots == run["used_shots"]
            assert run_shots <= step["budget"]["total_shots"]
            assert run["used_acquisitions"] == run["measurement"]["acquisition_count"] + run["verification"]["acquisition_count"]
            total_shots += run_shots
            for metric, rule in step["criteria"].items():
                observed = run["verification"]["metrics"][metric]
                assert rule.get("min",-math.inf) <= observed <= rule.get("max",math.inf)
            for name, candidate in run["candidate"].items():
                assert summary["parameters"][name]["value"] == candidate
                assert recipe[name] == candidate
                row = database.execute("SELECT value,revision FROM parameters WHERE name=?", (name,)).fetchone()
                assert json.loads(row[0]) == candidate
                assert row[1] == summary["parameters"][name]["revision"] == run["committed_versions"][name]
            row = database.execute("SELECT valid FROM evidence WHERE id=?", (run["verification"]["evidence_id"],)).fetchone()
            assert row[0] == 1
        assert kinds == set(COUNTS)
    finally:
        database.close()
    return {"runs":len(summary["runs"]),"scan_artifacts":len(paths),"total_shots":total_shots,"step_kinds":sorted(kinds),"data_kind":"simulated"}


class IndependentSerializedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(os.environ.get("QCAL_TWIN_OUTPUT", str(DEFAULT_OUTPUT)))
        if not (cls.directory / "twin_summary.json").is_file():
            raise unittest.SkipTest("没有本次物理序列化证据；请设置QCAL_TWIN_OUTPUT后重新验收")

    def test_serialized_counts_formulas_provenance_budget_and_versions(self):
        self.assertGreater(verify_bundle(self.directory)["total_shots"], 0)

    def test_independent_checker_detects_corrupted_observation(self):
        summary = load(self.directory / "twin_summary.json")
        run = next(r for r in summary["runs"] if r["step_id"].startswith("sq_t1"))
        scan = copy.deepcopy(load(self.directory / "backend" / run["verification"]["artifact_path"]))
        scan["observed"][0] += .125
        with self.assertRaises(AssertionError):
            check_scan(scan, run, "verify")

    def test_independent_checker_detects_wrong_ramsey_phase(self):
        summary = load(self.directory / "twin_summary.json")
        run = next(r for r in summary["runs"] if r["step_id"].startswith("sq_ramsey"))
        scan = copy.deepcopy(load(self.directory / "backend" / run["verification"]["artifact_path"]))
        q = int(run["step_id"].split(":")[1][1:])
        scan["records"][1]["request"]["sequence"]["segments"][2]["phases_rad"][q] *= -1
        with self.assertRaises(AssertionError):
            check_scan(scan, run, "verify")


if __name__ == "__main__":
    unittest.main()
