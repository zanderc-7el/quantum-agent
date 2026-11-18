import numpy as np
from copy import deepcopy
from .errors import *

class Gate():
    """
    中间表示类

    Args:
        name (_type_): 门名称
        targets (_type_, optional): 目标量子比特. Defaults to None.
        arg_value (_type_, optional): 参数（旋转门所需）. Defaults to None.
        type: 门类型。1：单比特门，2：两比特门，3：三比特门，0：测量，-1：同步，-2：移动
        hermitian: 是否是厄米
    """
    
    def __init__(
        self,
        name,
        targets=None,
        arg_value=None,
        type=1,
        hermitian=True,
        id=None,
        pulse=None,
    ) -> None:
        
        self.name = name
        self.targets = targets
        self.arg_value = arg_value if arg_value is not None else []
        if not isinstance(self.arg_value, list):
            self.arg_value = [self.arg_value]
        self.type = type
        self.hermitian = hermitian
        self.id = id
        self.pulse = deepcopy(pulse) if pulse is not None else {}

    def _inherit_metadata(self, gates: list):
        for gate in gates:
            if getattr(gate, "id", None) is None:
                gate.id = self.id
            if not getattr(gate, "pulse", None) and self.pulse:
                gate.pulse = deepcopy(self.pulse)
        return gates

    def decompose(self):
        """
        门对应的分解规则，如无指定规则，则调用默认的分解方法
        
        分解规则以字典的形式指定，配置在Global_Setting的decomposition_rule中，其每个item的形式为
            gate_name: #门名称
            {
                'param': [str] #形式化的门参数，数量与门实际所需一致，如无参数，该项可不填
                'gates': [based_gates] #指定的分解形式，以based_gates列表表示
            }
        其中based_gates为一个三元组（name, targets, exps）,name表示门名称，targets为作用比特下标列表(从0开始)，exps为参数对应的表达式，以字符串的形式表示，表达式中的操作数可为param中定义的形式化参数以及常量，常量中π可用pi表示
        
        举例如下：
        ecomposition_rule = {
            'u3':{
                'params': ['a', 'b', 'c'],
                'gates':[
                    ('rz', [0], ["c"]),
                    ('rx', [0], ["pi/2"]),
                    ('rz', [0], ["b+pi"]),
                    ('rx', [0], ["pi/2"]),
                    ('rz', [0], ["a+pi"]),
                ]
            }
            'h': {
                'gates': [
                    ('rx', [0], ['pi/2'])
                ]
            }
        }
        """
        custom_gate = None
        if custom_gate is None:
            return self._inherit_metadata(self.default_decompose())
        try:
            params_list = custom_gate.get('params', [])
            need_args = self.arg_value
            if len(params_list) != len(need_args): raise DecomposeError(1003, f"{self.name} gate need {len(need_args)} params but give {len(params_list)}")
            params = dict(zip(params_list, need_args))
            params['pi'] = np.pi
            decomposed_gates = custom_gate.get('gates', [])
            if len(decomposed_gates) == 0: return [self]
            gates = []
            for name, qids, arg_value in decomposed_gates:
                qubits = [self.targets[id] for id in qids]
                args = [eval(arg, params) for arg in arg_value]
                gates.append(create_gate(name, qubits, args))
            return self._inherit_metadata(gates)
        
        except Exception as e:
            raise DecomposeError(1003, e)
        
    def default_decompose(self):
        raise RuntimeError("please specify the decomposition gates")
    
    def __repr__(self):
        extras = []
        if self.id is not None:
            extras.append(f"id={self.id!r}")
        if self.pulse:
            extras.append(f"pulse={self.pulse!r}")
        suffix = ", " + ", ".join(extras) if extras else ""
        return f"{type(self).__name__}(targets={self.targets}, arg_value={self.arg_value}{suffix})"

# 实例化门，需包含一个默认的分解方法
class I(Gate):
    
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('i', targets, arg_value)
    
    def default_decompose(self):
        return []
    
class H(Gate):
    
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('h', targets, arg_value)
    
    def default_decompose(self):
        gates = []
        gates.append(RY(targets = self.targets,arg_value = -np.pi/2))
        gates.append(RZ(targets = self.targets,arg_value = np.pi))
        return gates

