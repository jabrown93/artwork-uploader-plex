"""Fork: the run-log viewer only serves files from the log directory."""

import pytest

import web_routes


def _fetch(web_harness, name):
    client = web_harness.socket_client()
    client.emit("get_log_file", {"instance_id": "t", "log_file_name": name})
    replies = [r for r in client.get_received() if r["name"] == "get_log_file"]
    return replies[-1]["args"][0]


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    (tmp_path / "upload_run.log").write_text("run output")
    (tmp_path / "config.json").write_text('{"token": "secret"}')
    monkeypatch.setattr(web_routes, "DEFAULT_LOG_PATH", str(tmp_path))
    return tmp_path


@pytest.mark.unit
def test_a_run_log_is_served(web_harness, log_dir):
    reply = _fetch(web_harness, "upload_run.log")
    assert reply["success"] and reply["contents"] == "run output"


@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "../upload_run.log",
    "config.json",
    "/etc/passwd",
    "../../../../etc/passwd",
])
def test_anything_outside_the_log_directory_or_not_a_log_is_refused(web_harness, log_dir, name, tmp_path):
    (tmp_path.parent / "upload_run.log").write_text("sibling")
    reply = _fetch(web_harness, name)
    assert not reply["success"] and reply["contents"] == ""
