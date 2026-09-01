from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from core.exceptions import PlexConnectorException
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


@pytest.mark.parametrize(
    ("setter", "library_name"),
    (("set_tv_libraries", "TV Shows"), ("set_movie_libraries", "Movies")),
)
def test_set_libraries_rejects_missing_server_after_connect(
    monkeypatch, setter, library_name
):
    connector = PlexConnector("http://plex:32400", "token")
    monkeypatch.setattr(connector, "connect", lambda: None)

    with pytest.raises(PlexConnectorException, match="did not initialize"):
        getattr(connector, setter)(library_name)


@pytest.mark.parametrize(
    ("setter", "libraries_attr", "library_name"),
    (
        ("set_tv_libraries", "tv_libraries", "TV Shows"),
        ("set_movie_libraries", "movie_libraries", "Movies"),
    ),
)
def test_concurrent_library_discovery_publishes_complete_lists_atomically(
    setter, libraries_attr, library_name
):
    connector = PlexConnector("http://plex:32400", "token")
    library = Mock(title=library_name)
    barrier = Barrier(2)
    server = Mock()

    def section(_library_name):
        barrier.wait(timeout=2)
        return library

    server.library.section.side_effect = section
    connector.plex = server

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: getattr(connector, setter)(library_name), range(2)
            )
        )

    assert results == [[library], [library]]
    assert getattr(connector, libraries_attr) == [library]


def test_reconnect_failure_clears_stale_state_and_recovers_with_new_names(
    monkeypatch,
):
    connector = PlexConnector("http://old-plex:32400", "old-token")
    connector._tv_library_names = ["Old TV"]
    connector._movie_library_names = ["Old Movies"]
    connector.tv_libraries = [Mock(title="Old TV")]
    connector.movie_libraries = [Mock(title="Old Movies")]

    movie = Mock(title="The Matrix", year=1999)
    movie_library = Mock(title="New Movies")
    movie_library.getGuid.return_value = movie
    server = Mock()
    server.library.section.return_value = movie_library
    attempts = 0

    def connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlexConnectorException("Plex unavailable")
        connector.plex = server

    monkeypatch.setattr(connector, "connect", connect)
    updated_config = Mock(
        base_url="http://new-plex:32400",
        token="new-token",
        tv_library=["New TV"],
        movie_library=["New Movies"],
    )

    with pytest.raises(PlexConnectorException):
        connector.reconnect(updated_config)

    assert connector.plex is None
    assert connector.base_url == "http://new-plex:32400"
    assert connector.token == "new-token"
    assert connector._tv_library_names == ["New TV"]
    assert connector._movie_library_names == ["New Movies"]
    assert connector.tv_libraries == []
    assert connector.movie_libraries == []

    items, libraries = connector.find_in_library("movie", _artwork())

    assert items == [movie]
    assert libraries == ["New Movies"]
    assert attempts == 2
    server.library.section.assert_called_once_with("New Movies")


def test_lookup_recovers_libraries_after_startup_connection_failure(monkeypatch):
    connector = PlexConnector("http://plex:32400", "token")
    movie = Mock(title="The Matrix", year=1999)
    movie_library = Mock(title="Movies")
    movie_library.getGuid.return_value = movie
    server = Mock()
    server.library.section.return_value = movie_library
    attempts = 0

    def connect():
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PlexConnectorException("Plex unavailable")
        connector.plex = server

    monkeypatch.setattr(connector, "connect", connect)

    with pytest.raises(PlexConnectorException):
        connector.set_tv_libraries("TV Shows")
    with pytest.raises(PlexConnectorException):
        connector.set_movie_libraries("Movies")

    items, libraries = connector.find_in_library("movie", _artwork())

    assert items == [movie]
    assert libraries == ["Movies"]
    assert attempts == 3
    server.library.section.assert_called_once_with("Movies")


def test_healthy_lookup_does_not_reconnect_or_rediscover_libraries(monkeypatch):
    connector = PlexConnector("http://plex:32400", "token")
    movie = Mock(title="The Matrix", year=1999)
    movie_library = Mock(title="Movies")
    movie_library.getGuid.return_value = movie
    server = Mock()
    server.library.section.return_value = movie_library
    connector.plex = server
    connect = Mock()
    monkeypatch.setattr(connector, "connect", connect)

    connector.set_movie_libraries("Movies")
    connector.find_in_library("movie", _artwork())
    connector.find_in_library("movie", _artwork())

    connect.assert_not_called()
    server.library.section.assert_called_once_with("Movies")
