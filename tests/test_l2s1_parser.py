"""Layer2 Step1 parser adapter tests (AC-L2S1-PARSER-01..05)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from transit_scholar.layer2.parser.base import ParserItem
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    read_artifacts,
    run_parse,
)

MANIFEST_KEYS = {
    "source_sha256",
    "parser_name",
    "parser_version",
    "parser_config",
    "parser_config_hash",
    "canonical_schema_version",
    "normalizer_version",
    "renderer_version",
    "chunker_version",
    "embedding_model",
    "reranker_model",
    "created_at",
}


def test_parser_unified_interface_exists():
    """AC-L2S1-PARSER-01: a unified adapter interface exists for primary /
    fallback / diagnostic adapters."""
    from transit_scholar.layer2.parser.base import (
        ParserAdapter,
        all_registered_names,
    )

    registered = set(all_registered_names())
    assert {"docling", "mineru", "pymupdf4llm", "pymupdf_native", "fake"} <= registered
    assert ParserAdapter.__abstractmethods__  # interface is enforced


def test_parser_swapping_adapter_only_changes_parser_identity(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-PARSER-01: swapping adapters changes only parser_name/version in
    the manifest without changing canonical/render/chunk shape."""
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.pipeline import parse_paper
    from tests.l2s1_fixtures import make_ready_paper

    items = canonical_fixture_items()
    adapter_a = FakeParserAdapter(items=items, page_count=1)
    adapter_b = FakeParserAdapter(items=items, page_count=1, version="2.0.0")
    shapes: list[str] = []
    for adapter in (adapter_a, adapter_b):
        paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path, title="Swap Paper")
        monkeypatch.setattr(
            "transit_scholar.layer2.pipeline.resolve_parsers",
            lambda config, a=adapter: [a],
        )
        # Pin the deterministic local store so this test is independent of
        # whether lancedb happens to be installed.
        config = Layer2Config.from_settings(Settings(data_root=project_tmp_path))
        object.__setattr__(config, "store", "local")
        result = parse_paper(paper_id, config=config)
        assert result.status == "passed"
        read_config = Layer2Config.from_settings(Settings(data_root=project_tmp_path))
        object.__setattr__(read_config, "store", "local")
        artifacts = read_artifacts(
            read_config,
            paper_id,
            result.parse_run_id,
        )
        manifest = artifacts["manifest"]
        assert manifest["parser_name"] == "fake"
        assert manifest["parser_version"] == adapter.version
        blocks = [b.to_dict() for b in artifacts["blocks"]]
        for block in blocks:
            block.pop("paper_id", None)
        chunks = []
        for line in artifacts["chunks"].splitlines():
            if line.strip():
                chunk = json.loads(line)
                chunk.pop("paper_id", None)
                chunk.pop("parse_run_id", None)
                chunks.append(chunk)
        shapes.append(
            json.dumps(
                (blocks, artifacts["markdown"], chunks),
                sort_keys=True,
            )
        )
    # canonical block shapes, markdown and chunks are identical across adapter
    # versions; only parser identity differs.
    assert shapes[0] == shapes[1]


