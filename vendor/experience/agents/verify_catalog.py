#!/usr/bin/env python3
"""Independent acceptance of the serialized 15 + 1 owner deliverables."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def leaves(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from leaves(v)
    else:
        yield str(value)


def inspect(root):
    ownership = read(root / "config/workflow_ownership.json")
    catalog = read(root / "config/agent_catalog.json")
    procedure_catalog = read(root / "config/procedure_catalog.json")
    old = read(root / "resources/previous_edition/config/agent_catalog.json")["agents"]
    assert catalog["schema_version"] == procedure_catalog["schema_version"] == 2
    agents = catalog["agents"]; procedures = procedure_catalog["procedures"]
    ids = [a["id"] for a in agents]
    expected_ids = [a["id"] for a in ownership["agents"]] + ["supervisor"]
    assert ids == expected_ids and len(set(ids)) == 16
    assert sum(a["kind"] == "experiment" for a in agents) == 15
    assert sum(a["kind"] == "supervisor" for a in agents) == 1
    assert not any(a["kind"] == "support" for a in agents)
    assert len(procedures) == len({p["id"] for p in procedures}) == 44
    old_exp = {a["id"]: a for a in old if a["kind"] == "experiment"}
    new_proc = {p["id"]: p for p in procedures}
    assert set(old_exp) == set(new_proc)
    owners = {p: a["id"] for a in ownership["agents"] for p in a["procedure_ids"]}
    assert len(owners) == 44
    fields_compared = 0
    detail_entries_checked = 0
    detailed_fields = ["purpose", "scope", "legacy_functions", "depends_on", "parameter_reads", "parameter_writes", "controls", "sequence", "observables", "analysis", "decision_rules", "retest_rules", "acceptance", "budgets", "sources", "source_support", "optional_parameter_reads"]
    for pid, original in old_exp.items():
        p = new_proc[pid]
        assert p["kind"] == "procedure" and p["active_agent"] is False
        assert p["owner_agent_id"] == owners[pid]
        for key, value in original.items():
            if key != "kind":
                assert p[key] == value, (pid, "preservation", key)
                fields_compared += 1
        card = (root / f"agents/{owners[pid]}/card.md").read_text(encoding="utf-8")
        marker = f"### 内部方法 `{pid}`"
        assert card.count(marker) == 1, (pid, "unique_method_section")
        block = card.split(marker, 1)[1].split("\n### 内部方法 ", 1)[0]
        for key in detailed_fields:
            for value in leaves(original.get(key, [])):
                assert value in block, (pid, "card_detail_missing", key, value)
                detail_entries_checked += 1
    required = {"id", "name", "kind", "domain", "purpose", "parameter_reads", "parameter_writes", "controls", "sequence", "observables", "analysis", "decision_rules", "retest_rules", "acceptance", "budgets", "sources", "card_path", "prompt_path", "original_function", "source_page", "source_box_index", "procedure_ids", "main_procedure_id", "depends_on", "execution_status"}
    by_id = {a["id"]: a for a in agents}
    for spec in ownership["agents"]:
        a = by_id[spec["id"]]
        assert a["procedure_ids"] == spec["procedure_ids"]
        expected_main = "tq_cz_chevron" if spec["id"] == "d03_cz_width" else spec["procedure_ids"][0]
        assert a["main_procedure_id"] == expected_main
        if "main_procedure_id" in spec:
            assert a["main_procedure_id"] == spec["main_procedure_id"]
        assert a["original_function"] == spec["source_function"]
        assert a["source_page"] == spec["source_page"]
        assert a["source_box_index"] == spec["source_box_index"]
    assert by_id["s03_rabi_amplitude"]["main_procedure_id"] == "sq_rabi_amplitude"
    assert by_id["s07_t2"]["main_procedure_id"] == "sq_cpmg"
    assert by_id["d06_cz_xeb"]["main_procedure_id"] == "tq_xeb"
    assert by_id["d03_cz_width"]["main_procedure_id"] == "tq_cz_chevron"
    for aid in ("s03_rabi_amplitude", "s07_t2", "d06_cz_xeb", "d04_cz_amplitude"):
        assert by_id[aid]["main_method_execution_status"] == "contract_only"
    edges = {a["id"]: a["depends_on"] for a in agents}
    expected_edges = {
        "s01_readout_frequency":[], "s02_qubit_frequency":["s01_readout_frequency"],
        "s03_rabi_amplitude":["s02_qubit_frequency"], "s04_readout_fidelity":["s03_rabi_amplitude"],
        "s05_readout_optimization":["s04_readout_fidelity"], "s06_t1":["s05_readout_optimization"],
        "s07_t2":["s05_readout_optimization"], "s08_single_qubit_rb":["s05_readout_optimization"],
        "d01_flux_spectroscopy":["s06_t1","s07_t2","s08_single_qubit_rb"],
        "d02_idle_interaction":["d01_flux_spectroscopy"], "d03_cz_width":["d02_idle_interaction"],
        "d04_cz_amplitude":["d03_cz_width"], "d05_cz_detuning":["d04_cz_amplitude"],
        "d06_cz_xeb":["d05_cz_detuning"], "d07_cz_optimizer":["d06_cz_xeb"], "supervisor":[]}
    assert edges == expected_edges
    visited = set()
    def visit(aid, stack):
        assert aid in by_id and aid not in stack, (aid, "cycle_or_missing")
        if aid not in visited:
            for dep in edges[aid]:
                visit(dep, stack | {aid})
            visited.add(aid)
    for aid in ids:
        visit(aid, set())
    assert {(r["from"], r["to"]) for r in catalog["retest_routes"]} == {("s05_readout_optimization","s04_readout_fidelity"),("d07_cz_optimizer","d06_cz_xeb")}
    prompt_keys = {"agent_id","decision","reason","evidence_ids","target_agents","parameter_proposals","confidence"}
    for a in agents:
        assert required <= set(a), a["id"]
        assert a["execution_status"] in {"partial_simulated","contract_only","source_suite_only","policy_contract"}
        assert a["controls"]["fixed"]
        assert isinstance(a["controls"]["scan"], list)
        if a["kind"] == "experiment":
            assert a["controls"]["scan"]
        else:
            assert a["controls"]["scan"] == []  # Supervisor schedules; it does not scan hardware.
        assert a["sequence"] and a["analysis"] and a["acceptance"]
        assert a["budgets"]["max_attempts"] == 3
        text = (root / a["prompt_path"]).read_text(encoding="utf-8")
        blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
        assert len(blocks) == 1
        value = json.loads(blocks[0])
        assert set(value) == prompt_keys and value["agent_id"] == a["id"]
        assert value["decision"] in {"measure","remeasure","change_scan","recalibrate_upstream","verify","escalate"}
        assert 0 <= value["confidence"] <= 1
        assert isinstance(value["evidence_ids"],list) and isinstance(value["target_agents"],list)
        assert not value["parameter_proposals"]
    cards = list((root / "agents").glob("*/card.md"))
    prompts = list((root / "agents").glob("*/prompt.md"))
    assert len(cards) == len(prompts) == 16
    assert {p.parent.name for p in cards} == set(ids)
    modules = by_id["supervisor"]["public_modules"]
    assert len(modules) == 6 and {p["legacy_role_id"] for p in modules} == set(ownership["former_support_roles"])
    supervisor_text = (root / by_id["supervisor"]["card_path"]).read_text(encoding="utf-8")
    module_entries_checked = 0
    for p in old:
        if p["kind"] != "experiment":
            marker = f"### 公共模块 `{p['id']}`"
            assert supervisor_text.count(marker) == 1
            block = supervisor_text.split(marker,1)[1].split("\n### 公共模块 ",1)[0]
            for key in detailed_fields:
                for value in leaves(p.get(key,[])):
                    assert value in block,(p['id'],key,value)
                    module_entries_checked += 1
    coverage = (root / "docs/experiment_coverage.md").read_text(encoding="utf-8")
    assert "|\n\n|" not in coverage  # Table rows must remain adjacent Markdown.
    assert all(aid in coverage for aid in ids if aid != "supervisor")
    assert all(pid in coverage for pid in old_exp)
    return {"active_agents":16,"experiment_agents":15,"supervisor_agents":1,"internal_procedures":44,"card_prompt_files":32,"old_fields_preserved_exactly":fields_compared,"procedure_detail_values_present_in_owner_cards":detail_entries_checked,"public_module_detail_values_preserved":module_entries_checked,"valid_prompt_examples":16,"owner_dag":"acyclic_exact_requested_chain","return_routes_not_dag_edges":2}


def main():
    started = time.monotonic()
    checks = inspect(ROOT)
    catalog = read(ROOT / "config/agent_catalog.json")
    output_rel = ["config/agent_catalog.json","config/procedure_catalog.json","docs/experiment_coverage.md"] + [a[key] for a in catalog["agents"] for key in ("card_path","prompt_path")]
    assert len(output_rel) == 35
    expected = {rel:sha(ROOT/rel) for rel in output_rel}
    input_rel = ["agents/build_catalog.py","config/workflow_ownership.json","resources/previous_edition/config/agent_catalog.json"]
    declared_sources = {s["file"] for a in catalog["agents"] for s in a["sources"]}
    for source in declared_sources:
        assert (ROOT/source).is_file(), source
    input_hashes = {rel:sha(ROOT/rel) for rel in sorted(set(input_rel)|declared_sources)}
    for p in read(ROOT / "config/procedure_catalog.json")["procedures"]:
        assert (ROOT/p["archived_card_path"]).is_file()
        assert (ROOT/p["archived_prompt_path"]).is_file()
    runs=[]
    for n in (1,2):
        with tempfile.TemporaryDirectory(prefix=f"qcal-15step-cold-{n}-") as td:
            fresh=Path(td)
            for rel in input_rel:
                dest=fresh/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,dest)
            (fresh/"docs").mkdir()
            command=[sys.executable,str(fresh/"agents/build_catalog.py")]
            run=subprocess.run(command,cwd="/tmp",check=True,capture_output=True,text=True)
            assert {rel:sha(fresh/rel) for rel in output_rel} == expected
            assert inspect(fresh) == checks
            runs.append({"kind":f"cold-{n}","command":command,"result":"byte_identical_and_independently_valid","stdout":run.stdout.strip(),"exit_code":run.returncode,"temporary_workspace_removed":True})
    command=[sys.executable,str(ROOT/"agents/build_catalog.py")]
    run=subprocess.run(command,cwd="/tmp",check=True,capture_output=True,text=True)
    assert {rel:sha(ROOT/rel) for rel in output_rel} == expected
    assert inspect(ROOT) == checks
    assert input_hashes == {rel:sha(ROOT/rel) for rel in input_hashes}
    runs.append({"kind":"same_workspace_repeat_from_other_cwd","command":command,"result":"byte_identical_and_independently_valid","exit_code":run.returncode})
    artifacts={rel:{"sha256":expected[rel],"bytes":(ROOT/rel).stat().st_size,"mode":oct((ROOT/rel).stat().st_mode & 0o777)} for rel in output_rel}
    (ROOT/"agents/catalog_artifacts.json").write_text(json.dumps({"schema_version":2,"artifacts":artifacts,"immutable_inputs":input_hashes},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    gates=[
        ("target_identity",["Fresh independent Python process inspects this 15-step package and captures PID/runtime/cwd; no previous acceptance reused."]),
        ("exact_contract",["Exactly 15 experiment owners + 1 supervisor, 44 unique internal procedures, 32 actual card/prompt files, original box/function mapping and main procedures checked.","All 16 JSON examples parse and match the unchanged seven-field proposal contract."]),
        ("independent_oracle",[f"Serialized artifacts compared against archived source and ownership without importing generator: {checks['old_fields_preserved_exactly']} original fields exact; {checks['procedure_detail_values_present_in_owner_cards']} method details present in their owner's method section.",f"All {checks['public_module_detail_values_preserved']} public-module details embedded in sole supervisor card; exact DAG and non-DAG return routes checked."]),
        ("clean_rerun",["Two fresh temporary roots with only shipped generator, immutable ownership and archived catalog regenerated all 35 outputs; independent validation and byte equality passed."]),
        ("idempotence",["Same-workspace repeat from /tmp preserved all 35 output bytes and all checked immutable source inputs."]),
        ("artifact_integrity",["35 output hashes, sizes and file modes frozen in agents/catalog_artifacts.json; source and archived card paths exist."]),
        ("residual_health",["All generator subprocesses exited; temporary roots removed; no service, background process, port or model request launched."]),
        ("authoritative_result",["This record follows independent assertions against serialized artifacts and actual cold/repeat execution; it claims static catalog acceptance only."])
    ]
    record={"schema_version":1,"task":"Original flow 15 experiment owners + one supervisor: catalog/card/procedure generation","attempt_class":"scoreable_test_result","decision":"PASS","utc_time":datetime.now(timezone.utc).isoformat(),"identity":{"pid":os.getpid(),"python":platform.python_version(),"platform":platform.platform(),"cwd":str(ROOT),"command":[sys.executable,str(Path(__file__).resolve())],"phase":"catalog_acceptance"},"checks":checks,"runs":runs,"gates":[{"name":name,"status":"pass","evidence":evidence} for name,evidence in gates],"not_applicable":[{"gate":"service_liveness","reason":"Static JSON/Markdown generation; no server or VM."},{"gate":"performance_margin","reason":"No speed claim; declared budgets are resource ceilings, not benchmark results."},{"gate":"physical_execution","reason":"Backend and hardware validation are parent task scope; this acceptance cannot declare any experiment physically passed."}],"limitations":["Partial simulator support does not mean the original main procedure is bound.","Rabi time is auxiliary to amplitude Rabi; Ramsey does not replace CPMG; tomography/Bell do not replace XEB.","Previous detailed procedure metadata is preserved as provenance and is not an active-agent API."],"elapsed_seconds":time.monotonic()-started}
    (ROOT/"agents/catalog_acceptance.json").write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({"decision":"PASS","checks":checks,"generated_files":35,"cold_runs":2,"repeat_runs":1,"acceptance":str(ROOT/"agents/catalog_acceptance.json")},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
