"""Schema plugin loader (FR-A-006).

Plugins are directories under ``layer2/schema_plugins/<plugin>/schema.yaml``.
The loader only reads YAML with ``yaml.safe_load`` and validates it against
the Pydantic models; it never imports plugin Python code, so the core cannot
couple to any specific domain schema (``bus_control_rl`` in particular).

No configuration module, environment variable, or config file is consulted;
the plugin root is resolved relative to this file.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import SchemaDefinition

_PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "schema_plugins"


class SchemaPluginNotFoundError(Exception):
    """Raised when no plugin with the requested schema id exists."""


class InvalidSchemaDefinitionError(Exception):
    """Raised when a discovered ``schema.yaml`` is not a valid schema definition."""


def plugins_root() -> Path:
    """Return the plugin discovery root (overridable in tests)."""
    return _PLUGINS_ROOT


def _iter_plugin_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _load_definition(plugin_dir: Path, plugin_id: str) -> SchemaDefinition:
    yaml_path = plugin_dir / "schema.yaml"
    if not yaml_path.is_file():
        raise SchemaPluginNotFoundError(
            f"schema plugin {plugin_id!r}: no schema.yaml under {plugin_dir}"
        )
    with yaml_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise InvalidSchemaDefinitionError(
            f"schema plugin {plugin_id!r}: schema.yaml must contain a YAML mapping"
        )
    try:
        return SchemaDefinition.model_validate(raw)
    except ValidationError as exc:
        raise InvalidSchemaDefinitionError(
            f"schema plugin {plugin_id!r}: invalid schema definition: {exc}"
        ) from exc


def list_schema_plugins() -> list[str]:
    """Return the sorted list of discovered schema ids.

    A plugin is any subdirectory of the plugin root that contains a
    ``schema.yaml``. If a discovered ``schema.yaml`` is invalid, an
    ``InvalidSchemaDefinitionError`` is raised (explicit error instead of
    silently skipping).
    """
    schema_ids: list[str] = []
    for plugin_dir in _iter_plugin_dirs(plugins_root()):
        if not (plugin_dir / "schema.yaml").is_file():
            continue
        definition = _load_definition(plugin_dir, plugin_dir.name)
        schema_ids.append(definition.schema_id)
    return sorted(schema_ids)


def get_schema_definition(schema_id: str) -> SchemaDefinition:
    """Load and validate the plugin whose schema id matches ``schema_id``.

    Raises ``SchemaPluginNotFoundError`` when no plugin matches (message
    contains the requested id) and ``InvalidSchemaDefinitionError`` when a
    matching plugin's ``schema.yaml`` is invalid (message contains the plugin
    directory name).
    """
    for plugin_dir in _iter_plugin_dirs(plugins_root()):
        if not (plugin_dir / "schema.yaml").is_file():
            continue
        definition = _load_definition(plugin_dir, plugin_dir.name)
        if definition.schema_id == schema_id:
            return definition
    raise SchemaPluginNotFoundError(f"schema plugin {schema_id!r} not found")