def test_parser_manifest_complete(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-PARSER-02: the parse run manifest carries all required keys with
    values matching the used adapter."""
    result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch
    )
    _, _, _, parse_result = result
    artifacts = read_artifacts(l2_config, parse_result.paper_id, parse_result.parse_run_id)
    manifest = artifacts["manifest"]
    for key in MANIFEST_KEYS:
        assert key in manifest, f"manifest missing {key}"
        assert manifest.get(key) not in (None, "", {}), f"manifest {key} empty"
    assert manifest["parser_name"] == "fake"
    assert manifest["source_sha256"]  # non-empty
    assert manifest["canonical_schema_version"] == "1.0"
    assert manifest["normalizer_version"] == "1.0"


def test_parser_dependency_missing_is_explicit(
    monkeypatch, l2_config, project_tmp_path
):
    """AC-L2S1-PARSER-03: a parser adapter whose dependency is missing returns
    an explicit dependency_missing status and parse_paper surfaces a structured
    blocker without writing fake canonical files."""
    from transit_scholar.layer2.parser.base import ParserResult
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, pdf_path = run_parse(
        project_tmp_path,
        canonical_fixture_items(),
        monkeypatch=monkeypatch,
    )[:3]

    class _MissingDepAdapter(FakeParserAdapter):
        name = "missing_dep"

        def availability(self):
            return self.availability.__class__  # placeholder

    # simpler: a fake adapter that always reports dependency_missing
    from transit_scholar.layer2.parser.base import ParserInfo

    class _DepMissingAdapter(FakeParserAdapter):
        name = "missing_dep"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._info = ParserInfo(
                name="missing_dep", version="0", config={}, config_hash="h"
            )

        def parse(self, path):
            return ParserResult(
                status="dependency_missing",
                info=self._info,
                error_code="DEPENDENCY_MISSING",
                error_message="not installed",
            )

    from transit_scholar.layer2.pipeline import parse_paper as _parse

    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.resolve_parsers",
        lambda config: [_DepMissingAdapter()],
    )
    result = _parse(paper_id, config=l2_config)
    assert result.status == "needs_review"
    assert result.error_code == "DEPENDENCY_MISSING"
    assert result.parse_run_id is not None
    # manifest written, but no canonical files (no fake canonical output)
    from transit_scholar.layer2.paths import run_paths

    rp = run_paths(l2_config, paper_id, result.parse_run_id)
    assert rp.manifest_path.is_file()
    assert not rp.document_path.exists()
    assert not rp.blocks_path.exists()


def test_parser_fake_works_regardless_of_heavy_deps(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-PARSER-04 (install-state independent): the deterministic fake
    parser drives the full pipeline whether or not docling/mineru/pymupdf4llm/
    lancedb are installed. Unavailable branches are verified via mocking (see
    ``test_parser_heavy_dep_missing_branch_via_monkeypatch``), never by
    asserting that the heavy extras are absent."""
    _, _, _, result = run_parse(
        project_tmp_path,
        canonical_fixture_items(),
        monkeypatch=monkeypatch,
        page_count=1,
        l2_config=l2_config,
    )
    assert result.status == "passed"
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    assert artifacts["document"].parser_name == "fake"
    assert artifacts["manifest"]["parser_version"] not in ("2.x", "latest", "0.x")


def test_parser_heavy_dep_missing_branch_via_monkeypatch(monkeypatch):
    """AC-L2S1-PARSER-03: the real adapters' ``dependency_missing`` branch is
    reached by monkeypatching the import (not by assuming the dependency is
    uninstalled), so the suite passes with or without the ``layer2`` extras."""
    import sys

    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    monkeypatch.setitem(sys.modules, "docling", None)
    availability = DoclingParserAdapter().availability()
    assert availability.available is False
    assert availability.reason == "dependency_missing"
    result = DoclingParserAdapter().parse("whatever.pdf")
    assert result.status == "dependency_missing"
    assert result.error_code == "DEPENDENCY_MISSING"
    assert result.items == []


def test_parser_docling_iterate_items_yields_item_level(monkeypatch):
    """Docling >= 2.x compatibility: ``document.iterate_items()`` yields
    ``(item, level)`` tuples; the adapter unpacks them (instead of treating the
    tuple as an element) and records heading level + provenance."""
    import sys
    import types

    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    class _Label:
        def __init__(self, value: str) -> None:
            self.value = value

    class _BBox:
        def __init__(self, values: list[float]) -> None:
            self._values = values

        def as_tuple(self) -> list[float]:
            return self._values

    class _Prov:
        def __init__(self, page_no: int) -> None:
            self.page_no = page_no
            self.bbox = _BBox([10.0, 20.0, 500.0, 600.0])

    class _Item:
        def __init__(self, label: str, text: str, level: int) -> None:
            self.label = _Label(label)
            self.text = text
            self.level = level
            self.prov = [_Prov(1)]

    class _Document:
        def __init__(self, items: list[_Item]) -> None:
            self._items = items
            self.pages = {1: object()}

        def iterate_items(self):
            for item in self._items:
                yield item, item.level

    class _Status:
        value = "success"

    class _Result:
        def __init__(self, document: _Document) -> None:
            self.document = document
            self.status = _Status()
            self.errors = []

    class _Converter:
        def __init__(self, document: _Document) -> None:
            self._document = document

        def convert(self, pdf_path: str) -> _Result:
            return _Result(self._document)

    document = _Document(
        [
            _Item("section_header", "Introduction", 1),
            _Item("paragraph", "Reinforcement learning for holding control.", 1),
            _Item("formula", "r_t = -w \\cdot wait_t", 2),
        ]
    )
    fake_docling = types.ModuleType("docling")
    fake_docling.document_converter = types.ModuleType("docling.document_converter")
    fake_docling.document_converter.DocumentConverter = lambda: _Converter(document)
    monkeypatch.setitem(sys.modules, "docling", fake_docling)
    monkeypatch.setitem(
        sys.modules, "docling.document_converter", fake_docling.document_converter
    )

    result = DoclingParserAdapter().parse("dummy.pdf")
    assert result.status == "ok"
    assert [it.item_type for it in result.items] == [
        "heading",
        "paragraph",
        "equation",
    ]
    assert result.items[0].level == 1
    assert result.items[2].level == 2
    assert result.items[1].page == 1
    assert result.items[1].bbox == [10.0, 20.0, 500.0, 600.0]
    assert result.page_count == 1


def test_parser_mineru_availability_checks_callable_entry(monkeypatch):
    """MinerU 3.x compatibility: availability checks a real callable entry for
    the installed version, not just ``import mineru``."""
    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: None)
    unavailable = MinerUParserAdapter().availability()
    assert unavailable.available is False
    assert unavailable.reason == "dependency_missing"

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)
    available = MinerUParserAdapter().availability()
    assert available.available is True
    assert available.version == "3.4.4"

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "2.2.1")
    old = MinerUParserAdapter().availability()
    assert old.available is False
    assert old.reason == "unsupported_version"

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: False)
    missing_entry = MinerUParserAdapter().availability()
    assert missing_entry.available is False
    assert missing_entry.reason == "parser_unavailable"


