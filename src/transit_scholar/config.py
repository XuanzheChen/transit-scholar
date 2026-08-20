"""Central configuration for TransitScholar.

All paths are derived from a single root directory so that tests and smoke
runs can point the whole subsystem at an isolated location.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Default name of the SQLite database file lived under data/database/.
DATABASE_FILENAME = "transit_scholar.db"


def _load_project_dotenv() -> None:
    """Load the project-root ``.env`` without overriding real env vars.

    ``override=False`` keeps the shell environment as the source of truth so
    ``TRANSIT_SCHOLAR_DATA_DIR`` (set by smoke tests / CI) is never clobbered
    by whatever sits in ``.env``. Falls back to a minimal parser when
    ``python-dotenv`` is not installed so tests still run.
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:  # pragma: no cover - fallback for dotenv-less envs
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def _env_bool(name: str, default: str = "false") -> bool:
    """Read a boolean-style env var (``true/1/yes``) with a default."""
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes")


def _env_int(name: str) -> int | None:
    """Read an integer env var, returning ``None`` when unset or invalid."""
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


_load_project_dotenv()


@dataclass
class Settings:
    """Immutable-ish settings object used across the project.

    The ``data_root`` can be overridden via the ``TRANSIT_SCHOLAR_DATA_DIR``
    environment variable, which is how smoke tests avoid polluting the real
    ``data/`` tree.
    """

    data_root: Path = Path(os.environ.get("TRANSIT_SCHOLAR_DATA_DIR", "data"))

    # --- path derivations ---------------------------------------------------
    @property
    def database_dir(self) -> Path:
        return self.data_root / "database"

    @property
    def database_path(self) -> Path:
        return self.database_dir / DATABASE_FILENAME

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def library_root(self) -> Path:
        return self.data_root / "library"

    @property
    def originals_dir(self) -> Path:
        return self.library_root / "originals"

    @property
    def temporary_dir(self) -> Path:
        return self.library_root / "temporary"

    @property
    def trash_dir(self) -> Path:
        return self.library_root / "trash"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    # --- Layer2 paths --------------------------------------------------------
    @property
    def layer2_dir(self) -> Path:
        return self.data_root / "layer2"

    @property
    def layer2_parsed_dir(self) -> Path:
        return self.layer2_dir / "parsed"

    @property
    def layer2_retrieval_dir(self) -> Path:
        return self.layer2_dir / "retrieval"

    @property
    def layer2_schema_dir(self) -> Path:
        """L2S2 schema run storage root (created on demand by the storage layer)."""
        return self.layer2_dir / "schemas"

    def layer2_parsed_paper_dir(self, paper_id: str) -> Path:
        return self.layer2_parsed_dir / paper_id

    # --- import / parsing configuration ------------------------------------
    allowed_mime_types: list[str] = field(
        default_factory=lambda: ["application/pdf"]
    )
    max_file_size_bytes: int = 100 * 1024 * 1024  # 100 MiB
    light_parse_page_count: int = 3  # pages read for lightweight metadata

    # --- deduplication thresholds -------------------------------------------
    duplicate_high_threshold: float = 0.95
    duplicate_probable_threshold: float = 0.85
    duplicate_weak_threshold: float = 0.70

    # --- doi metadata enrichment --------------------------------------------
    metadata_enrichment_enabled: bool = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_ENABLED", "true"
        ).lower() == "true")
    metadata_enrichment_strict_doi: bool = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_STRICT_DOI", "true"
        ).lower() == "true")
    metadata_enrichment_allow_network: bool = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_ALLOW_NETWORK", "false"
        ).lower() == "true")

    openalex_api_key: str | None = field(
        default_factory=lambda: os.environ.get("OPENALEX_API_KEY") or None)
    semantic_scholar_api_key: str | None = field(
        default_factory=lambda: os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or None)
    crossref_mailto: str | None = field(
        default_factory=lambda: os.environ.get("CROSSREF_MAILTO") or None)

    # --- Layer2 cloud provider configuration -----------------------------------
    # Read via the project-root ``.env`` / process environment (override=False),
    # never written to manifests or logs. ``layer2_block_network`` is an
    # explicit offline gate for tests and smoke runs.
    layer2_embedding_provider: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_EMBEDDING_PROVIDER") or None)
    layer2_embedding_api_key: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_EMBEDDING_API_KEY") or None)
    layer2_embedding_model: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_EMBEDDING_MODEL") or None)
    layer2_embedding_dimension: int | None = field(
        default_factory=lambda: _env_int("TRANSIT_SCHOLAR_EMBEDDING_DIMENSION"))
    jina_api_key: str | None = field(
        default_factory=lambda: os.environ.get("JINA_API_KEY") or None)
    layer2_reranker_provider: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_RERANKER_PROVIDER") or None)
    layer2_reranker_api_key: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_RERANKER_API_KEY") or None)
    layer2_reranker_model: str | None = field(
        default_factory=lambda: os.environ.get(
            "TRANSIT_SCHOLAR_RERANKER_MODEL") or None)
    layer2_retrieval_allow_network: bool = field(
        default_factory=lambda: _env_bool(
            "TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK", "false"))
    layer2_block_network: bool = field(
        default_factory=lambda: _env_bool(
            "TRANSIT_SCHOLAR_BLOCK_NETWORK", "false"))

    # --- directory initialisation ------------------------------------------
    def init_directories(self) -> None:
        """Create every data directory if it does not exist yet."""
        for directory in (
            self.database_dir,
            self.originals_dir,
            self.temporary_dir,
            self.trash_dir,
            self.logs_dir,
            self.layer2_dir,
            self.layer2_parsed_dir,
            self.layer2_retrieval_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# Module-level default instance. Importing code can either use this directly
# or construct its own ``Settings`` with a custom ``data_root``.
settings = Settings()
