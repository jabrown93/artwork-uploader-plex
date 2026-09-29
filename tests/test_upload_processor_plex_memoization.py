"""
Tests for UploadProcessor's Plex lookup memoization (issue #132).

A MediUX full-series set produces one artwork entry per season/episode for a
single show. Before this cache, every entry re-ran PlexConnector.find_in_library
(including a slow tmdb:// GUID lookup) plus Show.seasons()/Season.episodes() for
checks that only need to reflect Plex's state once per run. These tests assert
that processing many entries for one show collects those calls down to O(1)
per show/season, that misses are cached too, and that a fresh UploadProcessor
(a new run) re-queries rather than serving stale data.
"""

from types import SimpleNamespace

import pytest
from plexapi.exceptions import NotFound

from core import globals
from core.config import Config
from core.exceptions import PlexLookupError, ShowNotFound
from kometa.kometa_saver import KometaSaver
from models.options import Options
from processors.upload_processor import UploadProcessor

pytestmark = pytest.mark.unit


class CountingFakePlex:
    """Records how many times find_in_library actually hits the "server"."""

    def __init__(self, items=None, libraries=None):
        self._items = items
        self._libraries = libraries
        self.find_in_library_calls = 0
        self.state_generation = 0

    def find_in_library(self, item_type, artwork):
        self.find_in_library_calls += 1
        return self._items, self._libraries


class ScriptedFakePlex:
    """Plays back one scripted find_in_library outcome per call, in order, to
    exercise transient-error-vs-confirmed-miss caching without touching real Plex."""

    def __init__(self, script):
        self._script = list(script)
        self.find_in_library_calls = 0
        self.state_generation = 0

    def find_in_library(self, item_type, artwork):
        self.find_in_library_calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeArr:
    def __init__(self):
        self.radarr = None
        self.sonarr = SimpleNamespace(find_series=lambda tmdb_id, title, year: None)
        self.movie_fallback_enabled = False
        self.tv_fallback_enabled = False


def _tv_artwork(**overrides):
    artwork = {
        "title": "Breaking Bad", "url": "http://example.com/season.jpg", "season": 1,
        "episode": None, "year": 2008, "source": "mediux", "id": "season-1",
        "type": "season_cover", "author": "someone", "tmdb_id": 1396,
        "checksum": "abc123",
    }
    artwork.update(overrides)
    return artwork


class CountingFakeEpisode:
    def __init__(self, index, file_path):
        self.index = index
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=file_path)])]


class CountingFakeSeason:
    """Records how many times episodes() actually hits the "server"."""

    def __init__(self, index, episodes):
        self.index = index
        self._episodes = episodes
        self.librarySectionTitle = "TV Shows"
        self.labels = []
        self.episodes_calls = 0

    def episodes(self):
        self.episodes_calls += 1
        return self._episodes

    def episode(self, number):
        for e in self._episodes:
            if e.index == number:
                return e
        raise NotFound(f"episode {number} not found")


class CountingFakeShow:
    """Records how many times seasons() actually hits the "server"."""

    def __init__(self, title, seasons):
        self.title = title
        self._seasons = seasons
        self.librarySectionTitle = "TV Shows"
        self.labels = []
        self.seasons_calls = 0

    def seasons(self):
        self.seasons_calls += 1
        return self._seasons

    def season(self, number):
        for s in self._seasons:
            if s.index == number:
                return s
        raise NotFound(f"season {number} not found")


def _full_series_show(num_seasons=3, episodes_per_season=5):
    """Builds a show shaped like a MediUX full-series set: several seasons, each
    with several episodes, so a run can generate a cover + per-season + per-episode
    entry for the same underlying show."""
    seasons = []
    for season_num in range(1, num_seasons + 1):
        episodes = [
            CountingFakeEpisode(
                ep_num, f"/data/media/tv/Breaking Bad (2008)/Season {season_num:02}/S{season_num:02}E{ep_num:02}.mkv")
            for ep_num in range(1, episodes_per_season + 1)
        ]
        seasons.append(CountingFakeSeason(season_num, episodes))
    return CountingFakeShow("Breaking Bad", seasons)


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
        calls.append({"description": self.description})
        return f"✅ {self.description} | {self.artwork_type} saved (fake)"

    monkeypatch.setattr(KometaSaver, "save_to_kometa", fake_save)
    return calls


