# hardware_base.py
from .instrument_base import InstrumentBase
from .error import *

from dataclasses import dataclass, asdict
import time
from typing import Optional, Deque, Any, Dict, List, Callable
from types import MethodType
from collections import defaultdict, deque
from copy import deepcopy
from .driver.CY_Mocker import MockerDriver
from ..experiment.wave import Wave
from .utils import DriveTypeSchema
from .params import ParamSpec
import logging

@dataclass
class DeviceStatus:
    name: str
    model: Optional[str] = None
    addr: Optional[str] = None
    sync_bind: Optional[str] = None
    connected: bool = False
    busy: bool = False
    last_error: Optional[str] = None
    last_op: Optional[str] = None
    last_update_ts: str = ''
    last_heartbeat_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class WaveformRecord:
    channel: Optional[str]  # 通道号（AWG/ADC）
    ts: float               # 时间戳
    meta: Dict[str, Any]    # 额外信息（采样率、脉冲名等）
    y: Dict[str, Any]       # 波形数据

def _make_setter(method_name: str, short_name: str, spec: ParamSpec):
    if spec.requires_ch:
        def setter(self, value, ch, **kwargs):
            v = spec.cast(value) if spec.cast else value
            # 通道名 → channel_id：driver 的 CHs 为 (board, ch) 元组，
            # 传入通道名（如 "CY_Z5"）时须先解析为 id，否则 driver 断言失败。
            # 缓存仍按通道名记录，与 set_channel/get_param 的键保持一致。
            ch_id = self.resolve_channel(ch)
            
            if spec.use_cache and self._get_cache_param(short_name, ch) == value:
                return
            
            self.log.info(f"set {ch_id} {short_name}: {v}")
            self._set_cache_param(short_name, v, ch=ch)
            kwargs.update({'ch': ch_id})
            return self.write(spec.key, v, **kwargs)
    else:
        def setter(self, value, **kwargs):
            v = spec.cast(value) if spec.cast else value
            
            if spec.use_cache and self._get_cache_param(short_name) == value:
                return
            
            self.log.info(f"set {short_name}: {v}")
            self._set_cache_param(short_name, v)
            return self.write(spec.key, v, **kwargs)

    setter.__name__ = method_name
    setter.__doc__ = spec.doc or f"write({spec.key})"
    return setter

def expose_params(*, prefix: str = "set_", params: dict[str, ParamSpec]):
    """
    用法：
    @expose_params(params={
        "lo_freq": ParamSpec("lo_freq", "本振频率(Hz)", float),
        "awg_gain": ParamSpec("awg_gain", "AWG增益", float, requires_ch=True),
    })
    """
    def deco(cls):
        # 继承父类已有参数表
        base = {}
        for b in reversed(cls.__mro__[1:]):
            base.update(getattr(b, "__param_specs__", {}) or {})

        table = dict(base)
        for short_name, spec in params.items():
            method_name = f"{prefix}{short_name}"
            if hasattr(cls, method_name):
                raise AttributeError(f"{cls.__name__} already has method {method_name}")
            setattr(cls, method_name, _make_setter(method_name, short_name, spec))
            table[short_name] = spec

        cls.__param_specs__ = table  # 给 UI/前端枚举
        return cls
    return deco

