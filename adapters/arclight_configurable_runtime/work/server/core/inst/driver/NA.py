from .Quant import *
import pyvisa
import time
import numpy as np
import struct
import logging

class CYVNADriver:
    """
    网分驱动
    """
    
    support_models = ['E8363B', 'E8363C', 'E5071C', 'E5080A', 'E5080B', 'E5063A', 'ZNB8-4Port',
                      'ZNB20-4Port', 'ZNB20-2Port', 'N5232A', 'N5222B', 'ZVL-13', 'ZNA26-2Port', 'M9802A', '3672B','3656D']

    quants = [
        QReal('Power',
              value=-20,
              ch=1,
              unit='dBm',
              set_cmd='SOUR:POW %(value)e%(unit)s',
              get_cmd='SOUR:POW?'),
        # QOption('PowerMode',
        #         value='OFF',
        #         ch=1,
        #         set_cmd='SOUR%(ch)s:POW:MODE %(option)s',
        #         get_cmd='SOUR%(ch)s:POW:MODE?',
        #         options=[('OFF', 'OFF'), ('ON', 'ON')]),
        QReal('Bandwidth',
              value=1000,
              unit='Hz',
              ch=1,
              set_cmd='SENS%(ch)d:BAND %(value)e%(unit)s',
              get_cmd='SENS%(ch)d:BAND?'),
        QReal('Frequency center',
              value=5e9,
              ch=1,
              unit='Hz',
              set_cmd='SENS%(ch)d:FREQ:CENT %(value)e%(unit)s',
              get_cmd='SENS%(ch)d:FREQ:CENT?'),
        QReal('Frequency span',
              value=2e9,
              ch=1,
              unit='Hz',
              set_cmd='SENS%(ch)d:FREQ:SPAN %(value)e%(unit)s',
              get_cmd='SENS%(ch)d:FREQ:SPAN?'),
        QReal('Frequency start',
              value=4e9,
              ch=1,
              unit='Hz',
              set_cmd='SENS%(ch)d:FREQ:STAR %(value)e%(unit)s',
              get_cmd='SENS%(ch)d:FREQ:STAR?'),
        QReal('Frequency stop',
              value=6e9,
              ch=1,
              unit='Hz',
              set_cmd='SENS%(ch)d:FREQ:STOP %(value)e%(unit)s',
              get_cmd='SENS%(ch)d:FREQ:STOP?'),
        QVector('Frequencies', unit='Hz', ch=1),
        QVector('Trace', ch=1),
        QVector('S', ch=1),
        QOption('Sweep',
                value='ON',
                ch=1,
                set_cmd='INIT:CONT %(option)s',
                options=[('OFF', 'OFF'), ('ON', 'ON')]),
        QInteger('Number of points',
                 value=201,
                 ch=1,
                 unit='',
                 set_cmd='SENS%(ch)d:SWE:POIN %(value)d',
                 get_cmd='SENS%(ch)d:SWE:POIN?'),
        QOption('Format',
                value='Mlog',
                ch=1,
                set_cmd='CALC:FORM %(option)s',
                get_cmd='CALC:FORM?',
                options=[('Mlinear', 'MLIN'), ('Mlog', 'MLOG'),
                         ('Phase', 'PHAS'), ('Unwrapped_phase', 'UPH'),
                         ('Imag', 'IMAG'), ('Real', 'REAL'), ('Polar', 'POL'),
                         ('Smith', 'SMIT'), ('SWR', 'SWR'),('SmithComp', 'SCOM'),
                         ('Group_Delay', 'GDEL'),('SLIN', 'SLIN')]),
        QOption('SweepType',
                value='',
                ch=1,
                set_cmd='SENS%(ch)d:SWE:TYPE %(option)s',
                get_cmd='SENS%(ch)d:SWE:TYPE?',
                options=[('Linear', 'LIN'), ('Log', 'LOG'), ('Power', 'POW'),
                         ('CW', 'CW'), ('Segment', 'SEGM'), ('Phase', 'PHAS')]),
        QOption('AverageState',
                value='OFF',
                ch=1,
                set_cmd='SENS%(ch)d:AVER %(option)s',
                get_cmd='SENS%(ch)d:AVER?',
                options=[('OFF', 'OFF'), ('ON', 'ON')]),
        QInteger('AverageCount',
                 value=1,
                 ch=1,
                 unit='',
                 set_cmd='SENS%(ch)d:AVER:COUN %(value)d',
                 get_cmd='SENS%(ch)d:AVER:COUN?'),
        QOption('S_type',
                value='S21',
                ch=1,
                set_cmd='CALC%(ch)d:PAR1:DEF %(option)s',
                get_cmd='CALC:PAR:DEF?',
                options=[('S11','S11'), ('S12','S12'), ('S21','S21'), ('S22','S22')]),
    ]
    
    def __init__(self, addr: str, model: str, **kwargs):
        self.addr = addr
        self.model = model
        self.inst = None
        self.debug = kwargs.get("debug", False)
        self.log = kwargs.get("log", logging.getLogger("device.na.driver"))
        self.timeout = kwargs.get('timeout', 5)
        
    def open(self, **kwargs):
        # 建立连接
        if self.debug:
            return
        
        ch = kwargs.get('ch', 1)
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.addr)
        self.inst.timeout = self.timeout * 1000
        self.inst.read_termination = '\n'
        self.inst.write_termination = '\n'
        self.pna_select(ch=ch)

    def close(self, **kwargs):
        if self.debug:
            return
        
        self.inst.close()
        self.rm.close()
        
    
    def pna_select(self, ch=1, meas=0):
        '''Select the measurement'''
        if self.model in ['E5071C', 'ZNB20-4Port', 'E5063A', 'ZNB8-4Port']:
            return
        mname, params = self.get_measurements(ch=ch)
        measname = mname[meas]
        self.set('CALC%d:PAR:SEL "%s"' % (ch, measname))
    
    def get_measurements(self, ch=1):
        if self.model in ['E5071C', 'ZNB20-4Port', 'E5063A', 'ZNB8-4Port']:
            return
        if self.model in ['E8363C', 'E8363B', 'E5080A', 'E5080B', 'N5232A', 'N5222B', 'M9802A', '3672B','3656D']:
            quote = '" '
        elif self.model in ['ZNB20-2Port', 'ZVL-13', 'ZNA26-2Port']:
            quote = "' "
        else:
            raise NameError(f'{self.model} is not support!')
        msg = self.set('CALC%d:PAR:CAT?' % ch).strip(quote)
        meas = msg.split(',')
        return meas[::2], meas[1::2]
    
    def _find_quant(self, name):
        for q in self.quants:
            if q.name == name:
                return q
        raise KeyError(name)

    def write(self, name: str, value, **kwargs):
        """
        参数设置
        """
        if self.model in ['E5063A']:
            kwargs.update(unit='')
            
        quant = self._find_quant(name)
        cmd = quant.format_set_cmd(value, **kwargs)
        self.set(cmd)
        quant.value = value

    def read(self, name: str, **kwargs):
        """
        获取参数值
        """
        get_vector_methods = {
            'Frequencies': self.get_Frequency,
            'Trace': self.get_Trace,
            'S': self.get_S,
        }
        quant = self._find_quant(name)
        if name in get_vector_methods.keys():
            return get_vector_methods[quant.name](ch=kwargs.get('ch', 1))
        else:
            if self.model in ['E5063A', 'ZNA26-2Port']:
                kwargs.update(unit='')
            
            cmd = quant.format_get_cmd(**kwargs)
            return self.set(cmd) 

    def set(self, cmd: str, value = None, **kwargs):
        """
        原始 SCPI 透传，以?结尾的用query，其余用write
        """
        if self.debug:
            self.log.info(cmd)
            return b"ok"
            
        if cmd.endswith('?'):
            return self.inst.query(cmd)
        else:
            return self.inst.write(cmd)
    
    def socket_query(self, cmd, value=None, **kwargs):
        return self.set(cmd, value, **kwargs)
    
    def get_Trace(self, ch=1):
        '''Get trace'''
        return self.get_S(ch, formated=True)

    def get_S(self, ch=1, formated=False):
        '''Get the complex value of S paramenter or formated data'''
        
        self.write('Sweep', 'OFF', ch=ch)
        # Begin a measurement
        self.set('INIT:IMM')
        if self.model in ['E5063A']:
            Bandwidth = self.read('Bandwidth')
            points = self.read('Number of points')
            time.sleep(2*(points/Bandwidth+0.1))
        self.set('*WAI')
        # Get the data
        self.set('FORMAT:BORD NORM')
        if self.model in ['E5071C', 'E5063A', '3656D', 'E8363B']:
            self.set(':FORM:DATA ASC')
            cmd = ("CALC%d:DATA:FDATA?" %
                   ch) if formated else ("CALC%d:DATA:SDATA?" % ch)
            data = np.asarray(self.query_ascii_values(cmd))
        else:
            self.set(':FORM:DATA REAL,32')
            cmd = ("CALC%d:DATA? FDATA" %
                   ch) if formated else ("CALC%d:DATA? SDATA" % ch)
            data = np.asarray(self.query_binary_values(
                cmd, is_big_endian=True))
        self.set('FORMAT ASCII')
        if formated:
            if self.model in ['E5071C', 'E5063A']:
                data = data[::2]
        else:
            data = data[::2] + 1j * data[1::2]
        # Start the sweep
        self.write('Sweep', 'ON', ch=ch)
        return data

    def get_Frequency(self, ch=1):
        """Return the frequency of pna measurement"""

        # Select the measurement
        # self.pna_select(ch)
        if self.model in ['E8363C', 'E5080A', 'E5080B', ]:
            cmd = 'CALC%d:X?' % ch
            self.set(':FORM:DATA REAL,32')
            data = self.query_binary_values(cmd, is_big_endian=True)
            # self.write('FORMAT ASCII')
            return np.asarray(data)
        if self.model in ['E8363B', 'N5232A', 'N5222B', 'E5063A', 'M9802A', '3672B','3656D']:
            freq_star = self.read('Frequency start', ch=1)
            freq_stop = self.read('Frequency stop', ch=1)
            num_of_point = self.read('Number of points', ch=1)
            return np.array(np.linspace(freq_star, freq_stop, num_of_point))
        elif self.model in ['ZNB20-4Port', 'ZNB20-2Port', 'ZNA26-2Port', 'ZVL-13', 'ZNB8-4Port']:
            cmd = 'CALC%d:DATA:STIM?' % ch
            return np.asarray(self.query_ascii_values(cmd))
        
    def query_ascii_values(self, cmd, separator=','):
        """
        发送 SCPI 查询，解析 ASCII 数值列表
        """
        self.log.info(cmd)
        if self.debug:
            quant = self._find_quant("Number of points")
            return np.random.random(quant.value * 2)
        
        raw = self.set(cmd)
        raw = raw.strip()
        if not raw:
            return []
        parts = raw.split(separator)
        values = [float(p) for p in parts]
        return values
    
    def query_binary_values(
        self,
        cmd,
        datatype='f',         
        is_big_endian=False,
    ):
        """
        发送 SCPI 查询，解析 binary block 数据
        """
        self.log.info(cmd)
        if self.debug:
            quant = self._find_quant("Number of points")
            return np.random.random(quant.value * 2)
        
        self.inst.write(cmd)

        # 读取第一个字节，必须是 '#'
        first = self.inst.read_bytes(1)
        if first != b'#':
            raise ValueError("Not a binary block")

        # 读取长度字段位数 N
        n_digits = int(self.inst.read_bytes(1))

        # 读取 payload 长度
        length_str = self.inst.read_bytes(n_digits)
        payload_len = int(length_str)

        # 读取 payload
        payload = self.inst.read_bytes(payload_len)

        # （可选）读掉结尾的 \n
        self.inst.read_bytes(1)

        # 7. 按字节序解析
        endian = '>' if is_big_endian else '<'
        fmt = endian + datatype * (payload_len // struct.calcsize(datatype))

        data = struct.unpack(fmt, payload)

        return np.asarray(data)