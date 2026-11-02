"""
Tests for PlexConnector's fork invariants (see FORK.md): a transient per-library lookup error
must not be cached as a confirmed miss, a hit found after an earlier library (or index-build)
error is only partial, and state_generation marks the connection generation so consumers that
cache Plex objects (e.g. UploadProcessor's show/season/episode caches) know when to invalidate.

Upstream's PlexConnector resolves libraries eagerly via set_tv_libraries/set_movie_libraries and
recovers a Plex that was down at start-up via ensure_libraries (see test_plex_library_recovery.py)
rather than the fork's lazy per-call re-resolution, so those mechanics aren't exercised here.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from plexapi.exceptions import NotFound as PlexAPINotFound

from core.enums import MediaType
from core.exceptions import PlexLookupError
from models.artwork_types import MovieArtwork
from plex.plex_connector import PlexConnector

pytestmark = pytest.mark.unit


def _artwork() -> MovieArtwork:
    return {
        "title": "The Matrix",
        "url": "https://example.com/poster.jpg",
        "year": 1999,
        "source": "mediux",
        "id": "poster-1",
        "type": "movie_poster",
        "author": "someone",
        "tmdb_id": 603,
    }


def test_find_in_library_raises_lookup_error_on_transient_guid_error():
    """A non-NotFound exception from one library's getGuid is a transient failure, not a
    confirmed miss: it must surface as PlexLookupError carrying the partial result another
    library already found, not a plain tuple a cache could store."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library_a = Mock(title="Movies A")
    library_a.getGuid.side_effect = RuntimeError("connection reset")
    movie = Mock(title="The Matrix", year=1999)
    library_b = Mock(title="Movies B")
    library_b.getGuid.return_value = movie
    library_b.search.return_value = [movie]
    connector.movie_libraries = [library_a, library_b]

    with pytest.raises(PlexLookupError) as exc_info:
        connector.find_in_library(MediaType.MOVIE, _artwork())

    assert exc_info.value.partial_result == ([movie], ["Movies B"])


def test_find_in_library_treats_guid_not_found_as_genuine_miss():
    """plexapi's NotFound from getGuid means that library confirmed the item isn't there, so it
    must not be treated as an error and must not raise PlexLookupError."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library = Mock(title="Movies A")
    library.getGuid.side_effect = PlexAPINotFound("not found")
    library.search.return_value = []
    connector.movie_libraries = [library]

    items, libs = connector.find_in_library(MediaType.MOVIE, _artwork())

    assert items is None
    assert libs is None


def test_find_in_library_all_misses_without_errors_returns_none_uncached():
    """Every library confirming a genuine miss (NotFound) is a real answer, not a partial one -
    no PlexLookupError, a plain (None, None) a cache can store."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library_a = Mock(title="Movies A")
    library_a.getGuid.side_effect = PlexAPINotFound("not found")
    library_a.search.return_value = []
    library_b = Mock(title="Movies B")
    library_b.getGuid.side_effect = PlexAPINotFound("not found")
    library_b.search.return_value = []
    connector.movie_libraries = [library_a, library_b]

    items, libs = connector.find_in_library(MediaType.MOVIE, _artwork())

    assert items is None
    assert libs is None


def test_find_in_library_falls_back_to_title_year_search_when_guid_lookup_misses():
    """Fork: when the TMDb GUID lookup finds nothing (here because tmdb_id is missing, as
    movie_or_show() can return a title match without one), find_in_library must still try a
    title/year search before reporting a miss."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library = Mock(title="Movies A")
    library.getGuid.side_effect = PlexAPINotFound("not found")
    movie = Mock(title="The Matrix", year=1999)
    library.search.return_value = [movie]
    connector.movie_libraries = [library]

    items, libs = connector.find_in_library(MediaType.MOVIE, {**_artwork(), "tmdb_id": None})

    assert items == [movie]
    assert libs == ["Movies A"]


def _holder(path):
    return SimpleNamespace(title="Alien", year=1979, media=[SimpleNamespace(parts=[SimpleNamespace(file=path)])])


def test_title_fallback_applies_the_imported_file_constraint():
    """A Radarr import names the file it brought in; the title fallback must not hand the
    artwork to an older copy or another edition, and must report nothing (so the webhook
    retries) while Plex has not scanned the new file yet."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library = Mock(title="Movies")
    library.getGuid.side_effect = PlexAPINotFound("not found")
    old = _holder("/data/movies/Alien (1979)/Alien (1979) old.mkv")
    new = _holder("/data/movies/Alien (1979)/Alien (1979) new.mkv")
    connector.movie_libraries = [library]
    artwork = {**_artwork(), "tmdb_id": None, "media_folder": "Alien (1979)", "media_file": ("Alien (1979) new.mkv",)}

    library.search.return_value = [old, new]
    assert connector.find_in_library(MediaType.MOVIE, artwork) == ([new], ["Movies"])

    library.search.return_value = [old]
    assert connector.find_in_library(MediaType.MOVIE, artwork) == (None, None)


