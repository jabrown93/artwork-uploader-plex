"""Chunked uploads are isolated per Socket.IO client: two browser tabs uploading a file with
the same name must never share a temp file, one client's decode must never block another's
chunk from being accepted, and a client that disconnects mid-upload must only lose its own
pending upload.

Side-effecting plumbing (update_log/update_status/notify_web/debug_me/log_to_file/
resume_log_file/RunHistory) is stubbed out: it is upstream's run-tracking and progress-bar
instrumentation, orthogonal to the isolation mechanics under test here.
"""

import base64
import os
import re
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace

import pytest
from flask import Flask, request

import web_routes
from core import globals
from core.config import Config

pytestmark = pytest.mark.unit


class FakeSocket:
    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register


@pytest.fixture
def upload_handlers(monkeypatch, tmp_path):
    app_module = ModuleType("artwork_uploader")
    imported_names = (
        "process_scrape_url_from_web",
        "request_scrape_stop",
        "run_bulk_import_scrape_in_thread",
        "save_bulk_import_file",
        "load_bulk_import_file",
        "rename_bulk_import_file",
        "delete_bulk_import_file",
        "add_file_to_schedule_thread",
        "update_scheduled_jobs",
        "get_latest_version",
        "check_image_orientation",
        "sort_key",
        "process_uploaded_artwork",
    )
    for name in imported_names:
        setattr(app_module, name, lambda *args, **kwargs: None)
    setattr(app_module, "current_version", "test")
    monkeypatch.setitem(sys.modules, "artwork_uploader", app_module)

    config = Config(config_path=str(tmp_path / "config.json"))
    config.load()
    globals.config = config
    globals.plex = SimpleNamespace(reconnect=lambda cfg: None)
    globals.scrapes_running = 0
    globals.cancel_scrape = False
    globals.scrape_type = "stopped"
    globals.main_bar = {"active": False}
    socket = FakeSocket()
    globals.web_socket = socket

    monkeypatch.setattr(web_routes, "update_log", lambda *a, **k: None)
    monkeypatch.setattr(web_routes, "update_status", lambda *a, **k: None)
    monkeypatch.setattr(web_routes, "notify_web", lambda *a, **k: None)
    monkeypatch.setattr(web_routes, "debug_me", lambda *a, **k: None)
    monkeypatch.setattr(web_routes, "log_to_file", lambda label: "test.log")
    monkeypatch.setattr(web_routes, "resume_log_file", lambda path: None)
    monkeypatch.setattr(web_routes, "RunHistory", lambda: SimpleNamespace(add_run=lambda **k: None))

    real_named_temporary_file = tempfile.NamedTemporaryFile
    created_files = []

    def create_temp_file(*args, **kwargs):
        temp_file = real_named_temporary_file(*args, dir=tmp_path, **kwargs)
        created_files.append(temp_file)
        return temp_file

    monkeypatch.setattr(web_routes.tempfile, "NamedTemporaryFile", create_temp_file)
    web_routes.setup_socket_handlers(config, re.compile(".*"))

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"

    def emit(event, sid, data=None):
        with app.test_request_context("/socket.io/"):
            request.sid = sid
            if data is None:
                return socket.handlers[event]()
            return socket.handlers[event](data)

    return emit, created_files


def chunk_payload(contents, *, chunk_index=0, total_chunks=1):
    return {
        "instance_id": "browser-instance",
        "fileName": "set.zip",
        "chunkData": base64.b64encode(contents).decode(),
        "chunkIndex": chunk_index,
        "totalChunks": total_chunks,
    }


def complete_payload():
    return {
        "instance_id": "browser-instance",
        "fileName": "set.zip",
        "options": [],
        "filters": [],
        "plex_title": None,
        "plex_year": None,
    }


def emit_in_thread(emit, event, sid, data):
    errors = []

    def run():
        try:
            emit(event, sid, data)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_same_filename_is_isolated_between_clients(upload_handlers, monkeypatch):
    emit, created_files = upload_handlers
    processed = []

    def save_uploaded_file(*args):
        with open(args[6], "rb") as uploaded_file:
            processed.append(uploaded_file.read())

    monkeypatch.setattr(web_routes, "save_uploaded_file", save_uploaded_file)

    emit("upload_artwork_chunk", "client-a", chunk_payload(b"client-a"))
    emit("upload_artwork_chunk", "client-b", chunk_payload(b"client-b"))
    emit("upload_complete", "client-a", complete_payload())
    emit("upload_complete", "client-b", complete_payload())

    assert processed == [b"client-a", b"client-b"]
    assert len({temp_file.name for temp_file in created_files}) == 2
    assert all(temp_file.closed for temp_file in created_files)
    assert all(not os.path.exists(temp_file.name) for temp_file in created_files)


