# 2023.06.15更新
# 2023.07.14 修改
import struct
import numpy as np
import time

############################################################################
##################             采集模块               #######################
############################################################################

# ******************************************************************** #
# 模块数量
# 函数功能：问询，返回本机箱中的模块数量

def oscNum(connHandle):
    Info = connHandle.query('OSC:NUM?')
    return Info

# ******************************************************************** #
# 选择模块
# 函数功能：OSC采集板卡选择。boardPara：-1——全选 |  0——0号板卡 | 1——1号板卡......
def oscboardSelect(connHandle, boardPara):
    returnPara = connHandle.query("OSC:SELMODU " + str(boardPara))
    return returnPara

# ******************************************************************** #
# 模块闪灯
# 函数功能：当前模块闪灯
#          enable:'ON'/'OFF'
def oscLed(connHandle, enable):
    returnPara = connHandle.query("OSC:LED " + enable)
    return returnPara

# ******************************************************************** #
# 触发存储
# 函数功能：全局触发存储，一个触发存储一段
#          enable:'ON'/'OFF'
def oscTrigStore(connHandle, enable):
    returnPara = connHandle.query("OSC:TRIGSTORE " + enable)
    return returnPara

# ******************************************************************** #
# 触发延时
# 函数功能：触发延时
#          time: 时间值，单位s     最大1048576个采样周期，2.4GSa/s时为8.7378ms或2.8GSa/s时为7.4899ms

def oscTrigDelay(connHandle, time):
    time = time + 1690e-9
    returnPara = connHandle.query('OSC:TRIGDELAY '+ str(time))
    return returnPara

# ******************************************************************** #
# 段数
# 函数功能：设置存储多少段
#          num: 采集数据或解模段数,   对于硬件解模段数最大为2047段，如果段数太大则后面的解模数据无法存储，回读到的全为0

def oscSeqNum(connHandle, num):
    returnPara = connHandle.query('OSC:SEQNUM '+ str(num))
    return returnPara

# ******************************************************************** #
# 每段长度
# 函数功能：设置每段波形的存储长度，对应为采样点数量（一个采样点包含一个通道1采样点与一个通道2采样点）
#          len: 每段长度

def oscSeqLen(connHandle, len):
    returnPara = connHandle.query('OSC:SEQLEN '+ str(len))
    return returnPara

# ******************************************************************** #
# 通道1增益
# 函数功能：设置通道1衰减
#          num：衰减量（单位dB）0~31.25

def oscGain1(connHandle, num):
    returnPara = connHandle.query('OSC:GAIN1 '+ str(num))
    return returnPara

# ******************************************************************** #
# 通道2增益
# 函数功能：设置通道2衰减
#          num：衰减量（单位dB）

def oscGain2(connHandle, num):
    returnPara = connHandle.query('OSC:GAIN2 '+ str(num))
    return returnPara

# ******************************************************************** #
# 开始写DDR
# 函数功能：开始写DDR
def oscStartWriteDDR(connHandle):
    returnPara = connHandle.query("OSC:STARTWRITEDDR")
    return returnPara

# ******************************************************************** #
# 询问存储是否完成？
# 函数功能：# 询问存储是否完成？，完成返回1，未完成返回0
def oscStoreEnd(connHandle):
    returnPara = connHandle.query("OSC:STOREEND?")
    return returnPara

# ******************************************************************** #
# 停止写DDR
# 函数功能：停止写DDR
def oscEndWriteDDR(connHandle):
    returnPara = connHandle.query("OSC:ENDWRITEDDR")
    return returnPara



# ******************************************************************** #
# 读取长度
# 函数功能：设置单次读取的采样点长度，最大250个采样点
#          len: 单次读取长度

def oscReadLen(connHandle, len):
    returnPara = connHandle.query('OSC:READLEN '+ str(len))
    return returnPara

# ******************************************************************** #
# 读取数据
# 函数功能：返回double型数组，前len个为通道1的数据，后len个为通道2的数据
#          len: 上一条命令设置的读取长度（单个通道的读取点数）
#          bytes_num: 读取的字节数（=len*8（每个采样点8字节）*2（双通道））
def oscRead(connHandle, len_sample):
    print(len)
    bytes_num = len_sample*8*2
    # connHandle.write("OSC:READ?")    # 修改20250823
    # data = connHandle.read(bytes_num)
    connHandle.write("OSC:READ?")
    data = connHandle.read_bytes(bytes_num)
    data_array = bytes2float(data)
    return data_array

# ============================================================ #
# =================== 硬解模 解模参数配置 ===================== #

