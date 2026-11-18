from typing import Any, Dict, List, Tuple, Optional
from matplotlib import pyplot as plt
from copy import deepcopy
import numpy as np
import time
import json
import re
import asyncio
import traceback
from pathlib import Path
from .pulse import *
from .sequence import *
from core.config import *
from .data import H5SingleJob, ImshowData, PlotData
from .plot import *
from .models import Progress, Status
from .plugin import AnalysisManager, PostprocessManager
from .runner import *
from dataclasses import dataclass
from .rule import *
from .utils import (
    SpecKeySchema,
    AxisKeySchema,
    MetaKeySchema,
    ExpandSchema,
    SweepKeySchema,
    VNAKeySchema,
    RBMetaKeySchema,
    XYMetaKeySchema,
    get_scan_values,
    expand_points,
    _sweep_axis_shape,
    build_circuit_sweep_groups,
    expand_circuit_sweep_groups,
    normalize_circuit_selector_axes,
    is_sweep_group,
)
from ..utils import SettingConfig, dotpath_to_dict, deep_update
from ..inst.utils import DriveTypeSchema
from .mylog import *
from .iqClassifier import Classifier
from .dataPrecondition import dataConditioner 
from collections import defaultdict
from ..inst.instrument_base import InstrumentBase

@dataclass
class ExperimentSpec:
    meta: Dict[str, Any]
    post_process: List[str]
    analysis: List[str]
    static_config: Dict[str, Any]
    sequence_variants: Dict[str, Any]
    sweeps: List[Dict[str, Any]]
    sweep_groups: List[Dict[str, Any]]
    measurement_counts: bool = False

def _map_gate_targets(gate: dict, mapping: dict) -> list:
    qubits = gate.get("ctrls", []) + gate.get("targets", [])
    return [mapping.get(str(q), q) for q in qubits]

_NUMERIC_STR_RE = re.compile(
    r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$"
)

def _coerce_numeric(value: Any) -> Any:
    """数值型字符串（如 "5e-6"、"0.0"）转回数字，其余原样返回。

    历史版本前端保存参数时会把输入框文本原样存成字符串，污染过
    实验 static_config 与参数配置 settings.json；下游按数字运算
    （如 compile.get_xy_seq 的 cfg.xy_pi_len(q) + buff）会报
    ``can only concatenate str (not "float") to str``，这里统一归一化。
    """
    if isinstance(value, str):
        text = value.strip()
        if text and _NUMERIC_STR_RE.match(text):
            try:
                return int(text)
            except ValueError:
                return float(text)
    return value

