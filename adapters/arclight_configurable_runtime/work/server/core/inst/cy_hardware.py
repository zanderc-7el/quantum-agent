from .hardware_base import *
from .driver import *
from .error import *
from typing import List, Optional, Iterable
from .instrument_base import InstrumentBase
from .utils import DriveTypeSchema
from .params import *
from .adaptors.cy_adaptor import *
from .adaptors.cy_old_adaptor import CYOLDDriverAdapter

# 注册表：型号字符串 -> Hardware 子类
HARDWARE_REGISTRY = {}

def _register_hardware(cls):
    for model in getattr(cls, "support_models", []):
        HARDWARE_REGISTRY[model] = cls
    return cls

@_register_hardware
@expose_params(params=CY_OLD_PARAMS)
class CeyearStationHardware(HardwareBase):
    driver_adapter_cls = CYOLDDriverAdapter
    support_models = [DriveTypeSchema.CeyearStation]


@_register_hardware
@expose_params(params=CY_OLD_PARAMS)
class CeyearStationF8Hardware(HardwareBase):
    """F8: logical RF 14/ch0 -> RF8/ch0; ADC 15/ch0 -> OSC0/ch0."""
    driver_adapter_cls = CYOLDDriverAdapter
    support_models = ["CeyearStationF8"]


@_register_hardware
@expose_params(params=CY_STATION_DRIVE_PARAMS)
class CeyearStationSyncHardware(HardwareBase):
    """7 号机同步设备：Z 通道 + 触发主设备。"""
    driver_adapter_cls = CYOLDDriverAdapter
    support_models = [DriveTypeSchema.CeyearStationSync]


@_register_hardware
@expose_params(params=CY_STATION_DRIVE_PARAMS)
class CeyearStationXYHardware(HardwareBase):
    """7 号机 XY 驱动设备。"""
    driver_adapter_cls = CYOLDDriverAdapter
    support_models = [DriveTypeSchema.CeyearStationXY]


@_register_hardware
@expose_params(params=CY_OLD_PARAMS)
class CeyearStationProbHardware(HardwareBase):
    """7 号机采集/读出设备：ADC 采集 + 读出 AWG。"""
    driver_adapter_cls = CYOLDDriverAdapter
    support_models = [DriveTypeSchema.CeyearStationProb]

@_register_hardware
@expose_params(params=CY_6DA_PARAMS)
class CY6DAHardware(HardwareBase):
    driver_adapter_cls = CYDA6DriverAdapter
    support_models = [DriveTypeSchema.CY_6DA]


@_register_hardware
@expose_params(params=CY_10DA_PARAMS)
class CY10DAHardware(HardwareBase):
    driver_adapter_cls = CYDA10DriverAdapter
    support_models = [DriveTypeSchema.CY_10DA]


@_register_hardware
@expose_params(params=CY_6DADC_PARAMS)
class CY6DADCHardware(HardwareBase):
    driver_adapter_cls = CYDADCDriverAdapter
    support_models = [DriveTypeSchema.CY_6DADC]


@_register_hardware
@expose_params(params=CY_AD_PARAMS)
class CYADHardware(HardwareBase):
    driver_adapter_cls = CYADDriverAdapter
    support_models = [DriveTypeSchema.CY_AD]

@_register_hardware
class CYSYNCHardware(HardwareBase):
    driver_adapter_cls = CYSyncDriverAdapter
    support_models = [DriveTypeSchema.CY_SYNC]

@_register_hardware
@expose_params(params=CY_NA_PARAMS)
class CYNAHardware(HardwareBase):
    driver_adapter_cls = CYVNADriverAdapter
    support_models = ['E8363B', 'E8363C', 'E5071C', 'E5080A', 'E5080B', 'E5063A', 'ZNB8-4Port',
                      'ZNB20-4Port', 'ZNB20-2Port', 'N5232A', 'N5222B', 'ZVL-13', 'ZNA26-2Port', 'M9802A', '3672B','3656D']

def create_hardware(name: str, model: str, addr: str, group:str="default", **kwargs) -> HardwareBase:
    """
    工厂函数：根据 model 自动选择对应的 Hardware 子类。
    """
    if model not in HARDWARE_REGISTRY:
        raise DeviceAccessError(f"不支持的设备型号：{model}")

    cls = HARDWARE_REGISTRY[model]
    return cls(name=name, addr=addr, model=model, group=group, **kwargs)

def get_hardware(name: str, group: str="default"):
    
    dev = InstrumentBase.find_instrument(name, group)
    if dev is None: raise DeviceNotFoundError(f"组 [{group}] 没有设备：{name}")
    return dev
