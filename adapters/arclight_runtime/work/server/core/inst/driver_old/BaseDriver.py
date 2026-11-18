# Author: feihoo87  <xuhk@baqis.ac.cn>  2021/01/01
# Rebuild by lizhiyuan

import copy
import logging
import numpy as np
try:
    import pyvisa as visa  # 新版本导入方式更改
except:
    import visa
import struct
from datetime import datetime

from .quant import QReal, QInteger, QString, QOption, QBool, QVector, QList, newcfg

log = logging.getLogger(__name__)
__all__ = [
    'BaseDriver', 'visaDriver'
]

class BaseDriver(object):

    __log__=None

    @property
    def log(self):
        if self.__log__ is None:
            return log
        else:
            return self.__log__

    support_models = []
    """the confirmed models supported by this driver"""

    quants = []

    CHs=[1,2,3,4]
    '''Channels list, default is 4 channels'''

    def __init__(self, addr=None, timeout=3, **kw):
        self.addr = addr
        self.timeout = timeout
        self.handle = kw.get('handle',None)
        self.model = kw.get('model',None)
        self.config = newcfg(self.quants, self.CHs)
        self.quantities={}
        for quant in self.quants:
            self.quantities[quant.name] = copy.deepcopy(quant)

    def __repr__(self):
        return 'Driver(addr=%s,model=%s)' % (self.addr,self.model)

    def __del__(self):
        self.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.close()

    def newcfg(self):
        self.config = newcfg(self.quants, self.CHs)
        self.log.info('# new config!')

    def get_attr(self,attr='config'):
        assert hasattr(self,attr),f'{self} Not Find attr: {attr}!'
        return getattr(self,attr)

    def set(self, cfg={}, **kw):
        assert isinstance(cfg,dict),f'cfg is not dict!'
        cfg.update(kw)
        for key, value in cfg.items():
            if isinstance(value,dict):
                self.setValue(key, **value)
            else:
                self.setValue(key, value)

    def performOpen(self,**kw):
        pass

    def performClose(self,**kw):
        pass

    def open(self, **kw):
        self.performOpen(**kw)
        self.log.info(f'# {datetime.now()}    Open Instrument {self.model}@{self.addr}')

    def close(self, **kw):
        self.performClose(**kw)
        self.log.info(f'# {datetime.now()}    Close Instrument {self.model}@{self.addr}')

    def performOPC(self):
        return 1

    def performSetValue(self, quant, value, **kw):
        if quant.set_cmd != '':
            quant.set(self,value,**kw)
        else:
            pass

    def performGetValue(self, quant, **kw):
        if quant.get_cmd != '':
            return quant.get(self,**kw)
        else:
            return None

    def setValue(self, name, value, **kw):
        assert name in self.quantities,f'{self} Not Find quant: {name}!'
        quant=self.quantities[name]
        _kw=copy.deepcopy(quant.default)
        _kw.update(value=value,**kw)
        assert _kw['ch'] in self.CHs or _kw['ch']=='global',f"{self} Not Find CH: {_kw['ch']}!"
        self.performSetValue(quant, **_kw)
        self.log.info('# %s    Set Value of CH-%s: %s --> %s %s' % (datetime.now(),_kw.get('ch'),name,value,_kw.get('unit')))
        self.config[name][_kw.pop('ch')].update(_kw) # update config
        opc=self.performOPC()
        return opc

    def getValue(self, name, **kw):
        assert name in self.quantities,f'{self} Not Find quant: {name}!'
        quant=self.quantities[name]
        _kw=copy.deepcopy(quant.default)
        _kw.update(**kw)
        assert _kw['ch'] in self.CHs or _kw['ch']=='global',f"{self} Not Find CH: {_kw['ch']}!"
        value = self.performGetValue(quant, **_kw)
        if value is None:
            value = self.config[name][_kw.get('ch')]['value']
            self.log.info('# %s    Get Value of CH-%s: %s <-- %s %s (from config)' % (datetime.now(),_kw.get('ch'),name,value,_kw.get('unit')))
        else:
            self.config[name][_kw.get('ch')].update({'value':value}) # update config
            self.log.info('# %s    Get Value of CH-%s: %s <-- %s %s (update config)' % (datetime.now(),_kw.get('ch'),name,value,_kw.get('unit')))
        return value

    def query(self,message,**kw):
        return

    def write(self,message,**kw):
        pass

    def start(self):
        pass

    def end(self):
        pass

    def beforeSet(self):
        pass

    def afterSet(self):
        pass

    def beforeGet(self):
        pass

    def afterGet(self):
        pass

