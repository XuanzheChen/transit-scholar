"""Request-scoped dependencies for the formal API."""
from fastapi import Request


def get_product(request: Request):
    """Yield a request-owned facade configured by the application factory."""
    runtime_context = getattr(request.app.state, "runtime_context", None)
    if runtime_context is None:
        raise RuntimeError("API runtime context has not been initialized")
    product = runtime_context.create_product()
    try:
        yield product
    finally:
        product.close()
