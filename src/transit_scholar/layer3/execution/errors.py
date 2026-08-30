"""Stable errors for Layer3 Stage2 execution identities."""

from __future__ import annotations


class ExecutionError(RuntimeError):
    """Base error for framework-neutral execution lifecycle operations."""

    code = "execution_error"


class InvalidExecutionInputError(ExecutionError):
    """A supplied run, session, goal, question, or status is invalid."""

    code = "invalid_execution_input"


class AgentRunNotFoundError(ExecutionError):
    """No AgentRun exists with the requested identifier."""

    code = "agent_run_not_found"


class ResearchSessionNotFoundError(ExecutionError):
    """No ResearchSession exists with the requested identifier."""

    code = "research_session_not_found"


class ResearchSessionOwnershipError(ExecutionError):
    """A session was accessed through an AgentRun that does not own it."""

    code = "research_session_wrong_agent_run"


__all__ = [
    "ExecutionError",
    "InvalidExecutionInputError",
    "AgentRunNotFoundError",
    "ResearchSessionNotFoundError",
    "ResearchSessionOwnershipError",
]
