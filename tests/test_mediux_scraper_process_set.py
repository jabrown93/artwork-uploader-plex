"""
Regression test for MediuxScraper collection-poster detection.

Covers the case where MediUX tags a collection poster file with fileType
"movie_poster" instead of setting collection_id on the file itself.
"""

from unittest.mock import patch

import pytest

from models.options import Options
from scrapers.mediux_scraper import MediuxScraper


class _NoopCallbacks:
    """Every callback (debug/log/...) is a no-op that still evaluates its args."""

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _make_scraper():
    with patch("core.config.Config.load", lambda self: None):
        scraper = MediuxScraper(url="test", callbacks=_NoopCallbacks())
    scraper.set_options(Options())
    scraper.config.mediux_filters = ["collection_poster", "movie_poster"]
    return scraper


@pytest.mark.unit
def test_collection_poster_tagged_as_movie_poster_filetype_is_detected():
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
    assert len(scraper.collection_artwork) == 1
    assert scraper.collection_artwork[0]["title"] == "Some Saga"
    assert scraper.collection_artwork[0]["file_type"] == "collection_poster"


@pytest.mark.unit
@pytest.mark.parametrize("episode_key", ["episode_id", "tvdb_episode_id"])
@pytest.mark.parametrize("seasons", [[], [{"id": 10, "season_number": 1}]])
def test_title_card_uses_file_metadata_when_show_omits_episodes(episode_key, seasons):
    scraper = _make_scraper()
    scraper.config.mediux_filters = ["title_card"]
    set_data = {
        "show": {
            "id": 42, "name": "Some Show", "first_air_date": "2020-01-01",
            "seasons": seasons,
        },
        "files": [{
            "id": "card1", "title": "Some Show S01 E05",
            episode_key: {"id": 123, "season_id": {"season_number": 1}},
        }],
    }

    scraper._process_set(set_data)

    assert [(item["season"], item["episode"], item["file_type"]) for item in scraper.tv_artwork] == [
        (1, 5, "title_card"),
    ]
    assert scraper.errored == 0


@pytest.mark.unit
@pytest.mark.parametrize("season_key", ["season_id", "tvdb_season_id"])
@pytest.mark.parametrize("seasons", [[], [{"id": 10, "season_number": 1}]])
def test_season_cover_uses_file_metadata_when_show_omits_seasons(season_key, seasons):
    scraper = _make_scraper()
    scraper.config.mediux_filters = ["season_cover"]
    set_data = {
        "show": {
            "id": 42, "name": "Some Show", "first_air_date": "2020-01-01",
            "seasons": seasons,
        },
        "files": [{
            "id": "cover1", "title": "Some Show Season 1",
            season_key: {"id": 11, "season_number": 1},
        }],
    }

    scraper._process_set(set_data)

    assert [(item["season"], item["file_type"]) for item in scraper.tv_artwork] == [
        (1, "season_cover"),
    ]
    assert scraper.errored == 0
