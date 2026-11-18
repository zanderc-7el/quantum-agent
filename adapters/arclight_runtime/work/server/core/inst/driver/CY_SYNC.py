import socket
import time


class CYSYNCDriver:
    support_models = ['CY_SYNC']

    def __init__(self, addr, **kw):
        self.addr = addr
        self.model = 'CY_SYNC'
        self.timeout = kw.get('timeout', 10)

    def open(self):
        #建立连接
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.addr, 4196))
        self.sock.settimeout(self.timeout)
        #参考时钟选择
        self.set('GLOB:CLKSEL',1)
        self.set('GLOB:TRIGS',"CLOSE")
        #使能所有通道
        for i in range(1,21):
            time.sleep(0.1)
            self.set('GLOB:SELC',i)
            time.sleep(0.1)
            self.set('GLOB:CHLO',"ON")

    def close(self, **kw):
        self.sock.close()


    # *#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*# user defined #*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#*#

    def socket_query(self, cmd_name, params = 0, buffer_size=1024):
        self.send_command(cmd_name, params)
        
        # 循环接收直到获取所有数据（示例：假设响应以 b'END' 结尾）
        response = b''
        while True:
            chunk = self.sock.recv(buffer_size)
            if not chunk:
                break  # 连接关闭
            response += chunk
            if b'\n' in chunk:
                break  # 达到结束标志
        return response

    def send_command(self, cmd_name, params,a = 0):
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
            cmd_str = f"{cmd_name} {param_str};"    # 组合成完整指令（带结束符）
        else:
            cmd_str = f"{cmd_name};"    # 组合成完整指令（带结束符）
        
        # 2. 转换为字节流（socket传输必须是bytes类型）
        cmd_bytes = cmd_str.encode('utf-8')
        
        # 3. 发送指令
        self.sock.sendall(cmd_bytes)
        # print(f"已发送指令：{cmd_str}")

    def set(self, name, value=None):
        return self.socket_query(name, value)

    
    ##########################################################################################################
    def sync_triger_single(self):
        self.set('GLOB:TRIGS SINGLE')
    
    def sync_triger_cycle(self,value):
        self.set('GLOB:TRIGS',"CYCLE")
        time.sleep(0.01)
        self.set('GLOB:SPAC',value[0])
        time.sleep(0.01)
        self.set('GLOB:LOOP',value[1])
        

    def set_SYNC_TrigCycle(self):
        self.set('GLOB:TRIGS',"CYCLE")
        time.sleep(0.001)
        self.set('GLOB:SPAC',2e-6)
        # self.set('GLOB:SPAC',100e-6)
        time.sleep(0.001)
        self.set('GLOB:LOOP',1)
    
    def device_sync(self, deviceHandle):
        deviceHandle.set(':TRIG:TYPE',0)
        #触发模式：7044同步
        deviceHandle.set(':TRIG:CTRL',0)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        # 触发模式：多通道同步
        deviceHandle.set(':TRIG:CTRL',1)
        # 通道同步复位
        deviceHandle.set(':TRIG:CHLS')
        self.set_SYNC_TrigCycle()
        # time.sleep(1)
        # # #触发模式：波形触发模式
        deviceHandle.set(':TRIG:CTRL',2)
    
    def multDevice_sync(self, deviceHandleList):
        for handle in deviceHandleList:
            handle.set(':TRIG:TYPE',0)
            #触发模式：7044同步
            handle.set(':TRIG:CTRL',0)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        self.set_SYNC_TrigCycle()
        time.sleep(1)
        for handle in deviceHandleList:
            #触发模式：多通道同步
            handle.set(':TRIG:CTRL',1)
            #通道同步复位
            handle.set(':TRIG:CHLS')
        self.set_SYNC_TrigCycle()
        for handle in deviceHandleList:
            #触发模式：波形触发模式
            handle.set(':TRIG:CTRL',2)



