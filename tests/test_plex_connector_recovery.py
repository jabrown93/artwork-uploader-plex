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
