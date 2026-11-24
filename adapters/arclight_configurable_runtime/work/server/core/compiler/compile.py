
from .parser import get_abs_tree, get_ir
from .decompose import create_gate, transpiler, optimizer
from typing import Any, Dict, List, Optional, Union, Callable
from collections import defaultdict
from ..experiment.pulse import *
from ..utils import SettingConfig
from dataclasses import dataclass
from copy import deepcopy

@dataclass(frozen=True)
class CompileMethod:
    PARSER: str = "parser"
    AUTO: str = "auto"
    QISKIT: str = "qiskit"

@dataclass(frozen=True)
class TwoQubitGateMethod:
    NO_COUPLER: str = "no_coupler"
    WITH_COUPLER: str = "with_coupler"


class Compiler:
    def __init__(self, config: SettingConfig, compile_method: str = CompileMethod.AUTO, gate_method: str = TwoQubitGateMethod.NO_COUPLER, opt: bool = True):
        self.config = config
        self.compile_method = compile_method
        self.gate_method = gate_method
        self.opt= opt
    
    def compile(self, data, compile_method: Optional[str] = None, gate_method: Optional[str] = None, ctx: Optional[Dict[str, Any]] = None):
        
        compile_method = compile_method or self.compile_method
        if compile_method == CompileMethod.PARSER:
            qubits, ir = self.compile_by_parser(data)
        elif compile_method == CompileMethod.QISKIT:
            qubits, ir = self.compile_by_qiskit(data)
        elif compile_method == CompileMethod.AUTO:
            qubits, ir = self.compile_by_auto(data, ctx=ctx)
        else:
            raise RuntimeError(f"编译方法 {compile_method} 不存在")

        gate_method = gate_method or self.gate_method
        if self.opt: ir = optimizer(ir)
        ir = transpiler(ir)
        if self.opt: ir = optimizer(ir)
        seqs, meas = self.gate_to_pulse(ir, gate_method)
        return qubits, seqs, meas

    def compile_by_parser(self, qasm):
        """
        解析OpenQasm2.0，得到对应的脉冲序列
        Args:
            qasm (_type_): OpenQasm2.0
        """
        abs_tree = get_abs_tree(qasm)
        qnum, ir = get_ir(abs_tree)
        return set(range(qnum)), ir
    
    def compile_by_qiskit(self, qasm):
        
        from qiskit import QuantumCircuit, transpile
        
        qc = QuantumCircuit.from_qasm_str(qasm)
        qc_transpiled = transpile(qc, basis_gates=['rx', 'ry', 'rz', 'cz'])
        ir = []
        for ins in qc_transpiled:
            targets = [ins.qubits[0]._index]
            if ins.name == 'cz':
                targets.append(ins.qubits[1]._index)
            ir.append(create_gate(ins.name, targets=targets, arg_value=ins.params))
        return set([q._index for q in qc_transpiled.qubits]), ir
    
    def compile_by_auto(self, data, ctx):
        """
        解析元组形式的门序列，得到对应的脉冲序列
        """
        gates = []
        qubits = set()
        for ir in data:
            assert isinstance(ir, tuple | list)
            assert len(ir) > 1
            assert isinstance(ir[0], str)
            name, targets, arg_value = ir[0], [], []
            gate_meta = ir[3] if len(ir) > 3 and isinstance(ir[3], dict) else {}
            assert isinstance(ir[1], int | list | tuple)
            if isinstance(ir[1], int): targets = [ir[1]]
            else: targets = list(ir[1])
            qubits |= set(targets)
            if len(ir) > 2:
                if not isinstance(ir[2], list | tuple):
                    args = [ir[2]]
                else: args = list(ir[2])
                for v in args:
                    if isinstance(v, str):
                        v = eval(v, ctx)
                    arg_value.append(v)
            gates.append(create_gate(
                name,
                targets=targets,
                arg_value=arg_value,
                id=gate_meta.get("id"),
                pulse=gate_meta.get("pulse"),
            ))
        return qubits, gates
    
    def gate_to_pulse(self, gates, gate_method: str = TwoQubitGateMethod.NO_COUPLER):
        """
        将中间表示（门表示）转换到脉冲序列上

        Args:
            gates (_type_): 门列表
        """
        seqs = []
        measure = []
        phase_offset = defaultdict(float) 
        for gate in gates:
            if gate.name == 'i': continue
            
            if gate.name == 'rx' or gate.name == 'ry':
                for q in gate.targets:
                    # 虚拟RZ的相位是反的
                    phase = -phase_offset[q]
                    # 处理旋转角
                    theta = gate.arg_value[0]
                    theta = theta % (2 * np.pi)
                    if theta > np.pi:
                        theta = 2 * np.pi - theta
                        phase += np.pi
                    if gate.name == 'ry': phase += np.pi/2
                    seqs.append(self.get_xy_seq(q, theta=theta, phase=phase, pulse=gate.pulse))
            elif gate.name == 'rz':
                for q in gate.targets:
                    phase_offset[q] += gate.arg_value[0]
            elif gate.name == 'pz':
                for q in gate.targets:
                    seqs.append(self.get_z_seq(q, pulse=gate.pulse))
            elif gate.name == 'cz':
                c, t = gate.targets
                if gate_method == TwoQubitGateMethod.WITH_COUPLER:
                    seqs.append(self.get_cz_seq_with_coupler(c, t))
                elif gate_method == TwoQubitGateMethod.NO_COUPLER:
                    seqs.append(self.get_cz_seq_no_coupler(c, t))
                else:
                    raise ValueError(f"两比特脉冲方法 {gate_method} 不存在")
            elif gate.name == 'measure':
                measure += gate.targets
            elif gate.name == 'barrier':
                seqs.append(Barrier().on(gate.targets))
            elif gate.name == 'blank':
                assert len(gate.arg_value) > 0
                if gate.arg_value[0] == 0: continue
                seqs.append(Wait(gate.arg_value[0]).on(gate.targets))
            else:
                raise RuntimeError(f'{gate.name} 没有对应的脉冲')
        if measure:
            seqs.append(Gap().on([]))
            for q in measure:
                seqs.append(self.get_measure_seq(q))
        return seqs, measure
    
    def get_q_cfg(self, q: int, pulse: dict = {}):
        
        cfg = deepcopy(self.config)
        patch = {f"Q{q}": {"gate": { "U_gates": pulse}}}
        cfg.apply_dict(patch)
        return cfg
    
    def get_xy_seq(self, q:int, theta: float=np.pi,  phase: float=0, pulse: dict = {}):
        """
        获取比特q的xy驱动脉冲
        """
        cfg = self.get_q_cfg(q, pulse)
        fac = theta / np.pi
        buff = cfg.xy_buff(q)
        return DrivePulse(
                duration=cfg.xy_pi_len(q) + buff,
                buff=buff,
                A=cfg.xy_pi_amp(q) * fac,
                srate=cfg.xy_srate(q),
                mrate=cfg.xy_mrate(q),
                shape=cfg.xy_shape(q),
                phase=phase,
                drag=cfg.xy_drag(q),
                beta=cfg.xy_beta(q),
                pulse_type=PulseType.XY
            ).on(q)
    
    def get_z_seq(self, q:int, pulse: dict = {}):
        """
        获取比特q的z驱动脉冲
        """
        cfg = self.get_q_cfg(q, pulse)
        return DrivePulse(
                duration=cfg.z_pulse_width(q),
                A=cfg.z_pulse_amp(q),
                srate=cfg.z_srate(q),
                shape=cfg.z_shape(q),
                pulse_type=PulseType.Z
        ).on(q)
        
    def get_cz_seq_no_coupler(self, c: int, t: int):
        """
        获取不包含耦合比特的cz脉冲
        """
        cfg = self.config
        cz_pulse = CZPulse()
        # cz_pulse.add(DrivePulse(cfg.cz_width(c), A=cfg.cz_amp(c),srate=cfg.z_srate(c), shape=cfg.z_shape(c), pulse_type=PulseType.Z).on(c))
        cz_pulse.add(DrivePulse(cfg.cz_width(t), A=cfg.cz_amp(t),srate=cfg.z_srate(t), shape=cfg.z_shape(c), pulse_type=PulseType.Z).on(t))
        return cz_pulse
    
    def get_cz_seq_with_coupler(self, c: int, t: int):
        """
        获取作用在耦合比特上的cz脉冲
        """
        cfg = self.config
        cz_pulse = CZPulse()
        dura = cfg.cz_width((c, t))
        cz_pulse.add(DrivePulse(dura, A=cfg.cz_amp((c,t)),srate=cfg.z_srate((c, t)), shape=cfg.z_shape((c, t)), pulse_type=PulseType.Z).on((c, t)))
        return cz_pulse
    
    def get_measure_seq(self, q:int):
        """
        获取比特q对应的测量脉冲
        """
        cfg = self.config
        width = cfg.da_width(q) 
        pre_width = cfg.da_pre_width(q)
        factor = cfg.da_factor(q)
        A = cfg.da_amp(q)
        srate = cfg.da_srate(q)
        mrate = cfg.da_mrate(q)
        ro_pulse = ReadOutPulse()
        if pre_width > 0: ro_pulse.add(DrivePulse(pre_width, A, srate=srate, mrate=mrate, pulse_type=PulseType.RO).on(q))
        ro_pulse.add(DrivePulse(width-pre_width, A*factor, srate=srate, mrate=mrate, pulse_type=PulseType.RO).on(q), t_offset=pre_width)
        return ro_pulse
