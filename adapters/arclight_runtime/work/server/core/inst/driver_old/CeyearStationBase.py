import pyvisa as visa  # 新版本导入方式更改
from .BaseDriver import BaseDriver
from .quant import QInteger, QOption, QReal, QString, QVector, QList, QBool
import itertools
import sys
import time
import numpy as np
import logging
log = logging.getLogger(__name__)

try:
    from . import AWG1652AM as AWG_LF
    from . import Ceyear_AWG5000 as AWG_RF
    from . import Ceyear_OSC as OSC
except:
    print('Ceyear Import Error!')

ch_def = (0, 0)


class Driver(BaseDriver):

    __log__ = log
    support_models = []

    # (board,chann) 板卡编号和通道编号组成的元组
    CHs = list(itertools.product((0, 1, 2, 3, 4, 5, 6, 7), (0, 1, 2, 3)))
    LF_boards = []
    RF_boards = []
    LO_boards = []
    OSC_boards = []

    # reset_offset 使用的 (板位列表, 通道列表)，各机型按自身板卡布局覆写
    RESET_OFFSET_LF_BOARDS = ((0, 1, 2, 3, 4, 5), (0, 1, 2, 3))
    RESET_OFFSET_RF_BOARDS = ((1, 2, 3, 4, 5), (0, 1))

    quants = [
        # AWG
        QReal('Offset', unit='V', ch=ch_def,),
        QReal('Amplitude', unit='VPP', ch=ch_def,),
        # QOption('Output', ch=1),
        # QReal('triggerDelay', value=0, ch=ch_def),
        QVector('Waveform', value=[], ch=ch_def),
        QReal('OffsetDC', unit='V', ch=ch_def,),

        # # LO
        QReal('Frequency',
              ch=ch_def,
              unit='Hz',
              set_cmd='LO:SELMODU %(ch)d;LO:FREQ %(value).13e',
              get_cmd='LO:SELMODU %(ch)d;LO:FREQ?'),
        QReal('Power', value=0, ch=ch_def, unit='dBm'),
        # QReal('Power',
        #         ch=ch_def,
        #         unit='dBm',
        #         set_cmd=':POWER %(value).8e%(unit)s',
        #         get_cmd=':POWER?'),
        QOption('Output',
                ch=ch_def,
                set_cmd='LO:SELMODU %(ch)d;LO:OUTPUT %(option)s',
                options=[('OFF', 'OFF'), ('ON', 'ON')]),

        # ADC
        QInteger('pointNum', value=2048, ch=ch_def),  # 1024的整数倍
        QReal('triggerDelay', value=0, ch=ch_def),
        QReal('shots', value=1000, ch=ch_def),  # 重复次数
        QList('coefficient', value=None, ch=ch_def),
        QBool('avg', value=False, ch=ch_def),
        # mode=1为硬件解模模式，mode=0为软件解模模式
        QBool('capture_mode', value=0, ch=ch_def),
        QReal('StartCapture', value=None, ch=ch_def),

        QVector('IQ', value=[], ch=ch_def),
        QVector('TraceIQ', value=[], ch=ch_def),
        QReal('sampleRate', value=2.4e9, ch=ch_def, unit='Hz',),
        QList('f_list', value=[50e6], ch=ch_def),  # 解调频率列表
        QList('demodPointStartList', value=[0], ch=ch_def),            #
        QList('demodPointLenList', value=[500], ch=ch_def),            #
        QList('demodPhaseList', value=[0], ch=ch_def),
    ]

    def __init__(self, addr=None, **kw):
        super().__init__(addr=addr, **kw)
        self.model = ''
        self.timeout=10
        self.freqNum = 1
    def performOpen(self):
        '''
        地址格式为：
            port: 7110
            addr: 'TCPIP::{IP_addr}::{port}::SOCKET' 
        '''
        self.addr_hash = hash(self.addr)
        rm = visa.ResourceManager()
        self.handle = rm.open_resource(self.addr)
        self.handle.timeout = self.timeout * 1000
        self.handle.write_termination = '\n'
        self.handle.read_termination = '\n'
        IDN = self.handle.query("*IDN?").split(',')
        # company = IDN[0].strip()
        model = IDN[1].strip()
        # version = IDN[3].strip()
        self.model = model

    def performClose(self):
        self.handle.close()

    def performSetValue(self, quant, value, **kw):
        ch = kw.pop('ch', ch_def)
        self.config[quant.name][ch]['value'] = value
        assert isinstance(ch, tuple), f'ch:{ch} is not tuple!'
        _board, chann = ch


        if _board in self.LF_boards or _board in self.RF_boards:
            if _board in self.LF_boards:
                AWG = AWG_LF
                board = _board-self.LF_boards[0]
            elif _board in self.RF_boards:
                AWG = AWG_RF
                board = _board-self.RF_boards[0]
            AWG.boardSelect(self.handle, board)              # 选择板卡
            AWG.channSelect(self.handle, chann)

            if quant.name in ['OffsetDC']:
                pass
            elif quant.name in ['Offset']:
                if _board in self.LF_boards:
                    AWG.setOffset(self.handle, value, 'V')
                else:
                    pass
            elif quant.name in ['Amplitude']:
                AWG.setAmplitude(self.handle, value, 'V')
            elif quant.name in ['Output']:
                pass
            elif quant.name in ['sampleRate']:
                if  _board in self.RF_boards:
                    samrate = int(value/1e6)  #samrate unit:MHz
                    AWG.SelFilter(self.handle, samrate)
                else:
                    pass
            # 触发延时参数写入时注意通道号 区分输出和采集通道
            elif quant.name == 'triggerDelay':  # [0-2.56e-4]s
                AWG.waveDelay(self.handle, value, 's')
            elif quant.name in ['Waveform']:
                self.writeWaveform(value, ch=ch)
            else:
                super().performSetValue(quant, value, ch=ch, **kw)
        elif _board in self.LO_boards:
            board = _board-self.LO_boards[0]
            API = AWG_LF
            API.boardSelect(self.handle, board)              # 选择板卡
            super().performSetValue(quant, value, ch=ch, **kw)
        elif _board in self.OSC_boards:
            board = _board-self.OSC_boards[0]
            OSC.oscboardSelect(self.handle, board)              # 选择板卡
            if quant.name == 'pointNum':
                assert value%20 == 0, f'value:{value}'
                OSC.oscSeqLen(self.handle, value)
            elif quant.name == 'triggerDelay':
                OSC.oscTrigDelay(self.handle, value)
            elif quant.name == 'shots':
                # AWG_LF.trigCycleTimes(self.handle, value)
                OSC.oscSeqNum(self.handle, value)
            elif quant.name in ['sampleRate']:
                samrate = int(value/1e6)  #samrate unit:MHz
                OSC.oscSelSamRate(self.handle, samrate)
            elif quant.name == 'capture_mode':
                pass
            elif quant.name == 'StartCapture':
                self.start_capture(ch=ch)
            elif quant.name == 'coefficient':
                freq, phase, start_point, point_len, _, _ = value
                self.freqNum = len(freq)
                for i in range(len(freq)):
                    OSC.oscHardFreqIndex(self.handle, i+1)
                    print(f"解模频率:{freq[i]}, 相位:{phase[i]}, 起点:{start_point[i]}, 长度:{point_len[i]}")
                    OSC.oscHardFreq(self.handle, freq[i])
                    OSC.oscHardFreqPhase(self.handle, phase[i])
                    OSC.oscHardWindHead(self.handle, start_point[i])
                    OSC.oscHardWindLen(self.handle, point_len[i])
                    OSC.oscHardLoadChl(self.handle, chann)
                # OSC.InputDemodu(self.handle, value)
            # elif quant.name == 'f_list':
            #     self.freqNum = len(value)
            #     for i, freq in enumerate(value):
            #         OSC.oscHardFreqIndex(self.handle, i+1)
            #         OSC.oscHardFreq(self.handle, freq)
            #         # OSC.oscHardLoadChl(self.handle, chann)
            # elif quant.name == 'demodPhaseList':
            #     for i, phase in enumerate(value):
            #         OSC.oscHardFreqIndex(self.handle, i+1)
            #         OSC.oscHardFreqPhase(self.handle, phase)
            #         # OSC.oscHardLoadChl(self.handle, chann)
            # elif quant.name == 'demodPointStartList':
            #     for i, start in enumerate(value):
            #         OSC.oscHardFreqIndex(self.handle, i+1)
            #         OSC.oscHardWindHead(self.handle, start)
            #         # OSC.oscHardLoadChl(self.handle, chann)
            # elif quant.name == 'demodPointLenList':
            #     for i, length in enumerate(value):
            #         OSC.oscHardFreqIndex(self.handle, i+1)
            #         OSC.oscHardWindLen(self.handle, length)
            #         # OSC.oscHardLoadChl(self.handle, chann)
            else:
                super().performSetValue(quant, value, ch=ch, **kw)

    def performGetValue(self, quant, **kw):
        ch = kw.pop('ch', ch_def)
        _board, chann = ch
        #print(_board, chann, self.OSC_boards, quant.name)
        if _board in self.OSC_boards:
            board = _board-self.OSC_boards[0]
            OSC.oscboardSelect(self.handle, board)              # 选择板卡
            if quant.name in ['TraceIQ']:
                return self.getData(ch, fft=False)
            elif quant.name in ['IQ']:
                return self.getData(ch, fft=True)
            elif quant.name in ['sampleRate']:
                SamRate = OSC.oscSamRate(self.handle)
                return float(SamRate)*1e9
            else:
                return super().performGetValue(quant, ch=ch, **kw)
        else:
            return super().performGetValue(quant, ch=ch, **kw)

    def writeWaveform(self, data, ch=ch_def, count=1, preDistort='ALL'):
        _board, chann = ch
        print(f"write wave to {_board} {ch}")
        assert _board in self.LF_boards or _board in self.RF_boards, f'_board:{_board}'

        if _board in self.LF_boards:
            AWG = AWG_LF
            mode = 'LF'
            board = _board-self.LF_boards[0]
        elif _board in self.RF_boards:
            AWG = AWG_RF
            mode = 'RF'
            board = _board-self.RF_boards[0]
        else:
            raise
        AWG.boardSelect(self.handle, board)              # 选择板卡
        AWG.channSelect(self.handle, chann)              # 选择通道

        # AWG1652AM.ArbStartSet 部分步骤
        AWG.waveSelect(self.handle, 'ARB')                   # 设置为任意波输出模式
        # 关闭任意波播放使能，准备下载波形
        AWG.globalTrigArb(self.handle, 'OFF')

        if mode == 'LF':
            sampleRate = '1.2GHz'
            AWG.ArbDownStart(self.handle, sampleRate)            # 配置采样率并开始下载
            AWG.ArbDownSeqStart(self.handle, preDistort)
        elif mode == 'RF':
            AWG.ArbDownStart(self.handle)
        else:
            raise
        
        AWG.downSeqence(self.handle, data, count)
        # AWG.allSequenceEnd(self.handle)
        AWG.ArbDownAllEnd(self.handle)   # new

        AWG.ArbRunEn(self.handle, 'ON')
        AWG.ChannelOutput(self.handle, 'ON')
        AWG.globalTrigArb(self.handle, 'ON')

    def writeFuncWave(self, ch=(0, 1), wavefrq=100, wavephase=0):
        _board, chann = ch
        assert _board in self.LF_boards or _board in self.RF_boards, f'_board:{_board}'
        if _board in self.LF_boards:
            AWG = AWG_LF
            board = _board-self.LF_boards[0]
        elif _board in self.RF_boards:
            AWG = AWG_RF
            board = _board-self.RF_boards[0]
        AWG.boardSelect(self.handle, board)                  # 板卡选择
        AWG.channSelect(self.handle, chann)                  # 通道选择

        AWG.waveSelect(self.handle, 'FUCTN')                     # 选择 函数波形 模式
        # amp=0.2, offset=0,
        # AWG.setAmplitude(self.handle, amp, 'V')                   # 设置波形幅度
        # AWG.setOffset(self.handle, offset, 'V')                   # 设置波形偏置
        AWG.FuncWaveFrq(self.handle, wavefrq, 'MHz')              # 设置波形频率
        AWG.FuncWavePhase(self.handle, wavephase)                # 设置初相
        AWG.FuncWaveOutputEn(self.handle, 'ON')                  # 函数输出开关
        AWG.ChannelOutput(self.handle, 'ON')                     # 通道输出开关

    def start_capture(self, ch):
        _board, chann = ch
        assert _board in self.OSC_boards, f'_board:{_board}'
        board = _board-self.OSC_boards[0]
        # OSC.oscHardLoadChl(self.handle, chann)
        OSC.oscboardSelect(self.handle, board)
        OSC.oscTrigStore(self.handle, 'ON')
        OSC.oscStartWriteDDR(self.handle)

    # 读取数据，2数据通道独立输出
    def readData(self, ch):
        _board, chann = ch
        assert _board in self.OSC_boards, f'_board:{_board}'
        board = _board-self.OSC_boards[0]
        shots = self.config['shots'][ch]['value']
        pointNum = self.config['pointNum'][ch]['value']

        OSC.oscboardSelect(self.handle, board)
        # OSC.oscTrigStore(self.handle, 'ON')
        # OSC.oscStartWriteDDR(self.handle)
        
        state = 0
        t1=t0=time.time()
        while state == 0 or t1-t0>20:
            t1=time.time()
            state = OSC.oscStoreEnd(self.handle)  
            state = int(state)
            time.sleep(0.001)

        OSC.oscEndWriteDDR(self.handle)
        num = pointNum*shots
        # data1, data2 = OSC.readOut_real(self.handle, num) #old version drive
        data1, data2 = OSC.readOut_real(self.handle, pointNum,shots)
        # data1 = np.array(data1)
        # data2 = np.array(data2)
        # data1 = data1[:num].reshape(shots, pointNum)
        # data2 = data2[:num].reshape(shots, pointNum)
        return data1, data2
    
    def readFFTData(self, ch):
        print("---进入readFFTData---")
        _board, chann = ch
        assert _board in self.OSC_boards, f'_board:{_board}'
        board = _board-self.OSC_boards[0]
        OSC.oscboardSelect(self.handle, board)

        state = 0
        t1=t0=time.time()
        while state == 0 or t1-t0>20:
            t1=time.time()
            state = OSC.oscStoreEnd(self.handle)  
            state = int(state)
            # time.sleep(0.001) 
        OSC.oscEndWriteDDR(self.handle)

        # coeff = self.config['coefficient'][ch]['value']
        coeff = self.config['f_list'][ch]['value']
        shots = self.config['shots'][ch]['value']
        seq_num = shots
        freq_num = len(coeff)
        chA, chB = OSC.Demodu_Out(self.handle,seq_num,freq_num)

        if chann == 1:
            chA = np.array(chA) * 0
            return chB, chA.tolist()
        chB = np.array(chB) * 0
        print(f"chA: {chA}")
        print(f"chB: {chB}")
        return chA, chB.tolist() 
    
    def readHardFFTData(self, ch):
        _board, chann = ch
        assert _board in self.OSC_boards, f'_board:{_board}'
        board = _board-self.OSC_boards[0]
        OSC.oscboardSelect(self.handle, board)
        
        state = 0
        t1=t0=time.time()
        while state == 0 or t1-t0>20:
            t1=time.time()
            state = OSC.oscStoreEnd(self.handle)  
            state = int(state)
            # time.sleep(0.001) 
        OSC.oscEndWriteDDR(self.handle)

        # f_list = self.config['f_list'][ch]['value']
        shots = self.config['shots'][ch]['value']
        seq_num = shots
        freq_num = self.freqNum
        print(f"hardDemodu_Out {chann} {freq_num} {seq_num}")
        data = OSC.hardDemodu_Out(self.handle, chann, freq_num, seq_num)
        return data, np.array(data) * 0

    def getData(self, ch, fft=False, avg=False, timeout=None):
        # 形状为（shots, pointNum）
        if fft:
            ### 
            # coeff = self.config['coefficient'][ch]['value']
            # pointNum = self.config['pointNum'][ch]['value']
            # chA = (chA[:, :pointNum]).dot(coeff.T)/pointNum
            # chB = (chB[:, :pointNum]).dot(coeff.T)/pointNum
            ####
            # chA, chB = self.readFFTData(ch)
            chA, chB = self.readHardFFTData(ch)
        else:
            chA, chB = self.readData(ch)
        return chA, chB
    
    def set_trigger(self, shots, period):
        AWG_LF.setTrigType(self.handle, 'CYCLE')
        AWG_LF.trigCycleInterv(self.handle, period, 's')
        AWG_LF.trigCycleTimes(self.handle, shots) # 0为无限次

    def single_trigger(self):
        AWG_LF.localTrig()
    
    def reset_offset(self):
        CHs_Z = list(itertools.product(*self.RESET_OFFSET_LF_BOARDS))
        CHs_XY = list(itertools.product(*self.RESET_OFFSET_RF_BOARDS))
        for ch in CHs_Z:
            self.setValue('Offset',0,ch=ch)
            AWG_LF.boardSelect(self.handle, ch[0])              # 选择板卡
            AWG_LF.channSelect(self.handle, ch[1])
            AWG_LF.ChannelOutput(self.handle, 'OFF')
        for ch in CHs_XY:
            AWG_RF.boardSelect(self.handle, ch[0])              # 选择板卡
            AWG_RF.channSelect(self.handle, ch[1])
            AWG_RF.ChannelOutput(self.handle, 'OFF')
    
    def channelOpen(self, ch):
        _board, chann = ch
        assert _board in self.LF_boards or _board in self.RF_boards, f'_board:{_board}'
        if _board in self.LF_boards:
            AWG = AWG_LF
            board = _board-self.LF_boards[0]
        elif _board in self.RF_boards:
            AWG = AWG_RF
            board = _board-self.RF_boards[0]
        print(board, chann)
        AWG.boardSelect(self.handle, board)                  # 板卡选择
        AWG.channSelect(self.handle, chann)                  # 通道选择
        AWG.ChannelOutput(self.handle, 'ON')