# Author: feihoo87  <xuhk@baqis.ac.cn>  2021/01/01
# Modified 2021/01/02 by feihoo87 to fit complex lorentz peak
# Modified 2021/01/29 by lizhiyuan: 从analizer模块复制到Fit模块，并添加了F_ge/F_cav


import numpy as np


def lorentz(x, x0, gamma,amp,b):
    """lorentz peak"""
    return amp / (1 + ((x - x0) / gamma)**2)+b


def lorentzComplex(x, x0, gamma,amp,b):
    """complex lorentz peak

    lorentz(x, x0, gamma) = lorentzComplex(x, x0, gamma) * conj(lorentzComplex(x, x0, gamma))
    """
    return amp / (1 + 1j * (x - x0) / gamma)+b


def gaussian(x, x0, sigma):
    """gaussian peak"""
    return np.exp(-0.5 * ((x - x0) / sigma)**2)


def peaks(x, peaks, background=0):
    """
    peaks: list of (center, width, amp, shape)
           shape should be either 'gaussian' or 'lorentz'
    background: a float, complex or ndarray with the same shape of `x`
    """
    ret = np.zeros_like(x)
    for center, width, amp, shape in peaks:
        if shape == 'gaussian':
            ret += amp * gaussian(x, center, width)
        else:
            ret += amp * lorentz(x, center, width)

    return ret + background


def complexPeaks(x, peaks, background=0):
    """
    peaks: list of (center, width, amp)
    background: a float, complex or ndarray with the same shape of `x`
    """
    ret = np.zeros_like(x, dtype=np.complex)
    for x0, gamma, A, *_ in peaks:
        ret += A * lorentzComplex(x, x0, gamma)
    return ret + background


def decay(t, tau):
    """
    exponential decay
    """
    a = -(1 / np.asarray(tau))**(np.arange(len(tau)) + 1)
    a = np.hstack([a[::-1], [0]])
    return np.exp(np.poly1d(a)(t))


def oscillation(t, spec=((1, 1), ), amplitude=1, offset=0):
    """
    oscillation
    """
    ret = np.zeros_like(t, dtype=np.complex)
    for A, f in spec:
        ret += A * np.exp(2j * np.pi * f * t)
    return amplitude * np.real(ret) + offset


def fitCircle(x, y):
    u, v = x - x.mean(), y - y.mean()
    Suuu, Svvv = np.sum(u**3), np.sum(v**3)
    Suu, Svv = np.sum(u**2), np.sum(v**2)
    Suv = np.sum(u * v)
    Suuv, Suvv = np.sum(u**2 * v), np.sum(u * v**2)
    uc = (Suuv * Suv - Suuu * Svv - Suvv * Svv + Suv * Svvv)
    vc = (-Suu * Suuv + Suuu * Suv + Suv * Suvv - Suu * Svvv)
    uc /= 2 * (Suv**2 - Suu * Svv)
    vc /= 2 * (Suv**2 - Suu * Svv)
    xc, yc = uc + x.mean(), vc + y.mean()
    R = np.sqrt(np.mean((x - xc)**2 + (y - yc)**2))
    return xc, yc, R


def rabi(t, Tr, Omega, A, offset):
    return oscillation(t, [(1, Omega)], A, 0) * decay(t, [Tr]) + offset


def cpmg(t, T1, Tphi, Delta, A, offset, phi=0):
    return oscillation(t, [(np.exp(1j * phi), Delta)], A, 0) * decay(t, [2 * T1, Tphi]) + offset


def relaxation(t, T1, A, offset):
    return A * decay(t, [T1]) + offset


def _T1(t, A, B, T1):
    y = A*np.exp(-t/T1)+B
    return y

def _T1ef(t, A, B, C,T1f,T1e):
    y = A*np.exp(-t/T1f)+B*np.exp(-t/T1e)+C
    return y


def _Rabi(t, A, B, phi, Omega, Tr):
    y = A*np.exp(-t/Tr)*np.cos(2*np.pi*Omega*t+phi)+B
    return y

def _Rabief(t, A, B,C, phi, Omega, Tr,Tr_ge):
    y = (A*np.exp(-t/Tr_ge)+C*np.exp(-t/Tr))*np.cos(2*np.pi*Omega*t+phi)+B
    return y


