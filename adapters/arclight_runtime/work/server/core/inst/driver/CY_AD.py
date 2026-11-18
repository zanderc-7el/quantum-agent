import numpy as np
import socket

class CYADDriver:
    support_models = ['CY_AD']

    indexNum = 1
    #段数
    seqNum = 1023
    #段长
    seqLen = 5000

    def __init__(self, addr, **kw):
        self.addr = addr
        self.model = 'CY_AD'
        self.srate = 5e9

    def open(self):
        #建立连接
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.addr, 4196))
        # self.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        self.sock.settimeout(20)
        #复位
        self.sock_query('*RST')
        #通道衰减
        #self.rd_Att(0, 0)
    
    def ADInit(self):
        self.set(':ADCinit')

    def close(self, **kw):
        self.sock.close()

    def write(self, name: str, value, **kw):
        ch = kw.get('ch', 1)
        
        if name in ['triggerDelay',]:
            self.rd_Delay( ch, value)
        elif name in ['rdAtt',]:
            self.rd_Att(ch, value)  
        elif name in ['TrigType',]:
            self.set('TRIG:TYPE',value)
            if value ==1:
                self.set(':TRIG:ONCE')
    ############################################################
        elif name in ['StartCapture',]:
            print(f"start capture {ch}")
            self.adc_startCature(ch)
        elif name in ['shots']:
            self.rd_Shot_data(value,ch)
        elif name in ['pointNum']:
            self.rd_pointNum_data(value,ch)
        elif name in ['coefficient']:
            demoFreqList = value[0]
            demoPhaseList = value[1]
            demoStartList = value[2]
            demoLenList = value[3]
            judgeAngleList = self.JudgeAngle2FPGA(value[4])
            judgeThreshlodList = value[5]
            self.indexNum = len(demoFreqList)
            for i, freq in enumerate(demoFreqList):
                self.ChlSel(ch)
                ## 解模参数
                self.set(':READ:DEMODU:INDEX',i)
                self.set(':READ:DEMODU:FREQUENCY',freq)
                # print(freq)
                self.set(':READ:DEMODU:PHASE',demoPhaseList[i])
                self.set(':READ:DEMODU:WINHEAD',demoStartList[i])
                self.set(':READ:DEMODU:WINLEN',demoLenList[i])
                ##判决参数
                self.set(':READ:DEMODU:JAONe', judgeAngleList[0])
                self.set(':READ:DEMODU:JTONe', judgeThreshlodList[0])
                self.set(':READ:DEMODU:JATWo', judgeAngleList[1])
                self.set(':READ:DEMODU:JTTWo', judgeThreshlodList[1])
                self.set(':READ:DEMODU:JATHree', judgeAngleList[2])
                self.set(':READ:DEMODU:JTTHree', judgeThreshlodList[2])
                
                self.set(':READ:DEMODU:LOADCHL',ch-1)
        elif name in ['Acqinfo']:
            self.ChlSel(ch)
            self.set(':READ:ACQINTER',value[0])
            self.set(':READ:ACQTIMES',value[1])
        
        ###############################################################################################
        else:
            pass


    def read(self, name: str, **kw):
        ch = kw.get('ch', 1)
        print(f"read {name} from {ch}")
        if name in ['TraceIQ']:
            return self.rd_RawData(ch)
        elif name in ['IQ']:
            return self.read_IQ_data(ch)#回读解模数据
        elif name in ['JudgeIQ']:
            return self.read_JudgeIQ_data()
        else:
            pass


    def set(self, cmd_name, params = None):
            """
            发送带参数的程控指令
            :param sock: 已连接的socket对象
            :param cmd_name: 指令名称（如"SET_FREQ"）
            :param params: 可变参数（如频率值、通道号等）
            """
            print(f"set: {cmd_name} {params}")
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
            self.sock.sendall(cmd_bytes)
            # print(f"已发送指令：{cmd_str}")
            return self.sock_read()
    
    def set1(self, cmd_name, params = None):
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
        self.sock.sendall(cmd_bytes)


    def sock_read(self,buffer_size=1024):
        response = b''
        while True:
            chunk = self.sock.recv(buffer_size)
            if not chunk:
                break  # 连接关闭
            response += chunk
            if b'\n' in chunk:
                break  # 达到结束标志
        return response.strip()
        
    def sock_query(self, cmd_name,params = None):
        return self.set( cmd_name, params)
        
       
    def sock_readbytes(self):
        list_data_bytes = b''
        reData = self.sock.recv(1) #读#
        if(reData ==b'#'):
            reData = (int)(self.sock.recv(1)) #读位宽
            if(reData != 0):
                Len_bytes = (int)(self.sock.recv(reData)) #读数据长度
                remain = Len_bytes
                while remain > 0:
                    list_data_bytes = list_data_bytes + self.sock.recv(remain)
                    remain = Len_bytes - len(list_data_bytes)
        return list_data_bytes
    

    
    ##########################################################################################################
    def ChlSel(self,chl,name = ':AWG:CHLSEL'):
        chl = chl -1
        self.set(name,chl)

    def rd_Delay(self, chl, delay_ns):
        self.ChlSel(chl)
        self.set(':READ:DELAY',delay_ns)
        return
    def rd_Att(self, chl, att):
        self.ChlSel(chl)
        self.set(':READ:ATT',att)
        return
    
    # ------------------------------------------------------------------------
    # 功能说明: 【rdOut】原始数据回读       * 长数据
    # 输入参数:
    #       Handle: 【数据类型: TCPIPSocket】
    #               【参数意义: 待操作的仪器句柄】
    #       Len_bytes: 【数据类型: int】
    #                  【取值范围: [1,单次采集长度*采集段数]】
    #                  【参数意义: 回读数据长度, 单位:Byte】
    # 返回值: 返回Bytes类型数据序列
    # ------------------------------------------------------------------------
    def read_Data_long(self, num):

        self.set(':READ:OUT',num)
        self.set1(':READ:OUT?',None)
        return self.sock_readbytes()
        
    # ------------------------------------------------------------------------
    # 功能说明: bytes转float，并归一化(normalization)    【用于转换采集数据】
    # 特别说明: 中间参数 d_uint16
    #          d_uint16：数据有效位【14位】 【第14位为符号位，[13:0]为数据位】
    # 输入参数:
    #       data:【数据类型: bytes】
    #            【数据说明: 每16位为一组, 范围: 0~0xFFFF, 低14位有效】
    #            【参数意义: 待处理的bytes数据】
    # 输出参数:
    #       data_float:【数据类型: list】
    #                  【取值范围: 0~1】
    #                  【参数意义: 转换后生成的float数据列表】
    #       x_Data.tolist():【数据类型: list】
    #                       【取值范围: 正整数】
    #                       【参数意义: 计数用, x坐标】
    # 返回值: x_Data.tolist(), data_float
    # ------------------------------------------------------------------------
    # ------------------------------------------------------------------------
    # 功能说明: bytes转float，并归一化(normalization)    【用于转换解模数据】
    # 特别说明: 中间参数 d_uint32
    #          d_uint32: 数据有效位【32位】 【第32位为符号位，[31:0]为数据位】
    # 输入参数:
    #       data:【数据类型: bytes】
    #            【数据说明: 每32位为一组, 范围: 0~0xFFFFFFFF】
    #            【参数意义: 待处理的bytes数据】
    # 输出参数:
    #       data_float:【数据类型: list】
    #                  【取值范围: 0~1】
    #                  【参数意义: 转换后生成的float数据列表】
    # 返回值: data_float
    #------------------------------------------------------------------------
    # def bytes2float_u32(self,data):
    #     Step = 4
    #     data_float = []
    #     for i in range(int(len(data)/Step)):
    #         d_uint32 = int.from_bytes(data[i*Step:(i+1)*Step], byteorder="little")
    #         if d_uint32 >= pow(2,31):
    #             d_float = (d_uint32 - (pow(2,32)-1))
    #         else:
    #             d_float = d_uint32
    #         data_float.append(d_float)
    #     return data_float
    
    def bytes2float(self,data):
        Step = 2
        data_float = []
        # x_Data = np.linspace(0, (int(len(data)/Step)-1), int(len(data)/Step), dtype=int)

        for i in range(int(len(data)/Step)):
            d_uint16 = int.from_bytes(data[i*Step:(i+1)*Step], byteorder="little")
            if d_uint16 >= (pow(2,13)-1):
                d_float = (d_uint16 - (pow(2,14)-1))/pow(2,13)
            else:
                d_float = d_uint16/(pow(2,13)-1)
            data_float.append(d_float)

        return data_float
 

    def rd_RawData(self,ch=9):
        self.ChlSel(ch)
        # 存储结束标志位回读
        while((int(self.sock_query(":READ:ENDSTORE?"))) == 0):
            pass    
        rdOut_reData_bytes = self.read_Data_long(self.seqNum*self.seqLen)
        data_real = self.bytes2float(rdOut_reData_bytes)
        res = np.array(data_real).reshape(self.seqNum, self.seqLen)
        return res, res*0


    ###################################
    # ------------------------------------------------------------------------
    # 功能说明: bytes转float，并归一化(normalization)    【用于转换解模数据】
    # 特别说明: 中间参数 d_uint32
    #          d_uint32: 数据有效位【32位】 【第32位为符号位，[31:0]为数据位】
    # 输入参数:
    #       data:【数据类型: bytes】
    #            【数据说明: 每32位为一组, 范围: 0~0xFFFFFFFF】
    #            【参数意义: 待处理的bytes数据】
    # 输出参数:
    #       data_float:【数据类型: list】
    #                  【取值范围: 0~1】
    #                  【参数意义: 转换后生成的float数据列表】
    # 返回值: data_float
    # ------------------------------------------------------------------------
    def bytes2float_u32(self,data):
        Step = 4
        data_float = []
        for i in range(int(len(data)/Step)):
            d_uint32 = int.from_bytes(data[i*Step:(i+1)*Step], byteorder="little")
            if d_uint32 > pow(2,31):
                d_float = (d_uint32 - (pow(2,32)-1)) / pow(2,31)
            else:
                d_float = d_uint32 / pow(2,31)
            data_float.append(d_float)
        return data_float
        # ------------------------------------------------------------------------
    # 功能说明: 【rdOut】【主板】解模数据回读
    # 输入参数:
    #       Index: 【数据类型: int】
    #              【取值范围: [0,15]】
    #              【参数意义: 解模模块序号】
    #       rd_chl: 【数据类型: int】
    #               【取值范围: [1,24]】
    #               【参数意义: 数据回读通道】
    # 返回值: reData_i, reData_q
    #           【数据类型: list】
    #           【参数意义: 所选通道，所选解模模块解模结果(I&Q两路结果)】
    # ------------------------------------------------------------------------
    def demodu_read_M_new(self, Index, rd_chl,num):
        # 存储结束标志位回读
        self.set(':AWG:CHLSEL',rd_chl-1)
        self.set(':READ:DEMODU:RINDEX',Index) #解模模块选择
        while(int(self.sock_query(":READ:ENDSTORE?")) == 0):
            pass  
        self.set(':READ:DEMODU:READ',num)#解模读取的长度（等于设置的段数）
        self.set1(':READ:DEMODU:READ?',None)
        reData_bytes = self.sock_readbytes()
        #处理返回的解模数据
        reData_float = self.bytes2float_u32(reData_bytes)
        reData_i = reData_float[::2]#选取索引为偶数的元素
        reData_q = reData_float[1::2]
        return reData_i,reData_q

    def read_IQ_data(self,ch):
        #下发解模参数（在开始采集时下发）
        #回读解模数据：
        reData_i_list = []
        reData_q_list = []
        num = self.seqNum #段数
        for i in range(self.indexNum):
            Index = i
            reData_i,reData_q = self.demodu_read_M_new(Index,ch,num) #i解模模块读取
            reData_i_list.append(reData_i)
            reData_q_list.append(reData_q)
        chA = np.array(reData_i_list)
        chB = np.array(reData_q_list)
        return chA.T, chB.T
    
    def read_JudgeIQ_data(self):
        self.set(':READ:DEMODU:RINDEX 0')   # 0表示第一个解模频点
        times_0 = int(self.sock_query(':READ:DEMODU:JRONe?'))
        times_1 = int(self.sock_query(':READ:DEMODU:JRTWo?'))
        times_2 = int(self.sock_query(':READ:DEMODU:JRTHree?'))

        return (times_0, times_1, times_2)
    
    
    def adc_startCature(self, ch):
        self.ChlSel(ch)
        self.set(':READ:ACQINTER',1)
        self.set(':READ:ACQTIMES',1)
        self.set(':READ:CHLCHOOSE',1) #对应通道使能，255表示全开
        self.set(':READ:STAR')
       

