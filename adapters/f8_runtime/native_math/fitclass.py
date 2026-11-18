# Author: lizhiyuan  <lizhiyuan@baqis.ac.cn>  2021/01/29
# rebuild: lizhiyuan  <lizhiyuan@baqis.ac.cn>  2022/04/15

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import leastsq, least_squares
from scipy import interpolate
from inspect import signature
import logging
log = logging.getLogger(__name__)


class FitClass(object):
    """Fit class, based on scipy.optimiz.least_squares/leastsq """

    def __init__(self, data, fitfunc, fixed=None, p0=None, indep_var=0,
                 optimize_mode='leastsq', **kw):
        '''
        data: tuple (x,y),数据
        fitfunc: 拟合函数
        fixed: dict, 确定的参数
        p0: dict/list/tuple/array, 初始拟合参数,
            dict,可以只给部分参数的初值,其他初值默认为1;
            list/tuple/array,所有拟合参数的初值
        indep_var: str/int, 自变量,
            如果是int,则表示第n个参数为自变量,
            如果是str,则表示自变量的名字
        optimize_mode: least_squares/leastsq, 两版优化模式
        '''
        x, y = data
        self.x = np.asarray(x)
        self.y = np.asarray(y)

        self.fitfunc = fitfunc
        self.fixed = {} if fixed is None else fixed

        all_keys = list(signature(fitfunc).parameters.keys())
        self.indep_var = all_keys[indep_var] if isinstance(
            indep_var, int) else indep_var

        all_keys.remove(self.indep_var)
        for k in self.fixed.keys():
            all_keys.remove(k)
        self.fit_keys = all_keys

        p0 = {} if p0 is None else p0
        if isinstance(p0, dict):
            self.p0 = [p0.get(k, 1) for k in self.fit_keys]
        else:
            self.p0 = p0
        self._plotscript = None

        if optimize_mode == 'least_squares':
            self.perform_fit_by_least_squares(**kw)
        elif optimize_mode == 'leastsq':
            self.perform_fit_by_leastsq(**kw)
        else:
            raise Exception(f"optimize mode '{optimize_mode}' not support!")

    def gen_errFunc(self):
        def errFunc(params, x, y):
            # print(params)
            params_dict = dict()
            fit_params_dict = dict(zip(self.fit_keys, params))
            params_dict.update(fit_params_dict)
            params_dict.update(self.fixed)
            params_dict.update({self.indep_var: x})
            # print(params_dict)
            e = y - self.fitfunc(**params_dict)
            return np.abs(e)
        return errFunc

    def perform_fit_by_least_squares(self, **least_squares_kw):
        errFunc = self.gen_errFunc()
        res = least_squares(errFunc, self.p0, args=(
            self.x, self.y), **least_squares_kw)
        popt = res.x
        try:
            # covariance matrix when jac not degenerate
            J = res.jac
            pcov = np.linalg.inv(J.T.dot(J))
            # standard deviation errors on the parameters
            fit_error = dict(zip(self.fit_keys, np.sqrt(np.diag(pcov))))
        except Exception as e:
            fit_error = dict(zip(self.fit_keys, [np.nan]*len(self.fit_keys)))
            log.warning(f'failed to calculate pcov:{repr(e)}')

        self.res = res
        self.params = {}
        self.fited = dict(zip(self.fit_keys, popt))
        self.params.update(self.fited)
        self.params.update(self.fixed)
        self.error = {}
        self.error.update(fit_error)
        fix_error = dict(zip(self.fixed.keys(), [0]*len(self.fixed.keys())))
        self.error.update(fix_error)

    def perform_fit_by_leastsq(self, **leastsq_kw):
        errFunc = self.gen_errFunc()
        res = leastsq(errFunc, self.p0, args=(
            self.x, self.y), full_output=1, **leastsq_kw)
        popt, pcov, infodict, errmsg, ier = res
        if pcov is None:
            log.warning(f'ier: {ier}, msg: {errmsg}',)
            fit_error = dict(zip(self.fit_keys, [np.nan]*len(self.fit_keys)))
        else:
            # 根据curve_fit源代码重新计算协方差矩阵
            cost = np.sum(infodict['fvec'] ** 2)
            ysize = len(infodict['fvec'])
            s_sq = cost / (ysize - popt.size)
            pcov = pcov * s_sq
            # standard deviation errors on the parameters
            fit_error = dict(zip(self.fit_keys, np.sqrt(np.diag(pcov))))

        self.res = res
        self.params = {}
        self.fited = dict(zip(self.fit_keys, popt))
        self.params.update(self.fited)
        self.params.update(self.fixed)
        self.error = {}
        self.error.update(fit_error)
        fix_error = dict(zip(self.fixed.keys(), [0]*len(self.fixed.keys())))
        self.error.update(fix_error)

    def func(self, t):
        '''拟合后的函数'''
        indep_var_kw = {self.indep_var: t}
        return self.fitfunc(**indep_var_kw, **self.fited, **self.fixed)

    def gen_smooth_data(self, smooth_level=10):
        """产生一组拟合后的采样更密的画图数据
        smooth_level: deflault 10,插值的倍率,重新对x轴数据插值使画出的拟合曲线更平滑
        """
        t, y = self.x, self.y
        index_sorted = np.argsort(t)
        t, y = t[index_sorted], y[index_sorted]

        size = len(t)
        t_func = interpolate.interp1d(np.arange(size), t, kind='linear')
        _t = t_func(np.linspace(0, size-1, int((size-1)*smooth_level+1)))
        _y = self.func(_t)
        return _t, _y

    def plot(self, fmt='r-', plotscript=None):
        '''
        画图
            Parameters:
                fmt: plot curve format
                plotscript: 额外的画图脚本函数,接收self和ax两个参数
        '''
        t, y = self.x, self.y
        _t, _y = self.gen_smooth_data()

        ax = plt.gca()
        ax.scatter(t, y,)
        ax.plot(_t, _y, fmt)

        if plotscript is not None:
            plotscript(self, ax=ax)
        elif self._plotscript is not None:
            self._plotscript(self, ax=ax)
        else:
            pass

    def plot2(self, plot_fmt='-', modes=None, plot_option=None):
        '''
        画图2
            Parameters:
                modes: real/imag/amp(abs)/phase(angle) 等的列表或字符串
                plot_fmt: plot format
                plot_option: plot  keywords
        '''
        if modes is None:
            modes = ['real']
        elif isinstance(modes, str):
            modes = [modes]

        ax = plt.gca()
        t, y = self.x, self.y
        _t, _y = self.gen_smooth_data()

        plot_kw = {}
        if plot_option is not None:
            plot_kw.update(plot_option)

        if 'real' in modes:
            ax.scatter(t, y.real)
            ax.plot(_t, _y.real, plot_fmt, label='real', **plot_kw)
        if 'imag' in modes:
            ax.scatter(t, y.imag)
            ax.plot(_t, _y.imag, plot_fmt, label='imag', **plot_kw)
        if 'amp' in modes or 'abs' in modes:
            ax.scatter(t, np.abs(y))
            ax.plot(_t, np.abs(_y), plot_fmt, label='amp', **plot_kw)
        if 'phase' in modes or 'angle' in modes:
            ax.scatter(t, np.angle(y))
            ax.plot(_t, np.angle(_y), plot_fmt, label='phase', **plot_kw)