def test_find_in_library_title_fallback_hit_after_guid_error_is_partial():
    """A hit found only via the title/year fallback still can't be trusted as a confirmed,
    cacheable result if an earlier library's GUID lookup errored transiently - some other
    untried library might have held a better match."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    library_a = Mock(title="Movies A")
    library_a.getGuid.side_effect = RuntimeError("connection reset")
    library_a.search.return_value = []
    library_b = Mock(title="Movies B")
    library_b.getGuid.side_effect = PlexAPINotFound("not found")
    movie = Mock(title="The Matrix", year=1999)
    library_b.search.return_value = [movie]
    connector.movie_libraries = [library_a, library_b]

    with pytest.raises(PlexLookupError) as exc_info:
        connector.find_in_library(MediaType.MOVIE, _artwork())

    assert exc_info.value.partial_result == ([movie], ["Movies B"])


def test_reconnect_bumps_state_generation():
    """Fork: state_generation is the signal consumers (e.g. UploadProcessor) use to invalidate
    their own caches of Plex objects after a reconnect swaps the server/library handles."""
    connector = PlexConnector("http://old-plex:32400", "old-token")
    before = connector.state_generation

    server = Mock()
    server.library.section.side_effect = lambda name: Mock(title=name)
    connector.connect = lambda: setattr(connector, "plex", server)
    updated_config = Mock(
        base_url="http://new-plex:32400", token="new-token",
        tv_library=["TV Shows"], movie_library=["Movies"],
    )

    connector.reconnect(updated_config)

    assert connector.state_generation == before + 1


def test_state_generation_unchanged_without_a_reconnect():
    connector = PlexConnector("http://plex:32400", "token")
    before = connector.state_generation
    connector.plex = Mock()
    connector.movie_libraries = []

    assert connector.state_generation == before


def _movie_item(title="The Matrix", year=1999, rating_key=1, tmdb_id=603):
    return SimpleNamespace(
        title=title, year=year, ratingKey=rating_key, slug=None, originalTitle=None,
        addedAt=None, guids=[SimpleNamespace(id=f"tmdb://{tmdb_id}")],
    )


def _library(title, item):
    library = Mock(title=title, type="movie")
    library.reload.return_value = None
    library.totalSize = 1
    library.search.return_value = [item]
    library.all.return_value = [item]
    return library


def test_movie_or_show_raises_lookup_error_when_a_library_fails_to_index_but_another_matches():
    """A library that fails to (re)index this pass means the index may be stale or incomplete
    for it, so a match found via a different, successfully-indexed library is only partial -
    the failed library might hold a better match that was never checked."""
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()

    good_item = _movie_item()
    good_library = _library("Movies", good_item)

    bad_library = Mock(title="Movies 4K", type="movie")
    bad_library.reload.side_effect = RuntimeError("Plex timed out")

    connector.movie_libraries = [good_library, bad_library]

    with pytest.raises(PlexLookupError) as exc_info:
        connector.movie_or_show("The Matrix", 1999)

    media_type, tmdb_id, found_title, found_year = exc_info.value.partial_result
    assert media_type == "Movie"
    assert tmdb_id == 603
    assert found_title == "The Matrix"
    assert found_year == 1999


def test_movie_or_show_raises_lookup_error_when_index_build_fails_and_nothing_matched():
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()

    bad_library = Mock(title="Movies 4K", type="movie")
    bad_library.reload.side_effect = RuntimeError("Plex timed out")
    connector.movie_libraries = [bad_library]

    with pytest.raises(PlexLookupError) as exc_info:
        connector.movie_or_show("Some Movie", 2020)

    assert exc_info.value.partial_result == (None, None, None, None)


def test_movie_or_show_does_not_raise_when_every_library_indexes_cleanly():
    connector = PlexConnector("http://plex:32400", "token")
    connector.plex = Mock()
    good_item = _movie_item()
    good_library = _library("Movies", good_item)
    connector.movie_libraries = [good_library]

    media_type, tmdb_id, found_title, found_year = connector.movie_or_show("The Matrix", 1999)

    assert media_type == "Movie"
    assert tmdb_id == 603