# 选择 当前配置的解模频率的序号
# index 1~16        1-第1个解模频率  ...  16-第16个解模频率
def oscHardFreqIndex(connHandle, index):
    index_r = connHandle.query('OSC:HARD:INDEX '+ str(index))
    return index_r

# 当前 解模频率  Hz
def oscHardFreq(connHandle, freq):
    freq = freq/1e6
    a = str(freq)
    freq_r = connHandle.query('OSC:HARD:FREQ '+ str(freq))     # freq单位MHz
    return freq_r

# 当前解模频率 初相
# 0~360度
def oscHardFreqPhase(connHandle, phase):
    phase_r = connHandle.query('OSC:HARD:PHASE '+ str(phase))
    return phase_r

# 当前解模频率 解模窗-头长度    （触发后抛掉的采样点个数）
def oscHardWindHead (connHandle, num):
    num_r = connHandle.query('OSC:HARD:WINDHEAD '+ str(num))
    return num_r

# 当前解模频率 解模窗-有效长度
def oscHardWindLen (connHandle, len):
    len_r = connHandle.query('OSC:HARD:WINDLEN '+ str(len))
    return len_r

# 加载通道选择
# 函数功能：当前配置的解模频率 加载到哪个通道,0对应通道0，1对应通道1
def oscHardLoadChl (connHandle, ch):
    ch = ch + 1
    ch_r = connHandle.query('OSC:HARD:LOADCHL '+ str(ch))
    return ch_r


# 函数功能：将一个频率及其对应参数load至已选通道
def setHardParam_1freq_1load(handle, index, freq, phase, head_len, wind_len, chl):
    oscHardFreqIndex(handle, index)
    oscHardFreq(handle, freq)
    oscHardFreqPhase(handle, phase)
    oscHardWindHead(handle, head_len)
    oscHardWindLen(handle, wind_len)
    oscHardLoadChl(handle, chl)

# 函数功能，给定频率列表和单次触发采集长度，设置chl通道的解模参数
def setHardDemoduPara(handle,chl,freqList,wind_len):
    N = np.size(freqList)
    for k in range(N):
        setHardParam_1freq_1load(handle, k+1, freqList[k], 0, 0, wind_len, chl)


# =================== 硬解模 回读参数配置 ===================== #

# 回读频率选择 频率序号
# index 1~16        1-第1个解模频率  ...  16-第16个解模频率
def oscHardReadIndex(connHandle, index):
    index_r = connHandle.query('OSC:HARD:RDINDEX '+ str(index))
    return index_r

# 回读通道选择 0 通道0,1通道1
def oscHardReadChl (connHandle, ch):
    ch = ch+1
    ch_r = connHandle.query('OSC:HARD:RDCHL '+ str(ch))
    return ch_r

# 返回解模数据  len 段数(读取点数/2),输出为2*len长度,前len为I,后len为Q
def oscHardRead (connHandle, len):         
    bytes_num = len*8*2
    connHandle.write('OSC:HARD:READ?')        
    # import time
    # time.sleep(0.3)
    data = connHandle.read_bytes(bytes_num)
    d_tuple = bytes2float(data)
    return d_tuple

def oscHardRdout (connHandle, freqindex, chl, readlen):
    oscHardReadIndex(connHandle, freqindex)
    oscHardReadChl(connHandle, chl)
    d_tuple = oscHardRead(connHandle, readlen)
    return d_tuple

# 函数功能：给定频率数，段数，读取chl通道的解模数据
def hardDemodu_Out (connHandle, chl, freqNum, seqNum):
    demoduData = np.zeros([seqNum,freqNum], dtype=complex)
    # time.sleep(0.1)
    for k in range(freqNum):
        a = oscHardReadIndex(connHandle, int(k+1))
        oscHardReadChl(connHandle, chl)
        data_temp = oscHardRead(connHandle, seqNum)
        # data_temp = data_temp.reshape([-1,1])
        demoduData[:,k] = data_temp[0:seqNum] + 1j*data_temp[seqNum:2*seqNum]
    return demoduData


# ***********  以下指令函数仅适用于 6913A数字采集器  ******************* #
# ******************************************************************** #
# 函数功能：选择滤波器（带通）  ——  与外接滤波器匹配
#           freq：滤波器中心频率 MHz ——>可用通频带
#                 3500 ——> 3.00G~4.00G(采样率2.8GSa/s)
#                 4200 ——> 3.90G~4.50G(采样率2.4GSa/s)
#                 4900 ——> 4.40G~5.40G(采样率2.8GSa/s)
#                 5400 ——> 5.00G~5.80G(采样率2.4GSa/s)
#                 6200 ——> 5.80G~6.60G(采样率2.8GSa/s)
#                 6700 ——> 6.35G~7.05G(采样率2.4GSa/s)
#                 7400 ——> 7.15G~7.65G(采样率2.8GSa/s)
#
#  ！！！！！切换滤波器，改变其对应采样率时，需重启软件！！！！
def oscSelFilter(connHandle, freq):    
    returnPara = connHandle.query("OSC:SELCENTERFREQ"+ str(freq))
    return returnPara
