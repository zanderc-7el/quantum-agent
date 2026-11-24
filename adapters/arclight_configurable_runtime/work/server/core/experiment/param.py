from abc import ABC
from typing import Any

class Param(ABC): 
    
    def __init__(self, val) -> None:
        self.val = val
    
    def __call__(self) -> Any:
        raise NotImplementedError

    def __repr__(self) -> str:
        
        return f"{self.val}"

class ParamVal(Param):
    
    def __init__(self, val) -> None:
        self.val = val
        
    def __call__(self) -> Any:
        return self.val

class ParamRef(Param):
    def __init__(self, name, default=None):
        self.name, self.val = name, default
    
    def __call__(self) -> Any:
        if self.val is None:
            raise ValueError(f"missing {self.name}")
        return self.val
    
    def set(self, val):
        self.val = val
        
        
def as_param(x):
    """标量->ParamVal；已是 Param 则原样返回。"""
    return x if isinstance(x, Param) else ParamVal(x)

def set_val_for(*names):
    def deco(fn):
        def wrapper(self, *args, **kwargs):
            ctx = kwargs.get("ctx", {})
            for n in names:
                attr = getattr(self, n)
                if isinstance(attr, ParamRef) and attr.name in ctx:
                    attr.set(ctx[attr.name])
            return fn(self, *args, **kwargs)
        return wrapper
    return deco