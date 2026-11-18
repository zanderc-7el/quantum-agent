

import numpy as np
from dataclasses import dataclass
from .iqClassifier import Classifier





# 配套default_postprocess,circuit 测量的结果
# 跟前端交互的时候,可以使用export,load方法把数据保存为普通的python dict
@dataclass 
class ConditionedData:
    r'''
    # shots-key: 按照 measure_list从左到右,从高到低的顺序排列转换为10进制数
    # shots-val: shots 数量
    '''
    nQ: int
    shots: dict[int, int]

    def export(self)->dict:
        res = {
            "shots": self.shots
        }
        return res

    @classmethod
    def load(cls,data:dict):
        shots = data.get("shots",{}) 
        s = { int(k):int(v) for k,v in shots.items()}
        new = cls( s )
        return new

    def getProbs(self) -> dict[int, float]:
        """
        计算每个测量结果出现的概率。
        Returns:
            dict[int, float]: key与shots相同，value为该结果出现的概率 (0.0 到 1.0)
        """
        # 计算所有 shots 的总数
        total_shots = sum(self.shots.values())
        
        # 防止除以 0 的异常
        if total_shots == 0:
            return {k: 0.0 for k in self.shots.keys()}
            
        # 字典推导式计算概率
        probs = {k: v / total_shots for k, v in self.shots.items()}
        return probs




_clf = None 



def _init_clf_in_not(config):
    global _clf
    if _clf is None:
        _clf = Classifier(config) 
    return _clf





def dataConditioner(raw, config, meta=None):
    clf = _init_clf_in_not(config)
    r1, r2, measure_list = raw


    #-------------------------------------------
    #      raw -> I,Q
    #
    is_complex = np.issubdtype(r1[0][0].dtype, np.complexfloating)
    if is_complex:
        I = np.real(r1)
        Q = np.imag(r1) 
    else:
        I = r1 
        Q = r2 
    

    #-------------------
    #    I,Q  :=    [n][shots]

    nQ = len(measure_list)
    predict01 = []
    for i in range(nQ):
        predict01.append(clf.predicts(measure_list[i], I[i], Q[i]))
    

    #----- predict01 -> [n][shots] represent 0,1 
    arr = np.array(predict01)
    n, shots = arr.shape
    weights = 2 ** np.arange(n - 1, -1, -1)
    decimal_vals = arr.T.dot(weights)
    unique_vals, counts = np.unique(decimal_vals, return_counts=True)
    counts = dict(zip(unique_vals.astype(int), counts)) #e.g.输出: {0: 1, 3: 1, 5: 1, 6: 1}
    # counts-key: 按照 measure_list从左到右,从高到低的顺序排列转换为10进制数
    # counts-val: shots 数量
    return ConditionedData(nQ, counts)
