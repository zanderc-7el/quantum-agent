# 设备异常基类
class DeviceException(Exception):
    """设备相关异常的基类"""
    def __init__(self, msg: str, code: int):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg}")

# 具体异常类
class DeviceNotFoundError(DeviceException):
    def __init__(self, msg="Device not found"):
        super().__init__(msg, code=400)

class DeviceDefineError(DeviceException):
    def __init__(self, msg="Device define error"):
        super().__init__(msg, code=401)

class DeviceConnectionError(DeviceException):
    def __init__(self, msg="Device connection error"):
        super().__init__(msg, code=402)

class DeviceCloseError(DeviceException):
    def __init__(self, msg="Device close error"):
        super().__init__(msg, code=403)

class DeviceParameterError(DeviceException):
    def __init__(self, msg="Device parameter error"):
        super().__init__(msg, code=404)

class DeviceAccessError(DeviceException):
    def __init__(self, msg="Device access error"):
        super().__init__(msg, code=405)