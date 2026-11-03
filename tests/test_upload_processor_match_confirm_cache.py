"""
Regression test: UploadProcessor._artwork_matches_item's confirmation cache must be
keyed on the poster's own id, not just (kind, title, year, tmdb_id).

With local_library_matching on, two different TPDb poster entries that share a
display title/year get the same locally-inferred tmdb_id, so without the poster
id in the cache key they'd collide on one cache entry - the second poster would
skip its own confirmation fetch and silently inherit the first poster's result.
"""

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from core import globals
from core.config import Config
from processors.upload_processor import UploadProcessor

pytestmark = pytest.mark.unit


class FakeArr:
    def __init__(self):
        self.movie_fallback_enabled = False
        self.tv_fallback_enabled = False


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.load()
    cfg.save()
    globals.config = cfg
    globals.debug = False
    return cfg


def _artwork(poster_id, tmdb_id):
    return {
        "id": poster_id,
        "title": "Same Title",
        "year": 2020,
        "tmdb_id": tmdb_id,
    }


class TestMatchConfirmCacheKey:
    def test_two_posters_same_title_year_tmdb_id_are_confirmed_independently(self, configured, monkeypatch):
        # Two TPDb posters that the local title/year matcher inferred the same tmdb_id for,
        # but whose poster pages actually point at different TMDb ids.
        poster_a = _artwork(poster_id=111, tmdb_id=603)
        poster_b = _artwork(poster_id=222, tmdb_id=603)

        pages = {
            "https://theposterdb.com/poster/111": '<div data-media-id="603"></div>',
            "https://theposterdb.com/poster/222": '<div data-media-id="999"></div>',
        }
        fetched = []

        def fake_cook_soup(url):
            fetched.append(url)
            return BeautifulSoup(pages[url], "html.parser")

        monkeypatch.setattr("utils.soup_utils.cook_soup", fake_cook_soup)

        proc = UploadProcessor(plex=None, arr=FakeArr())
        plex_item = SimpleNamespace(guids=[SimpleNamespace(id="tmdb://603")])

        assert proc._artwork_matches_item(poster_a, plex_item, "movie") is True
        assert proc._artwork_matches_item(poster_b, plex_item, "movie") is False

        # Each poster must trigger its own confirmation fetch, not reuse the other's cached result.
        assert fetched == [
            "https://theposterdb.com/poster/111",
            "https://theposterdb.com/poster/222",
        ]

    def test_same_poster_confirmed_twice_is_still_cached(self, configured, monkeypatch):
        poster = _artwork(poster_id=111, tmdb_id=603)
        fetched = []

        def fake_cook_soup(url):
            fetched.append(url)
            return BeautifulSoup('<div data-media-id="603"></div>', "html.parser")

        monkeypatch.setattr("utils.soup_utils.cook_soup", fake_cook_soup)

        proc = UploadProcessor(plex=None, arr=FakeArr())
        plex_item = SimpleNamespace(guids=[SimpleNamespace(id="tmdb://603")])

        assert proc._artwork_matches_item(poster, plex_item, "movie") is True
        assert proc._artwork_matches_item(poster, plex_item, "movie") is True

        assert fetched == ["https://theposterdb.com/poster/111"]
