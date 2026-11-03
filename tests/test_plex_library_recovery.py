"""
Tests that a run re-resolves the Plex libraries when it is holding none.

Libraries are resolved once, at start-up. A Plex that is unreachable at that moment leaves
both lists empty for the life of the process, and an empty list matches nothing, so every
later run finds no titles and reports success having done nothing. These cover the recovery
that stops a restart being the only way out.
"""

import pytest

from core import globals
from core.exceptions import LibraryNotFound, PlexConnectorException
from plex.plex_connector import PlexConnector


class _FakeConfig:
    def __init__(self, tv_library=None, movie_library=None):
        self.tv_library = tv_library if tv_library is not None else []
        self.movie_library = movie_library if movie_library is not None else []


@pytest.fixture
def connector():
    return PlexConnector("http://plex.example:32400", "token")


@pytest.mark.unit
def test_libraries_already_held_are_not_resolved_again(connector, monkeypatch):
    """The happy path runs on every single run, so it has to cost nothing."""
    connector.tv_libraries = [object()]
    connector.movie_libraries = [object()]

    def _fail(_names):
        raise AssertionError("libraries were re-resolved when some were already held")

    monkeypatch.setattr(connector, "set_tv_libraries", _fail)
    monkeypatch.setattr(connector, "set_movie_libraries", _fail)

    assert connector.ensure_libraries(_FakeConfig(["TV Shows"], ["Movies"])) is True


@pytest.mark.unit
def test_a_type_that_failed_at_startup_is_retried_while_the_other_is_held(connector, monkeypatch):
    """Fork: TV down and movies up at start-up must not leave TV empty forever."""
    connector.movie_libraries = [object()]

    def _fail(_names):
        raise AssertionError("held movie libraries were re-resolved")

    monkeypatch.setattr(connector, "set_movie_libraries", _fail)
    monkeypatch.setattr(connector, "set_tv_libraries", lambda names: connector.tv_libraries.extend(names))

    assert connector.ensure_libraries(_FakeConfig(["TV Shows"], ["Movies"])) is True
    assert connector.tv_libraries == ["TV Shows"]


@pytest.mark.unit
def test_empty_libraries_are_resolved_again(connector, monkeypatch):
    monkeypatch.setattr(connector, "set_tv_libraries", lambda names: connector.tv_libraries.extend(names))
    monkeypatch.setattr(connector, "set_movie_libraries", lambda names: connector.movie_libraries.extend(names))

    assert connector.ensure_libraries(_FakeConfig(["TV Shows"], ["Movies"])) is True
    assert connector.tv_libraries == ["TV Shows"]
    assert connector.movie_libraries == ["Movies"]


@pytest.mark.unit
def test_a_plex_that_is_still_down_reports_no_libraries(connector, monkeypatch):
    def _down(_names):
        raise PlexConnectorException("Unable to connect to Plex server")

    monkeypatch.setattr(connector, "set_tv_libraries", _down)
    monkeypatch.setattr(connector, "set_movie_libraries", _down)

    assert connector.ensure_libraries(_FakeConfig(["TV Shows"], ["Movies"])) is False


@pytest.mark.unit
def test_a_movies_only_configuration_has_libraries(connector, monkeypatch):
    """An empty tv_library is a valid setup, not a failure, so it must not fail the run."""
    monkeypatch.setattr(connector, "set_tv_libraries", lambda names: connector.tv_libraries.extend(names))
    monkeypatch.setattr(connector, "set_movie_libraries", lambda names: connector.movie_libraries.extend(names))

    assert connector.ensure_libraries(_FakeConfig([], ["Movies"])) is True


@pytest.mark.unit
def test_movies_are_still_resolved_when_the_tv_library_is_missing(connector, monkeypatch):
    """set_tv_libraries raises on a renamed library. That must not cost us the movies."""
    def _missing(_names):
        raise LibraryNotFound('TV library named "TV Shows" not found.')

    monkeypatch.setattr(connector, "set_tv_libraries", _missing)
    monkeypatch.setattr(connector, "set_movie_libraries", lambda names: connector.movie_libraries.extend(names))

    assert connector.ensure_libraries(_FakeConfig(["TV Shows"], ["Movies"])) is True
    assert connector.movie_libraries == ["Movies"]


@pytest.mark.unit
def test_the_loaded_config_is_used_when_none_is_passed(connector, monkeypatch):
    monkeypatch.setattr(globals, "config", _FakeConfig(["TV Shows"], []))
    monkeypatch.setattr(connector, "set_tv_libraries", lambda names: connector.tv_libraries.extend(names))
    monkeypatch.setattr(connector, "set_movie_libraries", lambda names: connector.movie_libraries.extend(names))

    assert connector.ensure_libraries() is True
    assert connector.tv_libraries == ["TV Shows"]


@pytest.mark.unit
def test_no_config_at_all_reports_no_libraries(connector, monkeypatch):
    monkeypatch.setattr(globals, "config", None)

    assert connector.ensure_libraries() is False


@pytest.mark.unit
def test_a_failure_part_way_through_publishes_no_libraries(connector):
    """Fork: with the first library already published, ensure_libraries() would treat the
    list as resolved and never retry the ones after the failure."""
    from unittest.mock import Mock
    import requests

    connector.plex = Mock()

    def section(name):
        if name == "Movies B":
            raise requests.exceptions.ConnectionError("plex went away")
        return Mock(title=name)

    connector.plex.library.section.side_effect = section

    with pytest.raises(requests.exceptions.ConnectionError):
        connector.set_movie_libraries(["Movies A", "Movies B"])

    assert connector.movie_libraries == []
    assert connector.has_libraries() is False
