from .adaptor import DriverAdapter
from ..driver_old import *
from ..driver import MockerDriver


# 型号 -> 站级驱动（板卡布局按机型区分）
STATION_DRIVERS = {
    "CeyearStation": CeyearStation,
    "CeyearStationSync": CeyearStationSync,
    "CeyearStationXY": CeyearStationXY,
    "CeyearStationProb": CeyearStationProb,
    "CeyearStationF8": CeyearStationF8,
}


class CYOLDDriverAdapter(DriverAdapter):
    """
    思仪旧版驱动适配器，对接cy_driver
    适用于已经基本符合统一接口的 driver。
    """
    def __init__(self, addr, log=None, **kwargs):
        
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            model = kwargs.get('model')
            driver_cls = STATION_DRIVERS.get(model, CeyearStation)
            self.driver = driver_cls(addr, log=log, **kwargs)
            
        self.log = log

    def open(self, **kwargs):
        method = self._require_method("open", "open_inst", "connect")
        return method(**kwargs)

    def close(self, **kwargs):
        method = self._optional_method("close", "close_inst", "disconnect")
        if method is None:
            return None
        return method(**kwargs)

    def write(self, name: str, value, **kwargs):
        method = self._require_method("write", "set_value", "set_param")
        return method(name, value, **kwargs)

    def read(self, name: str, **kwargs):
        method = self._require_method("read", "get_value", "get_param")
        return method(name, **kwargs)

    def set(self, cmd: str, value=None, **kwargs):
        method = self._require_method("set")
        return method(cmd, value, **kwargs)

    def query(self, cmd: str, **kwargs):
        method = self._require_method("query", "socket_query", "sock_query")
        return method(cmd, **kwargs)
    
    def set_trigger(self, shots=1, period=0):
        
        method = self._require_method("set_trigger")
        if method:
            method(shots, period)
    
    def reset_offset(self):
        method = self._optional_method("reset_offset")
        if method:
            return method()
        return None

    def channel_open(self, ch):
        method = self._optional_method("channelOpen", "channel_open", "open_channel")
        if method:
            return method(ch)
        return None

    def capabilities(self) -> dict:
        return {
            "open": self._optional_method("open", "open_inst", "connect") is not None,
            "close": self._optional_method("close", "close_inst", "disconnect") is not None,
            "write": self._optional_method("write", "set_value", "set_param") is not None,
            "read": self._optional_method("read", "get_value", "get_param") is not None,
            "query": self._optional_method("query", "socket_query", "sock_query") is not None,
        }