class visaDriver(BaseDriver):

    error_command = 'SYST:ERR?'
    """The SCPI command to query errors."""


    def __init__(self, addr=None, timeout=3, visa_backend='', **kw):
        super(visaDriver, self).__init__(addr, timeout, **kw)
        self.visa_backend=visa_backend
        if 'ins' in kw.keys():#兼容旧版本diver使用方法,ins即为仪器句柄
            self.handle = kw.get('ins') 

    def __repr__(self):
        return 'visaDriver(addr=%s,model=%s)' % (self.addr,self.model)

    def performOpen(self, **kw):
        self.addr_hash = hash(self.addr)
        self.log.debug(f'# {self.addr_hash}: hash("{self.addr}")')
        try:
            self.handle.open()
        except:
            rm = visa.ResourceManager(self.visa_backend)
            self.log.debug(f'rm = visa.ResourceManager("{self.visa_backend}")')
            self.handle = rm.open_resource(self.addr)
            self.log.debug(f'handle_{self.addr_hash} = rm.open_resource("{self.addr}")')
        self.handle.timeout = self.timeout * 1000
        self.log.debug(f'handle_{self.addr_hash}.timeout = {self.timeout * 1000}')
        try:
            IDN = self.handle.query("*IDN?").split(',')
            # company = IDN[0].strip()
            model = IDN[1].strip()
            # version = IDN[3].strip()
            self.model = model
        except:
            raise IOError(f'{self} query IDN error!')

    def performClose(self, **kw):
        self.handle.close()
        self.log.debug(f'handle_{self.addr_hash}.close()')

    def performOPC(self):
        opc=self.query("*OPC?")
        return opc

    def set_timeout(self, t):
        self.timeout = t
        if self.handle is not None:
            self.handle.timeout = t * 1000
            self.log.debug(f'handle_{self.addr_hash}.timeout = {self.timeout * 1000}')

    def errors(self):
        """返回错误列表"""
        e = []
        if self.error_command == '':
            return e
        while True:
            s = self.handle.query(self.error_command)
            self.log.debug(f'handle_{self.addr_hash}.query("{self.error_command}")')
            _ = s[:-1].split(',"')
            code = int(_[0])
            msg = _[1]
            if code == 0:
                break
            e.append((code, msg))
        return e

    def query(self, message, mode=None, **kw):
        if self.handle is None:
            return None
        self.log.debug("# %s << %s", str(self.handle), message)
        try:
            if mode is None:
                res = self.handle.query(message,**kw)
                self.log.debug(f'handle_{self.addr_hash}.query("{message}",{kw})')
            elif mode in ['ascii']:
                res = self.handle.query_ascii_values(message, converter='f',
                        separator=',', container=list, delay=None, **kw)
                self.log.debug(f"handle_{self.addr_hash}.query_ascii_values('{message}',converter='f',\
                        separator=',', container=list, delay=None,{kw})")
            elif mode in ['binary']:
                res = self.handle.query_binary_values(message, datatype='f',
                        is_big_endian=False, container=list, delay=None,
                        header_fmt='ieee', **kw)
                self.log.debug(f"handle_{self.addr_hash}.query_binary_values('{message}',datatype='f',\
                        is_big_endian=False, container=list, delay=None,\
                        header_fmt='ieee',{kw})")
            self.log.debug("# %s >> %s", str(self.handle), res)
            return res
        except:
            self.log.exception("# %s << %s", str(self.handle), message)
            raise

    def query_ascii_values(self, message, converter='f', separator=',',
                           container=list, delay=None,):
        if self.handle is None:
            return None
        self.log.debug("# %s << %s", str(self.handle), message)
        try:
            res = self.handle.query_ascii_values(
                message, converter, separator, container, delay)
            self.log.debug(f"handle_{self.addr_hash}.query_ascii_values('{message}',converter='f',\
                        separator=',', container=list, delay=None)")
            self.log.debug("# %s >> <%d results>", str(self.handle), len(res))
            return res
        except:
            self.log.exception("# %s << %s", str(self.handle), message)
            raise

    def query_binary_values(self, message, datatype='f', is_big_endian=False,
                            container=list, delay=None,
                            header_fmt='ieee',):
        if self.handle is None:
            return None
        self.log.debug("# %s << %s", str(self.handle), message)
        try:
            res = self.handle.query_binary_values(message, datatype,
                            is_big_endian,container, delay, header_fmt)
            self.log.debug(f"handle_{self.addr_hash}.query_binary_values('{message}',datatype='f',\
                        is_big_endian=False, container=list, delay=None,\
                        header_fmt='ieee')")
            self.log.debug("# %s >> <%d results>", str(self.handle), len(res))
            return res
        except:
            self.log.exception("# %s << %s", str(self.handle), message)
            raise

    def write(self, message, **kw):
        """Send message to the instrument."""
        if self.handle is None:
            return None
        self.log.debug("# %s << %s", str(self.handle), message)
        try:
            _ = self.handle.write(message,**kw)
            self.log.debug(f"handle_{self.addr_hash}..write('{message}',{kw})")
        except:
            self.log.exception("%s << %s", str(self.handle), message)
            raise

    def write_ascii_values(self, message, values, converter='f', separator=',',
                           termination=None, encoding=None,):
        if self.handle is None:
            return None
        log_msg = message+('<%d values>' % len(values))
        self.log.debug("# %s << %s", str(self.handle), log_msg)
        try:
            _ = self.handle.write_ascii_values(message, values, converter,
                                              separator, termination, encoding)
            self.log.debug(f"handle_{self.addr_hash}.write_ascii_values('{message}',{values},\
                                    '{converter}','{separator}', {termination}, {encoding})")
        except:
            self.log.exception("# %s << %s", str(self.handle), log_msg)
            raise

    def write_binary_values(self, message, values, datatype='f',
                                is_big_endian=False, termination=None,
                                encoding=None,):
        if self.handle is None:
            return None
        block, header = IEEE_488_2_BinBlock(values, datatype, is_big_endian)
        log_msg = message+header+'<DATABLOCK>'
        self.log.debug("# %s << %s", str(self.handle), log_msg)
        try:
            _ = self.handle.write_binary_values(message, values, datatype,
                                    is_big_endian, termination, encoding)
            self.log.debug(f"handle_{self.addr_hash}.write_binary_values('{message}',{values},\
                                    '{datatype}', {is_big_endian}, {termination}, {encoding})")
        except:
            self.log.exception("# %s << %s", str(self.handle), log_msg)
            raise


