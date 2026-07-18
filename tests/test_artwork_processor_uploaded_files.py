"""
Characterization tests for ArtworkProcessor.process_uploaded_files.

Exercises the method end-to-end with a stubbed UploadProcessor and recording
callbacks, to pin down current behavior before refactoring.
"""

import os

import pytest

import services.artwork_processor as artwork_processor_module
from core.exceptions import (
    CollectionNotFound,
    MovieNotFound,
    NotProcessedByExclusion,
    NotProcessedByFilter,
    ShowNotFound,
)
from models.options import Options
from services.artwork_processor import ArtworkProcessor, ProcessingCallbacks

pytestmark = pytest.mark.unit


class StubUploadProcessor:
    """Stand-in for UploadProcessor; each call returns/raises per queued behavior."""

    def __init__(self, plex):
        self.plex = plex
        self.options = None
        self.collection_calls = []
        self.movie_calls = []
        self.tv_calls = []
        self.collection_result = ["✅ collection uploaded"]
        self.movie_result = ["✅ movie uploaded"]
        self.tv_result = ["✅ tv uploaded"]

    def set_options(self, options):
        self.options = options

    def process_collection_artwork(self, artwork):
        self.collection_calls.append(artwork)
        return _resolve(self.collection_result, artwork)

    def process_movie_artwork(self, artwork):
        self.movie_calls.append(artwork)
        return _resolve(self.movie_result, artwork)

    def process_tv_artwork(self, artwork):
        self.tv_calls.append(artwork)
        return _resolve(self.tv_result, artwork)


def _resolve(result, artwork):
    if isinstance(result, Exception):
        raise result
    if callable(result):
        return result(artwork)
    return result


class RecordingCallbacks:
    """Records every callback invocation for assertions."""

    def __init__(self):
        self.debug_calls = []
        self.log_calls = []
        self.progress_calls = []
        self.status_calls = []

    def as_processing_callbacks(self):
        return ProcessingCallbacks(
            on_status_update=lambda *a: self.status_calls.append(a),
            on_log_update=lambda msg: self.log_calls.append(msg),
            on_progress_update=lambda cur, total: self.progress_calls.append((cur, total)),
            on_debug=lambda msg, ctx: self.debug_calls.append((msg, ctx)),
        )


@pytest.fixture(autouse=True)
def _stub_upload_processor(monkeypatch):
    stub_holder = {}

    def factory(plex):
        stub = StubUploadProcessor(plex)
        stub_holder["instance"] = stub
        return stub

    monkeypatch.setattr(artwork_processor_module, "UploadProcessor", factory)
    yield stub_holder


def _make_temp_artwork(tmp_path, **overrides):
    d = tmp_path / "asset_dir"
    d.mkdir(exist_ok=True)
    f = d / "artwork.jpg"
    f.write_bytes(b"fake")
    artwork = {
        "media": "Movie",
        "title": "Some Movie",
        "year": 2020,
        "author": "SomeAuthor",
        "season": None,
        "episode": None,
        "path": str(f),
    }
    artwork.update(overrides)
    return artwork


class TestSuccessPath:
    def test_movie_artwork_processed_and_counted(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title="Some Title", zip_author="Some Author",
            zip_source="tpdb", options=Options(),
            callbacks=recorder.as_processing_callbacks(),
        )

        stub = _stub_upload_processor["instance"]
        assert len(stub.movie_calls) == 1
        assert "✅ movie uploaded" in recorder.log_calls
        assert not os.path.exists(artwork["path"])
        assert not os.path.exists(os.path.dirname(artwork["path"]))
        assert any("1 asset(s) updated" in msg for msg in recorder.log_calls)

    def test_collection_artwork_dispatches_to_collection_processor(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, media="Collection", title="Some Saga", year=None)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        stub = _stub_upload_processor["instance"]
        assert len(stub.collection_calls) == 1

    def test_tv_show_artwork_dispatches_to_tv_processor(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, media="TV Show", title="Some Show", season=1, episode=2)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        stub = _stub_upload_processor["instance"]
        assert len(stub.tv_calls) == 1

    def test_multiple_results_only_checkmark_and_recycle_count_as_success(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        # Patch the stub's movie_result after construction via the factory hook
        def make_results(_artwork):
            return ["✅ one", "♻️ two", "⚠️ three not a success"]

        import services.artwork_processor as mod

        def factory(plex):
            stub = StubUploadProcessor(plex)
            stub.movie_result = ["✅ one", "♻️ two", "⚠️ three not a success"]
            return stub

        import unittest.mock as mock
        with mock.patch.object(mod, "UploadProcessor", factory):
            processor.process_uploaded_files(
                [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
                options=Options(), callbacks=recorder.as_processing_callbacks(),
            )
        assert any("2 asset(s) updated" in msg for msg in recorder.log_calls)

    def test_override_title_applied_to_artwork(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, title="Original Title")
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
            override_title="Overridden Title",
        )
        stub = _stub_upload_processor["instance"]
        assert stub.movie_calls[0]["title"] == "Overridden Title"


class TestUnavailableMediaType:
    def test_unavailable_logs_warning_and_cleans_up_temp_file(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, media="unavailable", title="Ghost Movie", year=1999)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        assert any("Not available on Plex" in msg for msg in recorder.log_calls)
        assert not os.path.exists(artwork["path"])
        stub = _stub_upload_processor["instance"]
        assert stub.movie_calls == []
        assert stub.collection_calls == []
        assert stub.tv_calls == []

    def test_unavailable_with_no_callbacks_does_not_crash(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, media="unavailable", title="Ghost Movie")
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=None,
        )
        assert not os.path.exists(artwork["path"])

    def test_unavailable_without_on_log_update_still_reports_not_available(self, tmp_path, _stub_upload_processor):
        # Every ProcessingCallbacks field is independently Optional, so on_log_update=None
        # must not skip the warning/cleanup/continue logic in the "unavailable" branch.
        artwork = _make_temp_artwork(tmp_path, media="unavailable", title="Ghost Movie", year=1999)
        recorder = RecordingCallbacks()
        callbacks = ProcessingCallbacks(
            on_status_update=lambda *a: recorder.status_calls.append(a),
            on_log_update=None,
            on_debug=lambda msg, ctx: recorder.debug_calls.append((msg, ctx)),
        )
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=callbacks,
        )
        stub = _stub_upload_processor["instance"]
        assert stub.movie_calls == []
        assert not any("process_func" in str(call) for call in recorder.status_calls)
        assert not os.path.exists(artwork["path"])


