"""get_plex_libraries must work with the redacted token the Settings form round-trips (Fork)."""

import pytest

import web_routes

PLACEHOLDER = web_routes.SECRET_PLACEHOLDER


class _Section:
    def __init__(self, title, type_):
        self.title, self.type = title, type_


def _fake_plex_server(calls):
    class _Library:
        def sections(self):
            return [_Section("Movies", "movie"), _Section("TV Shows", "show")]

    class _FakePlexServer:
        def __init__(self, url, token, timeout=None):
            calls.append((url, token))
            self.library = _Library()

    return _FakePlexServer


def _ask(web_harness, payload):
    web_harness.config.plex_connect_timeout = 5
    client = web_harness.socket_client()
    client.get_received()
    client.emit("get_plex_libraries", {"instance_id": "abc", **payload})
    return [m for m in client.get_received() if m["name"] == "get_plex_libraries"]


@pytest.mark.unit
def test_placeholder_token_is_replaced_by_the_stored_token_for_the_configured_server(web_harness, monkeypatch):
    calls = []
    monkeypatch.setattr(web_routes, "PlexServer", _fake_plex_server(calls))
    web_harness.config.base_url = "https://plex.example:443/"
    web_harness.config.token = "real-token"

    received = _ask(web_harness, {"url": "https://plex.example:443", "token": PLACEHOLDER})

    assert calls == [("https://plex.example:443", "real-token")]
    assert received[0]["args"][0]["movie_libraries"] == ["Movies"]


@pytest.mark.unit
def test_placeholder_token_is_never_sent_to_a_different_server(web_harness, monkeypatch):
    calls = []
    monkeypatch.setattr(web_routes, "PlexServer", _fake_plex_server(calls))
    web_harness.config.base_url = "https://plex.example:443/"
    web_harness.config.token = "real-token"

    received = _ask(web_harness, {"url": "https://attacker.example", "token": PLACEHOLDER})

    assert calls == []
    assert received[0]["args"][0]["message"] == "Unable to connect to Plex"


@pytest.mark.unit
def test_an_explicit_token_is_used_as_typed(web_harness, monkeypatch):
    calls = []
    monkeypatch.setattr(web_routes, "PlexServer", _fake_plex_server(calls))
    web_harness.config.token = "real-token"

    _ask(web_harness, {"url": "https://other.example", "token": "typed-token"})

    assert calls == [("https://other.example", "typed-token")]
