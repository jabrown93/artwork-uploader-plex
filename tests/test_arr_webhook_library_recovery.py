"""Covers the Sonarr/Radarr import webhook's Plex-library gate (web_routes.py arr_webhook).

A Plex server that is unreachable at start-up leaves PlexConnector holding no resolved
library handles. Nothing else on the webhook-only path (no scrape, no scheduled run) ever
calls ensure_libraries() before this route's own check, so gating on has_libraries() alone
means the route keeps 503'ing every future webhook forever, even after Plex recovers and
even with the right library names configured - the exact gap WebhookService._attempt()'s
own ensure_libraries() call was added to cover for events that already got past this gate.
"""

from unittest.mock import MagicMock

import pytest

import core.globals as app_globals
from core.constants import WEBHOOK_TOKEN_HEADER

RADARR_DOWNLOAD = {"eventType": "Download", "movie": {"title": "Dune", "year": 2021, "tmdbId": 438631}}


@pytest.fixture(autouse=True)
def reset_globals():
    try:
        yield
    finally:
        app_globals.plex = None
        app_globals.webhook_service = None


def _post(web_harness, payload):
    return web_harness.http_client().post(
        "/webhook/radarr",
        json=payload,
        headers={WEBHOOK_TOKEN_HEADER: "secret"},
    )


@pytest.mark.unit
def test_a_webhook_is_accepted_once_plex_recovers_even_though_handles_were_never_resolved(web_harness):
    """Plex was down at start-up: movie_libraries/tv_libraries are both empty, but the
    configured library names are still there and ensure_libraries() can resolve them now."""
    web_harness.config.enable_webhooks = True
    web_harness.config.webhook_token = "secret"
    web_harness.config.webhook_tpdb_users = ["someone"]

    plex = MagicMock()
    plex.movie_libraries = []
    plex.tv_libraries = []
    plex.has_libraries.return_value = False

    def resolve(*args, **kwargs):
        plex.movie_libraries = ["Movies"]
        return True

    plex.ensure_libraries.side_effect = resolve
    app_globals.plex = plex
    app_globals.webhook_service = MagicMock()

    response = _post(web_harness, RADARR_DOWNLOAD)

    assert response.status_code == 200
    assert response.get_json()["status"] == "queued"
    plex.ensure_libraries.assert_called_once()
    app_globals.webhook_service.enqueue.assert_called_once()


@pytest.mark.unit
def test_a_webhook_is_still_rejected_when_no_libraries_are_configured(web_harness):
    """Nothing is configured at all: ensure_libraries() resolves nothing, so the route still
    reports Plex as not configured instead of queueing an import with nowhere to apply it."""
    web_harness.config.enable_webhooks = True
    web_harness.config.webhook_token = "secret"
    web_harness.config.webhook_tpdb_users = ["someone"]

    plex = MagicMock()
    plex.movie_libraries = []
    plex.tv_libraries = []
    plex.has_libraries.return_value = False
    plex.ensure_libraries.return_value = False
    app_globals.plex = plex
    app_globals.webhook_service = MagicMock()

    response = _post(web_harness, RADARR_DOWNLOAD)

    assert response.status_code == 503
    assert response.get_json()["status"] == "plex not configured"
    app_globals.webhook_service.enqueue.assert_not_called()
