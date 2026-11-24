from ..compiler.decompose import *
from typing import Iterable, Union, List, Dict, Tuple
from dataclasses import dataclass

@dataclass(frozen=True)
class AllXY:
    
    targets: Iterable[Union[int, str]]
    add_measure: bool = True
    repeat: int = 1
    
    @property
    def xy(self) -> List[str]:
        
        cirs = [
            "I,I","X,X","Y,Y",
            "X,Y", "Y,X",
            "X/2,I", "Y/2,I",
            "X/2,Y/2", "Y/2,X/2",
            "X/2,Y", "Y/2,X",
            "X,Y/2", "Y,X/2",
            "X/2,X", "X,X/2",
            "Y/2,Y", "Y,Y/2",
            "X,I", "Y,I",
            "X/2,X/2", "Y/2,Y/2"
        ]
        repeat_cirs = []
        for cir in cirs:
            repeat_cirs.extend([cir] * self.repeat)
        return repeat_cirs
    
    def _to_gate(self, seq: str):
        gates = []
        parts = [p.strip() for p in seq.split(",") if p.strip()]
        for p in parts:
            if p == 'I': continue
            name = p
            arg_value = []
            if p == 'X/2':
                name = 'RX'
                arg_value = [np.pi/2]
            elif p == '-X/2':
                name = 'RX'
                arg_value = [-np.pi/2]
            elif p == 'Y/2':
                name = 'RY'
                arg_value = [np.pi/2]
            elif p == '-Y/2':
                name = 'RY'
                arg_value = [-np.pi/2]
            gates.append((name, self.targets, arg_value))
        return gates
    
    def xy_seq(self, xy: str):
        gates = self._to_gate(xy)
        if self.add_measure:
            gates.append(('MEASURE', self.targets))
        return gates