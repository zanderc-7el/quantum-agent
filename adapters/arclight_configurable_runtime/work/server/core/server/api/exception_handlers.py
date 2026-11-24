from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ..exceptions import AppError

def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "type": exc.__class__.__name__},
        )