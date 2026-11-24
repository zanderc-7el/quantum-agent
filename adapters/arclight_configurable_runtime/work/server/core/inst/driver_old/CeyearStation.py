from .CeyearStationBase import Driver as StationBase
import itertools
import logging
log = logging.getLogger(__name__)
from .quant import QInteger

class CeyearStation(StationBase):

    __log__ = log
    support_models = []

    # (board,chann) 板卡编号和通道编号组成的元组
    LF_boards = [0, 1, 2, 3, 4, 5]
    RF_boards = [6, 7, 8, 9, 10, 11]
    LO_boards = []
    OSC_boards = [12]
    CHs = list(itertools.product(LF_boards, (0, 1, 2, 3)))\
        +list(itertools.product(RF_boards, (0, 1)))\
        +list(itertools.product(OSC_boards, (0, 1)))

    def __init__(self, addr=None, **kw):
        super().__init__(addr, **kw)
    
    def open(self, **kwargs):
        super().performOpen()
        
    def close(self, **kwargs):
        super().performClose()
        
    def write(self, name: str, value, **kwargs):
        if name == 'Coefficient':
            # freq, phase, start_point, point_len, _, _ = value
            # qfreq = "f_list"
            # super().setValue(qfreq, freq, **kwargs)
            # qphase = 'demodPhaseList'
            # super().setValue(qphase, phase, **kwargs)
            # qstart_point = 'demodPointStartList'
            # super().setValue(qstart_point, start_point, **kwargs)
            # qpoint_len = 'demodPointLenList'
            # super().setValue(qpoint_len, point_len, **kwargs)
            super().setValue('coefficient', value, **kwargs)
        else:
            q = name
            super().setValue(q, value, **kwargs)

    def read(self, name: str, **kwargs):
        q = name
        return super().getValue(q, **kwargs)   
        
    def set(self, cmd: str, value=None, **kwargs):
        q = QInteger('', value, unit='', ch=None, get_cmd='', set_cmd=cmd)
        super().performSetValue(q, value, **kwargs)
        
    def socket_query(self, cmd: str, **kwargs):
        q = QInteger('', '', unit='', ch=None, get_cmd=cmd, set_cmd='')
        return super().performGetValue(q, **kwargs)