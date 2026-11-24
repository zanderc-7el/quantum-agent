



# ------------- utiliz --------------
import random
import string
import os 
# from . import * # = from core import *
from . import InstrumentBase, create_hardware

def exp_getRandomExpID():
    exp_id=''.join(random.sample(string.ascii_letters + string.digits, 8))
    return exp_id





def exp_ini_local():
    def create_hardwares(debug=True, group='default'):
        # 创建设备并连接
        sync = InstrumentBase.find_instrument('SYNC', group=group) or create_hardware('SYNC', 'CY_SYNC', "169.254.164.100", group=group, debug=debug) 
        da = InstrumentBase.find_instrument('DA', group=group) or create_hardware('DA', 'CY_10DA', "169.254.164.70", sync_bind="SYNC", group=group, debug=debug)
        ad = InstrumentBase.find_instrument('AD', group=group) or create_hardware('AD', 'CY_AD', "169.254.164.76", sync_bind="SYNC", group=group, debug=debug)
        xy = InstrumentBase.find_instrument('XY', group=group) or create_hardware('XY', 'CY_6DA', "169.254.164.71", group=group, sync_bind="SYNC", debug=debug)
        z = InstrumentBase.find_instrument('Z', group=group) or create_hardware('Z', 'CY_6DADC', "169.254.164.72", sync_bind="SYNC", group=group, debug=debug)
        na = InstrumentBase.find_instrument('NA', group=group) or create_hardware("NA", "3656D", addr="xxxx", group=group,  debug=debug)
        
        
        for i in range(1, 10):
            da.set_channel(f"CH{i}", i)
            ad.set_channel(f"CH{i}", i)
            xy.set_channel(f"CH{i}", i)
            z.set_channel(f"CH{i}", i)
            na.set_channel(f"CH{i}", i)

    def create_old_hardwares(debug=True, group='default'):
        AWG = InstrumentBase.find_instrument("AWG", group=group) or create_hardware("AWG", "CeyearStation", "127.0.0.1", group=group, debug=debug)
        

    # create_hardwares(debug=True, group="17")
    create_old_hardwares(debug=True, group="default")


def exp_int_hardware():
    def create_hardwares():
        channel_dict = {
            'CH1': ('CY_Station1', (12,0)),
            'CH2': ('CY_Station1', (12,1)),
            'MASTER':('CY_Station1', (0,0)),
            'CY_R1': ('CY_Station1', (6,0)),
            'CY_R2': ('CY_Station1', (6,1)),
            'CY_XY1': ('CY_Station1', (7,0)),
            'CY_XY2': ('CY_Station1', (8,0)),
            'CY_XY3': ('CY_Station1', (9,0)),
            'CY_XY4': ('CY_Station1', (10,0)),
            'CY_XY5': ('CY_Station1', (11,0)),
            'CY_XY6': ('CY_Station1', (7,1)),
            'CY_XY7': ('CY_Station1', (8,1)),
            'CY_XY8': ('CY_Station1', (9,1)),
            'CY_XY9': ('CY_Station1', (10,1)),
            'CY_XY10': ('CY_Station1', (11,1)),
            'CY_Z1': ('CY_Station1', (0,0)),
            'CY_Z2': ('CY_Station1', (0,1)),
            'CY_Z3': ('CY_Station1', (0,2)),
            'CY_Z4': ('CY_Station1', (0,3)),
            'CY_Z5': ('CY_Station1', (1,3)),
            'CY_Z6': ('CY_Station1', (1,1)),
            'CY_Z7': ('CY_Station1', (1,2)),
            'CY_Z8': ('CY_Station1', (1,3)),
            'CY_Z9': ('CY_Station1', (2,0)),
            'CY_Z10': ('CY_Station1', (2,1)),
            'CY_Z11': ('CY_Station1', (2,2)),
            'CY_Z12': ('CY_Station1', (2,3)),
            'CY_Z13': ('CY_Station1', (3,0)),
            'CY_Z14': ('CY_Station1', (3,1)),
            'CY_Z15': ('CY_Station1', (3,2)),
            'CY_Z16': ('CY_Station1', (3,3)),
            'CY_Z17': ('CY_Station1', (4,0)),
            'CY_Z18': ('CY_Station1', (4,1)),
            'CY_Z19': ('CY_Station1', (4,2)),
            'CY_Z20': ('CY_Station1', (4,3)),
            'CY_Z21': ('CY_Station1', (5,0)),
            'CY_Z22': ('CY_Station1', (5,1)),
            'CY_Z23': ('CY_Station1', (5,2)),
            'CY_Z24': ('CY_Station1', (5,3))
        }
        AWG = InstrumentBase.find_instrument('Ceyear1652AM') or create_hardware("Ceyear1652AM", "CeyearStation", "TCPIP::192.0.2.50::7110::SOCKET", debug=False)
        for k,v in channel_dict.items():
           AWG.set_channel(k, v[1])
        AWG.open()
    create_hardwares()



