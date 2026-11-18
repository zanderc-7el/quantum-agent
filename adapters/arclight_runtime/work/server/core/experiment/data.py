import numpy as np
import time
import os
from scipy.optimize import curve_fit
from scipy.fftpack import fft, fftfreq
import json
import h5py
import numpy as np
from pathlib import Path
from dataclasses import asdict, dataclass
from .utils import FitMethodSchema
from typing import Any, Tuple, Optional


class H5SingleJob:
    """
    HDF5 数据存储类，支持流式增量写入，适合大规模扫描实验（如100万点）。
    
    使用方式：
    1. 初始化时传入 expected_shape 预分配空间
    2. 每完成一个扫描点调用 append_result() 增量写入
    3. 实验结束时调用 close()
    """
    
    def __init__(
        self,
        path: str,
        expected_shape: Tuple[int, ...],
        result_name: str = "count", 
        dtype="f8",    
        swmr: bool = True,
        title: str = None,
        chunk_size: int = 1024,
        flush_interval: int = 100,
    ):
        self.path = Path(path)
        self.result_name = result_name
        self.dtype = dtype
        self.chunk_size = chunk_size
        self.flush_interval = flush_interval
        
        self._dataset = None
        self._current_idx = 0
        self._write_count = 0
        self._meta_written = False
        self._result_group_created = False
        
        if not os.path.exists(self.path):
            print("Data will be save into ", self.path)
            os.makedirs(self.path)
        
        title = title or time.strftime("%H%M%S")
        self.file = self.path.joinpath(title + ".hdf5")
        self.f = h5py.File(self.file, "w", libver="latest")
        
        if swmr:
            self.f.swmr_mode = True
        
        #self._init_dataset(expected_shape)
        self._expected_max_rows = expected_shape[0]  # 只保存最大行数
        self._extra_shape = None  # 额外维度，延迟推断
        self._dataset = None
    
    def _init_dataset(self, sample_data):
        """预创建可扩展的 dataset，预分配空间以提升性能"""
        if self._dataset is not None:
            return
        
        # 推断额外维度
        sample_arr = np.asarray(sample_data)
        if sample_arr.ndim == 1:
            self._extra_shape = ()
        else:
            self._extra_shape = sample_arr.shape[1:]
        
        full_shape = (self._expected_max_rows,) + self._extra_shape
        
        resg = self.f.create_group("results")
        self._result_group_created = True
        
        self._dataset = resg.create_dataset(
            self.result_name,
            shape=full_shape,
            dtype=self.dtype,
            chunks=(min(1024, self._expected_max_rows),) + self._extra_shape,
            compression="lzf",
        )
        self.f.flush()
    
    def append_result(self, result):
        """增量写入单个结果，避免内存累积"""

        # 延迟初始化
        if self._dataset is None:
            self._init_dataset(result)
            
        assert self._current_idx <= self._dataset.shape[0], "result is too large"
        
        self._dataset[self._current_idx] = result
        self._current_idx += 1
        self._write_count += 1
        
        if self._write_count % self.flush_interval == 0:
            self.f.flush()
    
    def append_batch(self, results: list):
        """批量写入多个结果，比逐点写入更高效"""
        if not results:
            return
        
        results_arr = np.asarray(results)
        batch_size = len(results)
        
        # 延迟初始化
        if self._dataset is None:
            self._init_dataset(results_arr)
            
        end_idx = self._current_idx + batch_size
        
        assert end_idx <= self._dataset.shape[0], "batch size is too large"
        
        self._dataset[self._current_idx:end_idx] = results_arr
        self._current_idx = end_idx
        
        self.f.flush()
    
    def write_result(self, result: np.ndarray):
        """一次性写入所有结果"""
        resg = self.f.require_group("results")
        resg.create_dataset(
            self.result_name,
            data=np.asarray(result),
            dtype=self.dtype,
            compression="lzf",
            chunks=True,
        )
        self._dataset = resg[self.result_name]
        self._current_idx = result.shape[0] if hasattr(result, 'shape') else len(result)
        self.f.flush()

    def _write_scalar_or_array(self, group, name: str, data: Any, dtype=None):
        arr = np.asarray(data)
        kwargs = {}
        if dtype is not None:
            kwargs["dtype"] = dtype
        if arr.shape != ():
            kwargs["compression"] = "lzf"
            kwargs["chunks"] = True
        group.create_dataset(name, data=arr, **kwargs)

    def _write_value(self, group, name: str, value: Any):
        if value is None:
            group.attrs[name] = ""
            return
        if isinstance(value, str):
            group.attrs[name] = value
            return
        self._write_scalar_or_array(group, name, value)

    def _write_plot_line(self, group, name: str, line: Any):
        line_group = group.create_group(name)
        line_group.attrs["type"] = getattr(line, "type", "")
        legend = getattr(line, "legend", None)
        if legend is not None:
            line_group.attrs["legend"] = legend
        if hasattr(line, "x"):
            self._write_value(line_group, "x", getattr(line, "x"))
        if hasattr(line, "y"):
            self._write_value(line_group, "y", getattr(line, "y"))

    def _write_plot_object(self, group, name: str, obj: Any):
        obj_group = group.create_group(name)
        obj_group.attrs["type"] = getattr(obj, "type", "")
        for attr in ("title", "xLabel", "yLabel", "zLabel"):
            if hasattr(obj, attr):
                obj_group.attrs[attr] = getattr(obj, attr) or ""

        if isinstance(obj, PlotData):
            self._write_value(obj_group, "xPoints", obj.xPoints)
            lines_group = obj_group.create_group("lines")
            for idx, line in enumerate(obj.lines):
                self._write_plot_line(lines_group, str(idx), line)
            return

        if isinstance(obj, ImshowData):
            self._write_value(obj_group, "xList", obj.xList)
            self._write_value(obj_group, "yList", obj.yList)
            self._write_value(obj_group, "data", obj.data)
            return

        raise TypeError(f"不支持的 plot 对象类型: {type(obj).__name__}")

    def _write_plot_object_list(self, group, name: str, data: list):
        root = group.create_group(name)
        root.attrs["type"] = "plot_list"
        for idx, item in enumerate(data):
            self._write_plot_object(root, str(idx), item)

    def write_dataset(self, dataset_path: str, data: Any, dtype=None):
        """写入一个额外 dataset 或 plot group，用于保存 analysis 等派生结果。"""
        if "/" not in dataset_path:
            group_path, name = "", dataset_path
        else:
            group_path, name = dataset_path.rsplit("/", 1)
        group = self.f.require_group(group_path) if group_path else self.f
        if name in group:
            del group[name]

        if isinstance(data, list) and all(isinstance(item, (PlotData, ImshowData)) for item in data):
            self._write_plot_object_list(group, name, data)
        elif isinstance(data, (PlotData, ImshowData)):
            self._write_plot_object(group, name, data)
        else:
            self._write_scalar_or_array(group, name, data, dtype=dtype)
        self.f.flush()
        
    def create_meta(self, manifest):
        meg = self.f.require_group("metadata")
        meg.create_dataset("manifest", data=np.string_(json.dumps(manifest, ensure_ascii=False).encode('utf-8')))
        self._meta_written = True
        self.f.flush()
    
    def update_meta(self, manifest):
        """更新元数据（用于实时更新进度）"""
        meg = self.f.require_group("metadata")
        if "manifest" in meg:
            del meg["manifest"]
        meg.create_dataset("manifest", data=np.string_(json.dumps(manifest, ensure_ascii=False).encode('utf-8')))
        self.f.flush()

    def close(self):
        self.f.flush()
        try:
            self.f.close()
        except:
            pass
    
    @property
    def current_count(self) -> int:
        return self._current_idx
    
    def get_written_data(self):
        """获取已写入的数据（用于增量读取）"""
        if self._dataset is None:
            return np.array([])
        return self._dataset[:self._current_idx]