def _deep_coerce_numeric(node: Any) -> Any:
    """递归归一化 dict/list 中的数值型字符串叶子。"""
    if isinstance(node, dict):
        return {k: _deep_coerce_numeric(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_coerce_numeric(v) for v in node]
    return _coerce_numeric(node)

def _as_param_list(value):
    return list(value) if isinstance(value, (list, tuple)) else [value]

def _append_gate(gates, name, targets, arg_value, gate_id, gate_pulse_conf, repeat=None):
    meta = {'id': gate_id, 'pulse': gate_pulse_conf}
    if repeat is None:
        gates.append((name, targets, arg_value, meta))
        return
    for _ in range(repeat):
        gates.append((name, targets, arg_value, meta))

def parse_to_gate(gates_config, ctx):
    gates = []
    sequence = gates_config['sequences']
    mapping = gates_config['mapping']
    for gate in sequence:
        id = gate['id']
        name = gate['gate'].lower()
        targets = _map_gate_targets(gate, mapping)
        arg_value = gate.get('params', [])
        gate_pulse_conf = {}
        repeat = None
        if id in ctx:
            gate_ctx = ctx[id]
            if isinstance(gate_ctx, dict):
                if 'name' in gate_ctx:
                    name = str(gate_ctx['name']).lower()
                if 'params' in gate_ctx:
                    arg_value = _as_param_list(gate_ctx['params'])
                if 'pulse' in gate_ctx:
                    gate_pulse_conf = gate_ctx['pulse']
                repeat = gate_ctx.get('num')
        _append_gate(gates, name, targets, arg_value, id, gate_pulse_conf, repeat)
    return gates


class Experiment:
    """
    扫描实验类
    Args:
        config: 全局配置文件
        spec: 实验配置
    """
    def __init__(self, config: SettingConfig, spec: ExperimentSpec, exp_id: str = None):
        
        self.config = config
        self.spec = spec
        self.ppm = PostprocessManager()
        self.analysis_manager = AnalysisManager()
        self.last_analysis = None
        
        self.exp_id = exp_id or "test"
        handler, exp_log = create_experiment_logger(self.exp_id)
        self.handler = handler
        self.log = exp_log
        self.log.info("创建实验成功")
        
    @classmethod
    def load(cls, config: [str, dict], spec: [str, dict], exp_id: str = None):
        
        spec_dict = spec 
        spec_base_dir = Path.cwd()
        if isinstance(spec, str):
            spec_base_dir = Path(spec).resolve().parent
            with open(spec, 'r', encoding='utf-8') as f:
                spec_dict = json.loads(f.read())
        
        config_dict = config
        if isinstance(config, str):
            with open(config, 'r', encoding='utf-8') as f:
                config_dict = json.loads(f.read())
        # 参数配置历史上可能被前端存入数值型字符串（如 Q25.gate.U_gates.pi_len="1.41e-8"），
        # 统一在加载时归一化，防止下游数字运算报 str+float
        config_dict = _deep_coerce_numeric(config_dict)
        
        def _resolve_plugin_paths(paths):
            resolved = []
            for p in paths or []:
                path = Path(p)
                if not path.is_absolute():
                    candidates = [Path.cwd() / path, spec_base_dir / path]
                    # 函数资产目录：post_process 脚本常存于 assets/functions/post/，
                    # 相对路径（如 ./iq_process.py）可在此解析到
                    candidates.append(Path.cwd() / "assets" / "functions" / "post" / path.name)
                    # 自包含实验的 post_process：形如 ./exp_sample/exp_xxx/postprocess.py 或
                    # ./postprocess.py；脚本随实验目录存放于 assets/experiments/{group}/exp_{meta.name}/。
                    # exp_sample 路径直接取 exp_xxx；否则按实验 meta.name 推断
                    exp_name = None
                    if len(path.parts) >= 2 and path.parts[0] == "exp_sample":
                        exp_name = path.parts[1]
                    else:
                        exp_name = "exp_" + str((spec_dict.get("meta") or {}).get("name", ""))
                    if exp_name:
                        for group in ("single_qubit", "double_qubit", "single_qubit_parallel", "na"):
                            candidates.append(
                                Path.cwd() / "assets" / "experiments" / group / exp_name / path.name
                            )
                    candidates.extend(parent / path for parent in spec_base_dir.parents)
                    path = next(
                        (candidate for candidate in candidates if candidate.exists()),
                        spec_base_dir / path,
                    )
                resolved.append(str(path.resolve()))
            return resolved

        meta = spec_dict.get(SpecKeySchema.meta, {})
        top_sweeps = spec_dict.get(SpecKeySchema.sweeps, []) or []
        if not isinstance(top_sweeps, list):
            raise TypeError(
                f"{SpecKeySchema.sweeps} 必须是 list，实际是 {type(top_sweeps).__name__}"
            )
        sweep_groups = build_circuit_sweep_groups(
            spec_dict.get(SpecKeySchema.sequence_variants, {}),
            top_sweeps,
            mode=meta.get(MetaKeySchema.expand_mode, ExpandSchema.cartesian),
        )

        exp_spec = ExperimentSpec(meta=meta, 
                   static_config=spec_dict.get(SpecKeySchema.static_config, {}),
                   sequence_variants=spec_dict.get(SpecKeySchema.sequence_variants, {}),
                   post_process=_resolve_plugin_paths(spec_dict.get(SpecKeySchema.post_process, [])),
                   analysis=_resolve_plugin_paths(spec_dict.get(SpecKeySchema.analysis, [])),
                   sweeps=top_sweeps,
                   sweep_groups=sweep_groups,
                   measurement_counts=meta.get(MetaKeySchema.measurement_counts, False))
        
        exp_type =  exp_spec.meta.get(MetaKeySchema.type, "0")
        if exp_type == "1":
            return ScanExperiment(config=SettingConfig(config_dict), spec=exp_spec, exp_id=exp_id)
        elif exp_type == "2":
            return VNAExperiment(config=SettingConfig(config_dict), spec=exp_spec, exp_id=exp_id)
        elif exp_type == '3':
            return RBExperiment(config=SettingConfig(config_dict), spec=exp_spec, exp_id=exp_id)
        elif exp_type == '4':
            return XYExperiment(config=SettingConfig(config_dict), spec=exp_spec, exp_id=exp_id)
        else:
            raise ValueError(f"实验类型 {exp_type} 不存在")
        
    def _apply_static_config(self, cfg: SettingConfig) -> None:
        for k, v in (self.spec.static_config or {}).items():
            cfg.apply_dotpath(k, _coerce_numeric(v))

    def _expand_circuit_wildcard_axis(self, seq_config: dict, path: str, value: Any) -> dict | None:
        parts = path.split(".")
        if len(parts) < 4 or parts[:3] != ["exp", "cir", "*"]:
            return None

        circuits = seq_config.get(AxisKeySchema.seq_map_key, {})
        if not isinstance(circuits, dict) or not circuits:
            raise KeyError("circuit_variants 缺失或 circuits 为空，无法展开 exp.cir.* 扫描")

        expanded: dict = {}
        suffix = ".".join(parts[3:])
        for circuit_name in circuits:
            deep_update(expanded, dotpath_to_dict(f"exp.cir.{circuit_name}.{suffix}", value))
        return expanded
    
    def _apply_axes_overrides(self, cfg: SettingConfig, seq_config: dict, axes: dict) -> tuple[dict, dict]:
        whole_ctx: dict = {}
        axes = normalize_circuit_selector_axes(axes or {})
        for k, v in axes.items():
            if k.startswith(AxisKeySchema.cfg_prefix):
                cfg.apply_dotpath(k, v)
                continue

            if not k.startswith(AxisKeySchema.exp_prefix):
                continue

            if k == AxisKeySchema.exp_sequence_variants_default:
                seq_config[AxisKeySchema.seq_default_key] = v
                continue

            if k.startswith(AxisKeySchema.exp_sequence_variants_root + "."):
                d = self._expand_circuit_wildcard_axis(seq_config, k, v)
                if d is None:
                    d = dotpath_to_dict(k, v)
                exp_root = d.get(AxisKeySchema.exp_prefix.rstrip("."), {})
                sv = exp_root.get("cir", {})
                whole_ctx = deep_update(whole_ctx, sv)
                continue

        return seq_config, whole_ctx
    
    def post_process(self, raw_data):
        """依次运行所有后处理函数"""

        
        measurement_counts = self.spec.measurement_counts
        
        now_data = raw_data
        for pp_file in self.spec.post_process:
            #self.log.info(f"运行后处理函数：{pp_file}")
            if measurement_counts:
                now_data = dataConditioner(now_data,self.config, meta=self.spec.meta)
            now_data = self.ppm.run(pp_file, now_data, meta=self.spec.meta)
        return now_data

    def _get_analysis_axis_shape(self) -> list[int]:
        if self.spec.sweep_groups:
            return [group["point_count"] for group in self.spec.sweep_groups]
        expand_mode = self.spec.meta.get(MetaKeySchema.expand_mode, ExpandSchema.cartesian)
        return _sweep_axis_shape(self.spec.sweeps or [], mode=expand_mode)

    def _reshape_grouped_analysis_results(self, results):
        values = np.asarray(results)
        groups = self.spec.sweep_groups or []
        expected_points = sum(group["point_count"] for group in groups)
        if values.shape[0] != expected_points:
            raise ValueError(
                f"扫描结果点数和 circuit sweeps 不匹配: result={values.shape[0]}, "
                f"expected={expected_points}"
            )

        grouped = {}
        offset = 0
        for group in groups:
            count = group["point_count"]
            chunk = values[offset:offset + count]
            offset += count
            shape = group.get("axis_shape", [])
            if shape:
                if chunk.size == count:
                    grouped[group["name"]] = chunk.reshape(shape)
                else:
                    grouped[group["name"]] = chunk.reshape(shape + list(chunk.shape[1:]))
            else:
                grouped[group["name"]] = chunk[0] if count == 1 else chunk
        return grouped

    def _reshape_analysis_results(self, results):
        if self.spec.sweep_groups:
            return self._reshape_grouped_analysis_results(results)

        axis_shape = self._get_analysis_axis_shape()
        expected_points = int(np.prod(axis_shape, dtype=int)) if axis_shape else 1
        values = np.asarray(results)

        if values.shape[0] != expected_points:
            raise ValueError(
                f"扫描结果点数和 sweeps 不匹配: result={values.shape[0]}, "
                f"sweeps={axis_shape}, expected={expected_points}"
            )

        if values.size == expected_points:
            return values.reshape(axis_shape)

        return values.reshape(axis_shape + list(values.shape[1:]))

    def analyze(self, results):
        """整次扫描结束后运行 analysis 插件。"""
        now_data = self._reshape_analysis_results(results)
        for analysis_file in self.spec.analysis or []:
            now_data = self.analysis_manager.run(
                analysis_file,
                now_data,
                self.config,
                self.spec,
                PlotData,
                ImshowData,
            )
        return now_data
    
    def preflight(self, requires_devs, **kwargs):
        """
        占用需要的设备
        """
        # 确保设备都连接，并将状态设备未忙碌（在使用中），先连接同步器
        
        attach_handler_to_devices(self.handler, requires_devs)
        
        self.log.info(f"=====开始设备连接与初始化=====")
        
        #for dev in requires_devs:
        #    for model in getattr(dev, "support_models", []):
        #        if model == DriveTypeSchema.CY_SYNC:
        #            if not dev.is_connected(): dev.open(**kwargs)
        
        for dev in requires_devs:
            if not dev.is_connected(): dev.open(**kwargs)
            self.log.info(f"设备 {dev.name} 连接成功")
            dev.set_busy(busy=True)
        
        self.log.info(f"=====开始设备同步=====")
        sync_groups = defaultdict(list)
        for dev in requires_devs:
            if getattr(dev, "sync_bind", None):
                sync_groups[(dev.sync_bind, dev.group)].append(dev)
        
        for (sync_name, group), devs in sync_groups.items():
            sync_dev = InstrumentBase.find_instrument(sync_name, group)
            if sync_dev not in requires_devs:
                raise ValueError(f"同步设备 {sync_name} 未在 requires_devs 中")
            
            sync_dev.sync(devs)
            for dev in devs:
                self.log.info(f"设备 {dev.name} 同步至 {sync_dev.name}")          
    
    def release(self, requires_devs):
        """
        释放所有设备
        """
        self.log.info(f"设备释放")
        
        for dev in requires_devs:
            dev.set_busy(busy = False)
        
        detach_handler_from_devices(self.handler, requires_devs)

    def close(self):
        """
        关闭实验，释放日志 handler 资源
        """
        if self.log is not None:
            close_experiment_logger(self.log)
        self.handler = None
        self.log = None

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时自动释放资源"""
        self.close()
        return False

    # ---------- 异步扫描模板（子类通过重写钩子实现） ----------

    def _get_scan_points(self) -> List[Dict[str, Any]]:
        """返回本次扫描的所有 (axes) 点。"""
        expand_mode = self.spec.meta.get(MetaKeySchema.expand_mode, ExpandSchema.cartesian)
        if self.spec.sweep_groups:
            return expand_circuit_sweep_groups(self.spec.sweep_groups, mode=expand_mode)
        return expand_points(self.spec.sweeps, mode=expand_mode)

    def _prepare_progress_async(self, progress: Progress, points: List[Dict[str, Any]]) -> None:
        """在异步扫描开始前填充 progress（run_type、sweep_list 等）。子类可重写。"""
        pass

    def _plan_resources_for_async(self, points: List[Dict[str, Any]]) -> tuple:
        """返回 (requires_devs, preflight_kw)。preflight_kw 将传给 preflight(requires_devs, **preflight_kw)。"""
        raise NotImplementedError

    def _accumulate_result(self, result_list: list, point_result: Any) -> None:
        """将单点结果并入总结果（默认 append；VNA 用 extend）。"""
        result_list.append(point_result)

    def _update_progress_done_points(
        self, progress: Progress
    ) -> None:
        """每点完成后更新 progress done_points"""
        progress.done_points += 1

    def _update_progress_meta(self, progress: Progress, elapsed: float) -> None:
        progress.throughput_pts_per_s = progress.done_points / elapsed
        rem = progress.total_points - progress.done_points
        progress.eta_sec = rem / (progress.throughput_pts_per_s + 1e-6)
        
    def run_point(self, axes: dict):
        raise NotImplementedError("子类需实现 run_point(axes)")
    
    async def run_point_async(self, axes: dict):
        return await asyncio.to_thread(self.run_point, axes)

    async def execute_scan_async(
        self,
        progress: Progress,
        should_stop: callable
    ) -> None:
        """
        异步扫描的模板方法：准备 progress、占设备、跑点、写 H5、释放设备。
        
        Args:
            progress: 进度对象
            result_sink: 结果回调
            should_stop: 停止标志检查函数
        """
        job_id = self.exp_id
        progress.status = Status.running
        progress.started_at = progress.updated_at = time.time()
        progress.group = self.spec.meta.get(MetaKeySchema.group, "default")
        points = self._get_scan_points()
        progress.sweep_list = (
            list(self.spec.sweep_groups)
            if self.spec.sweep_groups
            else list(self.spec.sweeps or [])
        )
        progress.expand_mode = self.spec.meta.get(
            MetaKeySchema.expand_mode, ExpandSchema.cartesian
        )
        self._prepare_progress_async(progress, points)
        
        total_points = len(points)
        expected_shape = self._get_result_shape(total_points)
        progress.total_points = expected_shape[0]
        
        # 默认每点 flush：保证运行中 h5 结果可读，前端才能增量绘图；
        # 大数据实验可通过 meta.batch_size 调大（批量写，性能优先）
        batch_size = self.spec.meta.get(MetaKeySchema.batch_size, 1)
        collect_analysis_results = bool(self.spec.analysis)
        writer = H5SingleJob(
            Global_Setting.get_datapath(),
            expected_shape=expected_shape,
            title=job_id,
            flush_interval=batch_size,
        )
        writer.create_meta(progress.model_dump())
        
        requires_devs = None
        preflight_ok = False
        analysis_results = None
        try:
            requires_devs, preflight_kw = self._plan_resources_for_async(points)
            self.preflight(requires_devs, **preflight_kw)
            preflight_ok = True
            analysis_results = await self._run_scan_async_loop_streaming(
                points,
                progress,
                should_stop,
                writer,
                batch_size,
                collect_results=collect_analysis_results,
            )
        except Exception as e:
            progress.status = Status.error
            # KeyError 等异常 str(e) 可能只有 "1" 这种无信息量内容，带上异常类型
            progress.msg = f"{type(e).__name__}: {e}"
            progress.traceback = traceback.format_exc()
        finally:
            if preflight_ok and requires_devs is not None:
                try:
                    self.release(requires_devs)
                except Exception as e:
                    progress.status = Status.error
                    progress.msg = (progress.msg or "") + f" | release failed: {e}"
                    progress.traceback = (progress.traceback or "") + "\n" + traceback.format_exc()
        if progress.status == Status.done and collect_analysis_results:
            try:
                self.last_analysis = self.analyze(analysis_results)
                writer.write_dataset("results/analysis", self.last_analysis)
            except Exception as e:
                progress.status = Status.error
                progress.msg = f"analysis failed: {e}"
                progress.traceback = traceback.format_exc()
        if progress.status == Status.running:
            progress.status = Status.done
        progress.updated_at = time.time()
        writer.update_meta(progress.model_dump())
        writer.close()
    
    def _get_result_shape(self, total_points: int):
        """获取结果数组的预期形状，子类可重写"""
        return (total_points,)
    
    async def _run_scan_async_loop_streaming(
        self,
        points: list,
        progress: Progress,
        should_stop: callable,
        writer: 'H5SingleJob',
        batch_size: int,
        collect_results: bool = False,
    ):
        """流式写入的扫描循环，每完成一个点立即写入HDF5"""
        
        t0 = time.time()
        batch = []
        collected = [] if collect_results else None
        
        for i, ax in enumerate(points):
            if should_stop():
                progress.status = Status.canceled
                break
            t1 = time.time()
            try:
                res = await self.run_point_async(ax)
                self._accumulate_result(batch, res)
                if collected is not None:
                    self._accumulate_result(collected, res)

                elapsed = t1 - t0 + 1e-6
                progress.current_axes = ax
                # 先完成 done_points 计数再落盘：保证写进 manifest 的 done_points
                # 与已写入的数据行数一致，否则前端实时读取（count[:done_points]）
                # 会恒定缺失最后一个已完成的扫描点
                self._update_progress_done_points(progress)
                self._update_progress_meta(progress, elapsed)
                progress.updated_at = time.time()

                if len(batch) >= batch_size:
                    writer.append_batch(batch)
                    batch = []
                    writer.update_meta(progress.model_dump())
                
                if progress.status == Status.canceled:
                    break
            except Exception as e:
                progress.status = Status.error
                progress.msg = f"{type(e).__name__}: {e}"
                progress.traceback = traceback.format_exc()
                break
        
        if batch:
            writer.append_batch(batch)
        
        if progress.status == Status.running:
            progress.status = Status.done
        progress.updated_at = time.time()
        return collected


class VNAExperiment(Experiment):
    
    def __init__(self, config: SettingConfig, spec: ExperimentSpec, exp_id: str = None):
        
        super().__init__(config = config, spec=spec, exp_id=exp_id)
        self.runner = VNARunner()
    
    def _vna_scan_mode(self):
        scan_mode = 0
        if VNAKeySchema.start in self.spec.static_config:
            scan_mode = 1
        return scan_mode
    
    def _vna_sweep(self):
        
        cfg = self.config.clone()
        self._apply_static_config(cfg)
        scan_mode = self._vna_scan_mode()
        if scan_mode == 0:
            data = {
                "start": cfg.na_center() - cfg.na_span(),
                "stop": cfg.na_center() + cfg.na_span(),
                "points": cfg.na_points()
            }
        elif scan_mode == 1:
            data = {
                "start": cfg.na_start(),
                "stop": cfg.na_stop(),
                "points": cfg.na_points()
            }
        else:
            raise ValueError(f"扫描模式错误")
    
        return data
    
    def run_point(self, axes):
        """运行当前扫描点对应的线路"""
        
        self.log.info(f"扫描点运行：{axes}")
        
        cfg = self.config.clone()
        self._apply_static_config(cfg)
        self._apply_axes_overrides(cfg, {}, axes)
        raw_data = self.runner.get_raw_data(cfg, meta=self.spec.meta, scan_mode=self._vna_scan_mode())
        return self.post_process(raw_data)
    
    def execute_scan(self)->list:
        
        points = self._get_scan_points()
        requires_devs, ch = self.plan_resources()    
        self.preflight(requires_devs, sync=False, ch=ch)
        try:
            result = []
            for i, ax in enumerate(points):
                res = self.run_point(ax)
                result.extend(res)        
            if self.spec.analysis:
                self.last_analysis = self.analyze(result)
            return result
        finally:
            self.release(requires_devs)
    
    def plan_resources(self):
        """获取用到的设备。"""
        group = self.spec.meta.get(MetaKeySchema.group, "default")
        na_dev, ch = get_dev_and_ch(
            0, self.config, ChannelTypeSchema.na, group=group
        )
        dev = {na_dev}
        qubit = self.spec.meta.get(MetaKeySchema.qubit, -1)
        if qubit >= 0:
            z_dev, _ = get_dev_and_ch(
                qubit, self.config, ChannelTypeSchema.z, group=group
            )
            dev.add(z_dev)
        return dev, ch

    def _prepare_progress_async(self, progress: Progress, points: List[Dict[str, Any]]) -> None:
        progress.run_type = "vna"
        progress.sweep_list = list(progress.sweep_list or []) + [{
            SweepKeySchema.bindings: "vna_freq",
            SweepKeySchema.values: self._vna_sweep(),
        }]

    def _plan_resources_for_async(self, points: List[Dict[str, Any]]) -> tuple:
        requires_devs, ch = self.plan_resources()
        return requires_devs, {"sync": False, "ch": ch}

    def _accumulate_result(self, result_list: list, point_result: Any) -> None:
        result_list.extend(point_result)

    def _get_result_shape(self, total_points: int):
        return (total_points * len(get_scan_values(self._vna_sweep())), )

    def _get_analysis_axis_shape(self) -> list[int]:
        return super()._get_analysis_axis_shape() + [
            len(get_scan_values(self._vna_sweep()))
        ]

    def _update_progress_done_points(self, progress: Progress) -> None:
        progress.done_points += len(get_scan_values(self._vna_sweep()))




@dataclass
class ScanData:
    data: np.ndarray
    spec: ExperimentSpec
    config: SettingConfig
    axis_names: List[str]
    axis_values: List[np.ndarray]
    axis_bindings: List[str]

    @property
    def axes(self) -> Dict[str, np.ndarray]:
        return {
            name: values
            for name, values in zip(self.axis_names, self.axis_values)
        }

    def getAxesValue(self, axesName):
        for name, values in zip(self.axis_names, self.axis_values):
            if name == axesName:
                return values.tolist()
        for binding, values in zip(self.axis_bindings, self.axis_values):
            if binding == axesName:
                return values.tolist()
        raise KeyError(
            f"不存在扫描轴 {axesName!r}，可用 axis_names={self.axis_names}, "
            f"axis_bindings={self.axis_bindings}"
        )



class ScanExperiment(Experiment):
    """
    扫描实验类
    Args:
        config: 全局配置文件
        spec: 实验配置
    """
    def __init__(self, config: SettingConfig, spec: ExperimentSpec, exp_id: str = None, rules = None):
        
        super().__init__(config = config, spec=spec, exp_id=exp_id)
        self.shots = spec.meta.get(MetaKeySchema.shots, 100) 
        self.runner = SequenceRunner()
        rules = rules or default_rules()
        self.normalizer = Normalizer(rules=rules)

    def _resolve_sequence_variant(self, seq_config: dict, whole_ctx: dict) -> tuple[str, list]:
        seq_name = seq_config.get(AxisKeySchema.seq_default_key)
        if not seq_name:
            raise KeyError(
                f"circuit_variants 缺失键值 '{AxisKeySchema.seq_default_key}'. "
            )

        sequences = seq_config.get(AxisKeySchema.seq_map_key, {})
        if seq_name not in sequences:
            raise KeyError(f"不存在线路：'{seq_name}'. 可选线路： {list(sequences.keys())}")

        gates = parse_to_gate(
            sequences[seq_name],
            whole_ctx.get(seq_name, {})
        )
        return gates

    def _build_sequence_from_gates(self, setting_config: SettingConfig, gates: list):
        """由 gates 编译并构建 Sequence，供 get_sequence 复用。"""
        from ..compiler.compile import Compiler

        comp = Compiler(config=setting_config, opt=False)
        qset, pulses, _ = comp.compile(gates)
        group = self.spec.meta.get(MetaKeySchema.group, "default")
        seq = Sequence(qset, config=setting_config, group=group)
        seq.add_pulses(*pulses)
        return seq

    def _format_gates_for_debug(self, gates: list) -> str:
        chunks = []
        for name, targets, params in gates:
            params_text = "" if not params else f"({', '.join(map(str, params))})"
            chunks.append(f"{name.upper()}{params_text}@{targets}")
        return " -> ".join(chunks)

    def _get_default_circuit_config(self) -> dict:
        seq_config = self.spec.sequence_variants or {}
        seq_name = seq_config.get(AxisKeySchema.seq_default_key)
        if not seq_name:
            raise KeyError(f"circuit_variants 缺失键值 '{AxisKeySchema.seq_default_key}'")
        sequences = seq_config.get(AxisKeySchema.seq_map_key, {})
        if seq_name not in sequences:
            raise KeyError(f"不存在线路：'{seq_name}'. 可选线路： {list(sequences.keys())}")
        return sequences[seq_name]

    def _infer_measure_qubit(self):
        meta_qubit = self.spec.meta.get(MetaKeySchema.qubit)
        if meta_qubit is not None:
            return meta_qubit

        circuit = self._get_default_circuit_config()
        mapping = circuit.get("mapping", {})
        measure_targets = []
        for gate in circuit.get("sequences", []):
            if gate.get("gate", "").lower() != "measure":
                continue
            for target in gate.get("targets", []):
                measure_targets.append(mapping.get(str(target), target))

        measure_targets = list(dict.fromkeys(measure_targets))
        if len(measure_targets) != 1:
            raise ValueError(
                f"无法唯一推断测量 qubit: {measure_targets}，请在 meta.qubit 中指定"
            )
        return measure_targets[0]

    def _prepare_sequence_for_axes(self, axes: dict):
        cfg = self.config.clone()
        self._apply_static_config(cfg)
        seq_config = deepcopy(self.spec.sequence_variants or {})
        seq_config, whole_ctx = self._apply_axes_overrides(
            cfg=cfg, seq_config=seq_config, axes=axes
        )
        setting_raw = self.normalizer.apply(cfg_point=cfg.raw())
        setting_config = SettingConfig(setting_raw)
        gates = self._resolve_sequence_variant(seq_config, whole_ctx)
        seq = self._build_sequence_from_gates(setting_config, gates)
        return setting_config, gates, seq

    def get_sequence(self, axes: dict):
        """根据扫描参数修改配置文件，并获取实验所需的脉冲序列。"""
        _, _, seq = self._prepare_sequence_for_axes(axes)
        return seq     
    
    def run_point(self, axes: dict):
        """运行当前扫描点对应的线路"""
        self.log.info(f"扫描点运行：{axes}")
        #_, gates, seq = self._prepare_sequence_for_axes(axes)
        seq = self.get_sequence(axes)
        raw_data = self.runner.get_raw_data(seq, shots=self.shots, meta=self.spec.meta)
        return self.post_process(raw_data)
    
    def execute_scan(self):
        
        points = self._get_scan_points()
        requires_devs = self.plan_resources(points)    
        self.preflight(requires_devs)
        try:
            result = []
            for i, ax in enumerate(points):
                res = self.run_point(ax)
                result.append(res)        
            if self.spec.analysis:
                self.last_analysis = self.analyze(result)
            return result
        finally:
            self.release(requires_devs)


    def getQstScanedData(self)->ScanData:
        if self.spec.sweep_groups:
            raise ValueError("getQstScanedData 只支持顶层矩形 sweeps，不支持 circuit 自定义 sweeps")

        sweeps = self.spec.sweeps

        expand_mode = self.spec.meta.get(MetaKeySchema.expand_mode, ExpandSchema.cartesian)
        if expand_mode != ExpandSchema.cartesian:
            raise ValueError(f"getScanedData 只支持 cartesian 扫描，不支持: {expand_mode}")
        if any(is_sweep_group(sweep) for sweep in sweeps or []):
            raise ValueError("getScanedData 只支持顶层简单 cartesian sweeps，不支持嵌套 sweep group")

        shape = []
        axis_names = []
        axis_values = []
        axis_bindings = []
        for sweep in sweeps:
            sweep_type = sweep.get("type")
            if sweep_type not in {"global_config", "seq_sweep", "gate_param", "gate_variant"}:
                name = sweep.get("name", "<unnamed>")
                raise ValueError(
                    f"getScanedData 不支持 type={sweep_type!r} 的 sweep，"
                    f"{name!r} 的 type 是 {sweep_type!r}"
                )

            values = np.asarray(get_scan_values(sweep.get(SweepKeySchema.values)))
            shape.append(len(values))
            axis_names.append(sweep.get("name", sweep.get(SweepKeySchema.bindings)))
            axis_values.append(values)
            axis_bindings.append(sweep.get(SweepKeySchema.bindings))

        clf = Classifier(self.config)   # 分类
        qubit = self._infer_measure_qubit()
        prob0_list = []

        rawRes = self.execute_scan()
        for Ilist, Qlist in rawRes:
            prob0_list.append(clf.getProb0(qubit, Ilist, Qlist))

        rawArray = np.asarray(rawRes)
        expected_points = int(np.prod(shape, dtype=int)) if shape else 1
        if rawArray.shape[0] != expected_points:
            raise ValueError(
                f"扫描结果点数和 sweeps 不匹配: result={rawArray.shape[0]}, "
                f"sweeps={shape}, expected={expected_points}"
            )

        data = np.asarray(prob0_list).reshape(shape)
        return ScanData(
            data=data,
            spec=self.spec,
            config=self.config.clone(),
            axis_names=axis_names,
            axis_values=axis_values,
            axis_bindings=axis_bindings,
        )



        
    def _prepare_progress_async(self, progress: Progress, points: List[Dict[str, Any]]) -> None:
        progress.run_type = "scan"
        progress.shots_total = len(points) * self.shots

    def _plan_resources_for_async(self, points: List[Dict[str, Any]]) -> tuple:
        requires_devs = self.plan_resources(points)
        return requires_devs, {}

    def _update_progress_done_points(
        self, progress: Progress
    ) -> None:
        progress.done_points += 1
        progress.shots_done = progress.done_points * self.shots

    def plan_resources(self, points):
        """
        获取所有实验用到的设备
        """
        sync = get_hardware(self.spec.meta.get(MetaKeySchema.sync_inst, 'SYNC'), group=self.spec.meta.get(MetaKeySchema.group, 'default'))
        requires_devs = {sync}
        
        sample_point = [points[0]]
        if len(points) > 1:
            sample_point.append(points[-1])
        if len(points) > 2:
            sample_point.append(points[len(points)//2])
        
        for ax in sample_point:
            seq = self.get_sequence(ax)
            devs = seq.required_devices()
            requires_devs |= devs
        return requires_devs    

class CirExperiment(ScanExperiment):
    """基于电路（gates）的扫描实验，由子类实现 get_gates(axes)。"""

    def get_gates(self, axes: dict):
        raise NotImplementedError("子类需实现 get_gates(axes)")

    def get_sequence(self, axes: dict):
        cfg = self.config.clone()
        self._apply_static_config(cfg)
        setting_raw = self.normalizer.apply(cfg_point=cfg.raw())
        setting_config = SettingConfig(setting_raw)
        gates = self.get_gates(axes)
        return self._build_sequence_from_gates(setting_config, gates)   

class XYExperiment(CirExperiment):
    """ALLXY 测试实验。"""

    def __init__(
        self,
        config: SettingConfig,
        spec: ExperimentSpec,
        exp_id: str = None,
        rules=None,
    ):
        from ..circuit.allxy import AllXY

        super().__init__(config=config, spec=spec, exp_id=exp_id, rules=rules)
        qubit = self.spec.meta.get(MetaKeySchema.qubit, 0)
        if isinstance(qubit, int):
            qubit = [qubit]
        repeat = self.spec.meta.get(MetaKeySchema.repeat, 1)
        self.allxy = AllXY(targets=qubit, repeat=repeat)
        self.spec.sweeps.append({
            SweepKeySchema.bindings: XYMetaKeySchema.circ,
            SweepKeySchema.values: self.allxy.xy,
        })

    def get_gates(self, axes: dict):

        gates = self.allxy.xy_seq(xy=axes.get(XYMetaKeySchema.circ))
        return gates    

class RBExperiment(CirExperiment):
    """随机基准（RB）测试实验。"""

    def __init__(
        self,
        config: SettingConfig,
        spec: ExperimentSpec,
        exp_id: str = None,
        rules=None,
    ):
        from ..circuit.clifford import SingleQubitRB

        super().__init__(config=config, spec=spec, exp_id=exp_id, rules=rules)
        qubit = self.spec.meta.get(MetaKeySchema.qubit, 0)
        if isinstance(qubit, int):
            qubit = [qubit]
        self.run_seed = self.spec.meta.get(RBMetaKeySchema.run_seed, 1000)
        self.repeat = self.spec.meta.get(MetaKeySchema.repeat, 1)
        self.spec.sweeps.append({
            SweepKeySchema.bindings: MetaKeySchema.repeat,
            SweepKeySchema.values: list(range(self.repeat)),
        })
        self.rb = SingleQubitRB(targets=qubit)

    def get_gates(self, axes: dict):

        gates = self.rb.rb_sequence(m=axes.get(RBMetaKeySchema.clifford_m), k = axes.get(MetaKeySchema.repeat), run_seed=self.run_seed)
        
        return gates    