# ******************************************************************** #
# 函数功能：设置DDC载波频率  ——  与外接滤波器匹配
#           freq: 载波频率  MHz  需在滤波器可用同频带内
def oscDDCFreq (connHandle, freq):
    returnPara = connHandle.query("OSC:DDCFREQ"+ str(freq))
    return returnPara
# ******************************************************************** #
# 函数功能：同步DDC载波
#          enable:'ON'/'OFF'
def oscDDCSync (connHandle,enable):
    returnPara = connHandle.query("OSC:DDCSYNC " + enable)
    return returnPara
# ******************************************************************** #
# 函数功能：设置每段采集时间  
#           time: ns        >50
def oscSeqTime(connHandle, time):
    returnPara = connHandle.query('OSC: SEQTIME '+ str(time))
    return returnPara
# ******************************************************************** #
# 函数功能：读取当前板卡 OSC采样率
def oscSamRate(connHandle):
    SamRate = connHandle.query('OSC:SAMRATE?')
    return SamRate

# ******************************************************************** #
# 函数功能：选择读取那一段数据
def oscSelReadSeq(connHandle,index):
    returnPara = connHandle.query('OSC:SELREADSEQ '+ str(index))
    return returnPara
# ******************************************************************** #
# 函数功能：输入解模系数 data_bytes

def oscInputDemoduData(connHandle, data_bytes):
    connHandle.query("OSC:DOWNLEN {}".format(len(data_bytes)+18))
    temp1 = b"OSC:INPUTDEMODU " + data_bytes + b'0A'
    connHandle.write_raw(temp1)
    returnPara = connHandle.read()
    return returnPara

# ******************************************************************** #
# 函数功能：读取解模结果

def oscReadDemodu(connHandle, len):

    bytes_num = len*8*2
    connHandle.write('OSC:READDEMODU?')        # 11月版本
    data = connHandle.read_bytes(bytes_num)
    d_tuple = bytes2float(data)    
    return d_tuple



# =================================   SCPI指令函数结束     ================================= #
# ========================================================================================= #

# 函数功能：数据类型转换：bytes to np.float64，
def bytes2float(byte_value):   
    # dtype: 数据类型，与读取数据bytes位宽相匹配 float64占8字节，如2字节可选 uint16
    sample_num = int(len(byte_value)/8)
    # double_tuple = struct.unpack('d'*int(count),byte_value) 
    float_array = np.frombuffer(byte_value, dtype = np.float64, count= sample_num, offset=0)    # Inverse of tobytes() operation
    return float_array   # 数据为ndarray,size=(N,)

# 函数功能：数据类型转换：将IQ复数矩阵转为bytes
def demoduData2Bytes(data_complex):
    # data:维度（频点个数，每段点数，data为复数）
    sig_I = np.real(data_complex).reshape(1,-1)
    sig_Q = np.imag(data_complex).reshape(1,-1)
    sig_IQ = np.concatenate((sig_I,sig_Q),axis=1)

    data_uint16 = np.uint16((sig_IQ+1)/2*65024+256)      # /2*65535
    data_bytes = data_uint16.tobytes(order='C')
    return data_bytes

# 函数功能：数据读取（每个通道返回的数据为1维数据）
def readOut(connHandle,depth,ReadLen_once):   # 两通道数据独立输出 depth每通道读取点数 需为20的倍数
    data_ch1 = np.zeros(depth)
    data_ch2 = np.zeros(depth)

    M = depth//ReadLen_once
    N = depth%ReadLen_once
    oscReadLen(connHandle,ReadLen_once)
    if M !=0:
        for k in range(M):
            data = oscRead(connHandle,ReadLen_once)
            data_ch1[ReadLen_once*k:(k+1)*ReadLen_once] = data[:ReadLen_once]
            data_ch2[ReadLen_once*k:(k+1)*ReadLen_once] = data[ReadLen_once:]
            
    if N !=0:
        oscReadLen(connHandle,N)     
        data = oscRead(connHandle,N)
        data_ch1[ReadLen_once*M:] = data[:N]
        data_ch2[ReadLen_once*M:] = data[N:]

    return data_ch1,data_ch2

