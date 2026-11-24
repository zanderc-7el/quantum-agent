"""core HTTP 路由统一鉴权依赖。

核心对外 HTTP 接口（/exp、/devices）目前无鉴权即挂载于主应用，notebook/本机进程
可直接调用。这里统一补平台 JWT 鉴权：进程内网关调用不经 HTTP 不受影响；core 被
独立复用（无 webapi 的硬件联调环境）时自动降级为不鉴权。
"""
from typing import Any, List

from fastapi import Depends


def _auth_deps() -> List[Any]:
    try:
        from webapi.auth.deps import get_current_user

        return [Depends(get_current_user)]
    except Exception:  # noqa: BLE001 —— 独立复用 core 时降级为开放
        return []
