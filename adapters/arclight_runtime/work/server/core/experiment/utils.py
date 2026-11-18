import itertools
import json, hashlib
import numpy as np
from typing import Any, Dict, List, Type
from .param import *
import inspect
from .pulse import BaseOp
from . import pulse as pulse_module
from dataclasses import dataclass
from copy import deepcopy

@dataclass(frozen=True)
class AxisKeySchema:
    cfg_prefix: str = "cfg."
    exp_prefix: str = "exp."
    exp_sequence_variants_root: str = "exp.cir"
    exp_sequence_variants_default: str = "exp.cir.default"
    seq_default_key: str = "default"
    seq_map_key: str = "circuits"

@dataclass(frozen=True)
class SpecKeySchema:
    meta: str = "meta"
    static_config: str = "static_config"
    sequence_variants: str = "circuit_variants"
    gate_library: str = "gate_library"
    sweeps: str = "sweeps"
    post_process: str = "post_process"
    analysis: str = "analysis"
    
@dataclass(frozen=True)
class MetaKeySchema:
    shots: str = "shots"
    time_to_read: str = "time_to_read"
    expand_mode: str = "expand_mode"
    readout_mode: str = "readout_mode"
    sync_inst: str = "sync"
    type: str = "type"
    qubit: str = "qubit"
    group: str = "group"
    batch_size: str = "batch_size"
    repeat: str = "repeat"
    measurement_counts: str = "measurement_counts"

@dataclass(frozen=True)
class RBMetaKeySchema:
    run_seed: str = "run_seed"
    clifford_m: str = "clifford_m"

@dataclass(frozen=True)
class XYMetaKeySchema:
    circ: str = "circ"
    
@dataclass(frozen=True)
class SweepKeySchema:
    bindings: str = "bindings"
    values: str = "values"

@dataclass(frozen=True)
class ExpandSchema:
    cartesian: str = "cartesian"
    zip: str = "zip"    


@dataclass(frozen=True)
class SweepGroupKeySchema:
    mode: str = "mode"


CIRCUIT_SELECTOR_BINDINGS = {
    AxisKeySchema.exp_sequence_variants_default,
    "exp.circuit_variants",
    "exp.sequence_variants.default",
}

@dataclass(frozen=True)
class ExpTypeSchema:
    pulse: str = "1"
    non_pulse: str = "2"
    
@dataclass(frozen=True)
class ChannelTypeSchema:
    xy: str = "xy"
    z: str = "z"
    ro: str = "ro"
    ad: str = "ad"
    na: str = 'na'
    
@dataclass(frozen=True)
class FitMethodSchema:
    sindamp: str = "sindamp"
    gaussian: str = "gaussian"
    spectrum: str = "spectrum"
    t1: str="t1"
    ramsey: str="ramsey"

@dataclass(frozen=True)
class VNAKeySchema:
    center: str = "cfg.NA_Meas.para.center"
    span: str = "cfg.NA_Meas.para.span"
    start: str = "cfg.NA_Meas.para.start"
    stop: str = "cfg.NA_Meas.para.stop"


# ---------- 扫描/展开工具 ----------