def test_parser_mineru_parse_mocks_local_entry(monkeypatch, tmp_path):
    """MinerU 3.x compatibility: the adapter converts the local pipeline's
    markdown output through the public entry seam without running models."""
    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)

    def fake_pipeline(pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.md").write_text(
            "# Introduction\n\nReinforcement learning controls bus holding.",
            encoding="utf-8",
        )

    monkeypatch.setattr(mineru_mod, "_invoke_local_pipeline", fake_pipeline)

    result = MinerUParserAdapter().parse(str(tmp_path / "x.pdf"))
    assert result.status == "ok"
    headings = [it for it in result.items if it.item_type == "heading"]
    assert len(headings) == 1
    assert headings[0].level == 1
    assert headings[0].content.get("level") == 1
    assert any("reinforcement" in it.text.lower() for it in result.items)


def test_parser_pymupdf4llm_heading_level_fixed(monkeypatch):
    """PyMuPDF4LLM compat: heading items carry a defined level/content (the
    real-PDF path previously raised ``NameError`` on the undefined ``level``)."""
    import transit_scholar.layer2.parser.pymupdf4llm as pymupdf4llm_mod
    from transit_scholar.layer2.parser.base import ParserAvailability
    from transit_scholar.layer2.parser.pymupdf4llm import Pymupdf4LLMParserAdapter

    monkeypatch.setattr(
        Pymupdf4LLMParserAdapter,
        "availability",
        lambda self: ParserAvailability(available=True, version="1.28.2"),
    )
    monkeypatch.setattr(
        pymupdf4llm_mod,
        "_call_pymupdf4llm",
        lambda path: (
            "# Introduction\n\nBody text.\n\n## Sub section\n\nMore body text."
        ),
    )

    result = Pymupdf4LLMParserAdapter().parse("x.pdf")
    assert result.status == "ok"
    headings = [it for it in result.items if it.item_type == "heading"]
    assert [it.level for it in headings] == [1, 2]
    assert [it.content.get("level") for it in headings] == [1, 2]
    paragraphs = [it for it in result.items if it.item_type == "paragraph"]
    assert paragraphs and paragraphs[0].level == 1


