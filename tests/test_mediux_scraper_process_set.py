"""
Characterization tests for MediuxScraper._process_set.

These pin down the observable behavior of _process_set (artwork lists, counters,
skip paths) so the method can be refactored safely.
"""

import pytest

from core import globals
from core.config import Config
from models.options import Options
from scrapers.mediux_scraper import MediuxScraper

pytestmark = pytest.mark.unit


def _make_scraper(options=None):
    globals.config = Config()
    scraper = MediuxScraper(url="https://mediux.pro/sets/1")
    scraper.author = "someauthor"
    scraper.set_options(options or Options())
    return scraper


def _tv_file(**overrides):
    file_entry = {
        "movie_id": None,
        "collection_id": None,
        "show_id": None,
        "show_id_backdrop": None,
        "episode_id": None,
        "season_id": None,
        "id": "img1",
        "fileType": "poster",
    }
    file_entry.update(overrides)
    return file_entry


def _tv_set(files):
    return {
        "files": files,
        "show": {
            "name": "Some Show",
            "id": 42,
            "first_air_date": "2020-01-15",
            "seasons": [
                {"id": 7, "season_number": 1},
                {"id": 8, "season_number": 2},
            ],
        },
    }


class TestTvTitleCards:
    def test_title_card_included_with_season_and_episode(self):
        scraper = _make_scraper()
        files = [_tv_file(
            fileType="title_card",
            title="Some Show S01 E05",
            episode_id={"id": 9, "season_id": {"id": 7, "season_number": 1}},
        )]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        artwork = scraper.tv_artwork[0]
        assert artwork["title"] == "Some Show"
        assert artwork["season"] == 1
        assert artwork["episode"] == 5
        assert artwork["type"] == "title_card"
        assert artwork["year"] == 2020
        assert artwork["tmdb_id"] == 42
        assert artwork["author"] == "someauthor"
        assert "img1" in artwork["url"]

    def test_title_card_with_unparseable_episode_gets_none(self):
        scraper = _make_scraper()
        files = [_tv_file(
            fileType="title_card",
            title="no episode marker",
            episode_id={"id": 9, "season_id": {"id": 7, "season_number": 1}},
        )]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        assert scraper.tv_artwork[0]["episode"] is None

    def test_title_card_without_title_skipped(self):
        scraper = _make_scraper()
        files = [_tv_file(
            fileType="title_card",
            episode_id={"id": 9, "season_id": {"id": 7, "season_number": 1}},
        )]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork == []
        assert scraper.filtered == 0
        assert scraper.exclusions == 0


class TestTvBackdropsAndPosters:
    def test_backdrop_included(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="backdrop", show_id_backdrop={"id": 1})]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        artwork = scraper.tv_artwork[0]
        assert artwork["season"] == "Backdrop"
        assert artwork["episode"] is None
        assert artwork["type"] == "background"

    def test_backdrop_without_show_id_backdrop_skipped(self):
        scraper = _make_scraper()
        # episode_id forces TV media type detection despite missing backdrop id
        files = [_tv_file(fileType="backdrop", episode_id={"id": 9})]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork == []

    def test_season_cover_resolves_number_from_seasons_list(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="poster", season_id={"id": 8, "season_number": 99})]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        artwork = scraper.tv_artwork[0]
        assert artwork["season"] == 2  # from seasons list, not the bogus 99
        assert artwork["episode"] == "Cover"
        assert artwork["type"] == "season_cover"

    def test_season_cover_falls_back_to_own_season_number(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="poster", season_id={"id": 999, "season_number": 3})]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork[0]["season"] == 3

    def test_show_cover_included(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="poster", show_id={"id": 42})]
        scraper._process_set(_tv_set(files))
        artwork = scraper.tv_artwork[0]
        assert artwork["season"] == "Cover"
        assert artwork["episode"] is None
        assert artwork["type"] == "show_cover"

    def test_poster_without_ids_skipped(self):
        scraper = _make_scraper()
        files = [
            _tv_file(fileType="poster"),
            _tv_file(fileType="poster", id="img2", show_id={"id": 42}),
        ]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        assert scraper.tv_artwork[0]["id"] == "img2"

    def test_square_art_included(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="album_art", episode_id={"id": 9})]
        scraper._process_set(_tv_set(files))
        artwork = scraper.tv_artwork[0]
        assert artwork["season"] == "SquareArt"
        assert artwork["episode"] is None
        assert artwork["type"] == "square_art"

    def test_unknown_file_type_skipped(self):
        scraper = _make_scraper()
        files = [_tv_file(fileType="hologram", episode_id={"id": 9})]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork == []


class TestTvFiltersAndExclusions:
    def test_global_filters_respected(self):
        scraper = _make_scraper()
        globals.config.mediux_filters = ["title_card"]
        scraper.config = globals.config
        files = [_tv_file(fileType="poster", show_id={"id": 42})]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork == []
        assert scraper.filtered == 1

    def test_per_url_filter_overrides_global(self):
        options = Options(filters=["show_cover"])
        scraper = _make_scraper(options)
        globals.config.mediux_filters = []
        scraper.config = globals.config
        files = [_tv_file(fileType="poster", show_id={"id": 42})]
        scraper._process_set(_tv_set(files))
        assert len(scraper.tv_artwork) == 1
        assert scraper.filtered == 0

    def test_excluded_id_counted(self):
        options = Options(exclude=["img1"])
        scraper = _make_scraper(options)
        files = [_tv_file(fileType="poster", show_id={"id": 42})]
        scraper._process_set(_tv_set(files))
        assert scraper.tv_artwork == []
        assert scraper.exclusions == 1


class TestMovies:
    def test_single_movie_poster(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": {"id": 500},
                "collection_id": None,
                "id": "img1",
                "fileType": "poster",
            }],
            "movie": {"title": "Some Movie", "release_date": "2005-06-01"},
        }
        scraper._process_set(set_data)
        assert len(scraper.movie_artwork) == 1
        artwork = scraper.movie_artwork[0]
        assert artwork["title"] == "Some Movie"
        assert artwork["year"] == 2005
        assert artwork["type"] == "movie_poster"
        assert artwork["tmdb_id"] == 500

    def test_movie_poster_inside_collection_set(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": {"id": 501},
                "collection_id": None,
                "id": "img1",
                "fileType": "poster",
            }],
            "collection": {
                "collection_name": "Some Saga",
                "movies": [
                    {"id": 500, "title": "First", "release_date": "2001-01-01"},
                    {"id": 501, "title": "Second", "release_date": "2003-01-01"},
                ],
            },
        }
        scraper._process_set(set_data)
        artwork = scraper.movie_artwork[0]
        assert artwork["title"] == "Second"
        assert artwork["year"] == 2003

    def test_movie_backdrop(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": None,
                "collection_id": None,
                "movie_id_backdrop": {"id": 500},
                "id": "img1",
                "fileType": "backdrop",
            }],
            "movie": {"title": "Some Movie", "release_date": "2005-06-01"},
        }
        scraper._process_set(set_data)
        artwork = scraper.movie_artwork[0]
        assert artwork["type"] == "background"
        assert artwork["year"] == 2005

    def test_movie_square_art(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": None,
                "collection_id": None,
                "movie_id_ost": {"id": 500},
                "id": "img1",
                "fileType": "album_art",
            }],
            "movie": {"title": "Some Movie", "release_date": "2005-06-01"},
        }
        scraper._process_set(set_data)
        artwork = scraper.movie_artwork[0]
        assert artwork["type"] == "square_art"

    def test_square_art_without_ost_id_skipped(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": None,
                "collection_id": None,
                "id": "img1",
                "fileType": "album_art",
            }],
            "movie": {"title": "Some Movie", "release_date": "2005-06-01"},
        }
        scraper._process_set(set_data)
        assert scraper.movie_artwork == []


class TestCollections:
    def test_collection_background(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": None,
                "collection_id": None,
                "movie_id_backdrop": None,
                "id": "img1",
                "fileType": "backdrop",
            }],
            "collection": {"collection_name": "Some Saga", "movies": []},
        }
        scraper._process_set(set_data)
        assert len(scraper.collection_artwork) == 1
        artwork = scraper.collection_artwork[0]
        assert artwork["title"] == "Some Saga"
        assert artwork["type"] == "background"
        assert artwork["year"] is None

    def test_collection_poster_via_movie_poster_filetype(self):
        scraper = _make_scraper()
        set_data = {
            "files": [{
                "movie_id": None,
                "collection_id": None,
                "id": "img1",
                "fileType": "movie_poster",
            }],
            "collection": {"collection_name": "Some Saga", "movies": []},
        }
        scraper._process_set(set_data)
        assert scraper.collection_artwork[0]["type"] == "collection_poster"


class TestSetLevelBehavior:
    def test_empty_set_is_noop(self):
        scraper = _make_scraper()
        scraper._process_set({"files": []})
        assert scraper.total == 0
        assert scraper.tv_artwork == []

    def test_total_counts_all_files(self):
        scraper = _make_scraper()
        files = [
            _tv_file(fileType="poster", show_id={"id": 42}),
            _tv_file(fileType="poster", id="img2"),  # skipped: no ids
        ]
        scraper._process_set(_tv_set(files))
        assert scraper.total == 2
        assert len(scraper.tv_artwork) == 1
