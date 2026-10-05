"""Typed API errors and a consistent JSON error envelope.

Every handled error returns ``{"error": {"code", "message", "fields"?}}`` so web
and mobile can render messages and field-level validation uniformly.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, fields: dict[str, str] | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.fields = fields
        if code:
            self.code = code


class ValidationError(ApiError):
    status_code = 422
    code = "validation_error"


class AuthError(ApiError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    status_code = 403
    code = "forbidden"


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"

    def __init__(self, entity: str = "Resource"):
        super().__init__(f"{entity} not found.")


class ConflictError(ApiError):
    status_code = 409
    code = "conflict"


def _body(code: str, message: str, fields: dict[str, str] | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.fields))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
            fields[".".join(loc) or "_"] = err.get("msg", "Invalid value")
        return JSONResponse(
            status_code=422,
            content=_body("validation_error", "Please fix the highlighted fields.", fields),
        )
