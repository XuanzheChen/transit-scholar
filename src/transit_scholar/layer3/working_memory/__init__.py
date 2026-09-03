"""Unified, non-owning access to current-run working state."""

from .facade import WorkingMemory, WorkingMemoryBoundaryError, WorkingMemoryFacade

__all__ = ["WorkingMemory", "WorkingMemoryBoundaryError", "WorkingMemoryFacade"]