class ChannelMappingMixin:
    def __init__(self, *args, channel_map=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_map = channel_map or {}

    def set(self, name, value) -> None:
        if isinstance(value, str):
            try:
                v = eval(value)
                self.channel_map[name] = v
                return
            except:
                pass
        self.channel_map[name] = value
    
    def remove(self, name):
        if name in self.channel_map:
            self.channel_map.pop(name)

    def resolve_channel(self, ch) -> int:
        """返回实际的channel id"""
        if ch is None:
            return -1  # 或者返回 None
        if ch in self.channel_map:
            return self.channel_map[ch]
        return ch


class HardwareBase(InstrumentBase):
    """
    硬件抽象类：对接具体驱动（driver），对外提供统一的接口。
    """

    driver_adapter_cls = None
    support_models: list[str] = []

    def __init__(self,
                 name: str,
                 addr: str,
                 model: str | None = None,
                 group: str = "default",
                 *,
                 sync_bind: str | None = None,
                 max_waveforms_per_channel: int = 3,
                 debug: bool = False,
                 **kwargs) -> None:

        self.model = model or (self.support_models[0] if self.support_models else None)
        self.sync_bind = sync_bind
        self.debug = debug
        self.channel_map = ChannelMappingMixin()
        self._param_cache = {}
        # 设备状态
        self._status = DeviceStatus(
            name=name,
            model=model,
            addr=addr,
            sync_bind=self.sync_bind,
            connected=False,
            busy=False,
            last_update_ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        )
        
        self._max_waveforms_per_channel = max_waveforms_per_channel
        self._waveform_cache: Dict[Optional[str], Deque[WaveformRecord]] = defaultdict(
            lambda: deque(maxlen=self._max_waveforms_per_channel)
        )
        
        super().__init__(name, group=group, **kwargs)
        self._check_sync()
        
        self._driver_adapter = self._create_driver_adapter(addr, model=model, debug=debug, **kwargs)
        self._set_global_value()
        

    def _check_sync(self):
        if self.sync_bind:
            dev = InstrumentBase.find_instrument(self.sync_bind, self.group)
            if dev is None: raise DeviceNotFoundError(f"没有设备：{self.sync_bind}")
            is_sync = False
            for model in getattr(dev, "support_models", []):
                if model == DriveTypeSchema.CY_SYNC:
                    is_sync = True
            if not is_sync: raise DeviceParameterError(f"关联的设备 {self.sync_bind} 不是同步设备")

    def _set_global_value(self):
        self._ensure_inst_specs_snapshot()
        for name in self._param_specs_inst:
            if not self._param_specs_inst[name].requires_ch:
                self._set_cache_param(name, self._param_specs_inst[name].value)

    
    def modify(self, addr: str = None, sync_bind: str = None):
        
        assert self._status.connected == False, "请先关闭设备再进行修改"
        
        if addr:
            self._driver_adapter.addr = addr
        if sync_bind:
            self.sync_bind = sync_bind
            self._check_sync()
            
        self._update_status(
            last_op="update",
            addr = self._driver_adapter.addr,
            sync_bind = self.sync_bind
        )
        
    def sync(self, hardwares):
        if hasattr(self._driver_adapter, "sync"):
            self._driver_adapter.sync(hardwares)
        else:
            raise DeviceAccessError(f"设备 {self.name} 不支持同步功能")
    
    def set_trigger(self, shots=1, period=0):
        if hasattr(self._driver_adapter, "set_trigger"):
            self._driver_adapter.set_trigger(shots, period)
        else:
            raise DeviceAccessError(f"设备 {self.name} 不支持触发功能")
        
    def _to_sync(self):
        
        if self.sync_bind:
            dev = InstrumentBase.find_instrument(self.sync_bind, self.group)
            dev.sync([self])
            print(f"设备 {self.name} 同步至 {self.sync_bind}")
            
    # ---------- 通道 创建与删除 ----------
    
    def set_channel(self, channel_name, channel_id):
        # list → tuple：前端传入的 channel_id 是 JSON 数组（list），而驱动 CHs 为 tuple
        # 列表，类型不一致会导致真机写通道时断言 `ch in self.CHs` 失败
        if isinstance(channel_id, list):
            channel_id = tuple(channel_id)
        self.channel_map.set(channel_name, channel_id)
        self._ensure_inst_specs_snapshot()
        for name in self._param_specs_inst:
            if self._param_specs_inst[name].requires_ch:
                self._set_cache_param(name, self._param_specs_inst[name].value, ch=channel_name)
                
    def remove_channel(self, channel_name):
        
        self._ensure_inst_specs_snapshot()
        if channel_name in self.channel_map.channel_map:
            self.channel_map.remove(channel_name)
            key = "CHANNEL"
            self._param_cache[key].pop(channel_name)

    # ---------- driver_adapter 创建与访问 ----------

    def _create_driver_adapter(self, addr: str, **kwargs):
        """
        实例化底层驱动。
        """
        
        driver_logger = logging.getLogger(f"{self.log.name}.driver")
        
        if self.driver_adapter_cls is None:
            raise DeviceAccessError(
                f"{self.__class__.__name__} 必须设置 dirver_adapter_cls 才能实例化驱动"
            )
        
        return self.driver_adapter_cls(addr, log=driver_logger, **kwargs)

    @property
    def driver(self):
        """
        底层原始驱动实例
        """
        return self._driver_adapter.driver
    
    def is_connected(self):
        """
        是否已连接
        """
        return self._status.connected
    
    def is_busy(self):
        """
        是否在使用
        """
        return self._status.busy
    
    def set_busy(self, busy):
        
        self._update_status(busy = busy)
        if busy: self.log.info(f"设备 {self.name} 占用")
        else: self.log.info(f"设备 {self.name} 释放")


    # ---------- 统一生命周期接口 ----------

    def open(self, **kwargs):
        """
        打开设备连接（转发到底层 driver）。
        """
        
        self.log.info(f"设备 {self.name} 连接")
        #is_sync = kwargs.get('sync', True)
        
        try:
            if 'ch' in kwargs:
                kwargs['ch']=self.channel_map.resolve_channel(kwargs['ch'])
        except:
            pass
        
        self._update_status(last_op="open")
        #若已连接，则跳过
        if self._status.connected: return
        
        try:
            self._driver_adapter.open(**kwargs)
            self._driver_adapter.init()
            #if is_sync:
            #    self._to_sync()
            self._update_status(connected=True, last_error=None)
        except Exception as e:
            self._update_status(
                last_error=str(e)
            )
            raise DeviceConnectionError(str(e))

    def close(self, **kwargs):
        """
        关闭设备连接。
        """
        
        self.log.info(f"设备 {self.name} 断开")
        #若已关闭，则跳过
        if not self._status.connected: return
        
        self._update_status(last_op="close")
        err = None
        try:
            self._driver_adapter.close(**kwargs)
        except Exception as e:
            err = e
                
        # 无论 driver_adapter.close 成功与否，都把本地状态置为已关闭
        self._update_status(
            connected=False,
            busy=False,
            last_error=(str(err) if err else None),
        )

        if err:
            raise DeviceCloseError(str(err))
    
    def delete(self):
        """
        关闭设备连接，同时从 InstrumentBase 注册表中删除。
        无论底层连接关闭成功与否，都必须从注册表摘除，
        否则重建实例时会因重名抛"已存在设备"错误。
        """
        try:
            self.close()
        finally:
            self._update_status(last_op="delete")
            super().close()

    # ---------- 统一参数写接口 ----------

    def resolve_channel(self, ch):
        """
        通道名 -> 实际通道 ID。
        未配置或无有效映射的通道名直接给出可定位的错误，避免把通道名透传给底层驱动。
        """
        if ch == "global":
            return ch
        if isinstance(ch, str) and ch not in self.channel_map.channel_map:
            raise DeviceParameterError(
                f"设备 {self.name} 未配置通道 {ch}：请检查该设备型号与通道映射，"
                f"并执行一次“仪器注册”使通道映射下发到硬件"
            )
        resolved = self.channel_map.resolve_channel(ch)
        if isinstance(resolved, str):
            raise DeviceParameterError(
                f"设备 {self.name} 通道 {ch} 的映射值 {resolved!r} 不是合法的通道ID"
                f"（应形如 (板卡,通道)，如 (0,0)）"
            )
        return resolved

    def write(self, name: str, value, **kwargs):
        """
        高层统一写接口。
        """
        if 'ch' in kwargs:
            kwargs['ch']=self.resolve_channel(kwargs['ch'])
        return self._driver_adapter.write(name, value, **kwargs)

    # ---------- 统一参数读接口（如果有的话） ----------

    def read(self, name: str, **kwargs):
        """
        高层统一读接口。
        """
        if 'ch' in kwargs:
            kwargs['ch']=self.resolve_channel(kwargs['ch'])
        return self._driver_adapter.read(name, **kwargs)
        
    # ---------- 低层透传接口：SCPI 命令级 ----------

    def set(self, cmd: str, value=None, **kwargs):
        """
        高层统一set接口。
        """
        return self._driver_adapter.set(cmd, value, **kwargs)

    def query(self, cmd_name: str, **kwargs):
        """
        高层统一query接口。
        """
        return self._driver_adapter.query(cmd_name, **kwargs)
    
    def get_info(self):
        try:
            res = self.query('*IDN?')
            info = res.decode('latin-1')
        except Exception as e:
            raise DeviceAccessError(str(e))
            
        return info
    
    def send(self, data, **kwargs):
        """
        实验任务级别的发送接口，例如：
        - 上层构造好一整个实验的波形 / 参数 dict
        - 这里负责调用若干 write / set / download_xxx 的组合
        """
        pass

    def receive(self, **kwargs):
        """
        实验任务级别的接收接口，例如：
        - 对于 ADC，拉一帧 IQ 数据回来
        - 对于 SYNC，可能只是读状态位
        """
        pass

    def preprocess(self, send_data, **kwargs):
        """
        数据预处理钩子：上层有需要的话可以在这里做归一化/补零等。
        """
        return send_data

    def postprocess(self, received_data, **kwargs):
        """
        数据后处理钩子：例如对 ADC 采集的数据做解包 / reshape / 转成 IQ。
        """
        return received_data
    
    def get_status(self) -> dict:
        """给前端/上层用，返回可 JSON 化的状态 dict"""
        return self._status.to_dict()
    
    def _update_status(self, **kwargs):
        """内部辅助函数，更新状态字段并触发通知"""
        for k, v in kwargs.items():
            if hasattr(self._status, k):
                setattr(self._status, k, v)
        self._status.last_update_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    
    def _set_cache_param(self, name: str, value, ch=None) -> None:    
            
        if ch is None:
            key = "GLOBAL"
        else:
            key = "CHANNEL"
        if key not in self._param_cache:
            self._param_cache[key] = {}
        
        if ch is None:
            self._param_cache[key][name] = value
        else:
            if ch not in self._param_cache[key]:
                self._param_cache[key][ch] = {}
            self._param_cache[key][ch][name] = value
            
    def _get_cache_param(self, name: str, ch=None) -> Any:
        
        if ch is None:
            key = "GLOBAL"
        else:
            key = "CHANNEL"
        
        if key not in self._param_cache:
            return None
        
        if ch is None:
            return self._param_cache[key][name]
        else:
            return self._param_cache.get(key, {}).get(ch, {}).get(name, None)
    
    def _remove_cache_param(self, name):
        
        key = "GLOBAL"
        if name in self._param_cache[key]:
            self._param_cache[key].pop(name)
            return
        key = "CHANNEL"
        for ch in self.channel_map.channel_map:
            if name in self._param_cache[key][ch]:
                self._param_cache[key][ch].pop(name)
    
    def get_cache_param(self):
        return self._param_cache
    
    def get_param_specs(self):
        params = self.get_param_list()
        res = {}
        for k, v in params.items():
            value = asdict(v)
            if 'cast' in value:
                cast_value = value['cast']
                if cast_value is not None:
                    # 如果是类，我们尝试获取它的名字，否则转换为字符串
                    if hasattr(cast_value, '__name__'):
                        value['cast'] = cast_value.__name__
                    else:
                        value['cast'] = str(cast_value)
                else:
                    value['cast'] = None
            res[k] = value
        return res
               
        
    def set_waveform_buffer_size(self, n: int) -> None:
        """
        动态调整每个通道最多缓存的波形数量, 已有缓存会被重新塞到新的 deque 中。
        """
        if n <= 0:
            raise ValueError("max_waveforms_per_channel 必须 > 0")

        self._max_waveforms_per_channel = n
        # 重新构造所有 channel 的 deque
        new_cache: Dict[Optional[int], Deque[WaveformRecord]] = defaultdict(
            lambda: deque(maxlen=self._max_waveforms_per_channel)
        )
        for ch, dq in self._waveform_cache.items():
            for rec in dq:
                new_cache[ch].append(rec)
        self._waveform_cache = new_cache
        
    # ----- 内部：记录波形 -----
    def _record_waveform(self,
                         y,
                         ch,
                         **meta) -> None:
        """
        在下发 / 采集波形后调用。
        y: 波形数据
        ch: 通道号
        meta: 额外信息
        """
        if y is None:
            return

        if isinstance(y, Wave):
            y = y.to_sparse_dict()
        
        rec = WaveformRecord(
            channel=ch,
            ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            meta=meta,
            y=y,
        )

        self._waveform_cache[ch].append(rec)
        
    def set_wave(self, wave: Wave, ch):
        
        self.write('Waveform', wave.get_data(), ch=ch)
        self._record_waveform(y=wave, ch=ch)
        
    # ----- 给前端/上层用的波形查询接口 -----
    def get_waveforms(self,
                      ch: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        取出当前设备缓存的波形。
        param channel: 指定通道号；None 表示所有通道
        """
        
        if ch is None:
            records = {}
            for k, v in self._waveform_cache.items():
                records[k] = [asdict(r) for r in v]
            return records
        else:
            records = self._waveform_cache.get(ch, [])
            return [asdict(r) for r in records]

    def clear_waveforms(self,
                        channel: Optional[int] = None) -> None:
        """
        清空波形缓存。
        channel 为 None: 所有通道
        """
        if channel is None:
            self._waveform_cache.clear()
            return

        self._waveform_cache.pop(channel, None)
        return
    
    def get_param_list(self):
        self._ensure_inst_specs_snapshot()
        return self._param_specs_inst
    
    def _ensure_inst_specs_snapshot(self):
        if not hasattr(self, "_param_specs_inst"):
            cls_specs = getattr(self.__class__, "__param_specs__", {}) or {}
            self._param_specs_inst = dict(cls_specs)   # 快照 

    def add_param_inst(self, name, spec: ParamSpec, prefix="set_"):
        self._ensure_inst_specs_snapshot()
        if (name in self._param_specs_inst):
            raise KeyError(f"param '{name}' exists")
        self._param_specs_inst[name] = spec
        if not spec.requires_ch:
            self._set_cache_param("name", spec.value)
        mname = f"{prefix}{name}"
        fn = _make_setter(mname, name, spec)
        setattr(self, mname, MethodType(fn, self))

    def patch_param_inst(self, name, spec, prefix="set_"):
        self.remove_instance(name, prefix)
        self.add_param_inst(name, spec, prefix)

    def remove_param_inst(self, name, prefix="set_"):
        self._ensure_inst_specs_snapshot()
        if name not in self._param_specs_inst:
            raise KeyError(f"param '{name}' not found")
        self._remove_cache_param(name)
        self._param_specs_inst.pop(name, None)
        
        self.__dict__.pop(f"{prefix}{name}", None)  # 删实例方法
        
    def reset_offset(self):
        self._driver_adapter.reset_offset()
        
        for ch in self.channel_map.channel_map:
            self._set_cache_param('offset', 0, ch=ch)

    def channel_open(self, ch):
        self._driver_adapter.channel_open(self.resolve_channel(ch))