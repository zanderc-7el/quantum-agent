# 2023.04.19 更新
# 2023.07.14 更新
import numpy as np
import time
#import string

############################################################################
##################             AWG5000               #######################
############################################################################

#==========================================================================#
##################         通用参数配置              ########################
#==========================================================================#

# ******************************************************************** #
# AWG5000 模块数量 
# 函数功能：问询，返回本机箱中的模块数量

def Num(connHandle):
    Info = connHandle.query('AWG5000:NUM?')
    return Info

# ******************************************************************** #
# AWG5000 函数功能：AWG5000板卡选择。boardPara：-1——全选 |  0——0号板卡 | 1——1号板卡......
def boardSelect(connHandle, boardPara):
    returnPara = connHandle.query("AWG5000:SELMODU " + str(boardPara))
    return returnPara

# ******************************************************************** #
# 函数功能：所选板卡的通道选择。  channPara ：-1——2通道全选， 0——通道0 |  1——通道1 
def channSelect(connHandle, channPara):
    returnPara = connHandle.query("AWG5000:SELCHL " + str(channPara))
    return returnPara

# ******************************************************************** #
# 函数功能：选择滤波器（带通）—— 可用通频带
#          freq：滤波器中心频率 单位：MHz
#                3700 —— 3.0G~4.4G (采样率4.8GSa/s)
#                4700 —— 4.3G~5.0G (采样率  4GSa/s)
#                5700 —— 5.0G~6.4G (采样率4.8GSa/s)
#                6900 —— 6.3G~7.5G (采样率  6GSa/s)
def SelFilter(connHandle, sampleRate):
    if sampleRate == 4000:
        returnPara = connHandle.query("AWG5000:SELFILTERFREQ " + str(4700))
    elif sampleRate == 4800:
        returnPara = connHandle.query("AWG5000:SELFILTERFREQ " + str(3700))
    elif sampleRate == 6000:
        returnPara = connHandle.query("AWG5000:SELFILTERFREQ " + str(6900))
    return returnPara

# ******************************************************************** #
# 函数功能：同步载波初始相位
#          trigPara: 'ON'——打开 | 'OFF'——关闭
def globalTrigFunction(connHandle, trigPara):
    returnPara = connHandle.query("AWG5000:GLOBALTRIGFUCTN " + trigPara)
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：全局触发信号播放“任意波”使能（每个触发信号输出一个波形序列）
#         trigPara: 'ON'——打开 | 'OFF'——关闭
def globalTrigArb(connHandle, trigPara):
    returnPara = connHandle.query("AWG5000:GLOBALTRIGARB " + trigPara)
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：当前模块本地触发一次
def localTrig(connHandle):
    returnPara = connHandle.query("AWG5000:LOCALTRIG")
    return returnPara

# ******************************************************************** #
# 函数功能：当前通道选择函数波形或者任意波形
#           waveMode: 'FUCTN' —— 单载波模式    | 'ARB' —— 任意波/存储回放模式
def waveSelect(connHandle, waveMode):
    returnPara = connHandle.query("AWG5000:WAVESEL " + waveMode)
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：配置当前通道输出衰减    
#           ampliPara: 衰减幅度     0~31.75dBm
#           unit     : 单位'dBm'
def setAmplitude(connHandle, ampliPara,unit):
    returnPara = connHandle.query("AWG5000:AMPL " + str(ampliPara) + unit)
    return returnPara

#==========================================================================#
##################       函数模式参数配置            ########################
#==========================================================================#

# ******************************************************************** #
# 函数功能：配置当前通道函数波形频率
#           freqPara: 频率
#           unit    : 单位'GHz'/'MHz'/'kHz'/'Hz'
def FuncWaveFrq(connHandle, freqPara,unit):
    returnPara = connHandle.query("AWG5000:FUCTN:FREQ " + str(freqPara) + unit)
    return returnPara

#==========================================================================#
##################      任意波模式参数配置           ########################
#==========================================================================#

# ******************************************************************** #
# AWG5000 函数功能：配置当前通道的任意波形输出使能
#          enable:ON/OFF
def ArbRunEn(connHandle, enable):
    returnPara = connHandle.query("AWG5000:ARB:RUNCE " + enable)
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：下载开始（此条指令会自动停止“全局触发播放任意波使能”）
#          采样率通过软件设置
def ArbDownStart(connHandle):
    returnPara = connHandle.query("AWG5000:ARB:DOWNSTART")
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：当前通道下载一段波形（非脉冲）。按DWORD=32bit（两个采样点）为一组传输，
# 单次传输波形不能长于1000个DWORD (即2000个采样点)，超出部分需要分多次发送。
# 数据归一化说明：波形取值范围为-1.0~+1.0，归一化时每个采样点的波形数据为偏移二进制：
# 0x0100为最小电压，对应-1.0；0x8000为0电压值；0xFF00为最大电压，对应+1.0。

def ArbDownSeq(connHandle, data_bytes,len):
    connHandle.query("AWG5000:DOWNLEN {}".format(len*2+21))
    temp1 = b"AWG5000:ARB:DOWNSEQ " + data_bytes  +b'\n'         # "AWG5000:ARB:DOWNSEQ " + data +/n
                      # 不添加sleep会报指令错误
    connHandle.write_raw(temp1)
    returnPara = connHandle.read()
    return returnPara

