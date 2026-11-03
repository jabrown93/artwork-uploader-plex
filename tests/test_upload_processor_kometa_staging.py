"""
Regression tests for two fork behaviors dropped during the upstream catch-up merge
(jabrown93/artwork-uploader-plex#163):

- kometa_library_paths: a Plex library name can be mapped to a different Kometa
  asset directory name. Covers the movie, TV, collection, and arr pre-seed save
  paths.
- stage_collections: a collection missing from Plex is staged into every
  configured movie library instead of always raising CollectionNotFound.
"""

from types import SimpleNamespace

import pytest

from core import globals
from core.config import Config
from core.exceptions import CollectionNotFound
from kometa.kometa_saver import KometaSaver
from models.options import Options
from processors.upload_processor import UploadProcessor
from services.arr_service import ArrMovie

pytestmark = pytest.mark.unit


class FakePlex:
    """Stub PlexConnector covering find_in_library and find_collection."""

    def __init__(self, items=None, libraries=None, collection_items=None, collection_libraries=None):
        self._items = items
        self._libraries = libraries
        self._collection_items = collection_items
        self._collection_libraries = collection_libraries

    def find_in_library(self, item_type, artwork):
        return self._items, self._libraries

    def find_collection(self, collection_title, fuzzy=False):
        return self._collection_items, self._collection_libraries


class FakeRadarr:
    def __init__(self, movie=None):
        self._movie = movie

    def find_movie(self, tmdb_id, title, year):
        return self._movie


class FakeArr:
    def __init__(self, radarr=None):
        self.radarr = radarr or FakeRadarr()
        self.sonarr = None
        self.movie_fallback_enabled = True
        self.tv_fallback_enabled = True


class FakeMovieItem:
    def __init__(self, title, file_path):
        self.title = title
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=file_path)])]


class FakeEpisode:
    def __init__(self, index, file_path):
        self.index = index
        self.media = [SimpleNamespace(parts=[SimpleNamespace(file=file_path)])]


class FakeSeason:
    def __init__(self, index, episodes):
        self.index = index
        self._episodes = episodes

    def episodes(self):
        return self._episodes


class FakeShow:
    def __init__(self, title, seasons):
        self.title = title
        self.year = 2008
        self._seasons = seasons

    def seasons(self):
        return self._seasons


class FakeCollection:
    def __init__(self, title):
        self.title = title


def _movie_artwork(**overrides):
    artwork = {
        "title": "The Matrix", "url": "http://example.com/poster.jpg", "year": 1999,
        "source": "mediux", "id": "poster-1", "file_type": "movie_poster", "author": "someone",
        "tmdb_id": 603,
    }
    artwork.update(overrides)
    return artwork


def _tv_artwork(**overrides):
    artwork = {
        "title": "Breaking Bad", "url": "http://example.com/season.jpg", "season": 1,
        "episode": None, "year": 2008, "source": "mediux", "id": "season-1",
        "file_type": "season_cover", "author": "someone", "tmdb_id": 1396,
    }
    artwork.update(overrides)
    return artwork


def _collection_artwork(**overrides):
    artwork = {
        "title": "The Matrix Collection", "url": "http://example.com/collection.jpg",
        "source": "mediux", "id": "collection-1", "file_type": "movie_poster",
        "author": "someone",
    }
    artwork.update(overrides)
    return artwork


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
    cfg.stage_collections = False
    cfg.arr_root_folder_library_map = {}
    cfg.kometa_library_paths = {}
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
            "library": self.library,
        })
        return f"✅ {self.description} | {self.artwork_type} saved (fake)"

    monkeypatch.setattr(KometaSaver, "save_to_kometa", fake_save)
    return calls


def _processor(plex, arr=None, options=None):
    proc = UploadProcessor(plex, arr=arr or FakeArr())
    proc.set_options(options or Options(kometa=True))
    return proc


