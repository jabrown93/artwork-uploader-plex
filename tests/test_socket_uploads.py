import base64
import os
import re
import sys
import tempfile
import threading
from types import ModuleType

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
        "run_bulk_import_scrape_in_thread",
        "save_bulk_import_file",
        "load_bulk_import_file",
        "rename_bulk_import_file",
        "delete_bulk_import_file",
        "add_file_to_schedule_thread",
        "update_scheduled_jobs",
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
    socket = FakeSocket()
    globals.web_socket = socket

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
    complete.join(0.1)
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
    emit("disconnect", "client-a")


def test_completion_error_removes_temp_files(upload_handlers, monkeypatch, tmp_path):
    emit, created_files = upload_handlers
    emit("upload_artwork_chunk", "client-a", chunk_payload(b"complete"))
    temp_file = created_files[0]
    processing_folder = tmp_path / "processing"

    def make_processing_folder():
        processing_folder.mkdir()
        return str(processing_folder)

    monkeypatch.setattr(web_routes.tempfile, "mkdtemp", make_processing_folder)
    monkeypatch.setattr(
        web_routes,
        "extract_and_list_zip",
        lambda *args: (_ for _ in ()).throw(RuntimeError("processing failed")),
    )

    with pytest.raises(RuntimeError, match="processing failed"):
        emit("upload_complete", "client-a", complete_payload())

    assert temp_file.closed
    assert not os.path.exists(temp_file.name)
    assert not processing_folder.exists()