def test_parser_docling_artifacts_path_applied_and_recorded(monkeypatch, tmp_path):
    """DOCLING_ARTIFACTS_PATH is applied through the installed Docling public
    ``PdfPipelineOptions.artifacts_path`` API and the actually-applied value
    enters the converter config (manifest facts, not declared intentions)."""
    import sys
    import types

    from transit_scholar.layer2.config import (
        DOCLING_ARTIFACTS_PATH_ENV,
        resolve_docling_artifacts_path,
    )
    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    artifacts_dir = tmp_path / "models"
    artifacts_dir.mkdir()
    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, str(artifacts_dir))

    captured: dict[str, Any] = {}

    class _FakePdfPipelineOptions:
        def __init__(self) -> None:
            self.artifacts_path = None
            self.do_table_structure = None
            self.do_picture_description = None
            self.do_chart_extraction = None
            self.do_picture_classification = None
            self.do_formula_enrichment = None

    class _FakePdfFormatOption:
        def __init__(self, pipeline_options=None) -> None:
            captured["pipeline_options"] = pipeline_options

    class _FakeDocumentConverter:
        def __init__(self, format_options=None) -> None:
            captured["format_options"] = format_options
            captured["converter_built"] = True

    fake_docling = types.ModuleType("docling")
    fake_base_models = types.ModuleType("docling.datamodel.base_models")
    fake_pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    fake_converter_mod = types.ModuleType("docling.document_converter")
    class _FakeInputFormat:
        PDF = object()

    fake_base_models.InputFormat = _FakeInputFormat
    fake_pipeline_options.PdfPipelineOptions = _FakePdfPipelineOptions
    fake_converter_mod.PdfFormatOption = _FakePdfFormatOption
    fake_converter_mod.DocumentConverter = _FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling", fake_docling)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.base_models", fake_base_models
    )
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", fake_pipeline_options
    )
    monkeypatch.setitem(
        sys.modules, "docling.document_converter", fake_converter_mod
    )

    adapter = DoclingParserAdapter()
    assert adapter.config["artifacts_path"] == str(artifacts_dir)

    converter, applied, warnings = adapter._make_converter()
    assert converter is not None
    assert captured["converter_built"] is True
    assert captured["pipeline_options"].artifacts_path == artifacts_dir
    assert applied["artifacts"]["applied"] is True
    assert applied["artifacts"]["path"] == str(artifacts_dir)
    assert applied["artifacts"]["env"] == DOCLING_ARTIFACTS_PATH_ENV
    assert warnings == []
    assert resolve_docling_artifacts_path() == str(artifacts_dir)


def test_parser_docling_artifacts_path_invalid_dir_structured_error(monkeypatch, tmp_path):
    """A DOCLING_ARTIFACTS_PATH pointing at a non-directory is a structured
    failure (DOCLING_ARTIFACTS_PATH_INVALID), never a silent fallback to the
    default model download behavior."""
    import sys
    import types

    from transit_scholar.layer2.config import DOCLING_ARTIFACTS_PATH_ENV
    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    monkeypatch.setenv(
        DOCLING_ARTIFACTS_PATH_ENV, str(tmp_path / "does_not_exist")
    )

    class _FakePdfPipelineOptions:
        def __init__(self) -> None:
            self.artifacts_path = None

    fake_docling = types.ModuleType("docling")
    fake_base_models = types.ModuleType("docling.datamodel.base_models")
    fake_pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    fake_converter_mod = types.ModuleType("docling.document_converter")
    class _FakeInputFormat:
        PDF = object()

    fake_base_models.InputFormat = _FakeInputFormat
    fake_pipeline_options.PdfPipelineOptions = _FakePdfPipelineOptions
    fake_converter_mod.PdfFormatOption = object()
    fake_converter_mod.DocumentConverter = object()
    monkeypatch.setitem(sys.modules, "docling", fake_docling)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.base_models", fake_base_models
    )
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", fake_pipeline_options
    )
    monkeypatch.setitem(
        sys.modules, "docling.document_converter", fake_converter_mod
    )

    result = DoclingParserAdapter().parse("dummy.pdf")
    assert result.status == "error"
    assert result.error_code == "DOCLING_ARTIFACTS_PATH_INVALID"
    assert "does_not_exist" in (result.error_message or "")


