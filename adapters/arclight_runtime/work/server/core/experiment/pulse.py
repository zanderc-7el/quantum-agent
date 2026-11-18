import numpy as np
from abc import ABC
from .param import *
import inspect
from enum import Enum
from .shape import *

# =============================
# 操作基类定义
# =============================

class PulseType(str, Enum):
    XY = "XY"
    Z = "Z"
    RO = "RO"
    CZ = "CZ"
    RO_COMP = "RO-COMP"
    COMPOSITE = "COMPOSITE"
    DEFAULT="DEFAULT"


class BaseOp(ABC):
    """抽象基类"""
    def __init__(self, label=None, pulse_type=None, latency=0):
        self.qubit_index = []
        self.label = label
        self.pulse_type=pulse_type
        self.latency = latency
        
    def on(self, qubit_index):
        
        assert isinstance(qubit_index, int | tuple) or isinstance(qubit_index, list)
        
        if isinstance(qubit_index, int | tuple):
            qubit_index = [qubit_index]
        
        for q in qubit_index:
            assert isinstance(q, int | tuple)
        
        self.qubit_index = qubit_index
        return self
    
    def add_qubit(self, qubit):
        
        self.qubit_index.append(qubit)
        
        
    def __repr__(self):
        return f'{type(self).__name__}(target={self.qubit_index}, type={self.pulse_type}, lable={self.label})'
    
    def chek_paramref(self) -> set:
        
        param_ref = {}
        for k, v in self.__dict__.items():
            if isinstance(v, ParamRef):
                if v.name in param_ref:
                    raise RuntimeError(f"duplicate param in gate")
                param_ref[v.name] = {"default": v.val, "attr": k}
        self.param_ref = param_ref
        
    def to_fields(self) -> dict:
        """
        默认：按 __init__ 的参数名导出同名属性
        """
        sig = inspect.signature(self.__class__.__init__)
        params = [p for p in sig.parameters.keys() if p not in ("self", "ctx", "kwargs")]

        fields = {}
        for k in params:
            if hasattr(self, k):
                fields[k] = getattr(self, k)

        fields["qubit_index"] = getattr(self, "qubit_index", None)
        return fields
    
    @classmethod
    def from_fields(cls, fields: dict):
        """
        默认：按 __init__ 签名从 fields 取参数构造
        """
        import inspect
        sig = inspect.signature(cls.__init__)
        params = [p for p in sig.parameters.keys() if p not in ("self", "ctx", "kwargs")]

        kwargs = {k: fields[k] for k in params if k in fields}

        obj = cls(**kwargs)

        qi = fields.get("qubit_index", None)
        if qi is not None and hasattr(obj, "on"):
            obj.on(qi)
            
        return obj

class Gap(BaseOp):
    """门操作与测量之间的gap"""
    def __init__(self, label=None, pulse_type="gap"):
        super().__init__(label=label, pulse_type=pulse_type)
    pass

class Barrier(BaseOp):
    """零时长的同步屏障。"""
    def __init__(self, label=None, pulse_type="barrier"):
        super().__init__(label=label, pulse_type=pulse_type)
    pass

class Wait(BaseOp):
    """有时长的“等待/空闲”，会占用时间但不发波形。"""
    def __init__(self, duration, label=None, pulse_type='wait'):
        super().__init__(label=label, pulse_type=pulse_type)
        self.duration = duration
        self.chek_paramref()
        
    def __repr__(self):
        return f'{type(self).__name__}(target={self.qubit_index}, type={self.pulse_type}, lable={self.label}, duration={self.duration})'

# =============================
# 脉冲基类定义
# =============================

class BasePulse(BaseOp):
    """
    脉冲抽象类

    Args:
        duration (float, optional): 持续时间. Defaults to 0.
        A (float, optinal): 幅度. Defaults to 1.
        srate(float, optional): 采样频率. Defaults to 0.
        mrate(float, optional): 混频频率. Defaults to 0.
        upperside(bool, optional): 上变频. Defaults to True.
        shape(str, optional): 形状. Defaults to "CosPulse".
        shape_params(dict, optinal): 形状对应参数. Defaults to None.
        latency (int, optional): 延迟. Defaults to 0.
        pulse_type (_type_, optional): 脉冲类型. Defaults to None.
        label (_type_, optional): 标签. Defaults to None.
    """
    def __init__(self, 
                 duration = 0,
                 A = 1., 
                 srate = 0, 
                 mrate=0, 
                 upperside = True, 
                 shape: str = "CosPulse", # CosPulse, DC, Gaussian
                 shape_params: dict | None = None,
                 latency=0, 
                 pulse_type = None, 
                 label = None,
                 buff: float = 0.0,
                 drag: bool = False,
                 beta: float = 0.0) -> None:
        
        super().__init__(label=label, pulse_type=pulse_type, latency=latency)
        self.duration = duration
        self.A = A
        self.srate = srate
        self.mrate = mrate
        self.upperside = upperside
        self.shape = shape
        self.shape_params = shape_params or {}
        self.drag = drag
        self.beta = beta
        self.buff = buff
        self.wave_width = self.duration - self.buff
        self.chek_paramref()
    
    def get_envelop(self):
        """获取脉冲包络"""
        t = self.get_t()
        shape_fn = get_shape_fn(self.shape)
        envelop = shape_fn(t, self.wave_width, self.A, **self.shape_params)
        # drag correction
        if self.drag and len(t) > 1:
            dt = t[1] - t[0]
            Q = np.gradient(envelop, dt)
            envelop = envelop + 1j * self.beta * Q
        return envelop
    
    def get_t(self):
        """获取采样时间点"""
        if self.srate == 0.: raise ValueError("采样率为0.，请重新设置采样率")
        n = int(round(self.wave_width * self.srate))
        return np.arange(n, dtype=float) / self.srate
        
        
    def get_wave(self, phase = 0.0):
        """得到基带包络信号，相位只表示旋转轴/参考系偏移。"""
        envelop = self.get_envelop()
        if self.buff > 0.0:
            envelop = np.concatenate([np.zeros(int(self.buff / 2 * self.srate)), envelop, np.zeros(int(self.buff / 2 * self.srate))])
        return np.exp(1j*phase) * envelop
        
    def sample_generator(self, ctx=None):
        """
        波形数据生成函数 
        """
        def sample():
            return self.get_wave()
            
        return sample
        
    def __repr__(self) -> str:
        return f'{type(self).__name__}(target={self.qubit_index}, type={self.pulse_type}, lable={self.label}, duration={self.duration}, srate={self.srate}, mrate={self.mrate})'
    