class TestUnknownMediaType:
    def test_unknown_media_type_logged_and_skipped(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path, media="Sculpture")
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        assert any("Unknown media type: Sculpture" in msg for msg in recorder.log_calls)
        # Unknown media type does NOT get its temp file cleaned up (continues before dispatch)
        assert os.path.exists(artwork["path"])


class TestExceptionHandling:
    @pytest.mark.parametrize("exc_cls,expected_prefix", [
        (CollectionNotFound, "⚠️"),
        (MovieNotFound, "⚠️"),
        (ShowNotFound, "⚠️"),
        (NotProcessedByExclusion, "⏩"),
        (NotProcessedByFilter, "⏩"),
    ])
    def test_known_exceptions_logged_with_expected_prefix(self, tmp_path, _stub_upload_processor, exc_cls, expected_prefix):
        artwork = _make_temp_artwork(tmp_path)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        import services.artwork_processor as mod
        import unittest.mock as mock

        def raiser(_artwork):
            raise exc_cls("boom")

        def factory(plex):
            stub = StubUploadProcessor(plex)
            stub.movie_result = raiser
            return stub

        with mock.patch.object(mod, "UploadProcessor", factory):
            processor.process_uploaded_files(
                [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
                options=Options(), callbacks=recorder.as_processing_callbacks(),
            )
        matching = [msg for msg in recorder.log_calls if msg.startswith(expected_prefix) and "boom" in msg]
        assert len(matching) == 1
        assert not os.path.exists(artwork["path"])

    def test_unexpected_exception_logs_error_and_updates_status(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        import services.artwork_processor as mod
        import unittest.mock as mock

        def raiser(_artwork):
            raise RuntimeError("kaboom")

        def factory(plex):
            stub = StubUploadProcessor(plex)
            stub.movie_result = raiser
            return stub

        with mock.patch.object(mod, "UploadProcessor", factory):
            processor.process_uploaded_files(
                [artwork], skipped=0, zip_title=None, zip_author=None, zip_source=None,
                options=Options(), callbacks=recorder.as_processing_callbacks(),
            )
        assert any("Unexpected during process_uploaded_artwork" in msg and "kaboom" in msg for msg in recorder.log_calls)
        assert any(call[0].startswith("Error:") and call[1] == "danger" for call in recorder.status_calls)
        assert not os.path.exists(artwork["path"])


class TestProgressAndSummary:
    def test_progress_updates_fire_per_item_and_at_start_end(self, tmp_path, _stub_upload_processor):
        artworks = [_make_temp_artwork(tmp_path), _make_temp_artwork(tmp_path)]
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            artworks, skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        assert (0, 2) in recorder.progress_calls
        assert (1, 2) in recorder.progress_calls
        assert (2, 2) in recorder.progress_calls

    def test_empty_file_list_logs_no_files_message(self, _stub_upload_processor):
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [], skipped=0, zip_title=None, zip_author=None, zip_source=None,
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        assert any(msg == "No files to process in uploaded ZIP file" for msg, ctx in recorder.debug_calls)

    def test_skipped_count_included_in_summary_log(self, tmp_path, _stub_upload_processor):
        artwork = _make_temp_artwork(tmp_path)
        recorder = RecordingCallbacks()
        processor = ArtworkProcessor(plex=object())

        processor.process_uploaded_files(
            [artwork], skipped=3, zip_title="Title", zip_author="Author", zip_source="mediux",
            options=Options(), callbacks=recorder.as_processing_callbacks(),
        )
        assert any("Skipping 3 asset(s)" in msg for msg in recorder.log_calls)
