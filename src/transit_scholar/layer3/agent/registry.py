"""Static design-time Role registry and built-in definitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from .models import RoleDefinition, RoleId, RoleRuntimeProfile


class UnregisteredRoleError(LookupError):
    pass


class RoleRegistry:
    """Immutable lookup of Roles supplied by implementation configuration."""

    def __init__(self, definitions: Iterable[RoleDefinition]) -> None:
        indexed = {definition.role_id: definition for definition in definitions}
        self._definitions: Mapping[RoleId, RoleDefinition] = MappingProxyType(indexed)

    def get(self, role_id: RoleId | str) -> RoleDefinition:
        try:
            normalized = RoleId(role_id)
            return self._definitions[normalized]
        except (ValueError, KeyError) as exc:
            raise UnregisteredRoleError(f"Role is not registered: {role_id}") from exc

    def __contains__(self, role_id: object) -> bool:
        try:
            return RoleId(role_id) in self._definitions  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return False

    def list(self) -> tuple[RoleDefinition, ...]:
        return tuple(self._definitions.values())


def built_in_role_registry(
    profile_overrides: Mapping[RoleId | str, RoleRuntimeProfile | Mapping[str, object]] | None = None,
) -> RoleRegistry:
    from transit_scholar.layer3.roles import BuiltinRoleRuntimeConfig, built_in_roles

    config = BuiltinRoleRuntimeConfig.with_overrides(profile_overrides)
    return RoleRegistry(built_in_roles(config))


__all__ = ["RoleRegistry", "UnregisteredRoleError", "built_in_role_registry"]
