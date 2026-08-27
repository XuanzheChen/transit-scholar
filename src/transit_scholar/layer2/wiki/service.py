"""Workspace-bound maintenance, audit, and retrieval for wiki snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, UnavailableError

from .models import (
    AuditIssue,
    IndexRebuildResult,
    PageEntityLink,
    PageEntityResult,
    PaperMetadata,
    RelatedPageResult,
    UnlinkResult,
    WikiAuditReport,
    WikiEntity,
    WikiManifest,
    WikiPage,
    WikiSearchHit,
    WikiSearchResult,
    WorkspaceContext,
    entity_id_for,
    link_id_for,
    normalize_entity_name,
    page_id_for,
    utc_now,
    validate_identifier,
)
from .store import WikiNotFoundError, WikiNotInitializedError, WikiStore


_INDEX_VERSION = 1
_MAX_RESULTS = 100
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class WikiServiceError(RuntimeError):
    """Stable service error which is safe to serialize or show to callers."""

    def __init__(self, code: str, message: str, object_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.object_id = object_id


class WikiValidationError(WikiServiceError):
    pass


class WikiWorkspaceMismatchError(WikiServiceError):
    pass


class WikiIndexError(WikiServiceError):
    pass


class WikiAuditError(WikiServiceError):
    pass


class WikiEmbeddingUnavailableError(WikiServiceError):
    pass


class WikiEmbeddingProviderError(WikiServiceError):
    pass


def _tokens(value: str) -> set[str]:
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return set(_TOKEN_RE.findall(normalized))


def _safe_identifier(value: str) -> str:
    try:
        return validate_identifier(value)
    except (TypeError, ValueError) as error:
        raise WikiValidationError("invalid_identifier", "identifier is invalid") from error


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_RESULTS:
        raise WikiValidationError("invalid_limit", "limit must be between 1 and 100")
    return value


class WikiService:
    """A single-workspace facade whose views are always derived from JSON facts."""

    def __init__(
        self,
        context: WorkspaceContext,
        store: WikiStore,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        store_context = store.workspace_context
        if store_context != context:
            raise WikiWorkspaceMismatchError("workspace_mismatch", "store and service contexts differ")
        self.context = context.model_copy(deep=True)
        self.store = store
        self.embedding_provider = embedding_provider
        self._fingerprint: str | None = None
        self._pages: list[WikiPage] = []
        self._entities: list[WikiEntity] = []
        self._links: list[PageEntityLink] = []
        self._vector_build_error: str | None = None
        self._last_index_source: str | None = None
        self._bootstrap_vector_index()

    def _source_fingerprint(self) -> str:
        raw = self.store.read_raw_snapshot()
        digest = hashlib.sha256()
        for name in ("manifest", "pages", "entities", "links"):
            digest.update(name.encode("ascii"))
            digest.update(b"\0")
            digest.update((raw[name]["sha256"] or "missing").encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _refresh(self) -> None:
        fingerprint = self._source_fingerprint()
        if fingerprint == self._fingerprint:
            return
        try:
            self._pages = sorted(self.store.list_pages(), key=lambda item: item.page_id)
            self._entities = sorted(self.store.list_entities(), key=lambda item: item.entity_id)
            self._links = sorted(self.store.list_links(), key=lambda item: item.link_id)
        except WikiNotInitializedError:
            self._pages, self._entities, self._links = [], [], []
        self._fingerprint = fingerprint

    def _discard_view(self) -> None:
        self._fingerprint = None
        self._bootstrap_vector_index(force=True)

    def _bootstrap_vector_index(self, *, force: bool = False) -> None:
        """Maintain legacy provider-backed services without hiding stale indexes."""
        index_path = self.store.index_path / "package_b_index.json"
        if not self.store.manifest_path.exists():
            return
        existing: dict[str, object] | None = None
        persisted_source: str | None = None
        if index_path.exists():
            try:
                candidate = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return
            if isinstance(candidate, dict):
                existing = candidate
                source = candidate.get("source_fingerprint")
                if isinstance(source, str):
                    persisted_source = source
                    self._last_index_source = source
                if candidate.get("index_version") != _INDEX_VERSION or any(
                    not isinstance(candidate.get(key), list) for key in ("pages", "entities", "links")
                ):
                    return
            if not force:
                return
        provider = self.embedding_provider
        if provider is None or not provider.available:
            return
        if existing is not None and (persisted_source is None or persisted_source != self._last_index_source):
            return
        try:
            self.rebuild_indexes()
            self._vector_build_error = None
        except WikiIndexError as error:
            self._vector_build_error = error.code

    def _check_workspace(self, value: object) -> None:
        workspace_id = getattr(value, "workspace_id", None)
        if workspace_id is None and isinstance(value, dict):
            workspace_id = value.get("workspace_id")
        if workspace_id is not None and workspace_id != self.context.workspace_id:
            raise WikiWorkspaceMismatchError("workspace_mismatch", "object belongs to another workspace")

    def ensure_paper_page(self, metadata: PaperMetadata) -> WikiPage:
        if not isinstance(metadata, PaperMetadata):
            try:
                metadata = PaperMetadata.model_validate(metadata)
            except ValidationError as error:
                raise WikiValidationError("invalid_input", "paper metadata is invalid") from error
        if metadata.paper_id not in self.context.paper_ids:
            raise WikiValidationError("invalid_identifier", "paper is outside the workspace", metadata.paper_id)
        page_id = page_id_for(self.context.workspace_id, metadata.paper_id)
        try:
            return self.store.get_page(page_id)
        except (WikiNotFoundError, WikiNotInitializedError):
            page = WikiPage(
                page_id=page_id, workspace_id=self.context.workspace_id,
                paper_id=metadata.paper_id, title=metadata.title,
                schema_id=self.context.schema_id, schema_version=self.context.schema_version,
            )
            result = self.store.create_page(page)
            self._discard_view()
            return result

    def get_page(self, page_id: str) -> WikiPage:
        page_id = _safe_identifier(page_id)
        try:
            return self.store.get_page(page_id)
        except WikiNotInitializedError as error:
            raise WikiNotFoundError(f"page not found: {page_id}") from error

    def update_page_summary(self, page_id: str, summary: str) -> WikiPage:
        page = self.get_page(page_id)
        if not isinstance(summary, str):
            raise WikiValidationError("invalid_input", "summary must be a string", page_id)
        updated = page.model_copy(update={"summary": summary.strip(), "updated_at": utc_now(), "build_revision": page.build_revision + 1})
        result = self.store.update_page(updated)
        self._discard_view()
        return result

    def update_page_build_status(self, page_id: str, build_status: str) -> WikiPage:
        """Governed update of the compiler build status."""
        page = self.get_page(page_id)
        if build_status not in {"complete", "incomplete", "failed"}:
            raise WikiValidationError("invalid_build_status", "build status is invalid", page_id)
        updated = page.model_copy(update={
            "build_status": build_status,
            "updated_at": utc_now(),
            "build_revision": page.build_revision + 1,
        })
        result = self.store.update_page(updated)
        self._discard_view()
        return result

    def get_entity(self, entity_id: str) -> WikiEntity:
        entity_id = _safe_identifier(entity_id)
        try:
            return self.store.get_entity(entity_id)
        except WikiNotInitializedError as error:
            raise WikiNotFoundError(f"entity not found: {entity_id}") from error

    def search_entity(self, name: str) -> WikiEntity:
        normalized = normalize_entity_name(name)
        if not normalized:
            raise WikiValidationError("invalid_query", "entity name must not be empty")
        self._refresh()
        for entity in self._entities:
            if normalize_entity_name(entity.canonical_name) == normalized or any(normalize_entity_name(alias) == normalized for alias in entity.aliases):
                return entity
        raise WikiNotFoundError(f"entity not found: {name.strip()}")

    def find_entities_by_canonical_name(self, name: str) -> list[WikiEntity]:
        """Return all active-workspace canonical exact matches deterministically."""
        normalized = normalize_entity_name(name)
        if not normalized:
            raise WikiValidationError("invalid_query", "entity name must not be empty")
        self._refresh()
        return [
            entity for entity in self._entities
            if normalize_entity_name(entity.canonical_name) == normalized
        ]

    def find_entities_by_alias(self, name: str) -> list[WikiEntity]:
        """Return all active-workspace alias exact matches deterministically."""
        normalized = normalize_entity_name(name)
        if not normalized:
            raise WikiValidationError("invalid_query", "entity name must not be empty")
        self._refresh()
        return [
            entity for entity in self._entities
            if any(normalize_entity_name(alias) == normalized for alias in entity.aliases)
        ]

    def create_entity(
        self, canonical_name: str | WikiEntity, *, description: str = "", kind: str | None = None,
        aliases: list[str] | None = None,
    ) -> WikiEntity:
        self._check_workspace(canonical_name)
        if isinstance(canonical_name, WikiEntity):
            candidate = canonical_name
            canonical_name, description, kind, aliases = candidate.canonical_name, candidate.description, candidate.kind, candidate.aliases
        if not isinstance(canonical_name, str) or not normalize_entity_name(canonical_name):
            raise WikiValidationError("invalid_input", "canonical name must not be empty")
        entity = WikiEntity(
            entity_id=entity_id_for(self.context.workspace_id, canonical_name), workspace_id=self.context.workspace_id,
            canonical_name=canonical_name.strip(), description=description.strip() if isinstance(description, str) else "",
            kind=kind.strip() if isinstance(kind, str) and kind.strip() else None, aliases=aliases or [],
        )
        result = self.store.create_entity(entity)
        self._discard_view()
        return result

    def update_entity(self, entity_id: str | WikiEntity, **changes: object) -> WikiEntity:
        self._check_workspace(entity_id)
        if isinstance(entity_id, WikiEntity):
            incoming = entity_id
            existing = self.get_entity(incoming.entity_id)
            if incoming.entity_id != existing.entity_id or normalize_entity_name(incoming.canonical_name) != normalize_entity_name(existing.canonical_name):
                raise WikiValidationError("invalid_input", "entity identity is immutable", incoming.entity_id)
            changes = {"description": incoming.description, "kind": incoming.kind, "aliases": incoming.aliases}
            entity_id = incoming.entity_id
        existing = self.get_entity(_safe_identifier(entity_id))
        if "canonical_name" in changes and normalize_entity_name(str(changes["canonical_name"])) != normalize_entity_name(existing.canonical_name):
            raise WikiValidationError("invalid_input", "entity identity is immutable", existing.entity_id)
        supported = {"description", "kind", "aliases", "canonical_name"}
        if set(changes) - supported:
            raise WikiValidationError("invalid_input", "entity update contains unsupported fields", existing.entity_id)
        allowed = {key: value for key, value in changes.items() if key in {"description", "kind", "aliases"}}
        if "aliases" in allowed and not isinstance(allowed["aliases"], list):
            raise WikiValidationError("invalid_input", "aliases must be a list", existing.entity_id)
        if "description" in allowed and not isinstance(allowed["description"], str):
            raise WikiValidationError("invalid_input", "description must be a string", existing.entity_id)
        try:
            updated = WikiEntity.model_validate(
                {**existing.model_dump(), **allowed, "updated_at": utc_now()}
            )
        except ValidationError as error:
            raise WikiValidationError(
                "invalid_input", "entity update is invalid", existing.entity_id
            ) from error
        result = self.store.update_entity(updated)
        self._discard_view()
        return result

    def add_entity_alias(self, entity_id: str, alias: str) -> WikiEntity:
        if not isinstance(alias, str) or not normalize_entity_name(alias):
            raise WikiValidationError("invalid_input", "alias must not be empty", entity_id)
        entity = self.get_entity(_safe_identifier(entity_id))
        alias_normalized = normalize_entity_name(alias)
        aliases = list(entity.aliases)
        if alias_normalized != normalize_entity_name(entity.canonical_name) and all(normalize_entity_name(item) != alias_normalized for item in aliases):
            aliases.append(alias.strip())
        return self.update_entity(entity.entity_id, aliases=aliases)

    def add_entity_aliases(self, entity_id: str, aliases: list[str]) -> WikiEntity:
        """Validate and persist a batch of aliases in one governed update."""
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise WikiValidationError("invalid_input", "aliases must be a list of strings", entity_id)
        entity = self.get_entity(_safe_identifier(entity_id))
        combined = list(entity.aliases)
        seen = {normalize_entity_name(entity.canonical_name), *(normalize_entity_name(alias) for alias in combined)}
        for alias in aliases:
            normalized = normalize_entity_name(alias)
            if normalized and normalized not in seen:
                combined.append(alias.strip())
                seen.add(normalized)
        return self.update_entity(entity.entity_id, aliases=combined)

    def link_page_entity(
        self, page_id: str | WikiPage, entity_id: str | WikiEntity, *, source_field_id: str,
        source_status: str, confidence: float | None = None,
    ) -> PageEntityLink:
        self._check_workspace(page_id); self._check_workspace(entity_id)
        page = self.get_page(page_id.page_id if isinstance(page_id, WikiPage) else _safe_identifier(page_id))
        entity = self.get_entity(entity_id.entity_id if isinstance(entity_id, WikiEntity) else _safe_identifier(entity_id))
        try:
            source_field_id, source_status = validate_identifier(source_field_id), validate_identifier(source_status)
        except (TypeError, ValueError) as error:
            raise WikiValidationError("invalid_identifier", "link trace is invalid") from error
        link = PageEntityLink(
            link_id=link_id_for(self.context.workspace_id, page.page_id, entity.entity_id, source_field_id, source_status, self.context.schema_id, self.context.schema_version),
            workspace_id=self.context.workspace_id, page_id=page.page_id, entity_id=entity.entity_id,
            paper_id=page.paper_id, schema_id=self.context.schema_id, schema_version=self.context.schema_version,
            source_field_id=source_field_id, source_status=source_status, confidence=confidence,
        )
        result = self.store.create_link(link)
        self._discard_view()
        return result

    def unlink_page_entity(self, link_id: str | PageEntityLink) -> UnlinkResult:
        self._check_workspace(link_id)
        link_id = link_id.link_id if isinstance(link_id, PageEntityLink) else _safe_identifier(link_id)
        removed = self.store.remove_link(link_id)
        self._discard_view()
        return UnlinkResult(link_id=removed.link_id)

    def list_page_entities(self, page_id: str) -> PageEntityResult:
        page = self.get_page(_safe_identifier(page_id)); self._refresh()
        ids = {link.entity_id for link in self._links if link.page_id == page.page_id}
        return PageEntityResult(page=page, entities=[entity for entity in self._entities if entity.entity_id in ids])

    def find_pages_by_entity(self, entity_id: str) -> list[WikiPage]:
        entity = self.get_entity(_safe_identifier(entity_id)); self._refresh()
        ids = {link.page_id for link in self._links if link.entity_id == entity.entity_id}
        return [page for page in self._pages if page.page_id in ids]

    def find_related_pages(self, page_id: str, *, limit: int = _MAX_RESULTS, top_k: int | None = None) -> list[RelatedPageResult]:
        limit = _limit(limit if top_k is None else top_k); page = self.get_page(_safe_identifier(page_id)); self._refresh()
        own = {link.entity_id for link in self._links if link.page_id == page.page_id}
        result: list[RelatedPageResult] = []
        for candidate in self._pages:
            if candidate.page_id == page.page_id:
                continue
            shared = sorted(own & {link.entity_id for link in self._links if link.page_id == candidate.page_id})
            if shared:
                result.append(RelatedPageResult(page=candidate, shared_entity_ids=shared, shared_entity_count=len(shared)))
        return sorted(result, key=lambda item: (-item.shared_entity_count, item.page.page_id, item.shared_entity_ids))[:limit]

    def _hits(self, query: str, types: set[str], mode: Literal["lexical", "semantic"], limit: int) -> WikiSearchResult:
        if not isinstance(query, str) or not _tokens(query):
            raise WikiValidationError("invalid_query", "query must not be empty")
        if mode not in {"lexical", "semantic"}:
            raise WikiValidationError("invalid_input", "search mode is invalid")
        self._refresh()
        records: list[tuple[str, str, str, str, set[str]]] = []
        if "page" in types:
            records.extend(("page", page.page_id, page.title, page.summary, _tokens(f"{page.title} {page.summary}")) for page in self._pages)
        if "entity" in types:
            records.extend(("entity", entity.entity_id, entity.canonical_name, entity.description, _tokens(" ".join([entity.canonical_name, *entity.aliases, entity.description]))) for entity in self._entities)
        lexical = [(kind, object_id, title, snippet, float(len(_tokens(query) & words))) for kind, object_id, title, snippet, words in records]
        lexical = [item for item in lexical if item[4] > 0]
        if mode == "semantic":
            semantic = self._semantic(query, records)
            if isinstance(semantic, tuple):
                status, error_code, scored = semantic
                return WikiSearchResult(status=status, error_code=error_code, hits=self._render(scored, "lexical", limit))
            return WikiSearchResult(hits=self._render(semantic, "semantic", limit))
        return WikiSearchResult(hits=self._render(lexical, "lexical", limit))

    @staticmethod
    def _render(scored: list[tuple[str, str, str, str, float]], mode: Literal["lexical", "semantic"], limit: int) -> list[WikiSearchHit]:
        return [WikiSearchHit(type=kind, object_id=object_id, title=title, snippet=snippet, score=score, retrieval_mode=mode)
                for kind, object_id, title, snippet, score in sorted(scored, key=lambda item: (-item[4], item[1], 0 if item[0] == "entity" else 1))[:limit]]

    def _semantic(self, query: str, records: list[tuple[str, str, str, str, set[str]]]):
        provider = self.embedding_provider
        lexical = [(kind, object_id, title, snippet, float(len(_tokens(query) & words))) for kind, object_id, title, snippet, words in records]
        lexical = [item for item in lexical if item[4] > 0]
        if not records:
            return []
        if provider is None or not provider.available:
            return "degraded", "embedding_unavailable", lexical
        if self._vector_build_error is not None:
            return "error", self._vector_build_error, lexical
        try:
            index_path = self.store.index_path / "package_b_index.json"
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return "degraded", "vector_index_missing", lexical
            if payload.get("index_version") != _INDEX_VERSION:
                return "degraded", "vector_index_incompatible", lexical
            if payload.get("source_fingerprint") != self._source_fingerprint():
                return "degraded", "vector_index_stale", lexical
            info = provider.info
            metadata = payload.get("vector_metadata")
            dimension = provider.dimension()
            if not isinstance(metadata, dict) or metadata.get("dimension") != dimension:
                return "degraded", "vector_index_incompatible", lexical
            if info is not None and any(metadata.get(key) != getattr(info, key) for key in ("provider", "model", "revision")):
                return "degraded", "vector_index_incompatible", lexical
            implementation = f"{type(provider).__module__}.{type(provider).__qualname__}"
            if metadata.get("implementation") not in (None, implementation):
                return "error", "embedding_provider_failure", lexical
            vector_records = payload.get("vectors")
            by_id = {(item.get("kind"), item.get("object_id")): item.get("vector") for item in vector_records or [] if isinstance(item, dict)}
            vectors = [by_id.get((kind, object_id)) for kind, object_id, *_ in records]
            if any(not isinstance(vector, list) or len(vector) != dimension for vector in vectors):
                return "degraded", "vector_index_incompatible", lexical
            query_vector = provider.embed_query(query)
            if dimension is None or len(query_vector) != dimension:
                raise ValueError("embedding dimensions are inconsistent")
            if not any(query_vector):
                raise ValueError("query vector is zero")
            scored = []
            for record, vector in zip(records, vectors, strict=True):
                magnitude = math.sqrt(sum(value * value for value in vector)) * math.sqrt(sum(value * value for value in query_vector))
                if magnitude:
                    scored.append((*record[:4], float(sum(left * right for left, right in zip(vector, query_vector, strict=True)) / magnitude)))
            return scored
        except UnavailableError:
            return "degraded", "embedding_unavailable", lexical
        except Exception:
            return "error", "embedding_provider_failure", lexical

    def search_pages(self, query: str, *, limit: int = 20, top_k: int | None = None, mode: Literal["lexical", "semantic"] = "lexical", semantic: bool | None = None) -> WikiSearchResult:
        return self._hits(query, {"page"}, "semantic" if semantic else mode, _limit(limit if top_k is None else top_k))

    def search_entities(self, query: str, *, limit: int = 20, top_k: int | None = None, mode: Literal["lexical", "semantic"] = "lexical", semantic: bool | None = None) -> WikiSearchResult:
        return self._hits(query, {"entity"}, "semantic" if semantic else mode, _limit(limit if top_k is None else top_k))

    def search_wiki(self, query: str, *, limit: int = 20, top_k: int | None = None, mode: Literal["lexical", "semantic"] = "lexical", semantic: bool | None = None) -> WikiSearchResult:
        return self._hits(query, {"page", "entity"}, "semantic" if semantic else mode, _limit(limit if top_k is None else top_k))

    def rebuild_indexes(self) -> IndexRebuildResult:
        self._refresh()
        fingerprint = self._source_fingerprint()
        payload = {"index_version": _INDEX_VERSION, "source_fingerprint": fingerprint,
                   "pages": [page.model_dump(mode="json") for page in self._pages],
                   "entities": [entity.model_dump(mode="json") for entity in self._entities],
                   "links": [link.model_dump(mode="json") for link in self._links]}
        provider = self.embedding_provider
        vectors: list[dict[str, object]] = []
        metadata: dict[str, object] | None = None
        embedding_error: WikiIndexError | None = None
        if provider is not None and provider.available:
            texts = [f"{page.title} {page.summary}" for page in self._pages]
            texts += [" ".join([entity.canonical_name, *entity.aliases, entity.description]) for entity in self._entities]
            try:
                embedded = provider.embed_documents(texts)
                dimension = provider.dimension()
                if dimension is None or len(embedded) != len(texts) or any(len(vector) != dimension for vector in embedded):
                    raise ValueError("embedding dimensions are inconsistent")
                info = provider.info
                metadata = {"provider": info.provider if info else None, "model": info.model if info else None,
                            "revision": info.revision if info else None, "dimension": dimension,
                            "implementation": f"{type(provider).__module__}.{type(provider).__qualname__}"}
                offset = len(self._pages)
                vectors = [{"kind": "page", "object_id": page.page_id, "vector": vector} for page, vector in zip(self._pages, embedded[:offset], strict=True)]
                vectors += [{"kind": "entity", "object_id": entity.entity_id, "vector": vector} for entity, vector in zip(self._entities, embedded[offset:], strict=True)]
            except UnavailableError:
                metadata = None
            except Exception:
                embedding_error = WikiIndexError("embedding_provider_failure", "vector index could not be built")
        payload["vector_metadata"] = metadata
        payload["vectors"] = vectors
        target = self.store.index_path / "package_b_index.json"
        temporary = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            os.replace(temporary, target)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise WikiIndexError("index_write_failed", "derived index could not be written") from error
        self._last_index_source = fingerprint
        if embedding_error is not None:
            raise embedding_error
        return IndexRebuildResult(source_fingerprint=fingerprint, index_version=_INDEX_VERSION)

    def _audit_raw(self) -> tuple[list[AuditIssue], dict[str, list[object]], str]:
        raw = self.store.read_raw_snapshot(); issues: list[AuditIssue] = []; parsed: dict[str, list[object]] = {"pages": [], "entities": [], "links": []}
        fingerprint = self._source_fingerprint()
        models = {"pages": WikiPage, "entities": WikiEntity, "links": PageEntityLink}
        for name in ("manifest", "pages", "entities", "links"):
            asset = raw[name]
            if not asset["exists"]:
                issues.append(AuditIssue(code="missing_source_asset", object_id=name, message=f"missing {name}")); continue
            content = asset["bytes"]
            try: text = content.decode("utf-8")  # type: ignore[union-attr]
            except UnicodeDecodeError:
                issues.append(AuditIssue(code="invalid_utf8", object_id=name, message=f"invalid UTF-8 in {name}")); continue
            if name == "manifest":
                try:
                    manifest = WikiManifest.model_validate_json(text)
                    parsed["manifest"] = [manifest]
                    if (
                        manifest.workspace_id != self.context.workspace_id
                        or manifest.schema_id != self.context.schema_id
                        or manifest.schema_version != self.context.schema_version
                        or manifest.paper_ids != self.context.paper_ids
                    ):
                        issues.append(AuditIssue(code="manifest_context_mismatch", object_id="manifest", message="manifest context differs"))
                except json.JSONDecodeError:
                    issues.append(AuditIssue(code="malformed_json", object_id="manifest", message="manifest is invalid JSON"))
                except ValidationError:
                    issues.append(AuditIssue(code="invalid_model", object_id="manifest", message="manifest is invalid"))
                continue
            if text and not text.endswith("\n"):
                issues.append(AuditIssue(code="incomplete_jsonl", object_id=name, message=f"{name} lacks final newline"))
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip(): issues.append(AuditIssue(code="malformed_json", object_id=f"{name}:{number}", message="blank JSONL record")); continue
                try: value = models[name].model_validate_json(line)
                except (ValidationError, ValueError, json.JSONDecodeError): issues.append(AuditIssue(code="invalid_model", object_id=f"{name}:{number}", message="invalid JSONL record")); continue
                parsed[name].append(value)
        for name, attr in (("pages", "page_id"), ("entities", "entity_id"), ("links", "link_id")):
            seen: set[str] = set()
            for value in parsed[name]:
                identity = getattr(value, attr)
                if identity in seen: issues.append(AuditIssue(code="duplicate_record", object_id=identity, message=f"duplicate {name} record"))
                seen.add(identity)
        for values, key, label in (
            (parsed["pages"], lambda item: item.paper_id, "page paper identity"),
            (parsed["entities"], lambda item: normalize_entity_name(item.canonical_name), "entity canonical identity"),
            (parsed["links"], lambda item: (item.page_id, item.entity_id, item.source_field_id, item.source_status, item.schema_id, item.schema_version), "link trace identity"),
        ):
            seen_keys: set[object] = set()
            for value in values:
                identity = key(value)
                if identity in seen_keys:
                    issues.append(AuditIssue(code="duplicate_record", object_id=getattr(value, "page_id", None) or getattr(value, "entity_id", None) or getattr(value, "link_id", None), message=f"duplicate {label}"))
                seen_keys.add(identity)
        pages = {page.page_id: page for page in parsed["pages"]}; entities = {entity.entity_id: entity for entity in parsed["entities"]}
        for page in pages.values():
            if page.workspace_id != self.context.workspace_id or page.paper_id not in self.context.paper_ids or page.schema_id != self.context.schema_id or page.schema_version != self.context.schema_version or page.page_id != page_id_for(page.workspace_id, page.paper_id):
                issues.append(AuditIssue(code="workspace_mismatch", object_id=page.page_id, message="page context or identity differs"))
        manifests = parsed.get("manifest", [])
        if manifests:
            manifest = manifests[0]
            if isinstance(manifest, WikiManifest) and not {page.paper_id for page in pages.values()}.issubset(manifest.paper_ids):
                issues.append(AuditIssue(code="manifest_asset_mismatch", object_id="manifest", message="manifest papers differ from page assets"))
        for entity in entities.values():
            if entity.workspace_id != self.context.workspace_id or entity.entity_id != entity_id_for(entity.workspace_id, entity.canonical_name):
                issues.append(AuditIssue(code="workspace_mismatch", object_id=entity.entity_id, message="entity context or identity differs"))
        for link in parsed["links"]:
            if link.page_id not in pages: issues.append(AuditIssue(code="dangling_page_link", object_id=link.link_id, message="link page is missing"))
            if link.entity_id not in entities: issues.append(AuditIssue(code="dangling_entity_link", object_id=link.link_id, message="link entity is missing"))
            if link.workspace_id != self.context.workspace_id: issues.append(AuditIssue(code="workspace_mismatch", object_id=link.link_id, message="link workspace differs"))
            page, entity = pages.get(link.page_id), entities.get(link.entity_id)
            expected = link_id_for(link.workspace_id, link.page_id, link.entity_id, link.source_field_id, link.source_status, link.schema_id, link.schema_version)
            if link.link_id != expected or (page is not None and (link.paper_id != page.paper_id or link.schema_id != page.schema_id or link.schema_version != page.schema_version)) or (entity is not None and entity.workspace_id != link.workspace_id):
                issues.append(AuditIssue(code="referential_integrity_error", object_id=link.link_id, message="link trace is incompatible with source records"))
        linked = {link.entity_id for link in parsed["links"] if link.page_id in pages and link.entity_id in entities}
        for entity in entities.values():
            if entity.entity_id not in linked: issues.append(AuditIssue(code="orphan_entity", object_id=entity.entity_id, message="entity has no links", severity="warning"))
        index = self.store.index_path / "package_b_index.json"
        if not index.exists():
            issues.append(AuditIssue(code="index_missing", object_id="package_b_index.json", message="derived index is absent", severity="warning"))
            issues.append(AuditIssue(code="vector_index_missing", object_id="package_b_index.json", message="mandatory vector index is absent"))
        else:
            try:
                payload = json.loads(index.read_bytes().decode("utf-8"))
                if payload.get("index_version") != _INDEX_VERSION: issues.append(AuditIssue(code="index_corrupt", object_id="package_b_index.json", message="index version is invalid"))
                elif payload.get("source_fingerprint") != fingerprint: issues.append(AuditIssue(code="index_stale", object_id="package_b_index.json", message="index source fingerprint is stale", severity="warning"))
                elif any(not isinstance(payload.get(key), list) for key in ("pages", "entities", "links")): issues.append(AuditIssue(code="index_corrupt", object_id="package_b_index.json", message="index projection is invalid"))
                else:
                    expected = {
                        "pages": [item.model_dump(mode="json") for item in sorted(parsed["pages"], key=lambda item: item.page_id)],
                        "entities": [item.model_dump(mode="json") for item in sorted(parsed["entities"], key=lambda item: item.entity_id)],
                        "links": [item.model_dump(mode="json") for item in sorted(parsed["links"], key=lambda item: item.link_id)],
                    }
                    if any(payload[key] != expected[key] for key in expected):
                        issues.append(AuditIssue(code="index_corrupt", object_id="package_b_index.json", message="index projection differs from source"))
                issues.extend(self._vector_audit_issues(payload, fingerprint, parsed["pages"], parsed["entities"]))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError): issues.append(AuditIssue(code="index_corrupt", object_id="package_b_index.json", message="index is invalid"))
        return issues, parsed, fingerprint

    def _vector_audit_issues(self, payload: object, fingerprint: str, pages: list[object], entities: list[object]) -> list[AuditIssue]:
        if not isinstance(payload, dict):
            return [AuditIssue(code="vector_index_missing", object_id="package_b_index.json", message="vector index metadata is absent")]
        metadata, vectors = payload.get("vector_metadata"), payload.get("vectors")
        missing_vectors = not isinstance(metadata, dict) or not isinstance(vectors, list)
        provider = self.embedding_provider
        if provider is None or not provider.available:
            issues = [AuditIssue(code="embedding_unavailable", object_id="package_b_index.json", message="mandatory embedding provider is unavailable", severity="warning")]
            if missing_vectors:
                issues.append(AuditIssue(code="vector_index_missing", object_id="package_b_index.json", message="mandatory vector metadata or vectors are absent"))
            return issues
        if missing_vectors:
            return [AuditIssue(code="vector_index_missing", object_id="package_b_index.json", message="mandatory vector metadata or vectors are absent")]
        issues: list[AuditIssue] = []
        if payload.get("source_fingerprint") != fingerprint:
            issues.append(AuditIssue(code="vector_index_stale", object_id="package_b_index.json", message="vector source fingerprint is stale"))
        dimension = provider.dimension(); info = provider.info
        expected = {"dimension": dimension, "implementation": f"{type(provider).__module__}.{type(provider).__qualname__}"}
        if info is not None:
            expected.update({key: getattr(info, key) for key in ("provider", "model", "revision")})
        if any(metadata.get(key) != value for key, value in expected.items()):
            issues.append(AuditIssue(code="vector_index_incompatible", object_id="package_b_index.json", message="vector metadata is incompatible"))
        by_id = {(item.get("kind"), item.get("object_id")): item.get("vector") for item in vectors if isinstance(item, dict)}
        required = [("page", item.page_id) for item in pages] + [("entity", item.entity_id) for item in entities]
        if any(key not in by_id for key in required):
            issues.append(AuditIssue(code="vector_index_missing", object_id="package_b_index.json", message="required page or entity vectors are missing"))
        if dimension is None or any(not isinstance(by_id.get(key), list) or len(by_id[key]) != dimension for key in required if key in by_id):
            issues.append(AuditIssue(code="vector_index_incompatible", object_id="package_b_index.json", message="persisted vector dimensions are incompatible"))
        return issues

    @staticmethod
    def _report(issues: list[AuditIssue], fingerprint: str, page_id: str | None = None) -> WikiAuditReport:
        ordered = sorted(issues, key=lambda item: (item.code, item.object_id or "", item.message))
        return WikiAuditReport(ok=not any(item.severity == "error" for item in ordered), audited_at=datetime.now(UTC), issues=ordered, page_id=page_id, source_fingerprint=fingerprint)

    def audit_wiki(self) -> WikiAuditReport:
        issues, _, fingerprint = self._audit_raw()
        return self._report(issues, fingerprint)

    def audit_page(self, page_id: str) -> WikiAuditReport:
        page_id = _safe_identifier(page_id); issues, parsed, fingerprint = self._audit_raw()
        pages = {page.page_id for page in parsed["pages"]}
        if page_id not in pages:
            raise WikiNotFoundError(f"page not found: {page_id}")
        links = [link for link in parsed["links"] if link.page_id == page_id]
        object_ids = {page_id, "manifest", "pages", "package_b_index.json"}
        object_ids.update(link.link_id for link in links)
        object_ids.update(link.entity_id for link in links)
        relevant = [issue for issue in issues if issue.object_id in object_ids]
        if not (self.store.index_path / "package_b_index.json").exists():
            relevant = [
                issue.model_copy(update={"severity": "warning"})
                if issue.code == "vector_index_missing"
                else issue
                for issue in relevant
            ]
        return self._report(relevant, fingerprint, page_id)


WikiMaintainer = WikiService