def test_parser_docling_artifacts_path_unset_keeps_default_behavior(monkeypatch):
    """Without DOCLING_ARTIFACTS_PATH the default model-download behavior is
    kept and the recorded config states the artifacts path is None."""
    import sys
    import types

    from transit_scholar.layer2.config import DOCLING_ARTIFACTS_PATH_ENV
    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    monkeypatch.delenv(DOCLING_ARTIFACTS_PATH_ENV, raising=False)

    class _FakePdfPipelineOptions:
        def __init__(self) -> None:
            self.artifacts_path = None

    class _FakePdfFormatOption:
        def __init__(self, pipeline_options=None) -> None:
            pass

    class _FakeDocumentConverter:
        def __init__(self, format_options=None) -> None:
            pass

    fake_docling = types.ModuleType("docling")
    fake_base_models = types.ModuleType("docling.datamodel.base_models")
    fake_pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    fake_converter_mod = types.ModuleType("docling.document_converter")

    class _FakeInputFormat:
        PDF = object()

    fake_base_models.InputFormat = _FakeInputFormat
    fake_pipeline_options.PdfPipelineOptions = _FakePdfPipelineOptions
    fake_converter_mod.PdfFormatOption = _FakePdfFormatOption
    fake_converter_mod.DocumentConverter = _FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling", fake_docling)
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.base_models", fake_base_models
    )
    monkeypatch.setitem(
        sys.modules, "docling.datamodel.pipeline_options", fake_pipeline_options
    )
    monkeypatch.setitem(
        sys.modules, "docling.document_converter", fake_converter_mod
    )

    adapter = DoclingParserAdapter()
    assert adapter.config["artifacts_path"] is None
    converter, applied, warnings = adapter._make_converter()
    assert converter is not None
    assert applied["artifacts"]["path"] is None
    assert applied["artifacts"]["applied"] is False
    assert "unset" in applied["artifacts"]["note"]
    assert warnings == []


def test_parser_docling_config_hash_tracks_artifacts_path(monkeypatch, tmp_path):
    """The Docling adapter config hash changes with DOCLING_ARTIFACTS_PATH so
    benchmark/resume unit keys invalidate when the model directory changes."""
    from transit_scholar.layer2.config import DOCLING_ARTIFACTS_PATH_ENV
    from transit_scholar.layer2.parser.docling import DoclingParserAdapter

    monkeypatch.delenv(DOCLING_ARTIFACTS_PATH_ENV, raising=False)
    base_hash = DoclingParserAdapter().config_hash

    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, str(tmp_path / "m1"))
    hash_m1 = DoclingParserAdapter().config_hash
    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, str(tmp_path / "m2"))
    hash_m2 = DoclingParserAdapter().config_hash

    assert base_hash != hash_m1
    assert hash_m1 != hash_m2


