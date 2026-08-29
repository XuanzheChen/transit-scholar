"""Workspace-specific derived storage roots (Layer3 Stage1, REQ-004..REQ-006).

Workspace-owned heavy derived artifacts (Schema runs and Base Wiki snapshots)
are file-backed and MUST live under a Workspace-specific storage boundary:

- persisted SchemaInstance content / schema runs / current pointers go under
  ``<base>/<workspace_id>/schemas/``;
- Base Wiki manifests, pages, entities, links and indexes go under
  ``<base>/<workspace_id>/wiki/``.

The default base is ``Settings.data_root / layer3 / workspaces`` (matching
the storage layout recommended by the Layer3 Stage1 contract). Callers may
inject an isolated ``data_root`` or ``base_dir`` for tests.

This module only *derives* roots and constructs the existing L2S2
``SchemaRunStorage`` / L2S3 ``WikiStore`` implementations against them
(storage-root injection, REQ-006); it never reimplements Schema run or Wiki
snapshot persistence.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from transit_scholar.layer3.workspace.errors import InvalidWorkspaceInputError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer2.schema_extraction.persistence import SchemaRunStorage
    from transit_scholar.layer2.wiki.models import WorkspaceContext
    from transit_scholar.layer2.wiki.store import WikiStore

#: Sub-directory of ``data_root`` holding all Workspace-derived storage.
WORKSPACES_DIR_NAME = "workspaces"

#: Derived sub-directories inside one Workspace's storage boundary.
SCHEMAS_DIR_NAME = "schemas"
WIKI_DIR_NAME = "wiki"


def default_workspace_base_dir() -> Path:
    """Default derived-storage base: ``data_root/layer3/workspaces``.

    Imports the project settings lazily (mirroring the L2S2 persistence
    default-root pattern) so importing this module stays cheap.
    """
    from transit_scholar.config import settings  # noqa: PLC0415 - lazy import

    return Path(settings.data_root) / "layer3" / WORKSPACES_DIR_NAME


def _require_safe_workspace_id(workspace_id: str) -> str:
    """Validate a Workspace identifier before it becomes a path component.

    The identifier is persisted by the control plane (typically a uuid hex
    string), but code-enforced validation guarantees a caller-supplied
    identifier can never escape the derived-storage boundary.
    """
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise InvalidWorkspaceInputError("workspace_id must be a non-empty string")
    if workspace_id != workspace_id.strip():
        raise InvalidWorkspaceInputError("workspace_id must not have surrounding whitespace")
    if (
        workspace_id in {".", ".."}
        or "/" in workspace_id
        or "\\" in workspace_id
        or ":" in workspace_id
        or any(ord(character) < 32 for character in workspace_id)
    ):
        raise InvalidWorkspaceInputError(
            f"workspace_id {workspace_id!r} is unsafe for a derived storage path"
        )
    return workspace_id


def workspace_layout(
    workspace_id: str,
    *,
    data_root: Path | str | None = None,
    base_dir: Path | str | None = None,
) -> "WorkspaceStorageLayout":
    """Resolve the derived-storage layout for one Workspace.

    Exactly one of ``data_root`` (a root whose ``layer3/workspaces`` child is
    used) or ``base_dir`` (the direct parent of per-Workspace directories) may
    be supplied; when neither is given the project settings data root is used.
    """
    if data_root is not None and base_dir is not None:
        raise InvalidWorkspaceInputError(
            "supply exactly one of data_root or base_dir for workspace_layout"
        )
    if base_dir is not None:
        base = Path(base_dir)
    elif data_root is not None:
        base = Path(data_root) / "layer3" / WORKSPACES_DIR_NAME
    else:
        base = default_workspace_base_dir()
    return WorkspaceStorageLayout(workspace_id, base)


class WorkspaceStorageLayout:
    """One Workspace's file-backed derived-storage boundary.

    Exposes the Workspace-specific Schema root (injected into the existing
    L2S2 ``SchemaRunStorage``) and Wiki root (injected into the existing L2S3
    ``WikiStore``), plus destructive removal scoped to exactly this Workspace.
    """

    def __init__(self, workspace_id: str, base_dir: Path) -> None:
        self.workspace_id = _require_safe_workspace_id(workspace_id)
        self.base_dir = Path(base_dir)
        self.derived_dir = self.base_dir / self.workspace_id
        self.schemas_dir = self.derived_dir / SCHEMAS_DIR_NAME
        self.wiki_dir = self.derived_dir / WIKI_DIR_NAME

    # -- derived roots -------------------------------------------------------

    @property
    def wiki_store_base(self) -> Path:
        """Storage root injected into L2S3 ``WikiStore``.

        ``WikiStore`` builds its snapshot root as
        ``storage_root / context.workspace_id / wiki``; passing the parent of
        the per-Workspace derived directory therefore resolves to exactly
        ``self.wiki_dir`` for a context bound to this Workspace.
        """
        return self.base_dir

    def schema_storage(self) -> "SchemaRunStorage":
        """Existing L2S2 run storage bound to this Workspace's Schema root."""
        from transit_scholar.layer2.schema_extraction.persistence import (  # noqa: PLC0415
            SchemaRunStorage,
        )

        return SchemaRunStorage(storage_root=self.schemas_dir)

    def wiki_store(self, context: "WorkspaceContext") -> "WikiStore":
        """Existing L2S3 snapshot store bound to this Workspace's Wiki root.

        The store itself enforces the Workspace boundary (manifest/pages must
        match the bound ``WorkspaceContext``), so a snapshot built for another
        Workspace can never be loaded through this Workspace's store.
        """
        from transit_scholar.layer2.wiki.store import WikiStore  # noqa: PLC0415

        if context.workspace_id != self.workspace_id:
            raise InvalidWorkspaceInputError(
                f"wiki store for workspace {self.workspace_id!r} cannot be "
                f"bound to context of workspace {context.workspace_id!r}"
            )
        return WikiStore(context, storage_root=self.base_dir)

    def exists(self) -> bool:
        """True when any derived storage exists for this Workspace."""
        return self.derived_dir.exists()

    # -- scoped removal (REQ-004 / AC-006) -----------------------------------

    def delete_schema_storage(self) -> None:
        """Remove only this Workspace's Schema storage boundary.

        Other Workspaces' Schema roots are physically separate directories and
        are never touched (AC-006).
        """
        shutil.rmtree(self.schemas_dir, ignore_errors=True)

    def delete_wiki_storage(self) -> None:
        """Remove only this Workspace's Wiki snapshot boundary.

        Used by the rebuild path when the stored snapshot was captured under a
        different input context (e.g. membership changed) and can no longer be
        rebased in place by the existing L2S3 store. Scoped strictly to this
        Workspace's Wiki root.
        """
        shutil.rmtree(self.wiki_dir, ignore_errors=True)

    def delete(self) -> None:
        """Remove this Workspace's entire derived-storage boundary.

        Scoped strictly to ``derived_dir``; global Paper/L2S1 assets and other
        Workspaces' derived storage are never touched.
        """
        shutil.rmtree(self.derived_dir, ignore_errors=True)


__all__ = [
    "SCHEMAS_DIR_NAME",
    "WIKI_DIR_NAME",
    "WORKSPACES_DIR_NAME",
    "WorkspaceStorageLayout",
    "default_workspace_base_dir",
    "workspace_layout",
]