def test_decoding_one_client_does_not_block_another(upload_handlers, monkeypatch):
    emit, _ = upload_handlers
    real_decode = base64.b64decode
    first_started = threading.Event()
    release_first = threading.Event()
    second_decoded = threading.Event()

    def controlled_decode(value):
        decoded = real_decode(value)
        if decoded == b"client-a":
            first_started.set()
            release_first.wait(2)
        else:
            second_decoded.set()
        return decoded

    monkeypatch.setattr(web_routes.base64, "b64decode", controlled_decode)
    first, first_errors = emit_in_thread(
        emit, "upload_artwork_chunk", "client-a", chunk_payload(b"client-a")
    )
    assert first_started.wait(1)
    second, second_errors = emit_in_thread(
        emit, "upload_artwork_chunk", "client-b", chunk_payload(b"client-b")
    )
    decoded_without_waiting = second_decoded.wait(1)
    release_first.set()
    first.join(2)
    second.join(2)

    assert decoded_without_waiting
    assert not first.is_alive()
    assert not second.is_alive()
    assert first_errors + second_errors == []
    emit("disconnect", "client-a")
    emit("disconnect", "client-b")


def test_concurrent_decodes_are_written_in_chunk_order(upload_handlers, monkeypatch):
    emit, _ = upload_handlers
    processed = []
    real_decode = base64.b64decode
    first_started = threading.Event()
    release_first = threading.Event()

    def controlled_decode(value):
        decoded = real_decode(value)
        if decoded == b"first":
            first_started.set()
            release_first.wait(2)
        return decoded

    def save_uploaded_file(*args):
        with open(args[6], "rb") as uploaded_file:
            processed.append(uploaded_file.read())

    monkeypatch.setattr(web_routes.base64, "b64decode", controlled_decode)
    monkeypatch.setattr(web_routes, "save_uploaded_file", save_uploaded_file)
    first, first_errors = emit_in_thread(
        emit,
        "upload_artwork_chunk",
        "client-a",
        chunk_payload(b"first", chunk_index=0, total_chunks=2),
    )
    assert first_started.wait(1)
    second, second_errors = emit_in_thread(
        emit,
        "upload_artwork_chunk",
        "client-a",
        chunk_payload(b"second", chunk_index=1, total_chunks=2),
    )
    second.join(1)
    second_finished_first = not second.is_alive()
    release_first.set()
    first.join(2)
    second.join(2)

    assert second_finished_first
    assert not first.is_alive()
    assert first_errors + second_errors == []
    emit("upload_complete", "client-a", complete_payload())
    assert processed == [b"firstsecond"]


def test_completion_waits_for_registered_decode(upload_handlers, monkeypatch):
    emit, _ = upload_handlers
    processed = []
    real_decode = base64.b64decode
    decode_started = threading.Event()
    release_decode = threading.Event()

    def controlled_decode(value):
        decode_started.set()
        release_decode.wait(2)
        return real_decode(value)

    def save_uploaded_file(*args):
        with open(args[6], "rb") as uploaded_file:
            processed.append(uploaded_file.read())

    monkeypatch.setattr(web_routes.base64, "b64decode", controlled_decode)
    monkeypatch.setattr(web_routes, "save_uploaded_file", save_uploaded_file)
    chunk, chunk_errors = emit_in_thread(
        emit, "upload_artwork_chunk", "client-a", chunk_payload(b"complete")
    )
    assert decode_started.wait(1)
    complete, complete_errors = emit_in_thread(
        emit, "upload_complete", "client-a", complete_payload()
    )
    completion_waited = complete.is_alive()
    release_decode.set()
    chunk.join(2)
    complete.join(2)

    assert completion_waited
    assert not chunk.is_alive()
    assert not complete.is_alive()
    assert chunk_errors + complete_errors == []
    assert processed == [b"complete"]


def test_disconnect_removes_clients_pending_upload(upload_handlers):
    emit, created_files = upload_handlers
    emit(
        "upload_artwork_chunk",
        "client-a",
        chunk_payload(b"partial", total_chunks=2),
    )
    temp_file = created_files[0]

    emit("disconnect", "client-a")

    assert temp_file.closed
    assert not os.path.exists(temp_file.name)


