"""Unified, immutable catalog for built-in and user schema definitions."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schema_extraction.hashing import compute_schema_hash
from .schema_extraction.loader import (
    InvalidSchemaDefinitionError,
    SchemaPluginNotFoundError,
    get_schema_definition,
    list_schema_plugins,
)
from .schema_extraction.models import SchemaDefinition


class SchemaCatalogError(ValueError):
    pass


class SchemaNotFoundError(SchemaCatalogError):
    pass


class SchemaVersionExistsError(SchemaCatalogError):
    pass


class SchemaCatalog:
    """Resolve schemas from plugins and immutable user storage."""

    def __init__(self, data_root: str | Path | None = None):
        if data_root is None:
            from transit_scholar.config import settings
            data_root = settings.data_root
        self.root = Path(data_root) / "schemas"

    def _user_path(self, schema_id: str, version: str) -> Path:
        if not self._is_safe_identity(schema_id) or not self._is_safe_identity(version):
            raise SchemaCatalogError("schema_id and version must be simple schema path components")
        return self.root / schema_id / f"{version}.json"

    @staticmethod
    def _is_safe_identity(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)) and value not in {".", ".."}

    def _user_definitions(self) -> list[SchemaDefinition]:
        if not self.root.is_dir():
            return []
        result = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                result.append(SchemaDefinition.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, ValidationError):
                continue
        return result

    def list(self) -> list[SchemaDefinition]:
        builtins = [get_schema_definition(schema_id) for schema_id in list_schema_plugins()]
        merged = {(item.schema_id, item.version): item for item in self._user_definitions()}
        merged.update({(item.schema_id, item.version): item for item in builtins})
        return [merged[key] for key in sorted(merged)]

    def resolve(self, schema_id: str, version: str | None = None) -> SchemaDefinition:
        if version is not None:
            try:
                definition = get_schema_definition(schema_id)
                if definition.version == version:
                    return definition
            except (SchemaPluginNotFoundError, InvalidSchemaDefinitionError):
                pass
            path = self._user_path(schema_id, version)
            if path.is_file():
                try:
                    return SchemaDefinition.model_validate(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, ValidationError) as exc:
                    raise SchemaCatalogError(f"invalid stored schema {schema_id!r}/{version!r}") from exc
            raise SchemaNotFoundError(f"schema {schema_id!r} version {version!r} not found")
        try:
            definition = get_schema_definition(schema_id)
            return definition
        except (SchemaPluginNotFoundError, InvalidSchemaDefinitionError):
            matches = [item for item in self._user_definitions() if item.schema_id == schema_id]
            if not matches:
                raise SchemaNotFoundError(f"schema {schema_id!r} not found")
            return sorted(matches, key=lambda item: item.version)[-1]

    def validate(self, draft: Any) -> tuple[bool, list[dict[str, Any]], SchemaDefinition | None]:
        try:
            definition = draft if isinstance(draft, SchemaDefinition) else SchemaDefinition.model_validate(draft)
            self._user_path(definition.schema_id, definition.version)
            return True, [], definition
        except ValidationError as exc:
            return False, [{"type": e.get("type", "validation_error"), "loc": list(e.get("loc", ())), "message": e.get("msg", "invalid schema")} for e in exc.errors()], None
        except SchemaCatalogError as exc:
            return False, [{"type": "invalid_schema_identity", "loc": [], "message": str(exc)}], None

    def create(self, draft: Any) -> SchemaDefinition:
        valid, issues, definition = self.validate(draft)
        if not valid or definition is None:
            raise SchemaCatalogError(json.dumps(issues))
        path = self._user_path(definition.schema_id, definition.version)
        if path.exists() or any(item.schema_id == definition.schema_id and item.version == definition.version for item in self.list()):
            raise SchemaVersionExistsError(f"schema {definition.schema_id!r} version {definition.version!r} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(definition.model_dump_json(indent=2))
        except FileExistsError as exc:
            raise SchemaVersionExistsError(
                f"schema {definition.schema_id!r} version {definition.version!r} already exists"
            ) from exc
        return definition

    def describe(self, definition: SchemaDefinition) -> dict[str, Any]:
        return {"schema_id": definition.schema_id, "version": definition.version, "name": definition.name, "description": definition.description, "schema_hash": compute_schema_hash(definition), "definition": definition.model_dump(mode="json")}