def _processor(plex, options=None):
    proc = UploadProcessor(plex, arr=FakeArr())
    proc.set_options(options or Options(kometa=True))
    return proc


class TestFindInLibraryMemoization:
    def test_n_artwork_entries_for_one_show_call_find_in_library_once(self, configured, capture_kometa_saves):
        show = _full_series_show(num_seasons=3, episodes_per_season=5)
        plex = CountingFakePlex(items=[show], libraries=["TV Shows"])
        proc = _processor(plex)

        # Cover + one season cover per season + one title card per episode: 1 + 3 + 15 = 19 entries.
        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))
        for season_num in range(1, 4):
            proc.process_tv_artwork(_tv_artwork(season=season_num, episode=None))
            for ep_num in range(1, 6):
                proc.process_tv_artwork(_tv_artwork(season=season_num, episode=ep_num, type="title_card"))

        assert len(capture_kometa_saves) == 19
        assert plex.find_in_library_calls == 1
        assert show.seasons_calls == 1
        # Each season's episode list is fetched once no matter how many episodes in it are processed.
        assert all(s.episodes_calls == 1 for s in show._seasons)

    def test_miss_is_cached_within_a_run(self, configured, capture_kometa_saves):
        plex = CountingFakePlex(items=None, libraries=None)
        proc = _processor(plex)

        for _ in range(5):
            with pytest.raises(ShowNotFound):
                proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))

        assert plex.find_in_library_calls == 1

    def test_transient_lookup_error_is_not_cached_and_next_entry_can_succeed(self, configured, capture_kometa_saves):
        """A PlexLookupError (e.g. a Plex timeout) must not poison the cache like a
        confirmed miss: the next entry for the same show has to re-query and can
        still find it."""
        show = _full_series_show(num_seasons=1, episodes_per_season=1)
        plex = ScriptedFakePlex([
            PlexLookupError("transient Plex timeout", partial_result=(None, None)),
            ([show], ["TV Shows"]),
        ])
        proc = _processor(plex)

        with pytest.raises(ShowNotFound):
            proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))
        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))

        assert plex.find_in_library_calls == 2
        assert len(capture_kometa_saves) == 1

    def test_partial_result_from_errored_library_is_not_cached(self, configured, capture_kometa_saves):
        """When one library errors but another hits, the hit's items must still be
        usable immediately, but the result stays uncached because it isn't confirmed
        complete: the next entry re-queries rather than trusting the partial result."""
        show = _full_series_show(num_seasons=1, episodes_per_season=1)
        plex = ScriptedFakePlex([
            PlexLookupError("library A timed out", partial_result=([show], ["TV Shows B"])),
            ([show], ["TV Shows B"]),
        ])
        proc = _processor(plex)

        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))
        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))

        assert plex.find_in_library_calls == 2
        assert len(capture_kometa_saves) == 2

    def test_reconnect_invalidates_plex_object_caches(self, configured, capture_kometa_saves):
        """A reconnect swaps Plex's server and library handles, so the next entry
        must re-query instead of serving a cached item or its cached show structure."""
        show = _full_series_show(num_seasons=1, episodes_per_season=1)
        plex = CountingFakePlex(items=[show], libraries=["TV Shows"])
        proc = _processor(plex)

        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))
        plex.state_generation += 1
        proc.process_tv_artwork(_tv_artwork(season="Cover", episode=None, type="show_cover"))

        assert plex.find_in_library_calls == 2
        assert show.seasons_calls == 2
        assert len(capture_kometa_saves) == 2

    def test_new_run_requeries_plex(self, configured, capture_kometa_saves):
        show = _full_series_show(num_seasons=1, episodes_per_season=1)
        plex = CountingFakePlex(items=[show], libraries=["TV Shows"])

        proc1 = _processor(plex)
        proc1.process_tv_artwork(_tv_artwork(season=1, episode=None))
        proc1.process_tv_artwork(_tv_artwork(season=1, episode=1, type="title_card"))
        assert plex.find_in_library_calls == 1
        assert show.seasons_calls == 1

        # A new scrape_and_process run builds a fresh UploadProcessor (see
        # ArtworkProcessor.scrape_and_process); its caches must start empty so newly
        # added Plex episodes aren't hidden behind stale cached results.
        proc2 = _processor(plex)
        proc2.process_tv_artwork(_tv_artwork(season=1, episode=None))

        assert plex.find_in_library_calls == 2
        assert show.seasons_calls == 2
