from .adaptor import DriverAdapter
from ..driver import *

class CYDriverAdapter(DriverAdapter):
    """
    思仪驱动适配器，对接cy_driver
    适用于已经基本符合统一接口的 driver。
    """

    def open(self, **kwargs):
        method = self._require_method("open", "open_inst", "connect")
        return method(**kwargs)

    def close(self, **kwargs):
        method = self._optional_method("close", "close_inst", "disconnect")
        if method is None:
            return None
        return method(**kwargs)

    def write(self, name: str, value, **kwargs):
        if name in ['TriggerDelayAWG', 'TriggerDelay']:
            value = value * 1e9 
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
    
    def init(self):
        method = self._optional_method("ADInit")
        if method:
            method()

    def capabilities(self) -> dict:
        return {
            "open": self._optional_method("open", "open_inst", "connect") is not None,
            "close": self._optional_method("close", "close_inst", "disconnect") is not None,
            "write": self._optional_method("write", "set_value", "set_param") is not None,
            "read": self._optional_method("read", "get_value", "get_param") is not None,
            "query": self._optional_method("query", "socket_query", "sock_query") is not None,
        }

class CYSyncDriverAdapter(CYDriverAdapter):
    """
    思仪同步驱动适配器，对接cy_sync
    """
    def __init__(self, addr, log=None, **kwargs):
        
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYSYNCDriver(addr, log=log, **kwargs)
        self.log = log

    def set_trigger(self, shots=1, period=0):
        if shots == 1:
            method = self._require_method("sync_triger_single")
            method()
        else:
            method = self._require_method("sync_triger_cycle")
            method((period, shots))
            
    def sync(self, hardwares):
        
        method = self._optional_method("multDevice_sync")
        if method:
            devices = []
            for hd in hardwares:
                devices.append(hd._driver_adapter.driver)
            method(devices)
    
class CYADDriverAdapter(CYDriverAdapter):
    """
    思仪AD驱动适配器，对接cy_ad
    """
    def __init__(self, addr, log=None, **kwargs):
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYADDriver(addr, log=log, **kwargs)
        self.log = log

class CYDA6DriverAdapter(CYDriverAdapter):
    """
    思仪DA6驱动适配器，对接cy_da6
    """
    def __init__(self, addr, log=None, **kwargs):
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYDA6Driver(addr, log=log, **kwargs)
        self.log = log

class CYDADCDriverAdapter(CYDriverAdapter):
    """
    思仪DADC驱动适配器，对接cy_dadc
    """
    def __init__(self, addr, log=None, **kwargs):
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYDADCDriver(addr, log=log, **kwargs)
        self.log = log
        
class CYDA10DriverAdapter(CYDriverAdapter):
    """
    思仪DA10驱动适配器，对接cy_da10
    """
    def __init__(self, addr, log=None, **kwargs):
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYDA10Driver(addr, log=log, **kwargs)
        self.log = log

class CYVNADriverAdapter(CYDriverAdapter):
    """
    思仪VNAD驱动适配器，对接cy_vana
    """
    def __init__(self, addr, log=None, **kwargs):
        debug = kwargs.get('debug', False)
        if debug:
            self.driver = MockerDriver(addr, log=log, **kwargs)
        else:
            self.driver = CYVNADriver(addr, log=log, **kwargs)
        self.log = log
