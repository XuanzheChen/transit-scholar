"""Layer3 Stage1 Workspace-bound knowledge access (REQ-010/REQ-011/REQ-012).

The ``WorkspaceKnowledgeGateway`` is the preferred runtime-facing
Workspace-bound access object (AC-022): it is constructed with the Workspace
identity (and optionally an expected Grounded revision) and exposes normalized
read/search operations for visible Papers, member-Paper L2S1 evidence, the
Workspace-owned Schema content (bound mode) and the Workspace-owned Base Wiki
— every call revalidates the authoritative Workspace lifecycle, revision and
membership state in code (REQ-012) and delegates to the existing lower-layer
public APIs instead of reimplementing them (REQ-010 / AC-024).

This package is the stable upper-layer import surface; the gateway also
remains importable from ``transit_scholar.layer3.workspace`` for backward
compatibility with the control-plane package.
"""

from .gateway import L2S1EvidenceDelegate, WorkspaceKnowledgeGateway

__all__ = ["WorkspaceKnowledgeGateway", "L2S1EvidenceDelegate"]