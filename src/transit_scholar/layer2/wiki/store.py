"""Atomic file snapshot storage for Package A wiki models."""

from __future__ import annotations

import json
import os
import shutil
import uuid
import hashlib
from pathlib import Path
from typing import Generic, TypeVar

from transit_scholar.config import settings

from .models import (
    PageEntityLink,
    WikiEntity,
    WikiManifest,
    WikiPage,
    WorkspaceContext,
    entity_id_for,
    link_id_for,
    normalize_entity_name,
    page_id_for,
    utc_now,
)


class WikiStoreError(RuntimeError):
    """Base error for explicit wiki persistence failures."""


class WikiNotInitializedError(WikiStoreError):
    pass


class WikiCorruptionError(WikiStoreError):
    pass


class WikiNotFoundError(WikiStoreError):
    pass


class WikiConflictError(WikiStoreError):
    pass


class WikiReferentialIntegrityError(WikiStoreError):
    pass


T = TypeVar("T")


class WikiStore:
    """A context-bound, non-destructive store of complete wiki snapshots."""

    _NAMES = ("manifest.json", "pages.jsonl", "entities.jsonl", "page_entity_links.jsonl")

    def __init__(self, context: WorkspaceContext, storage_root: Path | str | None = None):
        self.context = context
        self.storage_root = Path(settings.layer2_dir if storage_root is None else storage_root)
        self.root = self.storage_root / context.workspace_id / "wiki"
        self.manifest_path = self.root / "manifest.json"
        self.pages_path = self.root / "pages.jsonl"
        self.entities_path = self.root / "entities.jsonl"
        self.links_path = self.root / "page_entity_links.jsonl"
        self.index_path = self.root / "index"
        self._loaded = False
        self._manifest: WikiManifest | None = None
        self._pages: dict[str, WikiPage] = {}
        self._entities: dict[str, WikiEntity] = {}
        self._links: dict[str, PageEntityLink] = {}
        self._loaded_fingerprint: str | None = None

    def _default_manifest(self) -> WikiManifest:
        return WikiManifest(
            workspace_id=self.context.workspace_id,
            schema_id=self.context.schema_id,
            schema_version=self.context.schema_version,
            paper_ids=list(self.context.paper_ids),
            builder_version="wiki-core-v1",
            build_status="partial",
        )

    def _paths(self) -> dict[str, Path]:
        return {
            "manifest.json": self.manifest_path,
            "pages.jsonl": self.pages_path,
            "entities.jsonl": self.entities_path,
            "page_entity_links.jsonl": self.links_path,
        }

    @property
    def workspace_context(self) -> WorkspaceContext:
        """A copy of the immutable workspace boundary for public consumers."""
        return self.context.model_copy(deep=True)

    @property
    def snapshot_paths(self) -> dict[str, Path]:
        """Return a fresh mapping of authoritative source snapshot paths."""
        return {
            "manifest": self.manifest_path,
            "pages": self.pages_path,
            "entities": self.entities_path,
            "links": self.links_path,
        }

    def read_raw_snapshot(self) -> dict[str, dict[str, object]]:
        """Read source bytes without loading or validating the wiki snapshot."""
        result: dict[str, dict[str, object]] = {}
        for name, path in self.snapshot_paths.items():
            content = path.read_bytes() if path.exists() else None
            result[name] = {
                "path": path,
                "exists": content is not None,
                "bytes": content,
                "sha256": hashlib.sha256(content).hexdigest() if content is not None else None,
            }
        return result

    def _source_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for name, asset in self.read_raw_snapshot().items():
            digest.update(name.encode("ascii"))
            digest.update(b"\0")
            digest.update(str(asset["sha256"] or "missing").encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _load(self, *, allow_uninitialized: bool = False) -> None:
        if self._loaded:
            if self._loaded_fingerprint == self._source_fingerprint():
                return
            self._loaded = False
            self._manifest = None
            self._pages = {}
            self._entities = {}
            self._links = {}
        paths = self._paths()
        present = [path.exists() for path in paths.values()]
        if not any(present) and not self.root.exists():
            if allow_uninitialized:
                return
            raise WikiNotInitializedError(f"wiki workspace {self.context.workspace_id!r} is not initialized")
        if not any(present):
            raise WikiCorruptionError("wiki snapshot directory exists without required files")
        if not all(present):
            raise WikiCorruptionError("wiki snapshot has missing required files")
        try:
            manifest = WikiManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
            pages = self._read_jsonl(self.pages_path, WikiPage)
            entities = self._read_jsonl(self.entities_path, WikiEntity)
            links = self._read_jsonl(self.links_path, PageEntityLink)
            self._validate_snapshot(manifest, pages, entities, links)
        except WikiStoreError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise WikiCorruptionError(f"invalid wiki snapshot: {error}") from error
        self._manifest, self._pages, self._entities, self._links = manifest, pages, entities, links
        self._loaded = True
        self._loaded_fingerprint = self._source_fingerprint()

    @staticmethod
    def _read_jsonl(path: Path, model: type[T]) -> dict[str, T]:
        records: dict[str, T] = {}
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            raise WikiCorruptionError(f"JSONL file is incomplete: {path.name}")
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                raise WikiCorruptionError(f"blank JSONL record in {path.name}:{line_number}")
            try:
                record = model.model_validate_json(line)
            except (ValueError, json.JSONDecodeError) as error:
                raise WikiCorruptionError(f"invalid JSONL record in {path.name}:{line_number}") from error
            identity = WikiStore._record_id(record)
            if identity in records:
                raise WikiCorruptionError(f"duplicate ID in {path.name}: {identity}")
            records[identity] = record
        return records

    @staticmethod
    def _record_id(record: WikiPage | WikiEntity | PageEntityLink) -> str:
        if isinstance(record, WikiPage):
            return record.page_id
        if isinstance(record, WikiEntity):
            return record.entity_id
        return record.link_id

    def _validate_snapshot(
        self,
        manifest: WikiManifest,
        pages: dict[str, WikiPage],
        entities: dict[str, WikiEntity],
        links: dict[str, PageEntityLink],
    ) -> None:
        if (
            manifest.workspace_id != self.context.workspace_id
            or manifest.schema_id != self.context.schema_id
            or manifest.schema_version != self.context.schema_version
            or manifest.paper_ids != self.context.paper_ids
        ):
            raise WikiCorruptionError("manifest does not match bound workspace context")
        papers: set[str] = set()
        names: set[str] = set()
        link_keys: set[tuple[str, str, str, str, str, str]] = set()
        for page in pages.values():
            self._validate_page(page)
            if page.paper_id in papers:
                raise WikiCorruptionError("duplicate page paper identity")
            papers.add(page.paper_id)
        for entity in entities.values():
            self._validate_entity(entity)
            name = normalize_entity_name(entity.canonical_name)
            if name in names:
                raise WikiCorruptionError("duplicate normalized entity identity")
            names.add(name)
        for link in links.values():
            self._validate_link(link, pages, entities)
            key = self._link_key(link)
            if key in link_keys:
                raise WikiCorruptionError("duplicate link trace identity")
            link_keys.add(key)

    def _validate_page(self, page: WikiPage) -> None:
        if (
            page.workspace_id != self.context.workspace_id
            or page.paper_id not in self.context.paper_ids
            or page.schema_id != self.context.schema_id
            or page.schema_version != self.context.schema_version
            or page.page_id != page_id_for(page.workspace_id, page.paper_id)
        ):
            raise WikiCorruptionError("page does not match workspace context")

    def _validate_entity(self, entity: WikiEntity) -> None:
        if entity.workspace_id != self.context.workspace_id or entity.entity_id != entity_id_for(entity.workspace_id, entity.canonical_name):
            raise WikiCorruptionError("entity does not match workspace context")

    @staticmethod
    def _link_key(link: PageEntityLink) -> tuple[str, str, str, str, str, str]:
        return (link.page_id, link.entity_id, link.source_field_id, link.source_status, link.schema_id, link.schema_version)

    def _validate_link(self, link: PageEntityLink, pages: dict[str, WikiPage], entities: dict[str, WikiEntity]) -> None:
        page, entity = pages.get(link.page_id), entities.get(link.entity_id)
        if page is None or entity is None:
            raise WikiReferentialIntegrityError("link references a missing page or entity")
        if (
            link.workspace_id != self.context.workspace_id
            or page.workspace_id != link.workspace_id
            or entity.workspace_id != link.workspace_id
            or link.paper_id != page.paper_id
            or link.schema_id != page.schema_id
            or link.schema_version != page.schema_version
            or link.link_id != link_id_for(link.workspace_id, link.page_id, link.entity_id, link.source_field_id, link.source_status, link.schema_id, link.schema_version)
        ):
            raise WikiReferentialIntegrityError("link trace is incompatible with its page or workspace")

    def _write_snapshot(self, manifest: WikiManifest, pages: dict[str, WikiPage], entities: dict[str, WikiEntity], links: dict[str, PageEntityLink]) -> None:
        self._validate_snapshot(manifest, pages, entities, links)
        existed = self.root.exists()
        originals = {name: path.read_bytes() if path.exists() else None for name, path in self._paths().items()}
        temporary: list[Path] = []
        temporary_by_name: dict[str, Path] = {}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.index_path.mkdir(exist_ok=True)
            payloads = {
                "manifest.json": manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
                "pages.jsonl": self._jsonl(pages.values()),
                "entities.jsonl": self._jsonl(entities.values()),
                "page_entity_links.jsonl": self._jsonl(links.values()),
            }
            for name, content in payloads.items():
                temporary_path = self.root / f".{name}.tmp-{uuid.uuid4().hex}"
                temporary_path.write_bytes(content)
                temporary.append(temporary_path)
                temporary_by_name[name] = temporary_path
                if temporary_path.read_bytes() != content:
                    raise OSError("temporary snapshot verification failed")
            self._validate_snapshot(
                WikiManifest.model_validate_json(temporary_by_name["manifest.json"].read_text(encoding="utf-8")),
                self._read_jsonl(temporary_by_name["pages.jsonl"], WikiPage),
                self._read_jsonl(temporary_by_name["entities.jsonl"], WikiEntity),
                self._read_jsonl(temporary_by_name["page_entity_links.jsonl"], PageEntityLink),
            )
            for name, target in self._paths().items():
                source = temporary_by_name[name]
                os.replace(source, target)
                temporary.remove(source)
        except OSError as error:
            for name, target in self._paths().items():
                original = originals[name]
                try:
                    if original is None:
                        if target.exists():
                            target.unlink()
                    else:
                        target.write_bytes(original)
                except OSError:
                    pass
            if not existed:
                try:
                    shutil.rmtree(self.root)
                except OSError:
                    pass
            raise WikiStoreError(f"atomic wiki snapshot write failed: {error}") from error
        finally:
            for path in temporary:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._manifest, self._pages, self._entities, self._links = manifest, pages, entities, links
        self._loaded = True
        self._loaded_fingerprint = self._source_fingerprint()

    @staticmethod
    def _jsonl(records: object) -> bytes:
        ordered = sorted(records, key=WikiStore._record_id)
        return b"".join(record.model_dump_json().encode("utf-8") + b"\n" for record in ordered)

    def _state_for_write(self) -> tuple[WikiManifest, dict[str, WikiPage], dict[str, WikiEntity], dict[str, PageEntityLink]]:
        self._load(allow_uninitialized=True)
        return (
            self._manifest or self._default_manifest(),
            dict(self._pages),
            dict(self._entities),
            dict(self._links),
        )

    def get_manifest(self) -> WikiManifest:
        self._load()
        assert self._manifest is not None
        return self._manifest

    def upsert_manifest(self, manifest: WikiManifest) -> WikiManifest:
        _, pages, entities, links = self._state_for_write()
        if self._manifest is not None:
            manifest = manifest.model_copy(update={"created_at": self._manifest.created_at, "updated_at": utc_now()})
        self._write_snapshot(manifest, pages, entities, links)
        return manifest

    def list_pages(self) -> list[WikiPage]:
        self._load()
        return sorted(self._pages.values(), key=lambda page: page.paper_id)

    def get_page(self, page_id: str) -> WikiPage:
        self._load()
        try:
            return self._pages[page_id]
        except KeyError as error:
            raise WikiNotFoundError(f"page not found: {page_id}") from error

    def create_page(self, page: WikiPage) -> WikiPage:
        manifest, pages, entities, links = self._state_for_write()
        self._validate_page(page)
        existing = next((value for value in pages.values() if value.paper_id == page.paper_id), None)
        if existing is not None:
            return existing
        pages[page.page_id] = page
        self._write_snapshot(manifest, pages, entities, links)
        return page

    def update_page(self, page: WikiPage) -> WikiPage:
        manifest, pages, entities, links = self._state_for_write()
        if page.page_id not in pages:
            raise WikiNotFoundError(f"page not found: {page.page_id}")
        existing = pages[page.page_id]
        self._validate_page(page)
        if page.paper_id != existing.paper_id:
            raise WikiConflictError("page paper identity is immutable")
        page = page.model_copy(update={"created_at": existing.created_at, "updated_at": utc_now()})
        pages[page.page_id] = page
        self._write_snapshot(manifest, pages, entities, links)
        return page

    def upsert_page(self, page: WikiPage) -> WikiPage:
        try:
            existing = self.get_page(page.page_id)
        except (WikiNotInitializedError, WikiNotFoundError):
            return self.create_page(page)
        return self.update_page(page.model_copy(update={"created_at": existing.created_at}))

    def list_entities(self) -> list[WikiEntity]:
        self._load()
        return sorted(self._entities.values(), key=lambda entity: entity.entity_id)

    def get_entity(self, entity_id: str) -> WikiEntity:
        self._load()
        try:
            return self._entities[entity_id]
        except KeyError as error:
            raise WikiNotFoundError(f"entity not found: {entity_id}") from error

    def create_entity(self, entity: WikiEntity) -> WikiEntity:
        manifest, pages, entities, links = self._state_for_write()
        self._validate_entity(entity)
        normalized = normalize_entity_name(entity.canonical_name)
        existing = next((value for value in entities.values() if normalize_entity_name(value.canonical_name) == normalized), None)
        if existing is not None:
            return existing
        entities[entity.entity_id] = entity
        self._write_snapshot(manifest, pages, entities, links)
        return entity

    def update_entity(self, entity: WikiEntity) -> WikiEntity:
        manifest, pages, entities, links = self._state_for_write()
        existing = entities.get(entity.entity_id)
        if existing is None:
            raise WikiNotFoundError(f"entity not found: {entity.entity_id}")
        self._validate_entity(entity)
        if normalize_entity_name(existing.canonical_name) != normalize_entity_name(entity.canonical_name):
            raise WikiConflictError("entity canonical identity is immutable")
        entity = entity.model_copy(update={"created_at": existing.created_at, "updated_at": utc_now()})
        entities[entity.entity_id] = entity
        self._write_snapshot(manifest, pages, entities, links)
        return entity

    def upsert_entity(self, entity: WikiEntity) -> WikiEntity:
        try:
            existing = self.get_entity(entity.entity_id)
        except (WikiNotInitializedError, WikiNotFoundError):
            return self.create_entity(entity)
        return self.update_entity(entity.model_copy(update={"created_at": existing.created_at}))

    def list_links(self) -> list[PageEntityLink]:
        self._load()
        return sorted(self._links.values(), key=lambda link: link.link_id)

    def get_link(self, link_id: str) -> PageEntityLink:
        self._load()
        try:
            return self._links[link_id]
        except KeyError as error:
            raise WikiNotFoundError(f"link not found: {link_id}") from error

    def create_link(self, link: PageEntityLink) -> PageEntityLink:
        manifest, pages, entities, links = self._state_for_write()
        self._validate_link(link, pages, entities)
        key = self._link_key(link)
        existing = next((value for value in links.values() if self._link_key(value) == key), None)
        if existing is not None:
            return existing
        links[link.link_id] = link
        self._write_snapshot(manifest, pages, entities, links)
        return link

    def update_link(self, link: PageEntityLink) -> PageEntityLink:
        manifest, pages, entities, links = self._state_for_write()
        if link.link_id not in links:
            raise WikiNotFoundError(f"link not found: {link.link_id}")
        self._validate_link(link, pages, entities)
        existing = links[link.link_id]
        if self._link_key(existing) != self._link_key(link):
            raise WikiConflictError("link trace identity is immutable")
        link = link.model_copy(update={"created_at": existing.created_at})
        links[link.link_id] = link
        self._write_snapshot(manifest, pages, entities, links)
        return link

    def upsert_link(self, link: PageEntityLink) -> PageEntityLink:
        try:
            existing = self.get_link(link.link_id)
        except (WikiNotInitializedError, WikiNotFoundError):
            return self.create_link(link)
        return self.update_link(link.model_copy(update={"created_at": existing.created_at}))

    def remove_link(self, link_id: str) -> PageEntityLink:
        """Atomically remove exactly one link while preserving all other facts."""
        manifest, pages, entities, links = self._state_for_write()
        try:
            removed = links.pop(link_id)
        except KeyError as error:
            raise WikiNotFoundError(f"link not found: {link_id}") from error
        self._write_snapshot(manifest, pages, entities, links)
        return removed
