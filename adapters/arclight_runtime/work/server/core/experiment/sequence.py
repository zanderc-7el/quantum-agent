import numpy as np
from .pulse import *
from .param import *
import copy
import json
from .utils import *
from collections import defaultdict
from collections.abc import Iterable
from .wave import *
from ..utils import SettingConfig
from .runner import get_dev_and_ch

class Sequence:
    """
    序列类
    Args:
        qset (set, optional): 序列中用到的比特.
        config（dict）: 配置文件
    """

    def __init__(self, qset=set(), config: SettingConfig=None, group: str="defualt"):
        self.current_time = 0 #表示上一个门结束的时间
        self.input_list = []
        self.qset = qset
        self.pulse_sequence = []
        self.time_stamp = defaultdict(float)
        self.pulse_list_per_qubit = defaultdict(list)
        self.awg_data = {}
        self.param_ref = {}
        self.param_ctx = {}
        self.struct_hash = None
        self.config = config
        self.group = group
        self.gate_start = 0
        self.measure_list = []
        
    def __check_op(self, op: BaseOp):
        
        qubits = op.qubit_index
        assert qubits != None
        
        assert not (op.param_ref.keys() & self.param_ref.keys())
        for k in op.param_ref:
            self.param_ref[k] = op.param_ref[k]
        

    def __add_pulse(self, pulse: BasePulse | CompositePulse):
        """
        脉冲时序分析
        根据脉冲的作用比特、延迟，以及ASAP，求出脉冲的起始时间
        根据脉冲的起始时间以及持续时间计算出结束时间，并更新作用比特对应的时间，以便对后续脉冲进行分析
        
        Args:
            pulse (_type_): 需要分析的脉冲
        """
        
        self.__check_op(pulse)
        
        tn = pulse.duration
        
        if tn == 0: return
        tl = pulse.latency

        qubits = pulse.qubit_index
        
        if isinstance(pulse, CompositePulse):
            # 所有目标比特中当前时刻最大值
            start_time = 0
            for q in qubits:
                assert q in self.qset
                start_time = max(start_time, self.time_stamp[q])
            
            if isinstance(pulse, ReadOutPulse):
                self.measure_list.extend(qubits)
            
            # 复合脉冲添加
            self.pulse_sequence.append((qubits, start_time+tl, tn, pulse.pulse_type, pulse))
            
            for q in qubits:
                self.time_stamp[q] = start_time + tl
            
            # 复合脉冲展开，每个比特添加对应脉冲
            for offset, dp in pulse.children:
                for q in dp.qubit_index:
                    if isinstance(q, int):
                        self.pulse_list_per_qubit[q].append(((self.time_stamp[q]+offset, self.time_stamp[q]+offset + dp.duration), dp))
                    else:
                        coupler_time = max(self.time_stamp[q[0]], self.time_stamp[q[1]])
                        self.pulse_list_per_qubit[q].append(((coupler_time+offset, coupler_time+offset+ dp.duration), dp))
                        
            # 每个比特当前时刻更新
            for q in qubits:
                self.time_stamp[q] = start_time + tl + tn
                            
            self.current_time = max(self.current_time, start_time + tl + tn)
            
        else:
            for q in qubits:
                if isinstance(q, int):
                    assert q in self.qset
                    start_time = self.time_stamp[q]
                    self.time_stamp[q] += tn + tl
                    self.pulse_list_per_qubit[q].append(((self.time_stamp[q]-tn, self.time_stamp[q]), pulse))
                
                else:
                    assert q[0] in self.qset and q[1] in self.qset
                    start_time = max(self.time_stamp[q[0]], self.time_stamp[q[1]])
                    self.time_stamp[q[0]] = start_time + tn + tl
                    self.time_stamp[q[1]] = start_time + tn + tl
                    self.pulse_list_per_qubit[q].append(((start_time + tl, start_time+tl+tn), pulse))

                self.current_time = max(self.current_time, start_time + tn + tl)                    
                self.pulse_sequence.append((q, start_time+tl, tn, pulse.pulse_type, pulse))                    
    
    def add_pulses(self, *args):
        """
        添加操作接口，args可以为list/tuple形式的操作序列，调用inner_add_pulses进行操作分析并添加脉冲
        这些操作将被添加到原序列的末尾
        """
        self.input_list.extend(list(args))
        self.inner_add_pulses(args)
    
    
    def inner_add_pulses(self, args):
        """
        操作分析及添加脉冲的入口函数，args可以为list/tuple形式的操作序列
        主要分3类：
            sync：同步操作
            BasePulse：脉冲操作
        Args:
            args (_type_): 脉冲序列
        """
        
        for item in args:
            for pulse in iter_pulse_or_tuple_or_list(item):
                if isinstance(pulse, BasePulse | CompositePulse):
                    self.__add_pulse(pulse)
                elif isinstance(pulse, Barrier):
                    tmax = -1
                    sync_qubits = pulse.qubit_index or self.qset
                    for q in sync_qubits:
                        tmax = max(tmax, self.time_stamp[q])
                    self.pulse_sequence.append((sync_qubits, tmax, 0, pulse.pulse_type, pulse))
                    for q in sync_qubits:
                        self.time_stamp[q] = tmax
                    self.current_time = max(self.current_time, tmax)
                elif isinstance(pulse, Wait):
                    self.__check_op(pulse)
                    tn = pulse.duration
                    tmax = 0
                    for q in pulse.qubit_index:
                        self.time_stamp[q] += tn
                        tmax = max(tmax, self.time_stamp[q])
                    self.current_time = max(self.current_time, tmax)
                elif isinstance(pulse, Gap):
                    prob_t0 = self.config.global_prob_t0()
                    gap_t = self.config.global_gap_time()
                    self.gate_start = prob_t0 - gap_t - self.current_time
                    if self.gate_start < 0:
                        raise ValueError("序列长度已超过限制")
                    for q in self.qset:
                        self.time_stamp[q] = prob_t0
                    self.current_time = prob_t0
                else:
                    raise RuntimeError("exception type pulse")
        
        self.struct_hash = self.make_struct_hash()

    def set_sequence(self, *args):
        """
        序列设置接口，args可以为list/tuple形式的操作序列
        这些操作将覆盖原序列
        """
        self.clear()
        self.input_list = list(args)
        self.inner_add_pulses(args)
    
    def expand_pulse_sequence(self):
        
        expand_pulse_sequence = []
        for q in self.pulse_list_per_qubit:
            for (ts, te), pulse in self.pulse_list_per_qubit[q]:
                expand_pulse_sequence.append((f"Q{q}", ts, te-ts, pulse.pulse_type, pulse))
        return expand_pulse_sequence
    
    def get_xy_wave(self, q: int):
        
        cfg = self.config
        xy_sr = cfg.xy_srate(q)
        return self.waves.wave(f'{ChannelTypeSchema.xy}_{q}', xy_sr, q)

    def get_z_wave(self, q: int | tuple):
        
        cfg = self.config
        z_sr = cfg.z_srate(q)    
        offset = cfg.flux_value(q)
        return self.waves.wave(f'{ChannelTypeSchema.z}_{q}', z_sr, qid=q, offset=offset)
    
    def get_ro_wave(self, q: int):
        
        cfg = self.config
        currentProb = cfg.adc_prob_key(q)
        ro_sr = cfg.da_srate(q)
        prob_t0 = cfg.global_prob_t0()
        ro = self.waves.wave(f'{ChannelTypeSchema.ro}_{currentProb}', ro_sr, q, start_at=prob_t0)    
        ro.fill = cfg.global_fill()
        return ro

    def _readout_only(self):
        """Opt into RO/AD-only operation; ordinary MEASURE retains Z bias setup."""
        raw = {} if self.config is None else self.config.raw()
        value = raw.get("ADC_Meas", {}).get("global", {}).get("readout_only", False)
        if not isinstance(value, bool):
            raise TypeError("ADC_Meas.global.readout_only must be a bool")
        if value and any(
            pulse.pulse_type != PulseType.RO
            for pulses in self.pulse_list_per_qubit.values()
            for _, pulse in pulses
        ):
            raise ValueError("readout_only=True only accepts readout pulses; XY/Z control is not allowed")
        return value

    def _preserve_existing_dc(self):
        """Allow XY/RO while leaving the station's existing DC bias untouched."""
        raw = {} if self.config is None else self.config.raw()
        global_config = raw.get("ADC_Meas", {}).get("global", {})
        value = global_config.get("preserve_existing_dc", False)
        if not isinstance(value, bool):
            raise TypeError("ADC_Meas.global.preserve_existing_dc must be a bool")
        if not value:
            return False
        if global_config.get("readout_only", False):
            raise ValueError("preserve_existing_dc and readout_only are separate, mutually exclusive modes")
        for q, pulses in self.pulse_list_per_qubit.items():
            if any(pulse.pulse_type not in (PulseType.XY, PulseType.RO)
                   for _, pulse in pulses):
                raise ValueError("preserve_existing_dc=True only accepts XY/readout pulses; Z control is forbidden")
            if self.config.z_amp(q) != 0:
                raise ValueError(f"preserve_existing_dc=True requires Q{q} U_gates.z_amp == 0")
        return True
    
    def required_devices(self):
        
        cfg = self.config
        readout_only = self._readout_only()
        preserve_dc = self._preserve_existing_dc()
        devs = set()
        for q in self.pulse_list_per_qubit:
            # Explicit cavity jobs omit control hardware. Ordinary MEASURE still
            # requires XY/Z because Z offset is part of its configured working point.
            if not readout_only:
                if not preserve_dc:
                    dev, _ = get_dev_and_ch(q, cfg, type=ChannelTypeSchema.z, group=self.group)
                    devs.add(dev)
                dev, _ = get_dev_and_ch(q, cfg, type=ChannelTypeSchema.xy, group=self.group)
                devs.add(dev)
            for (ts, te), pulse in self.pulse_list_per_qubit[q]:
                if pulse.pulse_type == PulseType.RO:
                    dev, _ = get_dev_and_ch(q, cfg, type=ChannelTypeSchema.ro, group=self.group)
                    devs.add(dev)
                    dev, _ = get_dev_and_ch(q, cfg, type=ChannelTypeSchema.ad, group=self.group)
                    devs.add(dev)
        return devs
                    
                
    def perform_sequence_awg(self):
        """
        求出每个比特的波形数据，并对波形数据做对齐（补0），使每个通道的数据长度保证一致
        """
        
        cfg = self.config
        readout_only = self._readout_only()
        preserve_dc = self._preserve_existing_dc()
        last_t = cfg.global_wd_len()
                
        self.waves = WaveStore(length_s=last_t)
        
        ro_baseband_waves = {}
        
        for q in self.pulse_list_per_qubit:
            z = xy = None
            if not readout_only:
                if not preserve_dc:
                    z = self.get_z_wave(q)
                    z.use = False
                # Keep a real XY waveform even for a zero-amplitude reference,
                # so a previous nonzero waveform is explicitly replaced.
                xy = self.get_xy_wave(q)
            xy_mrate = None
            for (ts, te), pulse in self.pulse_list_per_qubit[q]:
                sampler = pulse.sample_generator(self.param_ctx)
                
                if pulse.pulse_type == PulseType.XY:
                    ts += self.gate_start
                    te += self.gate_start
                    wd = sampler()
                    xy.add_segment(ts, te, wd)
                    if xy_mrate is None:
                        xy_mrate = pulse.mrate
                    elif not np.isclose(xy_mrate, pulse.mrate):
                        raise ValueError(f"XY pulses on Q{q} have different mrate: {xy_mrate} vs {pulse.mrate}")
                    z_amp = cfg.z_amp(q)
                    if z_amp != 0:
                        # 加入额外的z_amp
                        z.use = True
                        nz = int(max(0, round((te - ts) * z.srate)))
                        z_wave = np.zeros(nz, dtype=float)
                        z_wave[:] = z_amp
                        z.add_segment(ts, te, z_wave)
                elif pulse.pulse_type == PulseType.Z:
                    z.use = True
                    wd = sampler()
                    ts += self.gate_start
                    te += self.gate_start
                    z.add_segment(ts, te, wd.real)   
                elif pulse.pulse_type == PulseType.RO:
                    ro = self.get_ro_wave(q)
                    ro_key = (ro.name, q, pulse.mrate)
                    if ro_key not in ro_baseband_waves:
                        ro_baseband_waves[ro_key] = Wave(name=f'{ro.name}_Q{q}_bb', srate=ro.srate, qids=[q], length_s=ro.length_s, start_at=ro.start_at, fill=ro.fill)
                    wd = sampler()
                    ro_baseband_waves[ro_key].add_segment(ts, te, wd)
                else:
                    raise TypeError(f"unknown pulse type: {pulse.pulse_type}")
            
            if xy_mrate is not None:
                xy.mix(xy_mrate, iq=False)
                
        for (ro_name, _, mrate), ro_baseband in ro_baseband_waves.items():
            ro = self.waves._waves[ro_name]
            iq = abs(ro.srate - 2e9) < 1e-6
            ro_baseband.mix(mrate, iq=iq)
            ro.add_segment(0, ro.length_s, ro_baseband.data)
            
            
    def regenerate_sequence(self):
        """
        序列重分析
        """
        args = tuple(self.input_list)
        self.clear()
        self.inner_add_pulses(args)

    
    def clear(self):
        """
        清除原序列
        """
        self.pulse_sequence = []
        self.time_stamp = defaultdict(float)
        self.pulse_list_per_qubit = defaultdict(list)
        self.current_time = 0
        self.param_ref = {}
        self.awg_data = {}
        self.awg_time = None
        self.waves = None
        self.gate_start = 0
        self.measure_list = []
        
    def dumps(self, **json_kwargs) -> str:
        """
        脉冲/列表/字典（可嵌套） -> JSON 字符串
        """
        if "ensure_ascii" not in json_kwargs:
            json_kwargs["ensure_ascii"] = False
        if "indent" not in json_kwargs:
            json_kwargs["indent"] = 4
        
        obj = {
            "__sequence__": True,
            "qubits": list(self.qset),
            "config": self.config._cfg,
            "input_list": self.input_list,
        }
        return json.dumps(obj, cls=PulseEncoder, **json_kwargs)
    
    def make_struct_hash(self) -> str:
        """获取sequence的hash值"""
        raw = json.loads(self.dumps())
        norm = normalize_seq_json(raw)
        #return norm
        return sha256_of_text(stable_dumps(norm))
    
    @staticmethod
    def loads(s: str):
        """JSON 字符串 -> Sequence"""
        obj = json.loads(s, object_hook=object_hook)
        if not (isinstance(obj, dict) and obj.get("__sequence__")):
            raise TypeError("not a sequence JSON")
        seq = Sequence(
            qset=set(obj["qubits"]),
            config=SettingConfig(obj["config"]),
        )
        # 恢复 input_list
        seq.input_list = obj["input_list"]
        seq.inner_add_pulses(seq.input_list)
        
        return seq
    
    def get_pulses_code(self):
        
        pulse_str = []
        for pulse in self.input_list:
            pulse_str.append(pulse_to_str(pulse))
        return "\n".join(pulse_str)
    
            
def iter_pulse_or_tuple_or_list(item):
    if isinstance(item, list):
        for sub_item in item:
            yield(sub_item)
    else:
        yield item