def test_disconnect_only_affects_the_disconnecting_client(upload_handlers):
    emit, created_files = upload_handlers
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"partial-a", total_chunks=2))
    emit(
        "upload_artwork_chunk",
        "client-b",
        {**chunk_payload(b"partial-b", total_chunks=2), "fileName": "other.zip"},
    )
    file_a, file_b = created_files

    emit("disconnect", "client-a")

    assert file_a.closed and not os.path.exists(file_a.name)
    assert not file_b.closed
    assert os.path.exists(file_b.name)
    emit("disconnect", "client-b")


def test_chunk_error_removes_pending_upload(upload_handlers, monkeypatch):
    emit, created_files = upload_handlers
    monkeypatch.setattr(
        web_routes.base64,
        "b64decode",
        lambda value: (_ for _ in ()).throw(ValueError("bad chunk")),
    )

    emit("upload_artwork_chunk", "client-a", chunk_payload(b"invalid"))

    temp_file = created_files[0]
    assert temp_file.closed
    assert not os.path.exists(temp_file.name)


def test_first_chunk_replaces_abandoned_same_client_upload(upload_handlers):
    emit, created_files = upload_handlers
    emit(
        "upload_artwork_chunk",
        "client-a",
        chunk_payload(b"old", total_chunks=2),
    )
    abandoned_file = created_files[0]

    emit("upload_artwork_chunk", "client-a", chunk_payload(b"replacement"))

    assert abandoned_file.closed
    assert not os.path.exists(abandoned_file.name)


def test_duplicate_chunk_index_is_rejected(upload_handlers):
    emit, created_files = upload_handlers

    emit("upload_artwork_chunk", "client-a", chunk_payload(b"first", chunk_index=0, total_chunks=3))
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"second", chunk_index=1, total_chunks=3))
    # A resend of a non-zero chunk index is not legitimate (only chunk 0 replaces an abandoned
    # upload); it must tear the upload down rather than silently accept it.
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"dup", chunk_index=1, total_chunks=3))

    temp_file = created_files[0]
    assert temp_file.closed
    assert not os.path.exists(temp_file.name)


def test_concurrent_uploads_keep_separate_run_metadata(upload_handlers, monkeypatch):
    """Fork: run_label/start_time/log_file live on the per-upload entry (keyed by
    sid+file_name), not a shared global. Two concurrent uploads of different ZIPs must each
    keep their own run metadata even though the second upload's first chunk lands before the
    first upload completes."""
    emit, _ = upload_handlers
    captured = []

    def save_uploaded_file(*args):
        captured.append({"file_name": args[1], "run_label": args[10]})

    monkeypatch.setattr(web_routes, "save_uploaded_file", save_uploaded_file)

    def payload(file_name):
        return {
            "instance_id": "browser-instance",
            "fileName": file_name,
            "chunkData": base64.b64encode(b"data").decode(),
            "chunkIndex": 0,
            "totalChunks": 1,
        }

    def complete(file_name):
        return {
            "instance_id": "browser-instance",
            "fileName": file_name,
            "options": [],
            "filters": [],
            "plex_title": None,
            "plex_year": None,
        }

    # client-a starts uploading first.zip...
    emit("upload_artwork_chunk", "client-a", payload("first.zip"))
    # ...then client-b starts a concurrent upload of second.zip before client-a finishes.
    emit("upload_artwork_chunk", "client-b", payload("second.zip"))

    emit("upload_complete", "client-a", complete("first.zip"))
    emit("upload_complete", "client-b", complete("second.zip"))

    labels = {entry["file_name"]: entry["run_label"] for entry in captured}
    assert labels == {"first.zip": "first", "second.zip": "second"}


@pytest.mark.parametrize("failure_point", ["extract", "process", "setup"])
def test_processing_exception_releases_only_its_upload(upload_handlers, monkeypatch, failure_point):
    emit, created_files = upload_handlers
    events = []
    monkeypatch.setattr(web_routes, "notify_web", lambda instance, event, data, **kw: events.append((event, data)))

    def fail(*args, **kwargs):
        raise RuntimeError("processing failed")

    if failure_point == "setup":
        monkeypatch.setattr(web_routes.tempfile, "mkdtemp", fail)
    else:
        monkeypatch.setattr(
            web_routes,
            "extract_and_list_zip",
            fail if failure_point == "extract" else lambda *args: ([], 0, "", "", ""),
        )
        if failure_point == "process":
            monkeypatch.setattr(sys.modules["artwork_uploader"], "process_uploaded_artwork", fail)

    emit("upload_artwork_chunk", "client-a", chunk_payload(b"archive"))
    emit("upload_artwork_chunk", "client-b", chunk_payload(b"pending", total_chunks=2))

    with pytest.raises(RuntimeError, match="processing failed"):
        emit("upload_complete", "client-a", complete_payload())

    assert globals.scrapes_running == 1
    assert globals.scrape_type == "upload"
    assert not any(event == "scrape_state" and not data["running"] for event, data in events)
    assert not os.path.exists(created_files[0].name)

    # Completion removed client-a's entry: its disconnect must not release client-b's run.
    emit("disconnect", "client-a")
    assert globals.scrapes_running == 1
    emit("disconnect", "client-b")
    assert globals.scrapes_running == 0
    assert globals.scrape_type == "stopped"
    assert any(event == "scrape_state" and not data["running"] for event, data in events)