PATH_CORE = os.path.dirname(os.path.abspath(__file__))
PATH_SCCTRL = os.path.dirname(PATH_CORE)
PATH_CONFIG = os.path.join(PATH_SCCTRL, "config")












import numpy as np
from sklearn.svm import SVC
import pickle


class ShotClassfier:

    def __init__(self):
        self.clf = None

    @classmethod
    def learnModel(cls, scanedRes:dict):
        I0list, Q0list = scanedRes[0]
        I1list, Q1list = scanedRes[1]

        # 构造训练数据和标签
        X = np.vstack([np.column_stack((I0list, Q0list)), np.column_stack((I1list, Q1list))])
        y = np.array([0]*len(I0list) + [1]*len(I1list))

        clf = SVC(kernel='linear')
        clf.fit(X, y)
        newObj = cls() 
        newObj.clf = clf
        return newObj
    
    def predict(self, I, Q):
        return self.clf.predict([[I, Q]])[0]
    
    def predicts(self, Ilist, Qlist):
        X = np.column_stack((Ilist, Qlist))
        return self.clf.predict(X)

    def dump(self, path):
        with open(path, 'wb') as f:
            pickle.dump(self.clf, f)

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as f:
            clf = pickle.load(f)
        newObj = cls() 
        newObj.clf = clf
        return newObj
    
    def plot(self,ax,color="r",width=1):
        # 提取权重和偏置
        w = self.clf.coef_[0]
        b = self.clf.intercept_[0]

        # 计算分类线的斜率 (slope) 
        slope = -(w[0] / w[1])

        # 计算直线在 Q 轴上的截距 (当 I = 0 时)
        q_intercept = -(b / w[1])

        # 直接使用 ax.axline 绘制无限长直线
        # 参数 xy1 是直线上的一点 (这里取 I=0 的截距点)，slope 是斜率
        ax.axline(xy1=(0, q_intercept), slope=slope, color=color, linewidth=width, label='$Boundary(|0\\rangle,|1\\rangle)$')





# def iqData2probability(raw_result, classifier) -> np.ndarray:
#     """
#     将一次扫描得到的 IQ shot 数据转成每个扫描点的 |1> 概率。
#     raw_result 来自 Experiment.execute_scan()，每个扫描点形如 [Ilist, Qlist]。
#     """
#     prob1_data = []
#     for scan in raw_result:
#         i_list, q_list = scan
#         shots = classifier.predicts(i_list, q_list)
#         prob1_data.append(shots.mean())
#     return np.asarray(prob1_data)

# def reshapeBySweeps(values, spec, order=["amp", "phi"]) -> np.ndarray:
#     """
#     按 spec.sweeps 将一维扫描结果恢复成多维数组，并按 order 调整轴顺序。

#     例如 sweeps 原始顺序为 [cir, amp, phi]，其中 cir 只有一个取值时：
#         reshapeBySweeps(values, spec, ["amp", "phi"]).shape == (n_amp, n_phi)
#     """
#     if hasattr(spec, "spec"):
#         spec = spec.spec

#     sweeps = spec["sweeps"] if isinstance(spec, dict) else spec.sweeps
#     shape = []
#     names = []
#     for sweep in sweeps:
#         values_cfg = sweep["values"]
#         n_point = values_cfg["points"] if isinstance(values_cfg, dict) else len(values_cfg)
#         shape.append(n_point)
#         names.append(sweep["name"])

#     data = np.asarray(values).reshape(shape)
#     axes = [names.index(name) for name in order]
#     unused_axes = [idx for idx in range(len(names)) if idx not in axes]

#     for idx in unused_axes:
#         if shape[idx] != 1:
#             raise ValueError(f"sweep {names[idx]!r} 不是单点，不能自动丢弃")

#     data = np.transpose(data, axes + unused_axes)
#     if unused_axes:
#         data = np.squeeze(data, axis=tuple(range(len(axes), len(axes) + len(unused_axes))))
#     return data
















