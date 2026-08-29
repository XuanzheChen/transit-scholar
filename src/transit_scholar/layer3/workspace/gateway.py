"""Backward-compatible re-export of the Workspace knowledge gateway.

The Workspace-bound knowledge access implementation (``WorkspaceKnowledgeGateway``
and its ``L2S1EvidenceDelegate``) lives in the dedicated ``layer3.knowledge``
package (REQ-010..REQ-012 / AC-018..AC-024). This module keeps the historical
import path ``transit_scholar.layer3.workspace.gateway`` working so existing
control-plane consumers and tests do not need to change.
"""

from __future__ import annotations

from transit_scholar.layer3.knowledge.gateway import (  # noqa: F401
    L2S1EvidenceDelegate,
    WorkspaceKnowledgeGateway,
)

__all__ = ["L2S1EvidenceDelegate", "WorkspaceKnowledgeGateway"]