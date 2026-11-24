
# V1 2023.07.14
# from distutils.log import info
import pyvisa as visa
import numpy as np
import time
#import string


############################################################################
##################             远控连接              ########################
############################################################################
# ******************************************************************** #



    
# 函数功能：输入IP，和 端口，建立通信连接
# def connect(IP_addr,port):
#     instrument = import_socket.InstrumentController(IP_addr, port, timeout=60)
#     instrument.connect()
#     return instrument



# # 函数功能：输入IP，和 端口，建立通信连接
def connect(IP_addr,port):
    visa_dll='C:/Windows/System32/visa32.dll'
    tcp_addr = 'TCPIP::'+ IP_addr + '::' + str(port) + '::SOCKET'
    rm=visa.ResourceManager(visa_dll)
    tcp_inst=rm.open_resource(tcp_addr)
    tcp_inst.write_termination = '\n'
    tcp_inst.read_termination = '\n'
    tcp_inst.timeout = 1000000       # timeout时间设置为10s
    return tcp_inst

# ******************************************************************** #
# 关闭连接
def disconect(connHandle):
    connHandle.close()
    return

############################################################################
##################             系统信息              ########################
############################################################################

# ******************************************************************** #
# 函数功能：复位（暂时无处理）

def DeviceRST(connHandle):
    Info = connHandle.query('*RST')
    return Info

# ******************************************************************** #
# 函数功能：问询设备信息返回“MultiAWG-7110B,1.0.0

def acquireDeviceInfo(connHandle):
    Info = connHandle.query('*IDN?')
    return Info

############################################################################
##################           定时同步功能设置        ########################
############################################################################

# 函数功能：配置触发类型
#           trigType：'CLOSE' | 'CYCLE'  | 'SINGLE' | 'SNGUSER'
#                        关闭 |  循环间隔 |    单次  | 单次或用户触发
def setTrigType(connHandle,trigType):
    Info = connHandle.query('GLOBALTRIG:TRIGSEL ' + trigType)
    return Info

