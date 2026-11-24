import numpy as np
import socket
import time

class CYDA10Driver:
    support_models = ['CY_10DA']


    def __init__(self, addr, **kw):
        self.addr = addr
        self.model = 'CY_10DA'
        self.srate = 10e9
        self.timeout = kw.get('timeout', 10)
        print(self.timeout)
        
    def open(self):
        
        #建立连接
        self.sock_10 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock_10.settimeout(self.timeout)
        self.sock_10.connect((self.addr, 4196))
        # self.sock_10.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        #复位
        self.sock_query('*RST')

        #设置奈奎斯特区间为2区
        self.set(':AWG:NYZONE',2)

        self.CAWEn = 0 # 0: 任意波, 1: 载波
        self.CycleSeq = 1 # 1: 单次播放, 0: 循环播放


    def close(self, **kw):
        self.sock_10.close()

    def write(self, name: str, value, **kw):
        ch = kw.get('ch', 9)

        if name in ['TriggerDelayAWG',]:
            self.rd_Delay(ch, value)
        elif name in ['rdAtt',]:
            self.rd_Att(ch, value)  
        elif name in ['Waveform',]:
            self.download_AWG_waveData(value,ch)
        elif name in ['Nyzone']:
            self.set('::AWG:NYZONE',value)
        # elif name in ['CAWWave',]:
        #     self.Generate_Caw_Wave(value,ch)
        elif name in ['FeedBack']:
            self.set(':AWG:FEEDB 1') # 1 开启 0 关闭
        elif name in ['CAWEn']:
            """ 1: 载波 
                0: 任意波
            """
            assert value in [0, 1], f'CAWEn can only be 0 or 1!'
            self.CAWEn = value
        elif name in ['CycleSeq']:
            """ 1: 单次播放
                0: 循环播放
            """
            assert value in [0, 1], f'CycleSeq can only be 0 or 1!'
            self.CycleSeq = value
        else:
            pass


    # *#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*# user defined #*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#

    def set(self, cmd_name, params = None):
            """
            发送带参数的程控指令
            :param sock: 已连接的socket对象
            :param cmd_name: 指令名称（如"SET_FREQ"）
            :param params: 可变参数（如频率值、通道号等）
            """

            # 1. 拼接指令字符串（例如："SET_FREQ,100000 1;" 表示设置通道1的频率为100000Hz）
            if params != None:
                # param_str = ",".join(map(str, params))  # 将参数转换为字符串并拼接
                param_str = str(params)
                cmd_str = f"{cmd_name} {param_str}\n"    # 组合成完整指令（带结束符）
            else:
                cmd_str = f"{cmd_name}\n"    # 组合成完整指令（带结束符）
            # 2. 转换为字节流（socket传输必须是bytes类型）
            cmd_bytes = cmd_str.encode('utf-8')
            # 3. 发送指令
            self.sock_10.sendall(cmd_bytes)
            # print(f"已发送指令：{cmd_str}")

    def sock_read(self,buffer_size=1024):
        response = b''
        while True:
            chunk = self.sock_10.recv(buffer_size)
            if not chunk:
                break  # 连接关闭
            response += chunk
            if b'\n' in chunk:
                break  # 达到结束标志
        return response.strip()
        
    def sock_query(self, cmd_name,params = None):
        self.set(cmd_name, params)
        return self.sock_read()

    
    ##########################################################################################################
    def ChlSel(self,chl,name = ':AWG:CHLSEL'):
        chl = chl -1
        self.set(name,chl)


    def rd_Delay(self, chl, delay_ns):
        self.ChlSel(chl)
        self.set(':READ:DELAY',delay_ns)
        return
    def rd_Att(self, chl, att):
        self.ChlSel( chl)
        self.set(':READ:ATT',att)
        return
    def Generate_Caw_Wave(self,value,ch):
        self.ChlSel(ch)
        self.set(':CAW:FREQ',value)
        self.set(':CAW:PHAS',90)
        self.set(':CAW:ONLY',1)

    #############确定波形数据长度位宽#########################
    def digit_count(self,num):
        len = 0
        if num == 0:
            return 1
        else:
            while(num != 0):
                num = num // 10
                len = len + 1
            return len      
    #########################下发任意波数据#################################################
    # ------------------------------------------------------------------------
    # 功能说明: 【载波】 *用于任意波形数据下发
    #       输入参数:波形数据
    #
    # 返回值: 无
    # ------------------------------------------------------------------------
    def AWG_Data_Download(self,waveData):
        # self.ChlSel(ch)
        #下发波形数据
        len_once = 134000000
        m_Len_bytes = len(waveData)%len_once
        m_Len_count = len(waveData)//len_once
        if m_Len_count != 0:
            for k in range(m_Len_count):
                # self.set(':AWG:SEQLEN',len_once)
                # start = k * len_once  # 计算当前块的起始偏移量（整数）
                # end = start + len_once  # 计算当前块的结束偏移量
                # current_block = waveData[start:end]  # 通过切片获取当前块数据（bytes 类型）
                # self.sock.sendall(current_block)
                # self.sock_10.recv(1024).decode('utf-8').strip()
                dataSend = b':AWG:WRITe #9134000000'+waveData[134000000*k:134000000*(k+1)]
                self.sock_10.sendall(dataSend)
                self.sock_read()
        if m_Len_bytes != 0:
            # self.set(':AWG:SEQLEN',m_Len_bytes)
            # start = len_once*m_Len_count  # 计算当前块的起始偏移量（整数）
            # end = start + m_Len_bytes  # 计算当前块的结束偏移量
            # current_block = waveData[start:end]  # 通过切片获取当前块数据（bytes 类型）
            # self.sock.sendall(current_block)
            # self.sock_10.recv(1024).decode('utf-8').strip()
            digit_len = self.digit_count(m_Len_bytes)
            dataSend = b':AWG:WRITe #'+str(digit_len).encode('utf-8')+\
                       str(m_Len_bytes).encode('utf-8')+\
                       waveData[m_Len_count*134000000:m_Len_count*134000000+m_Len_bytes]
            self.sock_10.sendall(dataSend)
            self.sock_read()
        return
    # ------------------------------------------------------------------------
    # 功能说明: 量化float数据 并转bytes 【用于转换任意波形数据】
    # 输入参数:
    #       data:【数据类型: ndarray或list】 
    #            【取值范围: 0~1】
    #            【参数意义: 待处理的float数据数组或列表】
    # 输出参数:
    #       data_bytes:【数据类型: bytes】
    #                  【数据说明: 每16位为一组, 范围: 0~0xFFFF, 低14位有效】
    #                  【参数意义: 转换后的生成的bytes数据】
    # 返回值: data_bytes
    # ------------------------------------------------------------------------
    def float2bytes(self,data):
        # 如果为list格式，则转为array进行处理
        if isinstance(data,list):
            data = np.array(data).reshape(1,-1)
        else:
            # 转换为1维数组
            data = data.reshape(1,-1)

        len_data = np.size(data)
        # 是否为偶数个元素，如果不是，补1个0
        if len_data % 2 != 0:
            np.append(data,0)
            len_data = len_data +1
        # -------- 元素两个一组，每组前后对调 -----------------    
        data_uint16 = np.uint16(data*(pow(2,15)-1))
        #####################
        # data_uint16 = np.bitwise_or(data_uint16,1)
        ##################
        data_bytes = data_uint16.tobytes()
        return data_bytes


    def download_AWG_waveData(self,data,ch=9):
        # data_bytes = self.float2bytes(np.array(data).reshape(-1,1))
        # print("下发波形数据长度为:",len(data_bytes),"字节")
        #波形模块使能复位
        self.ChlSel(ch)
        self.set(':AWG:BDOWnlclear')
        #波形下发与控制
        self.AWG_Data_Download(data)
        #设置任意波模式
        self.set(':CAW:ONLY', self.CAWEn)
        #设置为单次博放
        self.set(':AWG:SEQNUM', self.CycleSeq)
        #通道输出打开
        self.ChlSel(ch)
        self.set(':AWG:SWITCH','ON')
        # #外触发
        # self.set(':TRIG:TYPE',0)
        # #触发模式：波形触发模式
        # self.set(':TRIG:CTRL',2)
    # ------------------------------------------------------------------------
    # 功能说明: 余弦数据生成
    # 输入参数:
    #       f0_cos:【数据类型: float】
    #              【参数意义: 频率, 单位:Hz】
    #       cycle: 【数据类型: int】
    #              【取值: 需根据数据长度计算】
    #              【参数意义: 周期数】
    #       fs_cos: 【数据类型: float】
    #               【参数意义: 采样率, 单位:Hz】
    # *参数示例: fs_cos = 1.2e9, f0_cos = 50e6, cycle = 10
    # 输出参数:
    #       y_cos.tolist():【数据类型: list】
    #                      【取值范围: 0~1】
    #                      【参数意义: 余弦幅度值】
    #       t_cos: 【数据类型: ndarray】
    #              【参数意义: 时间轴坐标】
    # 返回值: y_cos.tolist(), t_cos
    # ------------------------------------------------------------------------
    def GenWave_Cos(self,f0_cos, cycle, fs_cos):
        T_cos = abs(cycle/f0_cos)
        N = round(T_cos*fs_cos)
        t_cos = np.linspace(0,T_cos-1/fs_cos,N)
        y_cos = np.cos(2*np.pi*f0_cos*t_cos)
        
        # reData_array = np.array(y_cos.tolist()).reshape(-1,1)
        # data_bytes = self.float2bytes(0.1*reData_array)
        # print("下发波形数据长度为:",len(data_bytes),"字节")
        return y_cos.tolist()