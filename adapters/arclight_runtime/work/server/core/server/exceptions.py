from __future__ import annotations

class AppError(Exception):
    pass

class ValidationError(AppError):
    pass

class NotFoundError(AppError):
    pass

class ConflictError(AppError):
    pass

class AuthError(AppError):
    pass

class DeviceOfflineError(AppError):
    pass

class TaskStateError(AppError):
    pass

class DBError(AppError):
    pass

def require(condition: bool, message: str, exc_cls: type[AppError] = ValidationError) -> None:
    if not condition:
        raise exc_cls(message)


