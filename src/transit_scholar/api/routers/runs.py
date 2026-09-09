from fastapi import APIRouter, Depends, Request, Query, status

from transit_scholar.api.dependencies import get_product
from transit_scholar.api.errors import ApiError
from transit_scholar.api.runtime import RunnerBusyError
from transit_scholar.api.schemas.run_control import RunStateResponse, TimelineResponse, TimelineEventResponse
from transit_scholar.layer3.execution.errors import AgentRunNotFoundError
from transit_scholar.product.errors import ProductConflictError


def _state(product, agent_run_id: str) -> RunStateResponse:
    try:
        return RunStateResponse.model_validate(product.read_run_state(agent_run_id).__dict__)
    except AgentRunNotFoundError as exc:
        raise ApiError("NOT_FOUND", "agent run not found", {"agent_run_id": agent_run_id}, 404) from exc


router = APIRouter(prefix="/api/v1/runs")


@router.get("/{agent_run_id}", response_model=RunStateResponse)
def read_run(agent_run_id: str, product=Depends(get_product)):
    return _state(product, agent_run_id)


@router.get("/{agent_run_id}/timeline", response_model=TimelineResponse)
def read_timeline(
    agent_run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    product=Depends(get_product),
):
    try:
        events = product.read_run_timeline(agent_run_id, after_sequence)
    except AgentRunNotFoundError as exc:
        raise ApiError("NOT_FOUND", "agent run not found", {"agent_run_id": agent_run_id}, 404) from exc
    projected = [TimelineEventResponse.model_validate(event) for event in events]
    return TimelineResponse(events=projected, next_sequence=(projected[-1].sequence if projected else after_sequence))


@router.post("/{agent_run_id}/pause", response_model=RunStateResponse)
def pause_run(agent_run_id: str, product=Depends(get_product)):
    try:
        product.request_pause(agent_run_id)
        return _state(product, agent_run_id)
    except AgentRunNotFoundError as exc:
        raise ApiError("NOT_FOUND", "agent run not found", {"agent_run_id": agent_run_id}, 404) from exc
    except ProductConflictError as exc:
        raise ApiError("RUN_STATE_CONFLICT", str(exc), {"agent_run_id": agent_run_id}, 409) from exc


@router.post("/{agent_run_id}/resume", response_model=RunStateResponse, status_code=status.HTTP_202_ACCEPTED)
def resume_run(request: Request, agent_run_id: str, product=Depends(get_product)):
    try:
        current = product.read_run_state(agent_run_id)
        if current.status != "paused":
            raise ApiError("RUN_STATE_CONFLICT", "resume_run is only allowed for paused runs", {"agent_run_id": agent_run_id}, 409)
        if not request.app.state.runtime_context.agent_runtime_available:
            raise ApiError("PROVIDER_UNAVAILABLE", "Agent runtime is unavailable", {}, 503)
        request.app.state.execution_manager.submit(agent_run_id, resume=True)
        return RunStateResponse.model_validate(current.__dict__)
    except RunnerBusyError as exc:
        raise ApiError("RUNNER_BUSY", "Another AgentRun is already executing", {}, 409) from exc
    except AgentRunNotFoundError as exc:
        raise ApiError("NOT_FOUND", "agent run not found", {"agent_run_id": agent_run_id}, 404) from exc
    except ProductConflictError as exc:
        raise ApiError("RUN_STATE_CONFLICT", str(exc), {"agent_run_id": agent_run_id}, 409) from exc
