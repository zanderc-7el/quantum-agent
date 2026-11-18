import numpy as np

class MockerDriver:
    
    support_models = ['Mocker']
    
    def __init__(self, addr, *, seed=None, seqNum=64, seqLen=1024, freqNum=1, **kw):
        
        self.addr = addr
        self.seqNum = int(seqNum)
        self.seqLen = int(seqLen)
        self.freqNum = int(freqNum)
        self._cur_ch = 0
        self.reg = {}   # (ch, key)->value
        s = seed if seed is not None else (abs(hash(addr)) % (2**32))
        self.rng = np.random.default_rng(s)
        self.connected = False
        
    def check_connect(self):
        assert self.connected, "设备未连接"
        
    def open(self, **kwargs):
        self.connected = True
        
    def close(self):
        self.connected = False
    
    # ---------- 核心：write ----------
    def write(self, cmd: str, value, **kwargs):
        self.check_connect()
        cmd = str(cmd).strip()

        # 通道选择（你真实协议可能是别的名字，按需加）
        if cmd in (":CHL:SEL", ":READ:CHLSEL"):
            self._cur_ch = int(value)
            return 1

        # 允许上层改段数/段长
        if "shot" in cmd.lower():
            self.seqNum = int(value); return 1
        if cmd == "pointNum":
            self.seqLen = int(value); return 1
        if "coeff" in cmd.lower():
            self.freqNum = len(value[0]); return 1
        # 其它写：忽略/记录即可
        self.reg[(self._cur_ch, cmd)] = value
        return 1

    # ---------- 核心：read ----------
    def read(self, cmd: str, **kwargs):
        
        self.check_connect()
        cmd = str(cmd).strip()
        # (A) Raw
        if cmd == "TraceIQ":
            return self.rd_RawData()

        # (B) IQ 解模模式
        if cmd == "IQ":
            return self.read_IQ_data()

        # (C) Judge 模式
        if cmd  == "JudgeIQ":
            return self.read_JudgeIQ_data()

        return b"ok"

    # ---------- 兼容你现有 driver 的 set/sock_query 调用风格 ----------
    def set(self, cmd, params=0, **kwargs):
        self.check_connect()
        cmd = str(cmd).strip()
        if cmd.endswith("?"):
            # query：read
            return self.read(cmd, **kwargs)
        # write
        return self.write(cmd, params, **kwargs)

    def socket_query(self, cmd, params=0, **kwargs):
        self.check_connect()
        return self.set(cmd, params, **kwargs)

    def sock_query(self, cmd, params=0, **kwargs):
        self.check_connect()
        return self.set(cmd, params, **kwargs)

    # ---------- (1) Raw ADC ----------
    def rd_RawData(self, *args, **kwargs) -> np.ndarray:
        """
        返回 shape = (seqNum, seqLen)
        dtype = float32
        数值范围 ~ [-1, 1]
        """
        self.check_connect()
        t = np.arange(self.seqLen, dtype=np.float64)[None, :]
        base = np.sin(2 * np.pi * 0.01 * t)
        noise = 0.05 * self.rng.normal(size=(self.seqNum, self.seqLen))

        data = 0.6 * base + noise
        data = np.clip(data, -1.0, 1.0)

        return data.astype(np.float32)

    # ---------- (2) Demod IQ ----------
    def read_IQ_data(self, *args, **kwargs):
        """
        返回 (I, Q)
        推荐 shape = (freqNum, seqNum)
        dtype = float32
        """
        self.check_connect()
        t = np.arange(self.seqNum, dtype=np.float64)[None, :]
        k = np.arange(self.freqNum, dtype=np.float64)[:, None]

        phase = 0.3 * k + 0.1 * t
        amp = 5000

        I = amp * np.cos(phase) + self.rng.normal(0, 30, size=(self.freqNum, self.seqNum))
        Q = amp * np.sin(phase) + self.rng.normal(0, 30, size=(self.freqNum, self.seqNum))

        chA = np.array(I)
        chB = np.array(Q)
        return chA.T, chB.T

    # ---------- (3) Judge ----------
    def read_JudgeIQ_data(self, *args, **kwargs):
        """
        返回 (zero, one, two)
        """
        self.check_connect()
        z = int(self.rng.integers(0, self.seqNum))
        o = int(self.rng.integers(0, self.seqNum))
        t = int(self.rng.integers(0, self.seqNum))
        return z, o, t

    def multDevice_sync(self, dev_list):
        self.check_connect()
    
    def sync_triger_single(self):
        self.check_connect()
    
    def sync_triger_cycle(self, values: tuple):
        
        self.check_connect()
        assert len(values) == 2, "传入值长度不符"
    
    def ADInit(self):
        self.check_connect()
        
    def set_trigger(self, shots=1, period=0):
        self.check_connect()
