from types import SimpleNamespace

import pytest

import artwork_uploader
import services.artwork_processor as artwork_processor_module
from core import globals
from models.instance import Instance
from models.options import Options

pytestmark = pytest.mark.unit


class FailedScraper:
    def __init__(self, url, progress_callback=None):
        self.url = url
        self.title = "Failed Set"
        self.author = "Artist"
        self.source = "mediux"
        self.total = 1
        self.skipped = 0
        self.exclusions = 0
        self.filtered = 0
        self.collection_artwork = []
        self.movie_artwork = [{"title": "Failed Movie"}]
        self.tv_artwork = []

    def set_options(self, options):
        self.options = options

    def scrape(self):
        pass


class FailedUploadProcessor:
    def __init__(self, plex):
        self.plex = plex

    def set_options(self, options):
        self.options = options

    def process_movie_artwork(self, artwork):
        return ["❌ Failed Movie | failed to update poster in Movies"]


class RaisingUploadProcessor(FailedUploadProcessor):
    def process_movie_artwork(self, artwork):
        raise RuntimeError("unexpected upload failure")


@pytest.fixture
def bulk_ui(monkeypatch):
    logs = []
    statuses = []
    notifications = []
    debug = []
    plex = SimpleNamespace(
        tv_libraries=["TV"],
        movie_libraries=["Movies"],
        connect=lambda: None,
    )

    monkeypatch.setattr(globals, "plex", plex)
    monkeypatch.setattr(globals, "config", SimpleNamespace(apprise_urls=[]))
    monkeypatch.setattr(artwork_uploader, "update_log", lambda instance, message: logs.append(message))
    monkeypatch.setattr(
        artwork_uploader,
        "update_status",
        lambda instance, message, color=None, **kwargs: statuses.append((message, color)),
    )
    monkeypatch.setattr(artwork_uploader, "notify_web", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        artwork_uploader,
        "send_notification",
        lambda instance, message: notifications.append(message),
    )
    monkeypatch.setattr(
        artwork_uploader,
        "debug_me",
        lambda message, context=None: debug.append((message, context)),
    )

    return SimpleNamespace(
        logs=logs,
        statuses=statuses,
        notifications=notifications,
        debug=debug,
    )


@pytest.mark.parametrize(
    "upload_processor", [FailedUploadProcessor, RaisingUploadProcessor]
)
def test_all_failed_uploads_report_bulk_import_errors(
    monkeypatch, bulk_ui, upload_processor
):
    monkeypatch.setattr(artwork_processor_module, "Scraper", FailedScraper)
    monkeypatch.setattr(
        artwork_processor_module, "UploadProcessor", upload_processor
    )
    parsed_urls = [
        SimpleNamespace(url="https://mediux.pro/sets/123", options=Options())
    ]

    artwork_uploader.process_bulk_import_from_ui(
        Instance(mode="web"), parsed_urls, "nightly.txt", scheduled=True
    )

    completion = next(message for message in bulk_ui.logs if message.startswith("⚠️"))
    assert "with 1 error(s)" in completion
    assert "1 asset(s) processed • 0 asset(s) updated" in completion
    assert "completed successfully" not in completion
    assert bulk_ui.statuses[-1][1] == "warning"
    assert bulk_ui.notifications == [completion]


def test_tpdb_user_exception_is_logged_and_counted(monkeypatch, bulk_ui):
    def fail_user_scrape(*args, **kwargs):
        raise RuntimeError("TPDb unavailable")

    monkeypatch.setattr(artwork_uploader, "scrape_tpdb_user", fail_user_scrape)
    url = "https://theposterdb.com/user/example"
    parsed_urls = [SimpleNamespace(url=url, options=Options())]

    artwork_uploader.process_bulk_import_from_ui(
        Instance(mode="web"), parsed_urls, "users.txt"
    )

    assert f"❌ Error processing line: '{url}'" in bulk_ui.logs
    completion = next(message for message in bulk_ui.logs if message.startswith("⚠️"))
    assert "with 1 error(s)" in completion
    assert bulk_ui.statuses[-1][1] == "warning"
