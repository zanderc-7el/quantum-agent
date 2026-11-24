from .config import debug_mode
import typing as T

''' raise this when illegal line break occurs'''
    
def ErrorThrow(st):
    if debug_mode == True:
        print("test: ", st)
    raise CompileError(1002, st)


class CompileError(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg

class DecomposeError(Exception):
    def __init__(self, code, msg) -> None:
        self.code = code
        self.msg = msg