def sindamp(x, a, omega, phi, c, tau):
    return a * np.exp(-x / tau) * np.sin(2*np.pi*omega * x + phi) + c

def estimate_frequency(x_data, y_data):
    y_fft = fft(y_data - np.mean(y_data))
    freq = fftfreq(len(x_data), x_data[1] - x_data[0])
    max_index = np.argmax(np.abs(y_fft[:len(y_fft) // 2]))
    return np.abs(freq[max_index])

from scipy.signal import argrelextrema

def peak_decay(x_data, y_data):
    # Find the local maxima (peaks) of the data
    peak_indices = argrelextrema(y_data, np.greater)[0]
    x_peaks = x_data[peak_indices]
    y_peaks = y_data[peak_indices]

    # Fit an exponential decay to the peak amplitudes to estimate tau
    lb = [0.0, 1e-12] 
    ub = [np.inf, np.inf]
    popt_decay, _ = curve_fit(lambda x, a, tau: a * np.exp(-x / tau), x_peaks, y_peaks, p0=[np.ptp(y_data), x_data[-1] / 2], bounds=(lb, ub), maxfev=20000)
    return popt_decay[1]

def sindamp_fit(x_data, y_data):
    """
    阻尼正弦拟合
    """
    A0 = np.ptp(y_data) / 2
    C0 = np.mean(y_data)
    phi0 = 0.0

    # ---- 频率 ----
    try:
        f0 = estimate_frequency(x_data, y_data)
        if not np.isfinite(f0) or f0 <= 0:
            raise ValueError
    except Exception:
        f0 = 0.1 / (x_data[-1] - x_data[0])  # 非振荡退化

    # ---- tau ----
    peak_idx = argrelextrema(y_data, np.greater)[0]
    if len(peak_idx) >= 3:
        tau0 = peak_decay(x_data, y_data)
    else:
        tau0 = (x_data[-1] - x_data[0]) / 2

    p0 = [A0, f0, phi0, C0, tau0]
    lb = [0.0, 0.0, -2*np.pi, -np.inf, 1e-12] 
    ub = [np.inf, np.inf,  2*np.pi,  np.inf, np.inf]
    # Fit the rabi_oscillation_decay function to the data using curve_fit
    popt, pcov = curve_fit(sindamp, x_data, y_data, p0=p0, bounds=(lb, ub), maxfev=20000)

    return {'a': popt[0], 'omega': popt[1], 'phi': popt[2], 'c': popt[3], 'tau': popt[4]}

def gaussian(x, a, mu, sigma, c):
    return a*np.exp(-(x-mu)**2/(2*sigma**2)) + c

def gaussian_fit(x_data, y_data):
    """
    高斯拟合
    """
    C0 = float(np.min(y_data))
    A0 = float(np.max(y_data) - C0)
    mu0 = float(x_data[np.argmax(y_data)])
    sigma0 = float((x_data.max() - x_data.min()) / 10.0)
    p0 = [A0, mu0, sigma0, C0]
    lb = [0.0, x_data.min(), 1e-12, -np.inf]
    ub = [np.inf, x_data.max(), np.inf,  np.inf]
    popt, pcov = curve_fit(gaussian, x_data, y_data, p0=p0, bounds=(lb, ub), maxfev=20000)
    return {'a': popt[0], 'mu': popt[1], 'sigma': popt[2], 'c': popt[3]}

def spectrum(x, fmax, vss, dv, d):
    return fmax * ((np.cos(np.pi*(x - vss) / dv) ** 2 + d*d*np.sin(np.pi*(x - vss) / dv) ** 2)) ** (1/4)

def spectrum_fit(x_data, y_data):
    """
    光谱拟合
    """
    fmax0 = float(np.max(y_data))
    vss0 = float(x_data[np.argmax(y_data)])
    dv0 = float((x_data.max() - x_data.min()) / 2)
    ymin = float(np.min(y_data))
    d0 = float(np.clip((ymin / fmax0)**2, 1e-3, 0.999))
    
    p0 = [fmax0, vss0, dv0, d0]
    
    lb = [0.0, x_data.min(), 1e-12, 1e-4]
    ub = [np.inf, x_data.max(), np.inf, 1.0]
    
    popt, pcov = curve_fit(spectrum, x_data, y_data, p0=p0, bounds=(lb, ub), maxfev=20000)
    return {'fmax': popt[0], 'vss': popt[1], 'dv': popt[2], 'd': popt[3]}

def t1(t, A, T1, c):
    return A*np.exp(-t / T1) + c

def t1_fit(x_data, y_data):
    """
    t1拟合
    """
    tail = max(3, len(y_data)//10)        # 末尾 10% 点数
    c0 = float(np.mean(y_data[-tail:]))
    A0 = float(y_data[0] - c0)
    yy = y_data - c0
    mask = yy > 0
    if np.sum(mask) >= 3:
        p = np.polyfit(x_data[mask], np.log(yy[mask]), 1)
        slope = p[0]
        T10 = float(-1.0/slope) if slope < 0 else float((x_data.max()-x_data.min())/2)
    else:
        T10 = float((x_data.max()-x_data.min())/2)
    p0 = [A0, T10, c0]
    
    lb = [-np.inf, 1e-12, -np.inf]
    ub = [ np.inf,  np.inf,  np.inf]
    
    popt, pcov = curve_fit(t1, x_data, y_data, p0=p0, bounds=(lb, ub), maxfev=20000)
    return {'A': popt[0], 'T1': popt[1], 'c': popt[2]}


def ramsey(t, A, B, Tphi, tramsey):
    return A*np.exp(-t / 200 - (t/Tphi)**2) * np.cos(2*np.pi*t/tramsey) + B

def ramsey_fit(x_data, y_data):
    """
    t2拟合
    """
    tail = max(3, len(y_data)//10)
    B0 = float(np.mean(y_data[-tail:]))
    A0 = float(np.max(np.abs(y_data - B0)))
    dt = np.median(np.diff(x_data))
    Y = np.fft.rfft((y_data - B0))
    freq = np.fft.rfftfreq(len(x_data), d=dt)
    k = np.argmax(np.abs(Y[1:])) + 1
    f0 = float(freq[k])
    tramsey0 = float(1.0 / f0) if f0 > 0 else float((x_data.max()-x_data.min())/5)
    tr = float(x_data.max() - x_data.min())
    Tphi0 = tr / 2
    p0 = [A0, B0, Tphi0, tramsey0]
    lb = [-np.inf, -np.inf, 1e-12, 1e-12]
    ub = [ np.inf,  np.inf, np.inf,  np.inf]

    popt, pcov = curve_fit(ramsey, x_data, y_data, p0=p0, bounds=(lb, ub), maxfev=20000)
    return {'A': popt[0], 'B': popt[1], 'Tphi': popt[2], 'tramsey': popt[3]}



def fidelity_fit(m, p, A, B):
    return np.exp(np.log(A)+m*np.log(p)) + B

def result_fit(x_data, y_data, method, dense_num=1000):
    
    x_data = np.asarray(x_data)
    y_data = np.asarray(y_data)
    x_dense = np.linspace(x_data.min(), x_data.max(), dense_num)
    
    if method == FitMethodSchema.sindamp:
        para = sindamp_fit(x_data, y_data) 
        y_dense = sindamp(x_dense, **para)   
    elif method == FitMethodSchema.gaussian:
        para = gaussian_fit(x_data, y_data)
        y_dense = gaussian(x_dense, **para)
    elif method == FitMethodSchema.spectrum:
        para = spectrum_fit(x_data, y_data)
        y_dense = spectrum(x_dense, **para)
    elif method == FitMethodSchema.t1:
        para = t1_fit(x_data, y_data)
        y_dense = t1(x_dense, **para)
    elif method == FitMethodSchema.ramsey:
        para = ramsey_fit(x_data, y_data)
        y_dense = ramsey(x_dense, **para)
    else:
        raise ValueError(f"不存在拟合方法：{method}")
    
    return x_dense, y_dense, para

def read_data(path: str):
    
    res = {}
    with h5py.File(path, "r") as f:
        if "metadata/manifest" in f:
            manifest = f["metadata/manifest"][()]
            pd = json.loads(manifest.decode() if isinstance(manifest, bytes) else manifest) 
            res['progress'] = pd
                
        if 'results/count' in f:
            count = f["results/count"][:]
            res['data'] = count
            
    return res




































































@dataclass
class PlotLine:
    type: str
    legend: str | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class PlotLine_xAx(PlotLine):
    x: Any = None
    type: str = "xAxis"


@dataclass
class PlotLine_yAx(PlotLine):
    y: Any = None
    type: str = "yAxis"


@dataclass
class PlotLine_Points(PlotLine):
    y: Any = None
    x: Any = None
    type: str = "points"


class PlotData:
    def __init__(self, title="", xLabel="", yLabel="", xPoints=None):
        self.type = "plot"
        self.title = title
        self.xLabel = xLabel
        self.yLabel = yLabel
        self.xPoints = xPoints
        self.lines = []

    def _append(self,line:PlotLine):
        if not isinstance(line, PlotLine):
            raise TypeError(f"line 必须是 PlotLine，实际是 {type(line).__name__}")
        self.lines.append(line)
        return line

    def appendPoints(self,yList,legend:str=None,xList=None):
        if xList is None:
            xList = self.xPoints
        return self._append(PlotLine_Points(legend=legend, x=xList, y=yList))

    def appendAxis(self, value, axis: str = "x", legend: str = None):
        if axis == "x":
            return self._append(PlotLine_xAx(legend=legend, x=value))
        if axis == "y":
            return self._append(PlotLine_yAx(legend=legend, y=value))
        raise ValueError("axis 只能是 'x' 或 'y'")

    def to_dict(self):
        return {
            "type": self.type,
            "title": self.title,
            "xLabel": self.xLabel,
            "yLabel": self.yLabel,
            "xPoints": self.xPoints,
            "lines": [line.to_dict() for line in self.lines],
        }


class ImshowData:
    def __init__(
        self,
        title="",
        xLabel="",
        yLabel="",
        zLabel="",
        xList=None,
        yList=None,
        data=None,
    ):
        self.type = "imshow"
        self.title = title
        self.xLabel = xLabel
        self.yLabel = yLabel
        self.zLabel = zLabel
        self.xList = xList
        self.yList = yList
        self.data = data

    def to_dict(self):
        return {
            "type": self.type,
            "title": self.title,
            "xLabel": self.xLabel,
            "yLabel": self.yLabel,
            "zLabel": self.zLabel,
            "xList": self.xList,
            "yList": self.yList,
            "data": self.data,
        }




