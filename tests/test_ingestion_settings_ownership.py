from types import SimpleNamespace

import pytest

from transit_scholar.ingestion import file_ops
from transit_scholar.ingestion.errors import IngestionError


def test_cleanup_uses_application_directory(tmp_path, monkeypatch):
    global_dir = tmp_path / "global" / "job"
    app_dir = tmp_path / "application" / "job"
    for directory in (global_dir, app_dir):
        directory.mkdir(parents=True)
        (directory / "source.pdf").write_bytes(b"%PDF-owned")
    monkeypatch.setattr(file_ops, "settings", SimpleNamespace(temporary_dir=global_dir.parent))
    file_ops.cleanup_temporary("job", settings_obj=SimpleNamespace(temporary_dir=app_dir.parent))
    assert not app_dir.exists()
    assert (global_dir / "source.pdf").read_bytes() == b"%PDF-owned"


def test_size_error_reports_application_limit(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-too-large")
    monkeypatch.setattr(file_ops, "settings", SimpleNamespace(max_file_size_bytes=99999))
    with pytest.raises(IngestionError) as error:
        file_ops.validate_source_file(source, settings_obj=SimpleNamespace(max_file_size_bytes=4))
    assert "limit 4" in error.value.message
    assert "99999" not in error.value.message
