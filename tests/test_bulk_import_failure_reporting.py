"""Bulk import failures (a line that can't be scraped, or an upload that fails) are counted
and surface in the completion message, not swallowed silently.

The counting itself lives inside ArtworkProcessor.scrape_and_process via
ProcessingCallbacks.record_result(), which reads the emoji prefix of whatever
UploadProcessor.process_movie_artwork returns. Mocking at the Scraper/UploadProcessor level
(rather than mocking scrape_and_upload itself, which upstream's single-flight tests do for a
different purpose) keeps that real counting/message-building path under test.
"""

from types import SimpleNamespace

import pytest

import core.globals as globals
import services.artwork_processor as artwork_processor_module
from artwork_uploader import process_bulk_import_from_ui
from core.exceptions import ScraperException
from models.instance import Instance
from models.options import Options

pytestmark = pytest.mark.unit


class FailedScraper:
    """Finds one movie, which the upload processor below fails on."""

    def __init__(self, url, callbacks=None):
        self.url = url
        self.callbacks = callbacks
        self.title = "Failed Set"
        self.author = "Artist"
        self.source = "mediux"
        self.total = 1
        self.skipped = 0
        self.exclusions = 0
        self.filtered = 0
        self.errored = 0
        self.artist_assets = {}
        self.collection_artwork = []
        self.movie_artwork = [{"title": "Failed Movie", "year": 1999}]
        self.tv_artwork = []

    def set_options(self, options):
        self.options = options

    def scrape(self):
        pass


class RaisingScraper(FailedScraper):
    """A line that cannot be scraped at all - counted by the bulk-import loop itself."""

    def scrape(self):
        raise ScraperException("could not reach the provider")


class FailedUploadProcessor:
    def __init__(self, plex):
        self.plex = plex
        self.artist_assets = {}

    def set_options(self, options):
        self.options = options

    def process_movie_artwork(self, artwork):
        return ["❌ Failed Movie | failed to update poster in Movies"]


@pytest.fixture
def bulk_import_env(monkeypatch):
    import threading

    globals.bulk_import_lock = threading.Lock()
    globals.scrapes_running = 0
    globals.cancel_scrape = False
    globals.plex = SimpleNamespace(
        tv_libraries=["TV"],
        movie_libraries=["Movies"],
        ensure_libraries=lambda: True,
        connect=lambda: None,
    )
    globals.config = SimpleNamespace(apprise_urls=[])

    logs = []
    statuses = []
    notifications = []

    def fake_update_log(instance, message, *args, **kwargs):
        logs.append(message)

    def fake_update_status(instance, message, color=None, **kwargs):
        statuses.append((message, color))

    def fake_send_notification(instance, message, *args, **kwargs):
        notifications.append(message)

    monkeypatch.setattr("artwork_uploader.update_log", fake_update_log)
    monkeypatch.setattr("artwork_uploader.update_status", fake_update_status)
    monkeypatch.setattr("artwork_uploader.notify_web", lambda *a, **k: None)
    monkeypatch.setattr("artwork_uploader.send_notification", fake_send_notification)
    monkeypatch.setattr("artwork_uploader.debug_me", lambda *a, **k: None)

    try:
        yield SimpleNamespace(logs=logs, statuses=statuses, notifications=notifications)
    finally:
        if globals.bulk_import_lock.locked():
            globals.bulk_import_lock.release()
        globals.cancel_scrape = False
        globals.scrapes_running = 0
        globals.plex = None
        globals.config = None


@pytest.mark.unit
def test_a_failed_upload_reports_bulk_import_errors(monkeypatch, bulk_import_env):
    monkeypatch.setattr(artwork_processor_module, "Scraper", FailedScraper)
    monkeypatch.setattr(artwork_processor_module, "UploadProcessor", FailedUploadProcessor)
    parsed_urls = [SimpleNamespace(url="https://mediux.pro/sets/123", options=Options())]

    process_bulk_import_from_ui(Instance(mode="cli"), parsed_urls, "nightly.txt", notify=True)

    completion = next(message for message in bulk_import_env.logs if message.startswith("⚠️"))
    assert "with 1 error(s)" in completion
    assert "1 asset(s) processed" in completion
    assert "0 asset(s) updated" in completion
    assert "completed successfully" not in completion
    assert bulk_import_env.statuses[-1][1] == "warning"
    assert bulk_import_env.notifications == [completion]


@pytest.mark.unit
def test_a_line_that_cannot_be_scraped_is_logged_and_counted(monkeypatch, bulk_import_env):
    monkeypatch.setattr(artwork_processor_module, "Scraper", RaisingScraper)
    monkeypatch.setattr(artwork_processor_module, "UploadProcessor", FailedUploadProcessor)
    url = "https://theposterdb.com/sets/456"
    parsed_urls = [SimpleNamespace(url=url, options=Options())]

    process_bulk_import_from_ui(Instance(mode="cli"), parsed_urls, "nightly.txt", notify=True)

    assert f"❌ Error processing line: '{url}'" in bulk_import_env.logs
    completion = next(message for message in bulk_import_env.logs if message.startswith("⚠️"))
    assert "with 1 error(s)" in completion
    assert bulk_import_env.statuses[-1][1] == "warning"
    assert bulk_import_env.notifications == [completion]
