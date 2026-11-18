"""
后处理与拟合插件的热加载管理。

- 通过「文件路径 + 内容哈希」缓存模块，文件变更时自动重新加载。
- 插件为独立 Python 脚本，执行与主进程同权，仅适合可信配置/脚本路径。
"""
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, Tuple

import numpy as np

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_plugin_module(
    script_path: str,
    module_prefix: str,
    required_callables: Dict[str, str],
    cache: Dict[Tuple[str, str], ModuleType],
    not_found_error: str = "文件不存在",
) -> ModuleType:
    """
    按路径加载插件模块并做入口校验，结果按 (path, hash) 缓存。

    - module_prefix: 用于生成唯一模块名，避免与其它插件冲突。
    - required_callables: 必须存在的可调用属性名 -> 用于报错的描述，如 {"postprocess": "postprocess(raw, meta=None)"}。
    """
    path = Path(script_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"{not_found_error}: {path}")

    file_hash = _file_sha256(path)
    key = (str(path), file_hash)
    if key in cache:
        return cache[key]

    module_name = f"{module_prefix}_{path.stem}_{file_hash[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"插件加载失败: spec/loader 为空 ({path})")

    module = importlib.util.module_from_spec(spec)
    # 先注册再 exec，便于失败时从 sys.modules 清理
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)  # 执行用户代码，仅用于可信路径
    except Exception as e:
        sys.modules.pop(module_name, None)
        raise RuntimeError(f"插件执行失败: {e}") from e

    for attr, desc in required_callables.items():
        fn = getattr(module, attr, None)
        if not callable(fn):
            sys.modules.pop(module_name, None)
            raise AttributeError(f"插件必须提供可调用入口: {desc}")

    cache[key] = module
    return module


class PostprocessManager:
    """
    后处理插件热加载管理器。

    - 约定：脚本中需定义 postprocess(raw, meta=None)，返回处理后的数据。
    - 通过 (路径, 文件哈希) 缓存，文件内容变更时自动重新加载。
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], ModuleType] = {}

    def load(self, script_path: str) -> ModuleType:
        return _load_plugin_module(
            script_path,
            module_prefix="postprocess",
            required_callables={"postprocess": "postprocess(raw, meta=None)"},
            cache=self._cache,
            not_found_error="后处理脚本不存在",
        )

    def run(
        self,
        script_path: str,
        raw: Any,
        meta: Dict[str, Any] | None = None,
        **kwargs: Any
    ) -> Any:
        module = self.load(script_path)
        fn: Callable = module.postprocess
        return fn(raw, meta=meta, **kwargs)


class AnalysisManager:
    """
    整次扫描结果分析插件热加载管理器。

    - 约定：脚本中需定义 analyze(results, config, spec, PlotData, ImshowData)，返回分析后的数据。
    - 和 PostprocessManager 分开，避免单点 postprocess 与整次扫描 analysis 语义混淆。
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], ModuleType] = {}

    def load(self, script_path: str) -> ModuleType:
        return _load_plugin_module(
            script_path,
            module_prefix="analysis",
            required_callables={
                "analyze": "analyze(results, config, spec, PlotData, ImshowData)"
            },
            cache=self._cache,
            not_found_error="分析脚本不存在",
        )

    def run(
        self,
        script_path: str,
        results: Any,
        config: Any,
        spec: Any,
        PlotData: Any,
        ImshowData: Any,
    ) -> Any:
        module = self.load(script_path)
        fn: Callable = module.analyze
        return fn(results, config, spec, PlotData, ImshowData)


class FitPluginManager:
    """
    拟合插件热加载管理器。

    - 约定：脚本中需定义 fit(x, y) -> dict、model(x, **params) -> y。
    - 通过 (路径, 文件哈希) 缓存，文件内容变更时自动重新加载。
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], ModuleType] = {}

    def load(self, script_path: str) -> ModuleType:
        return _load_plugin_module(
            script_path,
            module_prefix="fitplugin",
            required_callables={
                "fit": "fit(x, y) -> dict",
                "model": "model(x, **params) -> y",
            },
            cache=self._cache,
            not_found_error="拟合脚本不存在",
        )

    def fit(
        self,
        script_path: str,
        x_data: Any,
        y_data: Any,
        dense_num: int = 1000,
    ) -> Tuple[Any, Any, Dict[str, float]]:
        x = np.asarray(x_data, dtype=float)
        y = np.asarray(y_data, dtype=float)

        module = self.load(script_path)
        params = module.fit(x, y)
        if not isinstance(params, dict):
            raise RuntimeError("fit() 必须返回 dict[str, float]")

        x_dense = np.linspace(x.min(), x.max(), dense_num)
        y_dense = module.model(x_dense, **params)

        return x_dense, y_dense, params