def calibrationQstFitting(phiList, probList, sign=1) -> tuple:
    '''
    fit  A cos( phi +  sign* theta  ) + C 
    '''
    phi = np.asarray(phiList, dtype=float)
    prob = np.asarray(probList, dtype=float)

    if phi.shape != prob.shape:
        raise ValueError(f"phiList 和 probList 长度不一致: {phi.shape} != {prob.shape}")
    if phi.ndim != 1:
        raise ValueError("phiList 和 probList 必须是一维数据")
    if len(phi) < 3:
        raise ValueError("至少需要 3 个点才能拟合 A*cos(phi +/- theta)+C")
    if sign not in (1, -1):
        raise ValueError("sign 只能是 1 或 -1")

    # A*cos(phi + sign*theta)+C
    # = u*cos(phi) + v*sin(phi) + C
    # u = A*cos(theta), v = -sign*A*sin(theta)
    x = np.column_stack([np.cos(phi), np.sin(phi), np.ones_like(phi)])
    u, v, C = np.linalg.lstsq(x, prob, rcond=None)[0]

    A = np.hypot(u, v)
    theta = np.arctan2(-sign * v, u) % (2 * np.pi)

    return theta, A, C





def conditionScanedDataP10_CirAmpPhi(classifier,resData,exp):
    '''
    返回数值为  P1 - P0 
    适用于实验中扫描格式如下sweeps

    "sweeps": [
        {"name": "cir","type": "seq_sweep","values": ["q10"],"bindings": "exp.circuit_variants"},
        {"name": "amp","type": "global_config","values": {"start": 0.01,"stop": 0.6,"points": 20},...},
        {"name": "phi","type": "global_config","values": {"start": 0,"stop": 6.28,"points": 13},...}
    ]

    '''
    shotData = []
    for scan in resData:
        shots = classifier.predicts(  scan[0],scan[1]  )
        prob1 = shots.mean()
        shotData.append( 2*prob1-1 )
    
    sweeps = exp.spec.sweeps
    ampStart = sweeps[1]['values']['start']
    ampStop = sweeps[1]['values']['stop']
    numAmp = sweeps[1]['values']['points']
    phiStart = sweeps[2]['values']['start']
    phiStop = sweeps[2]['values']['stop']
    numPhi = sweeps[2]['values']['points']

    shotData = np.asarray(shotData)
    s1 = shotData.reshape(numAmp,numPhi)   
    returnV = {
        "data":s1,
        "ampStart":ampStart,
        "ampStop":ampStop,
        "numAmp":numAmp,
        "phiStart":phiStart,
        "phiStop":phiStop,
        "numPhi":numPhi
    }
    return returnV


def plot_ampPhi_scan(ax, result, title=""):
    # 提取数据
    data2D = result['data']
    amp_start = result['ampStart']
    amp_stop = result['ampStop']
    phi_start = result['phiStart']
    phi_stop = result['phiStop']
    
    # 1. 矩阵转置：从 (numAmp, numPhi) 转成 (numPhi, numAmp)，即 (y, x)
    plot_data = data2D.T
    
    # 2. 设置 extent 映射实际数值: [x方向起点, x方向终点, y方向起点, y方向终点]
    extent_vals = [amp_start, amp_stop, phi_start, phi_stop]
    
    # 3. 使用 imshow 绘图
    # origin='lower' 让 y 轴从下往上递增
    # aspect='auto' 会自动调整宽高比，防止 amp 和 phi 数值量级差异过大导致图像变成一条细线
    # cmap='viridis' 是推荐的渐变色条，你也可以换成 'hot', 'jet', 'RdBu' 等
    im = ax.imshow(plot_data, 
                   origin='lower', 
                   extent=extent_vals, 
                   aspect='auto', 
                   cmap='viridis')
    
    # 添加颜色条，标明 z 轴物理意义
    ax.figure.colorbar(im, ax=ax, label='Probability (prob1)')
    
    # 设置坐标轴标签
    ax.set_xlabel('Amplitude (amp)')
    ax.set_ylabel('Phase (phi)')
    ax.set_title(title)
    
    return im




def CZ_fit_qst_phase(conditionedData, sign=1):
    plotData = conditionedData
    data = np.asarray(plotData['data'])
    amp_list = np.linspace(plotData['ampStart'], plotData['ampStop'], plotData['numAmp'])
    phi_list = np.linspace(plotData['phiStart'], plotData['phiStop'], plotData['numPhi'])
    theta_list = []

    offset_list = []
    for p10_list in data:
        theta, A, C = calibrationQstFitting(phi_list, p10_list, sign=sign)
        theta_list.append(theta)

    thetaList = np.asarray(theta_list)
    thetaListUnwarp = np.unwrap(thetaList)
    return {
        'amp': np.asarray(amp_list),
        'theta': np.asarray(theta_list),
        "thetaUnwrap": np.asarray(thetaListUnwarp),
    }





class FormatedScanedData():

    def __init__(self):
        pass 
    