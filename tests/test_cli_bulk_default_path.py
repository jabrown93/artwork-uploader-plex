"""Regression test for the CLI's default bulk-file resolution.

With no explicit filename on the command line (`bulk` with no second argument), the
`__main__` block used to resolve the default bulk file as
`os.path.join("bulk_imports", config.bulk_txt)` - a path relative to the process's current
working directory. That ignores both the Docker mount
(`docker-compose.example.yml` maps `./bulk_imports:/bulk_imports`, and
`DEFAULT_BULK_IMPORTS_DIR` is `/bulk_imports` under Docker) and the `BULK_IMPORTS_DIR`
env override that `BulkFileService` already understands. It must instead go through
`globals.bulk_file_service.get_bulk_file_path(config.bulk_txt)`, the same resolution every
other bulk-file read/write in the app uses.
"""

import runpy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
ARTWORK_UPLOADER = SRC_DIR / "artwork_uploader.py"


class _FakePlexConnector:
    """Stands in for plex.plex_connector.PlexConnector so a CLI bulk run never touches the
    network. Only the methods the __main__ block calls before reaching the bulk dispatch."""

    def __init__(self, *args, **kwargs):
        pass

    def _initialize_index(self):
        pass

    def set_tv_libraries(self, *_args, **_kwargs):
        return []

    def set_movie_libraries(self, *_args, **_kwargs):
        return []


@pytest.mark.unit
def test_default_cli_bulk_run_resolves_through_bulk_file_service(tmp_path, monkeypatch, capsys):
    # Named differently from the cwd-relative "bulk_imports" the old bug joined against, so a
    # pass can only mean the fix actually resolved through BulkFileService/BULK_IMPORTS_DIR.
    bulk_dir = tmp_path / "docker_mount"
    bulk_dir.mkdir()
    # Comment-only file: processed to completion (no scrape calls) without touching Plex.
    (bulk_dir / "bulk_import.txt").write_text("# parked for now\n")

    monkeypatch.chdir(tmp_path)  # the old bug resolved the default path against this cwd
    monkeypatch.setenv("BULK_IMPORTS_DIR", str(bulk_dir))
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(sys, "argv", ["artwork_uploader.py", "bulk"])
    monkeypatch.setattr("plex.plex_connector.PlexConnector", _FakePlexConnector)

    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    runpy.run_path(str(ARTWORK_UPLOADER), run_name="__main__")

    out = capsys.readouterr().out
    assert "File not found" not in out

    history_path = tmp_path / "config" / "run_history.json"
    assert history_path.exists()
    assert "bulk_import.txt" in history_path.read_text()