def test_parser_mineru_middle_json_structured_items(monkeypatch, tmp_path):
    """MinerU structured ``*_middle.json`` drives ParserItems with real
    page numbers (page_idx+1), bbox, heading levels, table HTML, captions and
    LaTeX; page_count comes from the structured artifact (never 0/None)."""
    import json

    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)

    middle = {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [1000, 1400],
                "para_blocks": [
                    {
                        "type": "title", "level": 1, "index": 0,
                        "bbox": [50.0, 40.0, 950.0, 80.0],
                        "lines": [{"spans": [{"type": "text", "content": "Introduction", "bbox": [50, 40, 950, 80]}]}],
                    },
                    {
                        "type": "text", "index": 1,
                        "bbox": [50.0, 90.0, 950.0, 140.0],
                        "lines": [{"spans": [{"type": "text", "content": "Reinforcement learning controls bus holding.", "bbox": [50, 90, 950, 140]}]}],
                    },
                    {
                        "type": "interline_equation", "index": 2,
                        "bbox": [50.0, 150.0, 950.0, 175.0],
                        "lines": [{"spans": [{"type": "equation", "content": "r_t = -w \\cdot wait_t", "bbox": [50, 150, 950, 175]}]}],
                    },
                ],
            },
            {
                "page_idx": 1,
                "page_size": [1000, 1400],
                "para_blocks": [
                    {
                        "type": "table", "index": 0,
                        "bbox": [50.0, 60.0, 950.0, 200.0],
                        "blocks": [
                            {
                                "type": "table_body",
                                "lines": [{"spans": [{"type": "table", "html": "<table><tr><td>1</td></tr></table>", "bbox": []}]}],
                            },
                            {
                                "type": "table_caption",
                                "lines": [{"spans": [{"type": "text", "content": "Table 1. Results."}]}],
                            },
                        ],
                    },
                    {
                        "type": "image", "index": 1,
                        "bbox": [50.0, 220.0, 950.0, 400.0],
                        "blocks": [
                            {
                                "type": "image_caption",
                                "lines": [{"spans": [{"type": "text", "content": "Figure 1. Illustration."}]}],
                            },
                        ],
                    },
                ],
            },
        ]
    }

    def fake_pipeline(pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper_middle.json").write_text(
            json.dumps(middle), encoding="utf-8"
        )
        (output_dir / "paper.md").write_text("# fallback must not be used", encoding="utf-8")

    monkeypatch.setattr(mineru_mod, "_invoke_local_pipeline", fake_pipeline)

    result = MinerUParserAdapter().parse(str(tmp_path / "x.pdf"))
    assert result.status == "ok"
    assert result.page_count == 2
    assert result.warnings == []
    assert result.info.config["output_mode"] == "middle_json"
    assert result.info.config["page_count_source"] == "mineru structured artifact"

    by_page: dict[int, list[str]] = {}
    for item in result.items:
        assert item.page in (1, 2)
        by_page.setdefault(item.page, []).append(item.item_type)
        if item.item_type == "heading":
            assert item.level == 1
            assert item.content["level"] == 1
        if item.bbox is not None:
            assert len(item.bbox) == 4

    assert by_page[1] == ["heading", "paragraph", "equation"]
    assert by_page[2] == ["table", "caption", "figure", "caption"]
    assert result.items[0].text == "Introduction"
    equation = [it for it in result.items if it.item_type == "equation"][0]
    assert equation.content["latex"] == "r_t = -w \\cdot wait_t"
    table = [it for it in result.items if it.item_type == "table"][0]
    assert "<table>" in table.text
    assert table.content["markdown"].startswith("<table>")
    captions = [it.text for it in result.items if it.item_type == "caption"]
    assert captions == ["Table 1. Results.", "Figure 1. Illustration."]