# ******************************************************************** #
# AWG5000 函数功能：当前通道一段波形下载完毕。播放次数取值0或1~65530，0为无限循环；
#          count: 播放次数
def ArbDownSeqEnd(connHandle, count):
    returnPara = connHandle.query("AWG5000:ARB:DOWNSEQEND " + str(count))
    return returnPara


# ******************************************************************** #
# AWG5000 函数功能：当前模块所有波形段下载完毕
def ArbDownAllEnd(connHandle):
    returnPara = connHandle.query("AWG5000:ARB:DOWNEND")
    return returnPara

# # ******************************************************************** #
# # AWG5000 函数功能：当前通道MARK输出延时，0~420ns
# #          delayTime: 延迟时间
# #          unit    : 单位's'/'ms'/'us'/'ns'
# def ArbMarkDelay(connHandle,delayTime,unit):
#     returnPara = connHandle.query("AWG5000:ARB:MARKDELAY " + str(delayTime) + unit)
#     return returnPara

# ******************************************************************** #
# AWG5000 函数功能：当前通道波形输出延时，303ns~3us
#          delayTime:
#          unit    : 单位's'/'ms'/'us'/'ns'

def waveDelay(connHandle,delayTime,unit):
    returnPara = connHandle.query("AWG5000:ARB:WAVEDELAY " + str(delayTime) + unit)
    return returnPara

# # ******************************************************************** #
# # AWG5000 函数功能：SmallDelay精确延时
# #          enable:ON/OFF
# def ArbRunEn(connHandle, enable):
#     returnPara = connHandle.query("AWG5000:ARB:SMALLDELAY " + enable)
#     return returnPara

#==========================================================================#
##################         通道输出配置              ########################
#==========================================================================#

# ******************************************************************** #
# AWG5000 函数功能：当前模块通道输出使能使能
#          enable:'ON'/'OFF'
def ChannelOutput(connHandle, enable):
    returnPara = connHandle.query("AWG5000:CHLOUTPUT " + enable)
    return returnPara

# ===================   SCPI指令函数结束     =================== #
# ============================================================= #


############################################################################
##################            任意波形输出           ########################
############################################################################

# ******************************************************************** #
    # 函数功能：将double或float类型数据转换为一定位数的16进制数据
    #          功能同float2hex(),但计算速度更快
    #           data: array类型或list  
def float2bytes(data):
    # 将float数据量化并转bytes（）
    # data list或ndarray
    if isinstance(data,list):   # 如果为list格式，则转为array进行处理
        data = np.array(data) 
    else: # ndarray
        data = data.reshape(-1,1)     # 转换为1维数组
        # IQ复数数据转换 I[1] Q[1] I[2] Q[2] I[3] Q[3] .......
        if str(data.dtype)[0:7] == 'complex':
            data = np.concatenate((data.real,data.imag),axis=1) 
            data = data.reshape(1,-1)
    len_data = np.size(data)
    if len_data % 2 != 0:       # 是否为偶数个元素，如果不是补1个0
        data = np.append(data,0)
        len_data = len_data +1
    # -------- 元素两个一组，每组前后对调 -----------------    

    data_uint16 = np.uint16((data+1)/2*65535)
    data_bytes = data_uint16.tobytes(order='C')   # order{‘C’, ‘F’, ‘A’}, optional Controls the memory layout of the bytes object. ‘C’ means C-order, ‘F’ means F-order, ‘A’ (short for Any) means ‘F’ if a is Fortran contiguous, ‘C’ otherwise. Default is ‘C’.
    return (data_bytes,len_data)

# ******************************************************************** #
# 函数功能：配置任意波参数并准备下载波形,当前版本只保留了正弦
#          boardPara    ：选择板卡，-1——全选 |  0——0号板卡 | 1——1号板卡
#          channPara    ：选择通道，-1——4通道全选， 0——通道0 |  1——通道1 |  2——通道2 |  3——通道3
#          ampliPara    ：单位 V
#          sampleRate   ：采样率选择 '1.2GHz' | '600MHz' | '300MHz'
def ArbStartSet(connHandle,boardPara, channPara, sampleRate, ampliPara):
    
    boardSelect(connHandle, boardPara)              # 选择板卡
    channSelect(connHandle, channPara)              # 选择通道
    waveSelect(connHandle, 'ARB')                   # 设置为任意波输出模式
    setAmplitude(connHandle, ampliPara,'V')         # 设置幅度
    globalTrigArb(connHandle,'OFF')                 # 关闭全局触发
    ArbDownStart(connHandle, sampleRate)            # 配置采样率并开始下载

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
                                     # 保证所有数据加载结束，然后停止加载
    # ------------------ 剩余数据段下载 ------------------
    if segMod != 0:
        temp = dataHex[hex_num*(segNum) : hex_num*(segNum) + segMod*2]
        returnPara = ArbDownSeq(connHandle, temp,segMod)          # 装载该段数据
        # time.sleep(0.1)                          # 保证所有数据加载结束，然后停止加载
    returnPara = ArbDownSeqEnd(connHandle, count)             # 该段数据装载完成
    return returnPara

def allSequenceEnd(connHandle):
    return ArbDownAllEnd(connHandle)