from abc import abstractmethod, ABC
from .error import *
import logging

class InstrumentBase(ABC):
    """
    设备抽象类
    
    Args:
        name (str): 设备名称
        
    _all_instruments：所有实例化的设备集合
    _type：当前实例对应的设备类
    _instances：当前设备类的实例化对象集合
    """
    
    _all_instruments: dict = {}
    _type = None
    _instances: set = set()

    def __init__(self, name: str, group: str = "default", **kwargs) -> None:
        
        self.name = name
        self.group = group
        self.log = logging.getLogger(f"device.{group}.{name}")
        for k, v in kwargs.items():
            super().__setattr__(k, v)        
        self.record_instance(self)
    
    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self.name}>"

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as e:
            raise DeviceCloseError(f"删除设备 {self.name} 错误：{str(e)}")

    @classmethod
    def record_instance(cls, instance) -> None:
        """
        Record (a weak ref to) an instance in a class's instance list.
        """
        name = instance.name
        group = instance.group
        
        if group not in cls._all_instruments:
            cls._all_instruments[group] = {}
        group_dict = cls._all_instruments[group]
        
        if name in group_dict:
            raise DeviceDefineError(
                f"组 [{group}] 中已存在设备: {name}"
            )

        group_dict[name] = instance
        
        if getattr(cls, "_type", None) is not cls:
            cls._type = cls
            cls._instances = set()
            
        cls._instances.add(instance)
    
    @classmethod
    def remove_instance(cls, instance) -> None:
        """
        Remove a particular instance from the record.
        """
        group = instance.group
        name = instance.name

        if instance in cls._instances:
            cls._instances.remove(instance)
            
        group_dict = cls._all_instruments.get(group, {})
        if group_dict.get(name) is instance:
            del group_dict[name]
                            
    @classmethod
    def instances(cls):
        """
        Get all currently defined instances of this instrument_base class.
        """
        if getattr(cls, "_type", None) is not cls:
            return []
        return list(getattr(cls, "_instances", []))

    @classmethod
    def find_instrument(cls, name, group='default'):
        """
        获取指定名称的硬件设备实例
        """
        return cls._all_instruments.get(group, {}).get(name, None)
    
    @classmethod
    def find(cls, path: str):
        if "/" not in path:
            return cls.find_instrument(path)
        group, name = path.split("/", 1)
        return cls.find_instrument(name, group)
        
    def close(self):
        self.remove_instance(self)
    
    def snapshot(self):
        return self.__dict__.copy()