# =============================
# 驱动脉冲
# =============================
    
class DrivePulse(BasePulse):
    """
    统一 驱动脉冲：
      shape: "Gaussian" | "CosPulse" | "DC"
      shape_params: dict 例如 {"sigma": 8e-9, "cos_n": 4}
      phase: virtual Z / frame phase（与 axis_phase 直接相加）
    """
    def __init__(
        self,
        duration: float | Param,
        A: float | Param,
        *,
        srate: float,
        mrate: float = 0.0,
        upperside: bool = True,
        latency: int = 0,
        pulse_type=PulseType.DEFAULT,
        label=None,
        # 脉冲形状
        shape: str = "DC",
        shape_params: dict | None = None,
        # 相位偏置
        phase: float = 0.0,
        buff: float = 0.0,
        # drag
        drag: bool = False,
        beta: float = 0.0,
    ):
        super().__init__(duration=duration, A = A, srate=srate, mrate=mrate,
                         upperside=upperside, shape=shape, shape_params=shape_params, latency=latency,
                         pulse_type=pulse_type, label=label, drag=drag, beta=beta, buff=buff)
        self.phase = phase
        
    def sample_generator(self, ctx=None):
        """
        波形数据生成函数 
        """
        def sample():
            
            return self.get_wave(phase = self.phase)
            
        return sample

# =============================
# 组合脉冲
# =============================

class CompositePulse(BaseOp):
    def __init__(self, label=None, pulse_type=PulseType.COMPOSITE, latency=0):
        super().__init__(label=label, pulse_type=pulse_type, latency=latency)
        self.children = []
        self.duration = 0
        self.chek_paramref()
        
    def add(self, pulse: BasePulse, t_offset: int = 0):
        self.children.append((t_offset, pulse))
        self.duration = max(self.duration, pulse.duration + t_offset)
        for q in pulse.qubit_index:
            if isinstance(q, int):
                q = [q]
            for qt in q:
                if qt not in self.qubit_index:
                    self.qubit_index.append(qt)
        return self
    
    def to_fields(self) -> dict:
        # 先拿基类默认字段
        fields = super().to_fields()
        
        # 再补 composite 专属字段
        fields["duration"] = self.duration
        fields["children"] = [
            {
                "t_offset": t,
                "op": {
                    "__type__": type(p).__name__,   # 记录类型
                    "fields": p.to_fields(),        # 递归
                },
            }
            for (t, p) in self.children
        ]
        return fields

    @classmethod
    def from_fields(cls, fields: dict):
        obj = super().from_fields(fields)

        obj.duration = fields.get("duration", 0)
        obj.children = []

        registry = {
            "DrivePulse": DrivePulse,
            "CompositePulse": CompositePulse,
            "CZPulse": CZPulse,
            "ReadOutPulse": ReadOutPulse
        }

        for item in fields.get("children", []):
            t = item["t_offset"]
            op_pack = item["op"]
            type_name = op_pack["__type__"]
            child_cls = registry.get(type_name)
            if child_cls is None:
                raise RuntimeError(f"Unknown pulse type: {type_name}")

            child = child_cls.from_fields(op_pack["fields"])
            obj.children.append((t, child))

        return obj
    
class ReadOutPulse(CompositePulse):
    """
    测量脉冲，由多个DC脉冲组合而成
    """
    def __init__(self,
                 pulse_type = PulseType.RO_COMP,
                 label=None) -> None:
        
        super().__init__( pulse_type=pulse_type, label=label)
    

class CZPulse(CompositePulse):
    """
    CZ脉冲基类，提供公共功能
    """
    def __init__(self,
                 pulse_type=PulseType.CZ,
                 label=None) -> None:
        super().__init__( pulse_type=pulse_type, label=label)
        
    def on(self, qubit_index):
        assert len(qubit_index) == 2
        return super().on(qubit_index)