class TestKometaLibraryPaths:

    def test_movie_in_plex_uses_mapped_directory(self, configured, capture_kometa_saves):
        configured.kometa_library_paths = {"Movies": "films"}
        configured.save()
        movie = FakeMovieItem("The Matrix", "/data/media/movies/The Matrix (1999)/movie.mkv")
        proc = _processor(FakePlex(items=[movie], libraries=["Movies"]))

        proc.process_movie_artwork(_movie_artwork())

        assert capture_kometa_saves[0]["dest_dir"].endswith("/assets/films/The Matrix (1999)")
        # KometaSaver.library (used for filters/labels) stays the real Plex library name.
        assert capture_kometa_saves[0]["library"] == "Movies"

    def test_movie_unmapped_library_falls_back_to_its_own_name(self, configured, capture_kometa_saves):
        # kometa_library_paths has no entry for "Movies" - resolve_library_directory must
        # fall back to the library name itself, not raise or produce an empty path segment.
        configured.kometa_library_paths = {"4K Movies": "films-4k"}
        configured.save()
        movie = FakeMovieItem("The Matrix", "/data/media/movies/The Matrix (1999)/movie.mkv")
        proc = _processor(FakePlex(items=[movie], libraries=["Movies"]))

        proc.process_movie_artwork(_movie_artwork())

        assert capture_kometa_saves[0]["dest_dir"].endswith("/assets/Movies/The Matrix (1999)")

    def test_radarr_preseed_uses_mapped_directory(self, configured, capture_kometa_saves):
        configured.kometa_library_paths = {"Movies": "films"}
        configured.save()
        arr_movie = ArrMovie(
            folder_name="The Matrix (1999)", root_folder_path="/data/media/movies",
            title="The Matrix", year=1999)
        proc = _processor(
            FakePlex(items=None, libraries=None), arr=FakeArr(radarr=FakeRadarr(movie=arr_movie)))

        proc.process_movie_artwork(_movie_artwork())

        assert capture_kometa_saves[0]["dest_dir"].endswith("/assets/films/The Matrix (1999)")

    def test_tv_in_plex_uses_mapped_directory(self, configured, capture_kometa_saves):
        configured.kometa_library_paths = {"TV Shows": "shows"}
        configured.save()
        episode = FakeEpisode(1, "/data/media/tv/Breaking Bad (2008)/Season 01/S01E01.mkv")
        show = FakeShow("Breaking Bad", [FakeSeason(1, [episode])])
        proc = _processor(FakePlex(items=[show], libraries=["TV Shows"]))

        proc.process_tv_artwork(_tv_artwork(season=1, episode=None))

        assert capture_kometa_saves[0]["dest_dir"].endswith("/assets/shows/Breaking Bad (2008)")

    def test_collection_in_plex_uses_mapped_directory(self, configured, capture_kometa_saves):
        configured.kometa_library_paths = {"Movies": "films"}
        configured.save()
        collection = FakeCollection("The Matrix Collection")
        proc = _processor(FakePlex(
            items=None, libraries=None,
            collection_items=[collection], collection_libraries=["Movies"]))

        proc.process_collection_artwork(_collection_artwork())

        assert capture_kometa_saves[0]["dest_dir"].endswith("/assets/films/The Matrix Collection")


class TestStageCollections:

    def test_disabled_raises_collection_not_found(self, configured, capture_kometa_saves):
        configured.stage_collections = False
        configured.save()
        proc = _processor(FakePlex(items=None, libraries=None, collection_items=None, collection_libraries=None))

        with pytest.raises(CollectionNotFound):
            proc.process_collection_artwork(_collection_artwork())

        assert not capture_kometa_saves

    def test_enabled_stages_into_every_configured_movie_library(self, configured, capture_kometa_saves):
        configured.stage_collections = True
        configured.movie_library = ["Movies", "4K Movies"]
        configured.save()
        proc = _processor(FakePlex(items=None, libraries=None, collection_items=None, collection_libraries=None))

        results = proc.process_collection_artwork(_collection_artwork())

        assert len(results) == 2
        assert all(r.startswith("✅") for r in results)
        libraries_saved = {call["library"] for call in capture_kometa_saves}
        assert libraries_saved == {"Movies", "4K Movies"}
        for call in capture_kometa_saves:
            assert call["dest_dir"].endswith(f"/assets/{call['library']}/The Matrix Collection")

    def test_non_kometa_mode_still_raises(self, configured, capture_kometa_saves):
        # stage_collections fallback is Kometa-mode only; without --kometa it has nothing
        # to write into, so the original "not available on Plex" behavior holds.
        configured.stage_collections = True
        configured.save_to_kometa = False
        configured.save()
        proc = _processor(
            FakePlex(items=None, libraries=None, collection_items=None, collection_libraries=None),
            options=Options(kometa=False))

        with pytest.raises(CollectionNotFound):
            proc.process_collection_artwork(_collection_artwork())

        assert not capture_kometa_saves