def _Ramsey(t, A, B, phi, T1, Tphi, Delta):
    y = A*np.exp(-t/2/T1-np.square(t/Tphi))*np.cos(2*np.pi*Delta*t+phi)+B
    return y

def _Ramseyef(t, A, B,C, phi, T1f, Tphif, T1, Tphi,Delta,Deltaf):
    y = np.exp(-t/2/T1f-np.square(t/Tphif))*(A*np.cos(2*np.pi*Deltaf*t+phi))+np.exp(-t/2/T1-np.square(t/Tphi))*B*np.cos(2*np.pi*Delta*t+phi)+C
    return y

def _Ramsey1(t, A, B, phi, T1, Tphi1, Tphi2, Delta):
    y = A*np.exp(-t/2/T1-t/Tphi1-np.square(t/Tphi2))*np.cos(2*np.pi*Delta*t+phi)+B
    return y

def _ExpRamsey(t, A, B, phi, T2, Delta):
    y = A*np.exp(-t/T2)*np.cos(2*np.pi*Delta*t+phi)+B
    return y


def _Ramsey_withBeat(t, A, B, phi, Delta, phi2, Delta2, T1, Tphi):
    y = A*np.exp(-t/2/T1-np.square(t/Tphi))*np.cos(
        2*np.pi*Delta*t+phi)*np.cos(2*np.pi*Delta2*t+phi2)+B
    return y


def _RB(t, A, B, p):
    y = A*p**t+B
    return y


def _rbleakage(m, gamma_up, gamma_down, p0):
    '''
    gamma_up: 计算子空间泄漏到2态的概率
    gamma_down: 2态衰减到计算子空间的概率
    p0: 2态的初始概率

    有测量错误时，gamma_up拟合会有误差，见参考文献补充材料。
    Reference: Equation 2 @10.1103/PhysRevLett.116.020501
    '''
    Gamma = gamma_up+gamma_down
    p_inf = gamma_up/Gamma
    p2m = p_inf+(p0-p_inf)*np.exp(-Gamma*m)
    return p2m


def Linear(t, k, b):
    y = k * t + b
    return y


def Sin(t, A, B, f, phi):
    y = A*np.sin(2*np.pi*f*t+phi)+B
    return y


def Cauchy(t, A, t0, FWHM):
    y = A*FWHM/((t-t0)**2+FWHM**2)/np.pi
    return y


def Gaussian(x, A, miu, sigma):
    return A*np.exp(-((x-miu)/sigma)**2/2)


def F_ge(I, F_ge_max, Period, I_SS, d):
    '''
    基态和第一激发态能级间距与磁通偏置电流的关系
    F_ge是指基态和第一激发态能级间距

    I：偏置电流大小
    F_ge_max: 最高点频率，
    Period: 对应一个磁通量子的电流周期，
    I_SS: 频率最高点对应的电流大小，
    d: SQUID双结的不对称度参数，完全对称为0，完全不对称为1,

    参考 arXiv:1907.05198
    '''
    phi = np.pi * (I - I_SS) / Period
    y = F_ge_max * (np.cos(phi)**2 + d * d * np.sin(phi)**2)**0.25
    return y


def F_cav(I, F_cav_bare, g_qr, F_ge_max, Period, I_SS, d):
    '''
    色散区域的谐振腔频率与磁通偏置电流的关系
    F_cav是谐振腔频率

    I：偏置电流大小
    F_cav_bare: 裸腔频率，
    g_qr: 谐振腔与量子比特的耦合常量，
    F_ge_max: 最高点频率，
    Period: 对应一个磁通量子的电流周期，
    I_SS: 频率最高点对应的电流大小，
    d: SQUID双结的不对称度参数，完全对称为0，完全不对称为1,

    参考 arXiv:1907.05198
    '''
    F_ge_i = F_ge(I, F_ge_max, Period, I_SS, d)
    term1 = (F_cav_bare + F_ge_i)/2
    term2 = np.sqrt(g_qr * g_qr + (F_ge_i - F_cav_bare)**2 / 4)
    a = np.where(F_cav_bare > F_ge_i, 1, -1)
    y = term1 + term2*a
    return y