########### 6913A数字采集器   采集数据读取  ###########
# 函数功能：数据读取（ 每个通道返回为2维数据，每行为一次触发采集的数据）
    # depth_onetrig     每段数据点数 20的倍数
    # trig_count        读取段数（触发次数）
def readOut_real(connHandle,depth_onetrig,trig_count):
    data_ch1,data_ch2 = readOut(connHandle,depth_onetrig*trig_count,6000000)
    data_ch1 = data_ch1.reshape(trig_count,depth_onetrig)
    data_ch2 = data_ch2.reshape(trig_count,depth_onetrig)
    return data_ch1,data_ch2


# 函数功能：加载解模系数
    # sig:维度（频点个数，每段点数，data为复数）
def InputDemodu(connHandle,sig):
    sig_bytes = demoduData2Bytes(sig)
    returnPara = oscInputDemoduData(connHandle,sig_bytes)
    return returnPara

# 函数功能：执行解模并读取解模数据
    # seq_num： 段数，
    # freq_num：频点数
def Demodu_Out(connHandle,seq_num,freq_num):
    depth_demodu = (freq_num*seq_num*2)
    data_demodu = oscReadDemodu(connHandle,depth_demodu)    # size = (depth_demodu,)
    # 两通道数据分开
    A = data_demodu[:depth_demodu]              
    B = data_demodu[depth_demodu:]
    # 每次触发数据分开
    demodu_A = A.reshape(-1,freq_num*2)
    demodu_B = B.reshape(-1,freq_num*2)
    # 转复数
    demodu_A = demodu_A[:,0:freq_num] + 1j*demodu_A[:,-freq_num:]
    demodu_B = demodu_B[:,0:freq_num] + 1j*demodu_B[:,-freq_num:]
    return demodu_A,demodu_B,A,B  # 维度：段数*频点数，元素为复数

# 该函数不使用
    # sig 解模数据
    # seq_len 单次采集长度（单通道采样点个数）
    # seq_num 采集次数
def readOut_Demodu(connHandle,sig,seq_len,seq_num):

    sig_bytes = demoduData2Bytes(sig)
    sig_size = np.size(sig)
    freq_num = int(sig_size/seq_len)

    depth_demodu = (freq_num*seq_num*2)
    data_demodu = oscDemodu(connHandle,sig_bytes,depth_demodu)

    A = np.array([*data_demodu[:depth_demodu]])
    B = np.array([*data_demodu[-depth_demodu:]])
    demodu_A = A.reshape(-1,freq_num*2)
    demodu_B = B.reshape(-1,freq_num*2)
    demodu_A_IQ = demodu_A[:,0:freq_num] + 1j*demodu_A[:,freq_num:] 
    demodu_B_IQ = demodu_B[:,0:freq_num] + 1j*demodu_B[:,freq_num:] 
    return demodu_A_IQ,demodu_B_IQ

# 读取IQ数据
def readOutIQ(connHandle,depth):
    d_tuple_ch1 = ()
    d_tuple_ch2 = ()
    d_tuple_ch1_I = ()
    d_tuple_ch1_Q = ()
    d_tuple_ch2_I = ()
    d_tuple_ch2_Q = ()
    ReadLen_once = 240              # 单次读取数据长度： 每通道 240 个采样点
    M = depth//ReadLen_once

    # data = oscRead(connHandle, ReadTime_once)
    for i in range(M+1):
        num10 = int(ReadLen_once/10)
        for j in range (num10):
            data = oscRead(connHandle, 10)
            data_ch1 = data[:5]
            data_ch2 = data[-5:]
        d_tuple_ch1 = d_tuple_ch1 + data_ch1
        d_tuple_ch2 = d_tuple_ch2 + data_ch2
    for index in range (depth/2):
        d_tuple_ch1_I[index] = d_tuple_ch1[2*index]
        d_tuple_ch1_Q[index] = d_tuple_ch1[2*index + 1]
        d_tuple_ch2_I[index] = d_tuple_ch2[2*index]
        d_tuple_ch2_Q[index] = d_tuple_ch2[2*index + 1]
    data_ch1_I = d_tuple_ch1_I[0:depth/2]
    data_ch1_Q = d_tuple_ch1_Q[0:depth/2]
    data_ch2_I = d_tuple_ch2_I[0:depth/2]
    data_ch2_Q = d_tuple_ch2_Q[0:depth/2]

    return data_ch1_I,data_ch1_Q,data_ch2_I,data_ch2_Q