def get_scan_values(data: Any) -> List[Any]:
    """从 list 或 {start, stop, points} 得到扫描点列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        start = data.get("start")
        stop = data.get("stop")
        points = data.get("points")
        return np.round(np.linspace(start, stop, points), 10).tolist()
    raise TypeError("扫描值必须为 list 或者 dict")


def is_sweep_group(sweep: Any) -> bool:
    return isinstance(sweep, dict) and isinstance(sweep.get(SpecKeySchema.sweeps), list)


def _group_mode(group: Dict[str, Any], default_mode: str) -> str:
    mode = group.get(MetaKeySchema.expand_mode, group.get(SweepGroupKeySchema.mode, default_mode))
    if mode not in {ExpandSchema.cartesian, ExpandSchema.zip}:
        raise ValueError(f"不支持扫描模式：{mode}")
    return mode


def _merge_point_fragments(fragments) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for fragment in fragments:
        flat.update(fragment)
    return flat


def _expand_single_sweep(sweep: Dict[str, Any]) -> List[Dict[str, Any]]:
    binding = sweep.get(SweepKeySchema.bindings)
    return [
        {binding: value}
        for value in get_scan_values(sweep.get(SweepKeySchema.values))
    ]


def _expand_sweep_entry(
    sweep: Dict[str, Any], mode: str = ExpandSchema.cartesian
) -> List[Dict[str, Any]]:
    if is_sweep_group(sweep):
        return _expand_sweep_items(
            sweep.get(SpecKeySchema.sweeps, []),
            mode=_group_mode(sweep, mode),
        )
    return _expand_single_sweep(sweep)


def _expand_sweep_items(
    sweeps: List[Dict[str, Any]], mode: str = ExpandSchema.cartesian
) -> List[Dict[str, Any]]:
    if mode not in {ExpandSchema.cartesian, ExpandSchema.zip}:
        raise ValueError(f"不支持扫描模式：{mode}")

    child_points = [
        _expand_sweep_entry(sweep, mode=mode)
        for sweep in sweeps
        if sweep
    ]
    if not child_points:
        return [{}]

    points: List[Dict[str, Any]] = []
    if mode == ExpandSchema.zip:
        lens = [len(child) for child in child_points]
        if len(set(lens)) != 1:
            raise ValueError(f"Zip 的长度不一致: {lens}")
        for i in range(lens[0]):
            points.append(_merge_point_fragments(child[i] for child in child_points))
        return points

    for combo in itertools.product(*child_points):
        points.append(_merge_point_fragments(combo))
    return points


def _sweep_entry_axis_shape(
    sweep: Dict[str, Any], mode: str = ExpandSchema.cartesian
) -> List[int]:
    if is_sweep_group(sweep):
        return _sweep_axis_shape(
            sweep.get(SpecKeySchema.sweeps, []),
            mode=_group_mode(sweep, mode),
        )
    return [len(get_scan_values(sweep.get(SweepKeySchema.values)))]


def _shape_point_count(shape: List[int]) -> int:
    return int(np.prod(shape, dtype=int)) if shape else 1


def _iter_leaf_sweeps(sweeps: List[Dict[str, Any]]):
    for sweep in sweeps:
        if not sweep:
            continue
        if is_sweep_group(sweep):
            yield from _iter_leaf_sweeps(sweep.get(SpecKeySchema.sweeps, []))
        else:
            yield sweep


def is_circuit_selector_binding(binding: Any) -> bool:
    return binding in CIRCUIT_SELECTOR_BINDINGS


def is_circuit_selector_sweep(sweep: Dict[str, Any]) -> bool:
    return is_circuit_selector_binding(sweep.get(SweepKeySchema.bindings))


def normalize_circuit_selector_axes(axes: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {}
    for key, value in (axes or {}).items():
        if is_circuit_selector_binding(key):
            normalized[AxisKeySchema.exp_sequence_variants_default] = value
        else:
            normalized[key] = value
    return normalized


def expand_points(
    sweeps: List[Dict[str, Any]], mode: str = ExpandSchema.cartesian
) -> List[Dict[str, Any]]:
    """展开扫描。sweeps 可为旧的平铺轴列表，也可包含嵌套 sweep group。"""
    return _expand_sweep_items(sweeps, mode=mode)


def _ensure_sweep_list(sweeps: Any, owner: str) -> List[Dict[str, Any]]:
    if sweeps is None:
        return []
    if not isinstance(sweeps, list):
        raise TypeError(f"{owner}.sweeps 必须是 list，实际是 {type(sweeps).__name__}")
    return [deepcopy(sweep) for sweep in sweeps if sweep]


def _circuit_binding(circuit_name: str, binding: Any) -> Any:
    if not isinstance(binding, str):
        return binding
    if binding.startswith(AxisKeySchema.cfg_prefix):
        return binding
    if binding.startswith(AxisKeySchema.exp_sequence_variants_root + ".*."):
        suffix = binding.split(".*.", 1)[1]
        return f"{AxisKeySchema.exp_sequence_variants_root}.{circuit_name}.{suffix}"
    if binding.startswith(AxisKeySchema.exp_prefix):
        return binding
    return f"{AxisKeySchema.exp_sequence_variants_root}.{circuit_name}.{binding}"


def _binding_conflict_key(circuit_name: str, binding: Any) -> Any:
    return _circuit_binding(circuit_name, binding)


def _normalize_circuit_sweeps(
    circuit_name: str, sweeps: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    normalized = []
    for sweep in sweeps:
        s = deepcopy(sweep)
        if is_sweep_group(s):
            s[SpecKeySchema.sweeps] = _normalize_circuit_sweeps(
                circuit_name,
                s.get(SpecKeySchema.sweeps, []),
            )
            normalized.append(s)
            continue
        binding = s.get(SweepKeySchema.bindings)
        if binding is not None:
            s[SweepKeySchema.bindings] = _circuit_binding(circuit_name, binding)
        normalized.append(s)
    return normalized


def _validate_group_sweeps(circuit_name: str, sweeps: List[Dict[str, Any]]) -> None:
    
    seen_bindings: Dict[str, Dict[str, Any]] = {}

    for sweep in _iter_leaf_sweeps(sweeps):
        binding = sweep.get(SweepKeySchema.bindings)
        if binding:
            key = _binding_conflict_key(circuit_name, binding)
            if key in seen_bindings:
                raise ValueError(
                    f"circuit {circuit_name!r} 的 sweeps 中 bindings={binding!r} 冲突；"
                    "顶层 sweeps 和 circuit 自定义 sweeps 不能扫描同一个参数"
                )
            seen_bindings[key] = sweep


def _sweep_axis_shape(
    sweeps: List[Dict[str, Any]], mode: str = ExpandSchema.cartesian
) -> List[int]:
    if mode not in {ExpandSchema.cartesian, ExpandSchema.zip}:
        raise ValueError(f"不支持扫描模式：{mode}")

    child_shapes = [
        _sweep_entry_axis_shape(sweep, mode=mode)
        for sweep in sweeps
        if sweep
    ]
    if not child_shapes:
        return []

    child_counts = [_shape_point_count(shape) for shape in child_shapes]
    if mode == ExpandSchema.zip:
        if len(set(child_counts)) != 1:
            raise ValueError(f"Zip 的长度不一致: {child_counts}")
        return [child_counts[0]]

    shape: List[int] = []
    for child_shape in child_shapes:
        shape.extend(child_shape)
    return shape


def build_circuit_sweep_groups(
    sequence_variants: Dict[str, Any],
    top_sweeps: List[Dict[str, Any]],
    mode: str = ExpandSchema.cartesian,
) -> List[Dict[str, Any]]:
    """把顶层共享 sweeps 与 circuits.<name>.sweeps 组装成按线路执行的扫描组。"""
    circuits = (sequence_variants or {}).get(AxisKeySchema.seq_map_key, {})
    if not isinstance(circuits, dict) or not circuits:
        return []

    circuit_sweeps = {
        circuit_name: _ensure_sweep_list(circuit.get("sweeps"), f"circuits.{circuit_name}")
        for circuit_name, circuit in circuits.items()
        if isinstance(circuit, dict)
    }
    has_circuit_sweeps = any(circuit_sweeps.values())
    if not has_circuit_sweeps:
        return []

    selector_sweeps = [
        sweep for sweep in top_sweeps if is_circuit_selector_sweep(sweep)
    ]
    if len(selector_sweeps) > 1:
        raise ValueError("顶层 sweeps 中只能定义一个 circuit 选择轴")

    shared_sweeps = [
        deepcopy(sweep)
        for sweep in top_sweeps
        if not is_circuit_selector_sweep(sweep)
    ]

    if selector_sweeps:
        circuit_names = get_scan_values(selector_sweeps[0].get(SweepKeySchema.values))
    else:
        circuit_names = list(circuits.keys())

    unknown = [name for name in circuit_names if name not in circuits]
    if unknown:
        raise KeyError(f"circuit sweep 选择了不存在的线路: {unknown}")

    groups = []
    for circuit_name in circuit_names:
        local_sweeps = _normalize_circuit_sweeps(
            circuit_name, circuit_sweeps.get(circuit_name, [])
        )
        sweeps = deepcopy(shared_sweeps) + local_sweeps
        _validate_group_sweeps(circuit_name, sweeps)
        axis_shape = _sweep_axis_shape(sweeps, mode=mode)
        point_count = int(np.prod(axis_shape, dtype=int)) if axis_shape else 1
        groups.append({
            "name": circuit_name,
            "sweeps": sweeps,
            "axis_shape": axis_shape,
            "point_count": point_count,
        })
    return groups


def expand_circuit_sweep_groups(
    sweep_groups: List[Dict[str, Any]], mode: str = ExpandSchema.cartesian
) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for group in sweep_groups or []:
        circuit_name = group["name"]
        group_points = expand_points(group.get("sweeps", []), mode=mode)
        for point in group_points:
            axes = {AxisKeySchema.exp_sequence_variants_default: circuit_name}
            axes.update(point)
            points.append(axes)
    return points


class PulseEncoder(json.JSONEncoder):
    """
    JSON 编码器：支持 numpy、ParamVal、ParamRef、以及 Pulse 对象
    """
    def default(self, obj):
        # numpy
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        # Param
        elif isinstance(obj, ParamVal):
            return obj.val
        elif isinstance(obj, ParamRef):
            d = {"type": "paramRef", "name": obj.name}
            if obj.val is not None:
                d["value"] = obj.val
            return d
        # Pulse 派生类
        elif isinstance(obj, BaseOp):
            return encode_pulse(obj)
        # 其他交给父类
        return json.JSONEncoder.default(self, obj)
    
def _get_pulse_cls(cls_name: str):
    # 从 pulse 模块全局找
    if hasattr(pulse_module, cls_name):
        cls = getattr(pulse_module, cls_name)
        if inspect.isclass(cls) and issubclass(cls, BaseOp):
            return cls
    # 递归子类树（适合你以后把 pulse 分文件）
    stack = [BaseOp]
    seen = set()
    while stack:
        c = stack.pop()
        for sub in c.__subclasses__():
            if sub in seen: 
                continue
            seen.add(sub)
            if sub.__name__ == cls_name:
                return sub
            stack.append(sub)
    raise TypeError(f"Unknown pulse class: {cls_name}")

def encode_pulse(pulse: BaseOp) -> Dict[str, Any]:
    """
    将脉冲对象编码为可 JSON 序列化的 dict
    """
    return {"__pulse__": type(pulse).__name__, "fields": pulse.to_fields()}


def object_hook(obj: Dict[str, Any]):
    # 先处理 ParamRef
    if isinstance(obj, dict) and obj.get("type") == "paramRef":
        name = obj["name"]
        val = obj.get("value", None)
        return ParamRef(name, default=val)

    # 再处理 Pulse 包装
    if isinstance(obj, dict) and "__pulse__" in obj:
        cls_name = obj["__pulse__"]
        fields = obj.get("fields", {})
        cls = _get_pulse_cls(cls_name)
        return cls.from_fields(fields)

    return obj


def stable_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_of_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def normalize_seq_json(raw: dict) -> dict:
    # 深复制后，递归将 ParamRef 的 "val" 清空，仅保留 "name"/"type"
    def _walk(x):
        if isinstance(x, dict):
            if x.get("type") == "paramRef":
                return {"type":"paramRef"}
            return {k:_walk(v) for k,v in sorted(x.items())}
        elif isinstance(x, list):
            return [_walk(v) for v in x]
        else:
            return x
    return _walk(deepcopy(raw))

def pulse_to_str(pulse: BaseOp) -> str:
    """将脉冲转化为字符串"""
    assert isinstance(pulse, BaseOp)
    allowed = pulse.to_fields()
    param = ''
    for k in allowed:
        if k == 'qubit_index': continue
        v = getattr(pulse, k)
        if isinstance(v, ParamRef): param += f'{k}=ParamRef("{v.name}", {v.val}),'
        elif isinstance(v, str): param += f'{k}="{v}",'
        else: param += f'{k}={v},'
    pulse_str = f'{type(pulse).__name__}({param})'
    if (targets := getattr(pulse, 'qubit_index')) is not None:
        pulse_str += f'.on({targets})'
    return pulse_str
