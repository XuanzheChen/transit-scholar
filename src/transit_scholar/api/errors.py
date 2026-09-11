from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from transit_scholar.product.errors import (
    ProductConflictError, ProductNotFoundError, ProductValidationError,
    ProductPayloadTooLargeError, ProviderUnavailableError,
)


class ApiError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None, status_code: int = 400):
        self.code, self.message, self.details, self.status_code = code, message, details or {}, status_code


def workspace_error_status(code: str) -> int:
    """Shared public taxonomy for Workspace, Schema and Wiki domain errors."""
    if code in {"workspace_not_found", "paper_not_found", "paper_not_member"}:
        return 404
    if code in {
        "workspace_not_active", "workspace_busy", "workspace_changed",
        "invalid_state",
        "schema_binding_immutable", "schema_disabled", "schema_missing",
        "schema_binding_mismatch", "wiki_unsupported", "wiki_missing",
        "wiki_stale", "wiki_corrupt", "empty_membership",
    }:
        return 409
    if code == "invalid_workspace_input":
        return 422
    return 500


def workspace_error_message(code: str, *, fallback: str = "Workspace operation failed") -> str:
    """Domain exception text may wrap storage diagnostics, even for 4xx."""
    return {
        "workspace_not_found": "Workspace not found",
        "paper_not_found": "Paper not found",
        "paper_not_member": "Paper is not a workspace member",
        "invalid_state": "Paper state does not permit workspace membership",
        "workspace_not_active": "Workspace is not active",
        "workspace_busy": "Workspace has a non-terminal AgentRun",
        "workspace_changed": "Workspace has changed; reload before retrying",
        "schema_binding_immutable": "Workspace schema binding cannot be changed",
        "schema_disabled": "Workspace schema is disabled",
        "schema_missing": "Workspace schema content is unavailable",
        "schema_binding_mismatch": "Workspace schema binding does not match",
        "wiki_unsupported": "Workspace Wiki is unsupported",
        "wiki_missing": "Workspace Wiki content is unavailable",
        "wiki_stale": "Workspace Wiki must be rebuilt",
        "wiki_corrupt": "Workspace Wiki content is unusable",
        "empty_membership": "Workspace Wiki requires member papers",
        "invalid_workspace_input": "Workspace input is invalid",
    }.get(code, fallback)


def install_error_handlers(app):
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"issues": exc.errors()},
                }
            },
        )

    @app.exception_handler(ProductNotFoundError)
    async def product_not_found_handler(request: Request, exc: ProductNotFoundError):
        return _product_response("NOT_FOUND", "Resource not found", 404)

    @app.exception_handler(ProductConflictError)
    async def product_conflict_handler(request: Request, exc: ProductConflictError):
        return _product_response("CONFLICT", "Operation conflicts with the current resource state", 409)

    @app.exception_handler(ProductValidationError)
    async def product_validation_handler(request: Request, exc: ProductValidationError):
        return _product_response("VALIDATION_ERROR", "Request is invalid", 422)

    @app.exception_handler(ProductPayloadTooLargeError)
    async def product_payload_too_large_handler(request: Request, exc: ProductPayloadTooLargeError):
        return _product_response("UPLOAD_TOO_LARGE", "Payload exceeds the configured size limit", 413)

    @app.exception_handler(ProviderUnavailableError)
    async def provider_unavailable_handler(request: Request, exc: ProviderUnavailableError):
        return _product_response("PROVIDER_UNAVAILABLE", "Provider is temporarily unavailable", 503)

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, exc: Exception):
        return _product_response("INTERNAL_ERROR", "An unexpected internal error occurred", 500)


def _product_response(code: str, message: str, status_code: int):
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "details": {}}})
