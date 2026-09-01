"""Layer3 Stage5 Role contracts."""

from .models import *
from .registry import RoleRegistry, UnregisteredRoleError, built_in_role_registry


def __getattr__(name: str):
    if name.endswith("Role") or name == "BuiltinRoleRuntimeConfig":
        from transit_scholar.layer3 import roles

        try:
            return getattr(roles, name)
        except AttributeError:
            pass
    raise AttributeError(name)
