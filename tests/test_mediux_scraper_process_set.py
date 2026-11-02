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
