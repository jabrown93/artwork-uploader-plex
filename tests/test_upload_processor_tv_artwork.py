"""
Characterization tests for UploadProcessor.process_tv_artwork's "show found in
Plex" path (the multi-library loop with season/episode dispatch, filter and
exclusion checks, and Plex/Kometa upload). Complements
tests/test_upload_processor_preseed.py, which covers the "show not in Plex"
Sonarr pre-seed path.
"""

from types import SimpleNamespace

import pytest
from plexapi.exceptions import NotFound

from core import globals
from core.config import Config
from core.exceptions import NotProcessedByExclusion, NotProcessedByFilter, ShowNotFound
from kometa.kometa_saver import KometaSaver
from models.options import Options
from plex.plex_uploader import PlexUploader
from processors.upload_processor import UploadProcessor
from services.arr_service import ArrSeries

pytestmark = pytest.mark.unit


class FakePlex:
    def __init__(self, items=None, libraries=None):
        self._items = items
        self._libraries = libraries

    def find_in_library(self, item_type, artwork):
        return self._items, self._libraries


class FakeSonarr:
    def __init__(self, series=None):
        self._series = series

    def find_series(self, tmdb_id, title, year):
        return self._series


class FakeArr:
    def __init__(self, sonarr=None, tv_enabled=True):
        self.radarr = None
        self.sonarr = sonarr or FakeSonarr()
        self.movie_fallback_enabled = False
        self.tv_fallback_enabled = tv_enabled


def _tv_artwork(**overrides):
    artwork = {
        "title": "Breaking Bad", "url": "http://example.com/season.jpg", "season": 1,
        "episode": None, "year": 2008, "source": "mediux", "id": "season-1",
        "type": "season_cover", "author": "someone", "tmdb_id": 1396,
        "checksum": "abc123",
    }
    artwork.update(overrides)
    return artwork


def _arr_series(season_numbers=()):
    return ArrSeries(
        folder_name="Breaking Bad (2008)", root_folder_path="/data/media/tv",
        title="Breaking Bad", year=2008, season_numbers=set(season_numbers))


class FakeEpisode:
    def __init__(self, index, file_path):
        self.index = index
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=file_path)])]


class FakeSeason:
    def __init__(self, index, episodes):
        self.index = index
        self._episodes = episodes
        self.librarySectionTitle = "TV Shows"
        self.labels = []

    def episodes(self):
        return self._episodes

    def episode(self, number):
        for e in self._episodes:
            if e.index == number:
                return e
        raise NotFound(f"episode {number} not found")


class FakeShow:
    def __init__(self, title, seasons):
        self.title = title
        self._seasons = seasons
        self.librarySectionTitle = "TV Shows"
        self.labels = []

    def seasons(self):
        return self._seasons

    def season(self, number):
        for s in self._seasons:
            if s.index == number:
                return s
        raise NotFound(f"season {number} not found")


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.load()
    cfg.kometa_base = str(tmp_path / "assets")
    cfg.save_to_kometa = True
    cfg.preseed_arr = True
    cfg.movie_library = ["Movies"]
    cfg.tv_library = ["TV Shows"]
    cfg.stage_assets = False
    cfg.stage_specials = False
    cfg.arr_root_folder_library_map = {}
    cfg.save()
    globals.config = cfg
    globals.debug = False
    return cfg


@pytest.fixture
def capture_kometa_saves(monkeypatch):
    calls = []

    def fake_save(self):
        calls.append({
            "dest_dir": self.dest_dir,
            "dest_file_name": self.dest_file_name,
            "description": self.description,
        })
        return f"✅ {self.description} | {self.artwork_type} saved (fake)"

    monkeypatch.setattr(KometaSaver, "save_to_kometa", fake_save)
    return calls


@pytest.fixture
def capture_plex_uploads(monkeypatch):
    calls = []

    def fake_upload(self):
        calls.append({
            "upload_target": self.upload_target,
            "artwork_type": self.artwork_type,
            "description": self.description,
        })
        return f"✅ {self.description} | {self.artwork_type} updated in {self.upload_target.librarySectionTitle}"

    monkeypatch.setattr(PlexUploader, "upload_to_plex", fake_upload)
    return calls


def _show_with_season1_episode1():
    episode = FakeEpisode(1, "/data/media/tv/Breaking Bad (2008)/Season 01/S01E01.mkv")
    season1 = FakeSeason(1, [episode])
    return FakeShow("Breaking Bad", [season1])


def _processor(plex, arr=None, options=None):
    proc = UploadProcessor(plex, arr=arr or FakeArr())
    proc.set_options(options or Options(kometa=True))
    return proc