# ******************************************************************** #
# 函数功能：配置触发间隔时间（触发类型为'CYCLE'，即循环间隔触发时有效）
#           interval: 触发间隔，最小为100ns
#           unit    : 单位's'/'ms'/'us'/'ns'
def trigCycleInterv(connHandle, interval,unit):
    returnPara = connHandle.query('GLOBALTRIG:SPACETIME '+ str(interval) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置循环次数（触发类型为'CYCLE'，即循环间隔触发时有效）
#          times: 0为无限循环，1~65530为有有限循环

def trigCycleTimes(connHandle, times):
    returnPara = connHandle.query('GLOBALTRIG:LOOPTIMES '+ str(times))
    return returnPara


# ******************************************************************** #
# 函数功能：输出一个触发信号(全机箱单次触发)

def sigleTrigOutPut(connHandle):
    returnPara = connHandle.query('GLOBALTRIG:SINGLETRIG')
    return returnPara

# ******************************************************************** #
# 函数功能：控制每个机箱进入“多机箱同步模式”，一个系统中只能有一个主机箱，其余均为从机箱。
#          注：该模式需主机箱的同步触发信号连接到所有从机箱。
#          strPara: MASTER（主机）|SLAVE（从机）

def multiCateSyncStart(connHandle,strPara):
    returnPara = connHandle.query('MULTICRATE:SYNCSTART ' + strPara)
    return returnPara

# ******************************************************************** #
# 函数功能：只对主机箱操作，执行同步

def mutiCateSyncRun(connHandle):
    returnPara = connHandle.query('MULTICRATE:SYNCRUN')
    return returnPara

# ******************************************************************** #
# 函数功能：控制每个机箱退出“多机箱同步模式”，完成同步

def mutiCateSyncEnd(connHandle):
    returnPara = connHandle.query('MULTICRATE:SYNCEND')
    return returnPara
############################################################################
##################             AWG7110B              #######################
############################################################################

# ******************************************************************** #
# 函数功能：AWG板卡选择。boardPara：-1——全选 |  0——0号板卡 | 1——1号板卡......
def boardSelect(connHandle, boardPara):
    returnPara = connHandle.query("AWG:SELMODU " + str(boardPara))
    return returnPara

# ******************************************************************** #
# 函数功能：所选板卡的通道选择。  channPara ：-1——4通道全选， 0——通道0 |  1——通道1 |  2——通道2 |  3——通道3
def channSelect(connHandle, channPara):
    returnPara = connHandle.query("AWG:SELCHL " + str(channPara))
    return returnPara

# ******************************************************************** #
# 函数功能：触发/Mark多功能接口选择（7选1）.
#         strPara: 'CLOSE '——关闭 | 'TRIGIN'——触发输入 | 'GLOBALTRIGOUT'——全局触发监视输出 | 
#                  'MARK1OUT'——通道1Mark输出 | 'MARK2OUT'——通道2Mark输出 | 'MARK3OUT'——通道3Mark输出 | 'MARK4OUT'——通道4Mark输出
#                   每个通道的Mark为数据的最低位，每4个采样点取一个，即第0、4、8、12......个点的最低位为Mark输出

def trigger_Mark(connHandle, strPara):
    returnPara = connHandle.query("AWG:MULTIIO " + strPara)
    return returnPara


# ******************************************************************** #
# 函数功能：“时基输出”选择（3选1）
#         refPara: 'CLOSE'——关闭 | '10MHz'——10MHz时基输出| '100MHz'——100MHz时基输出

def referenceOutput(connHandle, refPara):
    returnPara = connHandle.query("AWG:REFOUT " + refPara)
    return returnPara


# ******************************************************************** #
# 函数功能：全局触发信号同步“函数波形”使能（每个触发信号执行一次函数波形相位对齐）
#          trigPara: 'ON'——打开 | 'OFF'——关闭
def globalTrigFunction(connHandle, trigPara):
    returnPara = connHandle.query("AWG:GLOBALTRIGFUCTN " + trigPara)
    return returnPara


# ******************************************************************** #
# 函数功能：全局触发信号播放“任意波”使能（每个触发信号输出一个波形序列）
#         trigPara: 'ON'——打开 | 'OFF'——关闭
def globalTrigArb(connHandle, trigPara):
    returnPara = connHandle.query("AWG:GLOBALTRIGARB " + trigPara)
    return returnPara


# ******************************************************************** #
# 函数功能：当前模块本地触发一次
def localTrig(connHandle):
    returnPara = connHandle.query("AWG:LOCALTRIG")
    return returnPara

#==========================================================================#
##################         通用参数配置              ########################
#==========================================================================#
# ******************************************************************** #
# 函数功能：当前通道选择函数波形或者任意波形
#           waveMode: 'FUCTN' —— 函数波形    | 'ARB' —— 任意波形
def waveSelect(connHandle, waveMode):
#    dict_para = {'函数波形':'FUCTN','任意波形':'ARB'}
    returnPara = connHandle.query("AWG:WAVESEL " + waveMode)
    return returnPara


# ******************************************************************** #
# 函数功能：配置当前通道输出幅度（四个通道共用）
#           ampliPara: 幅度
#           unit     : 单位'V'/'mV'/'uV'
def setAmplitude(connHandle, ampliPara,unit):
    returnPara = connHandle.query("AWG:AMPL " + str(ampliPara) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置当前通道直流偏置
#           offsetPara: 直流偏置
#           unit     : 单位'V'/'mV'/'uV'
def setOffset(connHandle, offsetPara,unit):
    returnPara = connHandle.query("AWG:DCOFFSET " + str(offsetPara) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置当前通道输出耦合方式（四个通道共用），当前只支持DC耦合
#           strPara: 'DC' —— 直流耦合| 'AC' —— 交流耦合 | 'DAC' —— DAC直接输出
def setCouple(connHandle, strPara):
    returnPara = connHandle.query("AWG:COUPLE " + strPara)
    return returnPara

#==========================================================================#
##################         函数波形配置              ########################
#==========================================================================#
# ******************************************************************** #
# 函数功能：当前通道函数波形选择
#           waveType : '关闭'、'正弦'、'方波'、'三角'、'锯齿'、'DC'、'噪声'、'脉冲'
def FuncWaveSelect(connHandle, waveType):
    dict_para = {'关闭':'CLOSE','正弦':'SIN','方波':'SQU','三角':'TRG','锯齿':'TTH','DC':'DC','噪声':'NOS','脉冲':'PUL'}
    returnPara = connHandle.query("AWG:FUCTN:WAVESEL " + dict_para[waveType])
    # print(dict_para[waveType])
    return returnPara


# ******************************************************************** #
# 函数功能：配置当前通道函数波形频率
#           freqPara: 频率
#           unit    : 单位'GHz'/'MHz'/'kHz'/'Hz'
def FuncWaveFrq(connHandle, freqPara,unit):
    returnPara = connHandle.query("AWG:FUCTN:FREQ " + str(freqPara) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置当前通道函数波形初始相位
#           phasePara: 初始相位
def FuncWavePhase(connHandle, phasePara):
    returnPara = connHandle.query("AWG:FUCTN:PHASE " + str(phasePara) + 'deg')
    return returnPara


# ******************************************************************** #
# 函数功能：配置当前通道波形脉宽
#           pulseWidth: 脉宽
#           unit    : 单位's'/'ms'/'us'/'ns'
def FuncWavePulseWidth(connHandle, pulseWidth,unit):
    returnPara = connHandle.query("AWG:FUCTN:PULWID " + str(pulseWidth) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置当前通道函数波形脉冲边沿时间
#           edgeTime: 脉冲边沿时间
#           unit    : 单位's'/'ms'/'us'/'ns'
def FuncWavePulseEdge(connHandle, edgeTime,unit):
    returnPara = connHandle.query("AWG:FUCTN:PULEDGE " + str(edgeTime) + unit)
    return returnPara

# ******************************************************************** #
# 函数功能：配置当前通道的函数波形输出使能
#          enable:'ON'/'OFF'
def FuncWaveOutputEn(connHandle, enable):
    returnPara = connHandle.query("AWG:FUCTN:OUTPUT " + enable)
    return returnPara



#==========================================================================#
##################         任意波形配置              ########################
#==========================================================================#
# ******************************************************************** #
# 函数功能：配置当前通道的任意波形输出使能
#          enable:ON/OFF
def ArbRunEn(connHandle, enable):
    returnPara = connHandle.query("AWG:ARB:RUNCE " + enable)
    return returnPara

# ******************************************************************** #
# 函数功能：下载开始，同时配置波形采样率（此条指令会自动停止“全局触发播放任意波使能”）
#          sampleRate: '1.2GHz' | '600MHz' | '300MHz'
def ArbDownStart(connHandle, sampleRate):
    returnPara = connHandle.query("AWG:ARB:DOWNSTART " + sampleRate)
    return returnPara

# ******************************************************************** #
# 函数功能：下载一段波形开始，配置波形的去过冲类型：
#           HEAD：仅波形起始
#           LAST：仅波形尾部
#           HEADLAST：波形起始与尾部（cos波形推荐）
#           ALL：所有波形数据（脉冲波形推荐）
#           NULL：无，保留原波形数据（高斯波形推荐）
#          strPara: HEAD | LAST |HEADLAST| ALL|NULL
def ArbDownSeqStart(connHandle, strPara):
    returnPara = connHandle.query("AWG:ARB:DOWNSEQSTART " + strPara)
    return returnPara

# ******************************************************************** #
# 函数功能：当前通道下载一段波形（非脉冲）。按DWORD=32bit（两个采样点）为一组传输，
# 单次传输波形不能长于1000个DWORD (即2000个采样点)，超出部分需要分多次发送。
# 数据归一化说明：波形取值范围为-1.0~+1.0，归一化时每个采样点的波形数据为偏移二进制：
# 0x0100为最小电压，对应-1.0；0x8000为0电压值；0xFF00为最大电压，对应+1.0。
def ArbDownSeq(connHandle, data_bytes,lena):
    # temp = '4157473A4152423A444F574E53455120'+ data + '0A'     # "AWG:ARB:DOWNSEQ " + data +/n
    connHandle.query("AWG:DOWNLEN {}".format(lena*2+17))
    sendData = b"AWG:ARB:DOWNSEQ "+ data_bytes + b"\n"
    connHandle.write_raw(sendData)
    returnPara = connHandle.read()
    return returnPara

# ******************************************************************** #
# 函数功能：当前通道一段波形下载完毕。播放次数取值0或1~65530，0为无限循环；
#          count: 播放次数
def ArbDownSeqEnd(connHandle, count):
    returnPara = connHandle.query("AWG:ARB:DOWNSEQEND " + str(count))
    return returnPara


# ******************************************************************** #
# 函数功能：当前模块所有波形段下载完毕
def ArbDownAllEnd(connHandle):
    returnPara = connHandle.query("AWG:ARB:DOWNEND")
    return returnPara

# ******************************************************************** #
# 函数功能：当前通道MARK输出延时，0~420ns
#          delayTime: 延迟时间
#          unit    : 单位's'/'ms'/'us'/'ns'
def ArbMarkDelay(connHandle,delayTime,unit):
    delayTime = delayTime + 1200e-9
    returnPara = connHandle.query("AWG:ARB:MARKDELAY " + str(delayTime) + unit)
    return returnPara



# ******************************************************************** #
# 函数功能：当前通道波形输出延时，303ns~3us
#          delayTime:
#          unit    : 单位's'/'ms'/'us'/'ns'

def waveDelay(connHandle,delayTime,unit):
    returnPara = connHandle.query("AWG:ARB:WAVEDELAY " + str(delayTime) + unit)
    return returnPara
#==========================================================================#
##################         通道输出配置              ########################
#==========================================================================#
# ******************************************************************** #
# 函数功能：当前模块通道输出使能使能
#          enable:'ON'/'OFF'
def ChannelOutput(connHandle, enable):
    returnPara = connHandle.query("AWG:CHLOUTPUT " + enable)
    return returnPara


# ===================   SCPI指令函数结束     =================== #
# ============================================================= #


############################################################################
##################            函数波形输出           ########################
############################################################################

# ******************************************************************** #
# 函数功能：输出函数波形，并配置波形参数
#          boardPara    ：选择板卡，-1——全选 |  0——0号板卡 | 1——1号板卡
#          channPara    ：选择通道，-1——4通道全选， 0——通道0 |  1——通道1 |  2——通道2 |  3——通道3
#          waveType     ：'关闭'、'正弦'、'方波'、'三角'、'锯齿'、'DC'、'噪声'、'脉冲'
#          ampliPara    ：单位 V
#          offsetPara   : 单位 V
#          freqPara     : 单位 MHz
#          phasePara    : 单位 度
#          pulseWidth   : 单位 ns
#          edgeTime     : 单位 ns
def funcWaveOutputSet(connHandle, boardPara, channPara,
                    waveType, ampliPara, offsetPara,
                    freqPara, phasePara,pulseWidth,edgeTime):
    boardSelect(connHandle, boardPara)              # 选择板卡
    channSelect(connHandle, channPara)              # 选择通道
    waveSelect(connHandle, 'FUCTN')                 # 设置为函数输出模式
    FuncWaveSelect(connHandle, waveType)            # 选择波形类型
    setAmplitude(connHandle, ampliPara,'V')         # 设置幅度
    setOffset(connHandle, offsetPara,'V')           # 设置偏置
    
    FuncWaveFrq(connHandle, freqPara,'MHz')         # 设置频率
    FuncWavePhase(connHandle, phasePara)            # 设置相位
    FuncWavePulseWidth(connHandle, pulseWidth,'ns') # 设置脉宽
    FuncWavePulseEdge(connHandle, edgeTime,'ns')    # 设置边沿时间
    FuncWaveOutputEn(connHandle, 'ON')
    

############################################################################
##################            任意波形输出           ########################
############################################################################

# ******************************************************************** #


# ******************************************************************** #
    # 函数功能：将double或float类型数据转换为一定位数的16进制数据
    #           data: ndarray或list
def float2bytes(data):
    # 将float数据量化并转bytes（）
    # data list或ndarray
    if isinstance(data,list):   # 如果为list格式，则转为array进行处理
        data = np.array(data).reshape(1,-1)
    else:
        data = data.reshape(1,-1)     # 转换为1维数组
    len_data = np.size(data)
    if len_data % 2 != 0:       # 是否为偶数个元素，如果不是补1个0
        data = np.append(data,0)
        len_data = len_data +1
    # -------- 元素两个一组，每组前后对调-----------------    

    data_uint16 = np.uint16((data+1)/2*65024+256)
    # 增加marker XXXX
    data_bytes = data_uint16.tobytes(order='C')   # order{‘C’, ‘F’, ‘A’}, optional Controls the memory layout of the bytes object. ‘C’ means C-order, ‘F’ means F-order, ‘A’ (short for Any) means ‘F’ if a is Fortran contiguous, ‘C’ otherwise. Default is ‘C’.
    return (data_bytes,len_data)

# ******************************************************************** #
# 函数功能：装载波形数据
#       data            :   数据序列 -1<= data <=1,array或者list类型
#       count             ：   播放次数。
def downSeqence(connHandle,data,count):
    dataHex,dataLen = float2bytes(data)
    segLen = 48000000
    segNum = dataLen // segLen          # 采样点数据分的段数
    segMod = dataLen % segLen           # 分段后剩余的采样点数
    hex_num = segLen*2                  # 每个数据点16位，2个bytes
    #  --------------整数段波形下载----------------------
    if segNum !=0:
        for k in range(segNum):
            temp = dataHex[hex_num*k : hex_num*k + hex_num]
            returnPara = ArbDownSeq(connHandle,temp,segLen)       # 装载该段数据
            # time.sleep(0.001)                          # 保证所有数据加载结束，然后停止加载
    # ------------------ 剩余数据段下载 ------------------
    if segMod != 0:
        temp = dataHex[hex_num*(segNum) : hex_num*(segNum) + segMod*2]
        returnPara = ArbDownSeq(connHandle, temp,segMod)          # 装载该段数据
        # time.sleep(0.001)                          # 保证所有数据加载结束，然后停止加载
    returnPara = ArbDownSeqEnd(connHandle, count)             # 该段数据装载完成
    return returnPara

# ******************************************************************** #
# 函数功能：配置任意波参数并准备下载波形,当前版本只保留了正弦
#          boardPara    ：选择板卡，-1——全选 |  0——0号板卡 | 1——1号板卡
#          channPara    ：选择通道，-1——4通道全选， 0——通道0 |  1——通道1 |  2——通道2 |  3——通道3
#          ampliPara    ：单位 V
#          offsetPara   : 单位 V
#          sampleRate   ：采样率选择 '1.2GHz' | '600MHz' | '300MHz'
def ArbStartSet(connHandle,boardPara, channPara, sampleRate, ampliPara, offsetPara):
    

    boardSelect(connHandle, boardPara)              # 选择板卡
    channSelect(connHandle, channPara)              # 选择通道
    waveSelect(connHandle, 'ARB')                   # 设置为任意波输出模式
    setAmplitude(connHandle, ampliPara,'V')         # 设置幅度
    setOffset(connHandle, offsetPara,'V')           # 设置偏置
    globalTrigArb(connHandle,'OFF')    # 关闭全局触发
    ArbDownStart(connHandle, sampleRate)            # 配置采样率并开始下载


# ******************************************************************** #
# 函数功能：外触发模式加载脉冲
def pulseGen(connHandle,boardPara,channPara,sampleRate,ampliPara,pulseSig,N):
    setTrigType(connHandle,'CLOSE')    # 关闭触发
    globalTrigArb(connHandle,'OFF')    # 关闭全局触发
    offsetPara =0
    ArbStartSet(connHandle,boardPara, channPara, ampliPara, offsetPara)
    for k in range(N):
        ArbDownSeqStart(connHandle, 'ALL')
        downSeqence(connHandle,pulseSig,1) 
    allSequenceEnd(connHandle)
    ArbRunEn(connHandle,'ON')
    ChannelOutput(connHandle, 'ON')

    globalTrigArb(connHandle,'ON')


# ******************************************************************** #
#当前模块所有波形段下载完毕,同ArbDownAllEnd(connHandle)
def allSequenceEnd(connHandle):
    ArbDownAllEnd(connHandle)
    return