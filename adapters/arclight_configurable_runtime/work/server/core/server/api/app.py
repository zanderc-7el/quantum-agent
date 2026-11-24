from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from .exception_handlers import register_handlers
from . import exp, device
#from . import user, task, algorithm, device, params, experiment, image

def create_app() -> FastAPI:
    app = FastAPI(title="Quantum Lab API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 添加 Gzip 中间件，设置最小压缩阈值（例如 500 字节）
    #app.add_middleware(GZipMiddleware, minimum_size=500)
    register_handlers(app)
    # 注册路由
    app.include_router(exp.router)
    app.include_router(device.router)
    return app