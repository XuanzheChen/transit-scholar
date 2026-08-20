"""L2S2 schema plugins.

Each plugin is a directory with a ``schema.yaml`` (a ``SchemaDefinition``)
and, optionally, domain-specific ``validators.py`` exposing
``validate(instance) -> list[ValidationIssue]``. The engine core only knows
this directory convention; it never hard-codes a domain field tree.
"""