def test_parser_mineru_prefers_middle_json_over_content_list(monkeypatch, tmp_path):
    """When both structured artifacts exist, ``*_middle.json`` wins (richest
    structure: page_idx + bbox)."""
    import json

    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)

    def fake_pipeline(pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper_middle.json").write_text(
            json.dumps(
                {
                    "pdf_info": [
                        {
                            "page_idx": 0,
                            "page_size": [100, 100],
                            "para_blocks": [
                                {
                                    "type": "text", "index": 0,
                                    "lines": [{"spans": [{"type": "text", "content": "structured middle text"}]}],
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "paper_content_list.json").write_text(
            json.dumps(
                {
                    "pdf_info": [
                        {
                            "page_idx": 0,
                            "content_list": [{"type": "text", "text": "content-list text"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(mineru_mod, "_invoke_local_pipeline", fake_pipeline)

    result = MinerUParserAdapter().parse(str(tmp_path / "x.pdf"))
    assert result.status == "ok"
    assert result.info.config["output_mode"] == "middle_json"
    assert any("structured middle text" in it.text for it in result.items)
    assert not any("content-list text" in it.text for it in result.items)


def test_parser_mineru_content_list_fallback_when_middle_invalid(monkeypatch, tmp_path):
    """An unparsable middle json falls back to ``*_content_list.json``
    (page numbers preserved, warning recorded, bbox absent)."""
    import json

    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)

    def fake_pipeline(pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper_middle.json").write_text("{ not json", encoding="utf-8")
        (output_dir / "paper_content_list.json").write_text(
            json.dumps(
                {
                    "pdf_info": [
                        {
                            "page_idx": 0,
                            "content_list": [
                                {"type": "text", "text": "content list body"}
                            ],
                        },
                        {
                            "page_idx": 1,
                            "content_list": [
                                {"type": "title", "text_level": 2, "text": "Section Two"}
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(mineru_mod, "_invoke_local_pipeline", fake_pipeline)

    result = MinerUParserAdapter().parse(str(tmp_path / "x.pdf"))
    assert result.status == "ok"
    assert result.page_count == 2
    assert result.info.config["output_mode"] == "content_list"
    assert any("content list body" in it.text for it in result.items)
    assert any(it.item_type == "heading" and it.level == 2 for it in result.items)
    assert any(warning for warning in result.warnings if "_content_list.json" in warning)


def test_parser_mineru_markdown_fallback_when_no_structured(monkeypatch, tmp_path):
    """Without any structured artifact the markdown fallback keeps a readable
    body with page=0 items (no fabricated provenance) and an explicit warning;
    the pipeline would report degraded, not a fabricated hard failure."""
    import transit_scholar.layer2.parser.mineru as mineru_mod
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter

    monkeypatch.setattr(mineru_mod, "_mineru_version", lambda: "3.4.4")
    monkeypatch.setattr(mineru_mod, "_mineru_entry_available", lambda: True)

    def fake_pipeline(pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.md").write_text(
            "# Introduction\n\nReinforcement learning controls bus holding.",
            encoding="utf-8",
        )

    monkeypatch.setattr(mineru_mod, "_invoke_local_pipeline", fake_pipeline)

    result = MinerUParserAdapter().parse(str(tmp_path / "x.pdf"))
    assert result.status == "ok"
    assert result.info.config["output_mode"] == "markdown_fallback"
    headings = [it for it in result.items if it.item_type == "heading"]
    assert len(headings) == 1
    assert headings[0].content.get("level") == 1
    assert all(it.page == 0 for it in result.items)
    assert all(it.bbox is None for it in result.items)
    assert any("fell back to markdown" in warning for warning in result.warnings)


def test_parser_pymupdf4llm_page_chunks_real_pages(monkeypatch):
    """pymupdf4llm ``page_chunks=True`` output (list of per-page dicts) keeps
    real page numbers; the legacy string shape emits page=0 + warning."""
    import transit_scholar.layer2.parser.pymupdf4llm as pymupdf4llm_mod
    from transit_scholar.layer2.parser.base import ParserAvailability
    from transit_scholar.layer2.parser.pymupdf4llm import Pymupdf4LLMParserAdapter

    monkeypatch.setattr(
        Pymupdf4LLMParserAdapter,
        "availability",
        lambda self: ParserAvailability(available=True, version="1.28.2"),
    )
    monkeypatch.setattr(
        pymupdf4llm_mod,
        "_call_pymupdf4llm",
        lambda path: [
            {
                "metadata": {"page": 0},
                "text": "# Introduction\n\nBody text page one.",
            },
            {
                "metadata": {"page": 1},
                "text": "## Sub section\n\nMore body text page two.",
            },
        ],
    )

    result = Pymupdf4LLMParserAdapter().parse("x.pdf")
    assert result.status == "ok"
    assert result.warnings == []
    assert result.info.config["output_shape"] == "page_chunks_list"
    assert all(it.page in (1, 2) for it in result.items)
    assert [it.page for it in result.items][:2] == [1, 1]
    headings = [it for it in result.items if it.item_type == "heading"]
    assert [it.level for it in headings] == [1, 2]
    assert [it.page for it in headings] == [1, 2]

    # layout-path shape: 1-based metadata["page_number"]
    monkeypatch.setattr(
        pymupdf4llm_mod,
        "_call_pymupdf4llm",
        lambda path: [
            {"metadata": {"page_count": 2, "page_number": 1}, "text": "Page one."},
            {"metadata": {"page_count": 2, "page_number": 2}, "text": "Page two."},
        ],
    )
    result = Pymupdf4LLMParserAdapter().parse("x.pdf")
    assert result.status == "ok"
    assert [it.page for it in result.items] == [1, 2]

    # single-dict shape (one page)
    monkeypatch.setattr(
        pymupdf4llm_mod,
        "_call_pymupdf4llm",
        lambda path: {"metadata": {"page": 0}, "text": "Single page body."},
    )
    result = Pymupdf4LLMParserAdapter().parse("x.pdf")
    assert result.status == "ok"
    assert result.warnings == []
    assert [it.page for it in result.items] == [1]


def test_parser_pymupdf4llm_legacy_string_degraded_warning(monkeypatch):
    """A legacy plain-string return shape is tolerated: page=0 items with an
    explicit warning (degraded provenance, not a fabricated hard failure)."""
    import transit_scholar.layer2.parser.pymupdf4llm as pymupdf4llm_mod
    from transit_scholar.layer2.parser.base import ParserAvailability
    from transit_scholar.layer2.parser.pymupdf4llm import Pymupdf4LLMParserAdapter

    monkeypatch.setattr(
        Pymupdf4LLMParserAdapter,
        "availability",
        lambda self: ParserAvailability(available=True, version="1.28.2"),
    )
    monkeypatch.setattr(
        pymupdf4llm_mod,
        "_call_pymupdf4llm",
        lambda path: "# Introduction\n\nBody text.\n\n## Sub section\n\nMore body text.",
    )

    result = Pymupdf4LLMParserAdapter().parse("x.pdf")
    assert result.status == "ok"
    assert result.info.config["output_shape"] == "legacy_string"
    assert all(it.page == 0 for it in result.items)
    assert any("legacy" in warning for warning in result.warnings)


def test_parser_versions_from_installed_metadata(monkeypatch):
    """Adapter version fields come from installed package metadata, never
    hardcoded ``2.x`` / ``latest`` / ``0.x`` placeholders."""
    import transit_scholar.layer2.util as layer2_util
    from transit_scholar.layer2.parser.docling import DoclingParserAdapter
    from transit_scholar.layer2.parser.mineru import MinerUParserAdapter
    from transit_scholar.layer2.parser.pymupdf4llm import Pymupdf4LLMParserAdapter

    for adapter in (
        DoclingParserAdapter(),
        MinerUParserAdapter(),
        Pymupdf4LLMParserAdapter(),
    ):
        version = adapter.version
        assert isinstance(version, str) and version
        assert version not in ("2.x", "latest", "0.x", "1.x")

    # when the dependency is absent the version is a stable explicit marker
    monkeypatch.setattr(layer2_util, "dependency_version", lambda dist: None)
    assert DoclingParserAdapter().version == "unavailable"
    assert MinerUParserAdapter().version == "unavailable"
    assert Pymupdf4LLMParserAdapter().version == "unavailable"


def test_parser_no_llm_repair_or_voting_in_source():
    """AC-L2S1-PARSER-05: the Layer2 source contains no LLM repair / multi-parser
    voting constructs and config flags them disabled."""
    import pathlib

    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.config import Settings

    config = Layer2Config.from_settings(Settings(data_root="data"))
    assert config.llm_parser_repair is False
    assert config.multi_parser_item_voting is False

    layer2_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "transit_scholar" / "layer2"
    forbidden = (
        "from openai",
        "import openai",
        "from anthropic",
        "import anthropic",
        "langchain",
        "chat.completions",
        "client.chat",
        # LLM generation happens through a client/model object method call
        # (``client.generate(``); a bare ``generate(`` may be a plain helper
        # name, so the guard keys on the method-call form only
        ".generate(",
    )
    for path in layer2_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for term in forbidden:
            if term.lower() in lowered:
                raise AssertionError(
                    f"{path} contains forbidden construct {term!r}"
                )
