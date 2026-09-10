"""Request-scoped dependencies for the formal API."""
from fastapi import Request
from contextlib import contextmanager


@contextmanager
def exclusive_workspace_mutation(request, product, workspace_id):
    """Share Prompt admission while retaining authoritative Workspace conflicts."""
    from .runtime import RunnerBusyError
    from .errors import ApiError
    try:
        reservation = request.app.state.execution_manager.reserve()
    except RunnerBusyError as exc:
        product._guard_workspace_mutation(workspace_id)
        raise ApiError("RUNNER_BUSY", "Another AgentRun is already executing", {}, 409) from exc
    try:
        yield
    finally:
        reservation.release()


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
