from abc import ABC
from typing import Any


class DriverAdapter(ABC):
    """
    驱动适配器基类。

    对上提供统一接口：
    open / close / write / read / set / query

    对下适配具体 driver
    """

    def open(self, **kwargs):
        raise NotImplementedError

    def close(self, **kwargs):
        raise NotImplementedError

    def write(self, name: str, value: Any, **kwargs):
        raise NotImplementedError

    def read(self, name: str, **kwargs):
        raise NotImplementedError

    def set(self, cmd: str, value=None, **kwargs):
        raise NotImplementedError

    def query(self, cmd: str, **kwargs):
        raise NotImplementedError

    def reset_offset(self):
        return None

    def channel_open(self, ch):
        return None
    
    def init(self, **kwargs):
        return None

    def capabilities(self) -> dict:
        """
        返回驱动能力描述，给上层或前端判断。
        """
        return {}

    def _require_method(self, *names):
        for name in names:
            method = getattr(self.driver, name, None)
            if callable(method):
                return method

        raise AttributeError(
            f"{self.__class__.__name__} driver [ {self.driver.__class__.__name__} ] 不支持方法，候选方法名: {names}"
        )

    def _optional_method(self, *names):
        for name in names:
            method = getattr(self.driver, name, None)
            if callable(method):
                return method
        return None