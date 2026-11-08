"""Erreurs métier avec code HTTP et code stable pour l'API."""
from .jobs import UserError


class AppError(UserError):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if status:
            self.status = status
        self.retry_after = retry_after


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Throttled(AppError):
    status, code = 429, "rate_limited"


class Conflict(AppError):
    status, code = 409, "conflict"