################################################
    def rd_Shot_data(self, value=1023, ch=9):
        self.seqNum = value
        self.ChlSel(ch)
        self.set(':READ:SEQNUM',self.seqNum)
    
    def rd_pointNum_data(self, value=500, ch=9):
        self.seqLen = value
        self.ChlSel(ch)
        self.set(':READ:SEQLEN', self.seqLen)

    
    def JudgeAngle2FPGA(self, AngleList):
        """将判决旋转角转换为三角函数值"""
        cos_x_16_1,sin_x_16_1 = self.judgeAngle_Conversion(AngleList[0])
        cos_x_16_2,sin_x_16_2 = self.judgeAngle_Conversion(AngleList[1])
        cos_x_16_3,sin_x_16_3 = self.judgeAngle_Conversion(AngleList[2])

        angle_32_1 = (cos_x_16_1 << 16) | sin_x_16_1
        angle_32_2 = (cos_x_16_2 << 16) | sin_x_16_2
        angle_32_3 = (cos_x_16_3 * np.power(2,16)) + sin_x_16_3

        return [angle_32_1, angle_32_2, angle_32_3]
    
    def judgeAngle_Conversion(self, JudgeAngle):
        """将判决旋转角转换为三角函数值"""
        cos_x = np.cos(np.deg2rad(JudgeAngle))
        sin_x = np.sin(np.deg2rad(JudgeAngle))
        #归一化到【0x8000,0x7fff】
        cos_x_16 = (cos_x + 1) * 32767.5 - 32768
        sin_x_16 = (sin_x + 1) * 32767.5 - 32768

        return int(cos_x_16), int(sin_x_16)