class TestWholeShowArtwork:
    """SEASON_COVER / SEASON_BACKDROP / SEASON_SQUARE_ART: uploaded to the show itself."""

    def test_show_cover_uploads_regardless_of_season_availability(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        results = proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))

        assert results[0].startswith("✅")
        assert capture_kometa_saves[0]["dest_file_name"] == "poster"

    def test_backdrop_uploads(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        results = proc.process_tv_artwork(_tv_artwork(season="Backdrop", episode=None, type="background"))

        assert results[0].startswith("✅")
        assert capture_kometa_saves[0]["dest_file_name"] == "background"

    def test_square_art_uploads(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        results = proc.process_tv_artwork(_tv_artwork(season="SquareArt", episode=None, type="square_art"))

        assert results[0].startswith("✅")
        assert capture_kometa_saves[0]["dest_file_name"] == "square"


class TestSeasonCoverInPlex:
    def test_season_present_uploads_to_kometa(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=None))

        assert results[0].startswith("✅")
        assert capture_kometa_saves[0]["dest_file_name"] == "Season01"
        assert "pre-seeded via Sonarr" not in capture_kometa_saves[0]["description"]

    def test_season_present_uploads_to_plex_when_not_kometa(self, configured, capture_plex_uploads):
        configured.save_to_kometa = False
        configured.save()
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]), options=Options(kometa=False))

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=None))

        assert results[0].startswith("✅")
        assert capture_plex_uploads[0]["upload_target"] is show.season(1)

    def test_season_missing_no_staging_no_sonarr_warns_without_raising(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]), arr=FakeArr(tv_enabled=False))

        results = proc.process_tv_artwork(_tv_artwork(season=2, episode=None))

        assert results[0].startswith("⚠️")
        assert "Season 02 not available in TV Shows" in results[0]
        assert not capture_kometa_saves

    def test_filtered_artwork_raises_not_processed_by_filter(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(
            FakePlex(items=[show], libraries=["TV Shows"]),
            options=Options(kometa=True, filters=["background"]),
        )

        with pytest.raises(NotProcessedByFilter):
            proc.process_tv_artwork(_tv_artwork(season=1, episode=None))

    def test_excluded_artwork_raises_not_processed_by_exclusion(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(
            FakePlex(items=[show], libraries=["TV Shows"]),
            options=Options(kometa=True, exclude=["season-1"]),
        )

        with pytest.raises(NotProcessedByExclusion):
            proc.process_tv_artwork(_tv_artwork(season=1, episode=None))


class TestEpisodeTitleCardInPlex:
    def test_episode_present_uploads(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=1, type="title_card"))

        assert results[0].startswith("✅")
        assert capture_kometa_saves[0]["dest_file_name"] == "S01E01"

    def test_episode_missing_season_present_warns_with_episode_detail(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]), arr=FakeArr(tv_enabled=False))

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=9, type="title_card"))

        assert results[0].startswith("⚠️")
        assert "Episode 09 not available in TV Shows" in results[0]
        assert not capture_kometa_saves

    def test_episode_missing_but_staging_enabled_uploads_anyway(self, configured, capture_kometa_saves):
        configured.stage_assets = True
        configured.save()
        show = _show_with_season1_episode1()
        proc = _processor(
            FakePlex(items=[show], libraries=["TV Shows"]),
            arr=FakeArr(tv_enabled=False),
            options=Options(kometa=True, stage=True),
        )

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=9, type="title_card"))

        assert results[0].startswith("✅")

    def test_episode_missing_season_but_sonarr_knows_episode_preseeds(self, configured, capture_kometa_saves):
        show = _show_with_season1_episode1()
        arr_series = _arr_series(season_numbers={1, 2})
        proc = _processor(
            FakePlex(items=[show], libraries=["TV Shows"]),
            arr=FakeArr(sonarr=FakeSonarr(series=arr_series)),
        )

        results = proc.process_tv_artwork(_tv_artwork(season=2, episode=3, type="title_card"))

        assert results[0].startswith("✅")
        assert "pre-seeded via Sonarr" in capture_kometa_saves[0]["description"]


class TestMultipleLibraries:
    def test_second_library_missing_season_warns_but_first_still_succeeds(self, configured, capture_kometa_saves):
        show1 = _show_with_season1_episode1()
        # Has a season 0 (so tv_show.seasons()[0] resolves for asset-folder derivation)
        # but not season 1, the one the artwork targets.
        other_episode = FakeEpisode(1, "/data/media/tv/Breaking Bad (2008)/Specials/S00E01.mkv")
        show2 = FakeShow("Breaking Bad", [FakeSeason(0, [other_episode])])
        proc = _processor(
            FakePlex(items=[show1, show2], libraries=["TV Shows", "TV Shows 4K"]),
            arr=FakeArr(tv_enabled=False),
        )

        results = proc.process_tv_artwork(_tv_artwork(season=1, episode=None))

        assert len(results) == 2
        assert results[0].startswith("✅")
        assert results[1].startswith("⚠️")
        assert "TV Shows 4K" in results[1]


class TestUnexpectedPlexErrors:
    def test_missing_media_file_raises_show_not_found(self, configured, capture_kometa_saves):
        # A show with zero seasons: tv_show.seasons()[0] raises IndexError, which is
        # NOT one of (AttributeError, KeyError, NotFound) - characterizing this
        # so the refactor can't silently widen or narrow the except clause.
        show = FakeShow("Breaking Bad", [])
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        with pytest.raises(IndexError):
            proc.process_tv_artwork(_tv_artwork(season=1, episode=None))