def IEEE_488_2_BinBlock(datalist, dtype="int16", is_big_endian=True):
    """将一组数据打包成 IEEE 488.2 标准二进制块

    datalist : 要打包的数字列表
    dtype    : 数据类型
    endian   : 字节序

    返回二进制块, 以及其 'header'
    """
    types = {"b"      : (  int, 'b'), "B"      : (  int, 'B'),
             "h"      : (  int, 'h'), "H"      : (  int, 'H'),
             "i"      : (  int, 'i'), "I"      : (  int, 'I'),
             "q"      : (  int, 'q'), "Q"      : (  int, 'Q'),
             "f"      : (float, 'f'), "d"      : (float, 'd'),
             "int8"   : (  int, 'b'), "uint8"  : (  int, 'B'),
             "int16"  : (  int, 'h'), "uint16" : (  int, 'H'),
             "int32"  : (  int, 'i'), "uint32" : (  int, 'I'),
             "int64"  : (  int, 'q'), "uint64" : (  int, 'Q'),
             "float"  : (float, 'f'), "double" : (float, 'd'),
             "float32": (float, 'f'), "float64": (float, 'd')}

    datalist = np.asarray(datalist)
    datalist.astype(types[dtype][0])
    if is_big_endian:
        endianc = '>'
    else:
        endianc = '<'
    datablock = struct.pack('%s%d%s' % (endianc, len(datalist), types[dtype][1]), *datalist)
    size = '%d' % len(datablock)
    header = '#%d%s' % (len(size),size)

    return header.encode()+datablock, header