class X(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('x', targets, arg_value)
        
    def default_decompose(self):
        return [RX(targets = self.targets,arg_value = np.pi)]

class Y(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('y', targets, arg_value)
        
    def default_decompose(self):
        return [RY(targets = self.targets,arg_value = np.pi)]

class Z(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('z', targets, arg_value)
        
    def default_decompose(self):
        return [RZ(targets = self.targets,arg_value = np.pi)]

class PZ(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('pz', targets, arg_value)
        
    def default_decompose(self):
        return [self]

class S(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('s', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [RZ(targets = self.targets,arg_value = np.pi/2)]

class SDG(Gate):
    
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('sdg', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [RZ(targets = self.targets,arg_value = -np.pi/2)]

class T(Gate):
    
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('t', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [RZ(targets = self.targets,arg_value = np.pi/4)]

class TDG(Gate):
    
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('tdg', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [RZ(targets = self.targets,arg_value = -np.pi/4)]

class RX(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('rx', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [self]

class RY(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('ry', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [self]

class RZ(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('rz', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        return [self]

class CZ(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('cz', targets, arg_value, type)
        
    def default_decompose(self):
        return [self]

class CX(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('cx', targets, arg_value, type)
        
    def default_decompose(self):
        gates = H(targets=[self.targets[1]]).decompose()
        gates.append(CZ(self.targets))
        gates += H(targets=[self.targets[1]]).decompose()
        return gates

class CY(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('cy', targets, arg_value, type)
        
    def default_decompose(self):
        gates = []
        gates += SDG([self.targets[1]]).decompose()
        gates += CX(self.targets).decompose()
        gates += S([self.targets[1]]).decompose()
        return optimizer(gates)

class CH(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('ch', targets, arg_value, type)
    
    def default_decompose(self):
        gates = []
        gates += H([self.targets[1]]).decompose()
        gates += SDG([self.targets[1]]).decompose()
        gates += CX(self.targets).decompose()
        gates += T([self.targets[1]]).decompose()
        gates += CX(self.targets).decompose()
        gates += T([self.targets[1]]).decompose()
        gates += H([self.targets[1]]).decompose()
        gates += S([self.targets[1]]).decompose()
        gates += X([self.targets[1]]).decompose()
        gates += S([self.targets[0]]).decompose()
        return optimizer(gates)

class CRX(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('crx', targets, arg_value, type, hermitian=False)
    
    def default_decompose(self):
        gates = []
        gates += H([self.targets[1]]).decompose()
        gates += CX(self.targets).decompose()
        gates.append(RZ(targets = [self.targets[1]],arg_value = -self.arg_value[0] / 2))
        gates += CX(self.targets).decompose()
        gates.append(RZ(targets = [self.targets[1]],arg_value = self.arg_value[0] / 2))
        gates += H([self.targets[1]]).decompose()
        return optimizer(gates)

class CRY(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('cry', targets, arg_value, type, hermitian=False)
    
    def default_decompose(self):    
        gates = []
        gates += CX(self.targets).decompose()
        gates.append(RY(targets = [self.targets[1]],arg_value = -self.arg_value[0] / 2))
        gates += CX(self.targets).decompose()
        gates.append(RY(targets = [self.targets[1]],arg_value = self.arg_value[0] / 2))
        return optimizer(gates)
    
class CRZ(Gate):
    def __init__(self, targets=None, arg_value=None, type=2) -> None:
        super().__init__('crz', targets, arg_value, type, hermitian=False)
    
    def default_decompose(self):
        gates = []
        gates += CX(self.targets).decompose()
        gates.append(RZ(targets = [self.targets[1]],arg_value = -self.arg_value[0] / 2))
        gates += CX(self.targets).decompose()
        gates.append(RZ(targets = [self.targets[1]],arg_value = self.arg_value[0] / 2))
        return optimizer(gates)

class CCX(Gate):
    def __init__(self, targets=None, arg_value=None, type=3) -> None:
        super().__init__('ccz', targets, arg_value, type)
    
    def default_decompose(self):
        gates = []
        gates += H([self.targets[2]]).decompose()
        gates += CX([self.targets[1],self.targets[2]]).decompose()
        gates += TDG([self.targets[2]]).decompose()
        gates += CX([self.targets[0],self.targets[2]]).decompose()
        gates += T([self.targets[2]]).decompose()
        gates += CX([self.targets[1],self.targets[2]]).decompose()
        gates += TDG([self.targets[2]]).decompose()
        gates += CX([self.targets[0],self.targets[2]]).decompose()
        gates += T([self.targets[2]]).decompose()
        gates += T([self.targets[1]]).decompose()
        gates += H([self.targets[2]]).decompose()
        gates += CX([self.targets[0],self.targets[1]]).decompose()
        gates += T([self.targets[0]]).decompose()
        gates += TDG([self.targets[1]]).decompose()
        gates += CX([self.targets[0],self.targets[1]]).decompose()
        return optimizer(gates)

class U1(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('u1', targets, arg_value, hermitian=False)
    
    def default_decompose(self):
        return [RZ(self.targets, self.arg_value)]

class U2(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('u2', targets, arg_value, hermitian=False)
        
    def default_decompose(self):
        gates = []
        gates.append(RZ(self.targets, self.arg_value[0] + np.pi/2))
        gates.append(RX(self.targets, np.pi/2))
        gates.append(RZ(self.targets, self.arg_value[1] - np.pi/2))
        return gates[::-1]

class U3(Gate):
    def __init__(self, targets=None, arg_value=None) -> None:
        super().__init__('u3', targets, arg_value, hermitian=False)
    
    def default_decompose(self):
        gates = []
        gates.append(RZ(self.targets, self.arg_value[1] + np.pi /2))
        gates.append(RX(self.targets, self.arg_value[0]))
        gates.append(RZ(self.targets, self.arg_value[2] - np.pi / 2))
        return gates[::-1]

class BLANK(Gate):
    def __init__(self, targets=None, arg_value=None, type=-1) -> None:
        super().__init__('blank', targets, arg_value, type, hermitian=False)
        
    def default_decompose(self):
        return [self]

class BARRIER(Gate):
    def __init__(self, targets=None, arg_value=None, type=-1) -> None:
        super().__init__('barrier', targets, arg_value, type, hermitian=False)
    
    def default_decompose(self):
        return [self]

class MEASURE(Gate):
    def __init__(self, targets=None, arg_value=None, type=0) -> None:
        super().__init__('measure', targets, arg_value, type, hermitian=False)
    
    def default_decompose(self):
        return [self]

def _with_metadata(gate, id=None, pulse=None):
    gate.id = id
    gate.pulse = deepcopy(pulse) if pulse is not None else {}
    return gate


def create_gate(name, targets = None, arg_value = None, allow_undefined=False, id=None, pulse=None):
    """
    创建Gate对象
    Args:
        name (_type_): 门名称
        targets (_type_, optional): 作用比特列表. Defaults to None.
        arg_value (_type_, optional): 参数列表. Defaults to None.
        allow_undefined (bool, optional): 是否允许自定义的门（线路切割可视化添加切割点时设置为True）. Defaults to False.
        id (_type_, optional): 实验 spec 中的 gate id. Defaults to None.
        pulse (dict, optional): 当前 gate 的局部 pulse 参数覆盖. Defaults to None.
    """
    name = name.lower()
    if name == 'h': gate = H(targets, arg_value)
    elif name == 'i': gate = I(targets, arg_value)
    elif name == 'x': gate = X(targets, arg_value)
    elif name == 'x/2' or name == 'xd2': gate = RX(targets, [np.pi/2])
    elif name == '-x/2' or name == 'mxd2': gate = RX(targets, [-np.pi/2])
    elif name == 'y': gate = Y(targets, arg_value)
    elif name == 'y/2' or name == 'yd2': gate = RY(targets, [np.pi/2])
    elif name == '-y/2' or name == 'myd2': gate = RY(targets, [-np.pi/2])
    elif name == 'z': gate = Z(targets, arg_value)
    elif name == 'pz': gate = PZ(targets, arg_value)
    elif name == 'rx': gate = RX(targets, arg_value)
    elif name == 'ry': gate = RY(targets, arg_value)
    elif name == 'rz': gate = RZ(targets, arg_value)
    elif name == 's': gate = S(targets, arg_value)
    elif name == 't': gate = T(targets, arg_value)
    elif name == 'sdg': gate = SDG(targets, arg_value)
    elif name == 'tdg': gate = TDG(targets, arg_value)
    elif name == 'cx': gate = CX(targets, arg_value)
    elif name == 'cy': gate = CY(targets, arg_value)
    elif name == 'cz': gate = CZ(targets, arg_value)
    elif name == 'ch': gate = CH(targets, arg_value)
    elif name == 'crx': gate = CRX(targets, arg_value)
    elif name == 'cry': gate = CRY(targets, arg_value)
    elif name == 'crz': gate = CRZ(targets, arg_value)
    elif name == 'ccx': gate = CCX(targets, arg_value)
    elif name == 'u1': gate = U1(targets, arg_value)
    elif name == 'u2': gate = U2(targets, arg_value)
    elif name == 'u3': gate = U3(targets, arg_value)
    elif name == 'barrier': gate = BARRIER(targets, arg_value)
    elif name == 'measure': gate = MEASURE(targets, arg_value)
    elif name == 'blank': gate = BLANK(targets=targets, arg_value=arg_value)
    else:
        if allow_undefined: gate = Gate(name, targets=targets, arg_value=arg_value)
        else:
            raise RuntimeError(f'{name} is not support')
    return _with_metadata(gate, id=id, pulse=pulse)

def transpiler(ir: list):
    """
    将中间表示中的门分解到硬件脉冲直接支持的门上
    
    Args:
        ir (list): 中间表示
    """
    gates = []
    for gate in ir:
        gates += gate.decompose()
    return gates


def _same_metadata(*gates) -> bool:
    if len(gates) < 2:
        return True
    first = gates[0]
    first_id = getattr(first, "id", None)
    first_pulse = getattr(first, "pulse", {})
    return all(
        getattr(gate, "id", None) == first_id
        and getattr(gate, "pulse", {}) == first_pulse
        for gate in gates[1:]
    )


def pass_hermitian(ir: list):
    """如果末尾的两个门相同，且都为hermitian，则消去

    Args:
        ir (list): 中间表示
    """
    passed = False
    while True:
        if len(ir) < 2: break
        if ir[-1].name != ir[-2].name: break
        if ir[-1].targets != ir[-2].targets: break
        if not _same_metadata(ir[-1], ir[-2]): break
        if ir[-1].hermitian:
            ir.pop(-1)
            ir.pop(-1)
            passed = True
            continue
        break
    return passed

def pass_merge_theta(ir: list):
    """如果末尾的两个门是作用在同一比特上的同一类旋转门，则角度可以合并

    Args:
        ir (list): 中间表示
    """
    passed = False
    while True:
        if len(ir) < 2: break
        if ir[-1].name != ir[-2].name: break
        if ir[-1].targets != ir[-2].targets: break
        if not _same_metadata(ir[-1], ir[-2]): break
        if ir[-1].name in ['rx', 'ry', 'rz', 'crx', 'cry', 'cry', 'u1']:
            ir[-2].arg_value[0] += ir[-1].arg_value[0]
            ir[-2].arg_value[0] %= (2*np.pi)
            ir.pop(-1)
            if abs(ir[-1].arg_value[0]) < 1e-5:
                ir.pop(-1)
            passed = True
            continue
        break
    return passed

def pass_u_udg(ir: list):
    """如果末尾的两个门是作用在同一比特上的s和sdg或者t和tdg，则可以消去

    Args:
        ir (list): 中间表示
    """
    passed = False
    while True:
        if len(ir) < 2: break
        if ir[-1].targets != ir[-2].targets: break
        if not _same_metadata(ir[-1], ir[-2]): break
        if (ir[-1].name == 's' and ir[-2].name == 'sdg') or (ir[-1].name == 'sdg' and ir[-2].name == 's') or (ir[-1].name == 't' and ir[-2].name == 'tdg') or (ir[-1].name == 'tdg' and ir[-2].name == 't'):
            ir.pop(-1)
            ir.pop(-1)
            passed = True
            continue
        break
    return passed

def pass_three_gate_model(ir: list):
    """HZH -> X, HXH -> Z, XRy(θ)X -> Ry(-θ)

    Args:
        ir (list): 中间表示
    """
    passed = False
    while True:
        if len(ir) < 3: break
        if (ir[-1].targets != ir[-2].targets) or (ir[-1].targets != ir[-3].targets): break
        if not _same_metadata(ir[-1], ir[-2], ir[-3]): break
        if (ir[-1].name == 'h' and ir[-3].name == 'h'):
            if ir[-2].name == 'x' or ir[-2].name == 'z':
                ir.pop(-1)
                ori_gate = ir.pop(-1)
                ir.pop(-1)
                new_name = 'z' if ori_gate.name == 'x' else 'x'
                ir.append(create_gate(
                    new_name,
                    ori_gate.targets,
                    ori_gate.arg_value,
                    id=ori_gate.id,
                    pulse=ori_gate.pulse,
                ))
                passed = True
                continue
        if (ir[-1].name == 'x' and ir[-3].name == 'x'):
            if ir[-2].name == 'ry':
                ir.pop(-1)
                ori_gate = ir.pop(-1)
                ir.pop(-1)
                ir.append(create_gate(
                    ori_gate.name,
                    ori_gate.targets,
                    -1.0 * ori_gate.arg_value[0],
                    id=ori_gate.id,
                    pulse=ori_gate.pulse,
                ))
                passed = True
                continue
        break
    return passed

def do_pass(ir: list):
    """一次执行pass，直到ir不发生变化

    Args:
        ir (list): 中间表示
    """
    passed = True
    while passed:
        passed = False
        passed |= pass_hermitian(ir)
        passed |= pass_merge_theta(ir)
        passed |= pass_u_udg(ir)
        passed |= pass_three_gate_model(ir) 
    return

def optimizer(ir: list):
    """基础门优化
    优化策略主要包含如下几个：
        1. 连续的两个作用在相同比特上的厄米共轭门可以消除
        2. 连续两个相同的选择门，可以合并旋转角
        3. 旋转角->0的门等同于I，可以忽略
        4. HZH -> X, HXH -> Z
        5. XRy(θ)X -> -Ry(θ) 
    Args:
        ir (list): 中间表示
    """
    optimize_gates = []
    for gate in ir:
        optimize_gates.append(gate)
        do_pass(optimize_gates)
        
    return optimize_gates
