"""HTTP transport for Product-layer conversations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from transit_scholar.api.dependencies import get_product
from transit_scholar.api.errors import ApiError
from transit_scholar.api.schemas.conversations import PublicAssistantResponse
from transit_scholar.api.runtime import RunnerBusyError
from transit_scholar.api.schemas import (
    ConversationCreateRequest, ConversationListResponse, ConversationResponse,
    ConversationSummaryResponse, TurnCreateRequest, TurnResponse,
    TurnSubmissionResponse,
)
from transit_scholar.product.errors import (
    ProductConflictError,
    ProductNotFoundError,
    ProductValidationError,
)


router = APIRouter(prefix="/api/v1")


def _summary(conversation) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        conversation_id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        created_at=conversation.created_at,
    )

def _public_assistant_response(response: object) -> PublicAssistantResponse | None:
    if not isinstance(response, dict):
        return None
    public = {}
    for key in ("answer_text", "answer"):
        if isinstance(response.get(key), str):
            public[key] = response[key]
    for key in ("citation_references", "citations"):
        if isinstance(response.get(key), list):
            public[key] = [value for value in response[key] if isinstance(value, str)]
    return PublicAssistantResponse.model_validate(public)


def _turn(turn, product=None) -> TurnResponse:
    response = _public_assistant_response(turn.final_assistant_response)
    return TurnResponse(
        turn_id=turn.id,
        conversation_id=turn.conversation_id,
        sequence=turn.sequence,
        user_message=turn.user_message,
        resolved_user_goal=turn.resolved_user_goal,
        agent_run_id=turn.agent_run_id,
        status=turn.status,
        assistant_response=response,
        final_answer=_final_answer(turn.final_assistant_response),
        answer_citations=(
            product.answer_citations(turn.agent_run_id, turn.final_assistant_response)
            if product is not None else []
        ),
        error_message=turn.error_message,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
    )


def _final_answer(response: object) -> str | None:
    if not isinstance(response, dict):
        return None
    for key in ("answer_text", "answer"):
        value = response.get(key)
        if isinstance(value, str):
            return value
    return None


@router.get("/workspaces/{workspace_id}/conversations", response_model=ConversationListResponse)
def list_conversations(workspace_id: str, product=Depends(get_product)):
    try:
        return ConversationListResponse(items=[_summary(row) for row in product.list_conversations(workspace_id)])
    except ProductNotFoundError as exc:
        raise ApiError("NOT_FOUND", str(exc), {"workspace_id": workspace_id}, 404) from exc


@router.post(
    "/workspaces/{workspace_id}/conversations",
    response_model=ConversationSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(workspace_id: str, payload: ConversationCreateRequest, product=Depends(get_product)):
    try:
        return _summary(product.create_conversation(workspace_id, payload.title))
    except ProductNotFoundError as exc:
        raise ApiError("NOT_FOUND", str(exc), {"workspace_id": workspace_id}, 404) from exc
    except ProductConflictError as exc:
        raise ApiError("WORKSPACE_NOT_AVAILABLE", str(exc), {"workspace_id": workspace_id}, 409) from exc


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def read_conversation(conversation_id: str, product=Depends(get_product)):
    try:
        view = product.read_conversation(conversation_id)
    except ProductNotFoundError as exc:
        raise ApiError("NOT_FOUND", str(exc), {"conversation_id": conversation_id}, 404) from exc
    conversation = product.get_conversation(conversation_id)
    return ConversationResponse(
        **_summary(conversation).model_dump(),
        turns=[
            TurnResponse(
                turn_id=turn["turn_id"], conversation_id=conversation_id,
                sequence=turn["sequence"], user_message=turn["user_message"],
                resolved_user_goal=turn["resolved_user_goal"], agent_run_id=turn["agent_run_id"],
                status=turn["status"], assistant_response=_public_assistant_response(turn["assistant_response"]),
                final_answer=turn.get("final_answer"), answer_citations=turn.get("answer_citations", []),
                error_message=turn["error_message"],
            )
            for turn in view["turns"]
        ],
    )


@router.post(
    "/conversations/{conversation_id}/turns",
    response_model=TurnSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_turn(request: Request, conversation_id: str, payload: TurnCreateRequest, product=Depends(get_product)):
    if not request.app.state.runtime_context.agent_runtime_available:
        raise ApiError("PROVIDER_UNAVAILABLE", "Provider is temporarily unavailable", {}, 503)
    manager = request.app.state.execution_manager
    try:
        reservation = manager.reserve()
    except RunnerBusyError as exc:
        raise ApiError("RUNNER_BUSY", "Another AgentRun is already executing", {}, 409)
    prepared = None
    scheduled = False
    try:
        prepared = product.prepare_message(conversation_id, payload.message)
        manager.submit_reserved(reservation, prepared.agent_run_id)
        scheduled = True
    except ProductNotFoundError as exc:
        raise ApiError("NOT_FOUND", str(exc), {"conversation_id": conversation_id}, 404) from exc
    except ProductValidationError as exc:
        raise ApiError("VALIDATION_ERROR", str(exc), {"conversation_id": conversation_id}, 422) from exc
    except ProductConflictError as exc:
        raise ApiError("CONVERSATION_CONFLICT", str(exc), {"conversation_id": conversation_id}, 409) from exc
    except Exception as exc:
        if prepared is not None:
            product.discard_prepared_message(prepared)
        raise ApiError("INTERNAL_ERROR", "Unable to schedule AgentRun", {}, 500) from exc
    finally:
        if not scheduled:
            reservation.release()
    return TurnSubmissionResponse(turn_id=prepared.turn_id, agent_run_id=prepared.agent_run_id)


@router.get("/turns/{turn_id}", response_model=TurnResponse)
def read_turn(turn_id: str, product=Depends(get_product)):
    turn = product.read_turn(turn_id)
    if turn is None:
        raise ApiError("NOT_FOUND", "turn not found", {"turn_id": turn_id}, 404)
    return _turn(turn, product)