@pytest.mark.parametrize("cancel_during", ["extract", "process", None])
def test_processing_completion_releases_one_run(upload_handlers, monkeypatch, cancel_during):
    emit, _ = upload_handlers
    events = []
    monkeypatch.setattr(web_routes, "notify_web", lambda instance, event, data, **kw: events.append((event, data)))

    def extract(*args):
        if cancel_during == "extract":
            globals.cancel_scrape = True
        return [], 0, "", "", ""

    def process(*args, **kwargs):
        if cancel_during == "process":
            globals.cancel_scrape = True

    monkeypatch.setattr(web_routes, "extract_and_list_zip", extract)
    monkeypatch.setattr(sys.modules["artwork_uploader"], "process_uploaded_artwork", process)
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"archive"))
    emit("upload_artwork_chunk", "client-b", chunk_payload(b"pending", total_chunks=2))
    emit("upload_complete", "client-a", complete_payload())

    assert globals.scrapes_running == 1
    assert globals.scrape_type == "upload"
    assert not any(event == "scrape_state" and not data["running"] for event, data in events)
    emit("disconnect", "client-a")
    assert globals.scrapes_running == 1
    emit("disconnect", "client-b")
    assert globals.scrapes_running == 0
    assert globals.scrape_type == "stopped"
    assert globals.cancel_scrape is False


def test_last_processing_failure_clears_run_state_and_progress(upload_handlers, monkeypatch):
    emit, created_files = upload_handlers
    events = []
    monkeypatch.setattr(web_routes, "notify_web", lambda instance, event, data, **kw: events.append((event, data)))

    def fail(*args):
        raise RuntimeError("invalid archive")

    monkeypatch.setattr(web_routes, "extract_and_list_zip", fail)
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"archive"))
    globals.cancel_scrape = True

    with pytest.raises(RuntimeError, match="invalid archive"):
        emit("upload_complete", "client-a", complete_payload())

    assert globals.scrapes_running == 0
    assert globals.scrape_type == "stopped"
    assert globals.cancel_scrape is False
    assert globals.main_bar["active"] is False
    assert not os.path.exists(created_files[0].name)
    assert [data["running"] for event, data in events if event == "scrape_state"] == [True, False]

    emit("upload_complete", "client-a", complete_payload())
    emit("disconnect", "client-a")
    assert globals.scrapes_running == 0
    assert [data["running"] for event, data in events if event == "scrape_state"] == [True, False]


def test_second_temp_directory_failure_removes_first_and_releases_run(upload_handlers, monkeypatch, tmp_path):
    emit, created_files = upload_handlers
    real_mkdtemp = tempfile.mkdtemp
    created_dirs = []

    def mkdtemp(*args, **kwargs):
        if created_dirs:
            raise OSError("cannot create extraction directory")
        path = real_mkdtemp(*args, dir=tmp_path, **kwargs)
        created_dirs.append(path)
        return path

    monkeypatch.setattr(web_routes.tempfile, "mkdtemp", mkdtemp)
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"archive"))

    with pytest.raises(OSError, match="cannot create extraction directory"):
        emit("upload_complete", "client-a", complete_payload())

    assert globals.scrapes_running == 0
    assert globals.scrape_type == "stopped"
    assert not os.path.exists(created_files[0].name)
    assert not os.path.exists(created_dirs[0])


def test_completion_log_failure_releases_run(upload_handlers, monkeypatch):
    emit, created_files = upload_handlers
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"archive"))

    def fail(*args, **kwargs):
        raise RuntimeError("logging failed")

    monkeypatch.setattr(web_routes, "update_log", fail)
    with pytest.raises(RuntimeError, match="logging failed"):
        emit("upload_complete", "client-a", complete_payload())

    assert globals.scrapes_running == 0
    assert globals.scrape_type == "stopped"
    assert globals.main_bar["active"] is False
    assert not os.path.exists(created_files[0].name)
