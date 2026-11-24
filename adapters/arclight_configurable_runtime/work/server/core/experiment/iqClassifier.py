import numpy as np

from ..utils import SettingConfig





class Classifier():

    def __init__(self, config: SettingConfig):
        self.conf = config

    def _single_shot_config(self, q: int):
        q_key = q if isinstance(q, str) and q.startswith("Q") else f"Q{q}"
        return self.conf.raw()[q_key]["cali"]["singleShot"]

    def predicts(self,q:int, Ilist:list, Qlist:list)->list[int]:
        iq_config = self._single_shot_config(q)# 从配置文件中拿到 cali-singleShot字典
        # 细节以后在补充,现在就for调用predict
        phi = iq_config['phi']
        threshold = iq_config['thr']
        res = [] 
        for I, Q in zip(Ilist, Qlist):
            z = I + 1j*Q 
            v = (z * np.exp(-1j * phi)).real
            if v <= threshold:
                res.append(0)
            else:
                res.append(1)
        return res

    def predict(self,q, I, Q)->int:
        iq_config = self._single_shot_config(q)# 从配置文件中拿到 cali-singleShot字典
        # 核心逻辑先空着,现在写个假的   随机返回0,1
        phi = iq_config['phi']
        threshold = iq_config['thr']
        z = I + 1j*Q 
        v = (z * np.exp(-1j * phi)).real
        if v <= threshold:
            return 0
        else:
            return 1

    def getProb0(self,q,Ilist,Qlist)->float:
        '''测量为0的概率'''
        shots = self.predicts(q, Ilist, Qlist)
        if len(shots) == 0:
            raise ValueError("Ilist/Qlist 为空，无法计算 P0")
        p1 = np.mean(shots)
        return 1 - float(p1)
