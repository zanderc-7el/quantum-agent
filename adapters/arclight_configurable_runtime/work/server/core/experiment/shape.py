import numpy as np
from typing import Callable, Dict
import math
# ----------------------------
# Shape registry
# ----------------------------
_SHAPE_REGISTRY: Dict[str, Callable[..., np.ndarray]] = {}


def register_shape(name: str):
    """
    波形形状修饰器
    """
    def _decorator(fn: Callable[..., np.ndarray]):
        key = str(name)
        if key in _SHAPE_REGISTRY:
            raise KeyError(f"shape '{key}' already registered by {_SHAPE_REGISTRY[key]}")
        _SHAPE_REGISTRY[key] = fn
        return fn
    return _decorator


def get_shape_fn(name: str) -> Callable[..., np.ndarray]:
    key = str(name)
    if key not in _SHAPE_REGISTRY:
        available = ", ".join(sorted(_SHAPE_REGISTRY.keys()))
        raise KeyError(f"unknown shape '{key}'. available: {available}")
    return _SHAPE_REGISTRY[key]


# ----------------------------
# Built-in shapes
# ----------------------------

@register_shape("Gaussian")
def shape_gaussian(t: np.ndarray, T: float, A: float, **kwargs) -> np.ndarray:
    """gaussian 中心点平移到时间中段 """
    sigma = kwargs.get("sigma", 1)
    y = np.zeros_like(t)
    m = t <= T
    tc = T / 2.0
    y[m] = A * np.exp(- (t[m] - tc)**2 / (2.0 * sigma**2))
    return y

@register_shape("Sin2")
def shape_sin2(t: np.ndarray,  T: float, A: float, **kwargs) -> np.ndarray:
    """sin^2 """
    y = np.zeros_like(t)
    m = t <= T
    y[m] = A * np.sin(np.pi * t[m] / T)**2
    return y


@register_shape("CosPulse")
def shape_cos(t: np.ndarray, T: float, A: float, **kwargs) -> np.ndarray:
    """cos"""
    return shape_sin2(t, T, A)

@register_shape("DC")
def shape_dc(t: np.ndarray, T: float, A: float, **kwargs) -> np.ndarray:
    """平顶 + 边沿平滑"""
    trise = kwargs.get("trise", 0)
    tfall = kwargs.get("tfall", 0)
    y = np.zeros_like(t)
    # rise
    if trise > 0:
        m = t < trise
        y[m] = 0.5 * A * (1 - np.cos(np.pi * (t[m] / trise)))
    # flat
    t_flat = T - tfall
    m = (trise <= t) & (t <= t_flat)
    y[m] = A
    # fall
    if tfall > 0:
        m = ( t_flat < t) & (t <= T)
        y[m] = 0.5 * A * (1 + np.cos(np.pi * ((t[m] - t_flat) / tfall)))
    return y

@register_shape("ERF_BUMP")
def shape_erf_bump(t: np.ndarray, T: float, A: float, **kwargs):
    """erf S 曲线拼成的平滑脉冲：
    y(t) = A/2 * [erf((t - t_on)/tau) - erf((t - t_off)/tau)]
    其中 t_on = guard, t_off = T - guard，使得在 t=0 与 t=T 处近似为 0。
    建议 guard ≈ 3*tau（或更大）以保证端点极小且导数也极小。
    """
    tau = kwargs.get("tau", 1)
    guard = kwargs.get("guard", 0)
    t_on = max(guard, 0.0)
    t_off = T - max(guard, 0.0)
    if t_off <= t_on:
        return np.zeros_like(t)
    erf_vec = np.vectorize(math.erf)
    return 0.5 * A * (erf_vec((t - t_on)/tau) - erf_vec((t - t_off)/tau))


@register_shape("TANH_BUMP")
def shape_tanh_bump(t: np.ndarray, T: float, A: float, **kwargs) -> np.ndarray:
    """tanh S 曲线拼成的平滑脉冲：
    y(t) = A/2 * [tanh((t - t_on)/s) - tanh((t - t_off)/s)]
    其中 t_on = guard, t_off = T - guard，使得在 t=0 与 t=T 处近似为 0。
    建议 guard ≈ 3*s（或更大）。
    """
    s = kwargs.get("s", 1)
    guard = kwargs.get("guard", 0)
    t_on = max(guard, 0.0)
    t_off = T - max(guard, 0.0)
    if t_off <= t_on:
        return np.zeros_like(t)
    return 0.5 * A * (np.tanh((t - t_on)/s) - np.tanh((t - t_off)/s))