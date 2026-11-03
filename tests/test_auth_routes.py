"""Auth routes: password login, the OIDC authorization-code flow with PKCE, and the
redaction of saved secrets in settings responses.

oidc_service.py (Authlib, a separate OidcService class) does not exist upstream - OIDC here is
a thin wrapper around requests_oauthlib.OAuth2Session directly in web_routes.py. This file
replaces the old test_oidc_service.py and test_auth_routes.py (both written against the fork's
OidcService/AUTH_MODE machinery, which upstream's shape does not have); the PKCE and redaction
coverage from those files is kept here.
"""

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask
from flask_socketio import SocketIO

import core.globals as app_globals
import web_routes
from core.config import Config
from core.constants import DEFAULT_CONFIG_PATH
from services.authentication_service import AuthenticationService

pytestmark = pytest.mark.unit

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


class _Response:
    """Stands in for a requests.Response: only .json() is used by web_routes."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


DISCOVERY = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
    "end_session_endpoint": "https://idp.example.com/logout",
    "issuer": "https://idp.example.com",
}
USER_INFO = {"preferred_username": "jared", "groups": ["media"]}


def _fake_oauth_get(self, url, **kwargs):
    return _Response(USER_INFO if url.endswith("/userinfo") else DISCOVERY)


def _make_id_token(claims):
    """Build a minimally-shaped JWT (header.payload.signature) carrying `claims` in its
    payload segment - the only part web_routes._decode_id_token_claims reads."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _valid_id_token_claims(**overrides):
    claims = {
        "iss": "https://idp.example.com",
        "aud": "artwork-uploader",
        "exp": time.time() + 300,
        "sub": "jared",
    }
    claims.update(overrides)
    return claims


class TestPasswordLogin:

    def test_valid_credentials_start_a_session(self, web_harness):
        web_harness.config.auth_enabled = True
        web_harness.config.auth_username = "jared"
        web_harness.config.auth_password_hash = AuthenticationService.hash_password("hunter2")
        client = web_harness.http_client()

        response = client.post("/login", data={"username": "jared", "password": "hunter2"})

        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        with client.session_transaction() as session:
            assert session["authenticated"] is True
            assert session["auth_type"] == "basic"
            assert session["username"] == "jared"

    def test_invalid_credentials_are_rejected(self, web_harness):
        web_harness.config.auth_enabled = True
        web_harness.config.auth_username = "jared"
        web_harness.config.auth_password_hash = AuthenticationService.hash_password("hunter2")
        client = web_harness.http_client()

        response = client.post("/login", data={"username": "jared", "password": "wrong"})

        assert response.status_code == 200
        assert b"Invalid username or password" in response.data
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_protected_route_redirects_to_login(self, web_harness):
        web_harness.config.auth_enabled = True

        response = web_harness.http_client().get("/")

        assert response.status_code == 302
        assert response.headers["Location"] == "/login"

    def test_home_is_open_when_auth_is_off(self, web_harness):
        assert web_harness.http_client().get("/").status_code == 200

    def test_logout_clears_the_session(self, web_harness):
        web_harness.config.auth_enabled = True
        web_harness.config.auth_username = "jared"
        web_harness.config.auth_password_hash = AuthenticationService.hash_password("hunter2")
        client = web_harness.http_client()
        client.post("/login", data={"username": "jared", "password": "hunter2"})

        response = client.get("/logout")

        assert response.status_code == 302
        with client.session_transaction() as session:
            assert "authenticated" not in session


class TestLoginMethodGating:
    """Fork: a direct POST cannot use a login method the config has turned off, even when a
    stale credential for it is still stored."""

    def test_password_post_is_refused_when_only_oidc_is_enabled(self, web_harness):
        web_harness.config.auth_enabled = False
        web_harness.config.oidc_enabled = True
        web_harness.config.auth_username = "jared"
        web_harness.config.auth_password_hash = AuthenticationService.hash_password("hunter2")
        client = web_harness.http_client()

        response = client.post("/login", data={"username": "jared", "password": "hunter2"})

        assert response.status_code == 200
        with client.session_transaction() as session:
            assert not session.get("authenticated")

    def test_oidc_post_is_refused_when_only_password_is_enabled(self, web_harness, monkeypatch):
        web_harness.config.auth_enabled = True
        web_harness.config.oidc_enabled = False
        monkeypatch.setattr(web_routes.OAuth2Session, "get", lambda *a, **k: pytest.fail("discovery must not run"))

        response = web_harness.http_client().post("/login", data={"oidc-login": "1"})

        assert response.status_code == 200


class TestOidcPkce:
    """The authorization-code flow runs through requests_oauthlib.OAuth2Session with PKCE
    (S256): a code_verifier is generated and stashed in session, its S256 hash goes out as
    code_challenge, and the callback sends the stored verifier back with the token request."""

    def _configure_oidc(self, config):
        config.oidc_enabled = True
        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "artwork-uploader"
        config.oidc_client_secret = "a-secret"
        config.oidc_scopes = ["openid"]

    def test_authorization_url_carries_a_pkce_challenge_matching_the_stored_verifier(
        self, web_harness, monkeypatch
    ):
        self._configure_oidc(web_harness.config)
        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        client = web_harness.http_client()

        response = client.post("/login", data={"oidc-login": "1"})

        assert response.status_code == 302
        location = response.headers["Location"]
        query = parse_qs(urlparse(location).query)
        assert query["code_challenge_method"] == ["S256"]

        with client.session_transaction() as session:
            verifier = session["oidc_code_verifier"]
        assert verifier

        # RFC 7636: code_challenge = BASE64URL-ENCODE(SHA256(code_verifier)), no padding.
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert query["code_challenge"] == [expected_challenge]

    def test_each_login_attempt_gets_its_own_verifier(self, web_harness, monkeypatch):
        self._configure_oidc(web_harness.config)
        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        client = web_harness.http_client()

        client.post("/login", data={"oidc-login": "1"})
        with client.session_transaction() as session:
            first_verifier = session["oidc_code_verifier"]

        client.post("/login", data={"oidc-login": "1"})
        with client.session_transaction() as session:
            second_verifier = session["oidc_code_verifier"]

        assert first_verifier != second_verifier

    def test_callback_forwards_the_stored_verifier_to_the_token_request(
        self, web_harness, monkeypatch
    ):
        self._configure_oidc(web_harness.config)
        calls = {}

        def fake_fetch_token(self, **kwargs):
            calls.update(kwargs)
            self.token = {"id_token": _make_id_token(_valid_id_token_claims())}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "the-stored-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 302
        assert calls["code_verifier"] == "the-stored-verifier"
        # The verifier is single-use: a second callback has none left to replay.
        with client.session_transaction() as session:
            assert "oidc_code_verifier" not in session

    def test_callback_establishes_a_session(self, web_harness, monkeypatch):
        self._configure_oidc(web_harness.config)

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims())}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert session["authenticated"] is True
            assert session["username"] == "jared"

    def test_groups_claim_only_in_the_id_token_still_allows_login(self, web_harness, monkeypatch):
        """Fork: a provider may put `groups` in the ID token only, not userinfo - the case
        upstream's userinfo-only lookup denied every such user."""
        self._configure_oidc(web_harness.config)
        web_harness.config.oidc_allowed_groups = ["media"]

        def fake_oauth_get(self, url, **kwargs):
            # No groups in userinfo at all - only the ID token carries it.
            return _Response({"preferred_username": "jared"} if url.endswith("/userinfo") else DISCOVERY)

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims(groups=["media"]))}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert session["authenticated"] is True
            assert session["username"] == "jared"

    def test_groups_claim_only_in_userinfo_still_allows_login(self, web_harness, monkeypatch):
        """The ID token carries no `groups` claim; userinfo (upstream's only source) still
        satisfies the allowed-groups check."""
        self._configure_oidc(web_harness.config)
        web_harness.config.oidc_allowed_groups = ["media"]

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims())}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        with client.session_transaction() as session:
            assert session["authenticated"] is True

    def test_id_token_with_wrong_audience_is_rejected(self, web_harness, monkeypatch):
        self._configure_oidc(web_harness.config)

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims(aud="someone-else"))}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 200
        assert b"invalid ID token audience" in response.data
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_expired_id_token_is_rejected(self, web_harness, monkeypatch):
        self._configure_oidc(web_harness.config)

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims(exp=time.time() - 60))}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        response = client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        assert response.status_code == 200
        assert b"expired ID token" in response.data
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_callback_stores_the_id_token_expiry_in_the_session(self, web_harness, monkeypatch):
        """session['oidc_exp'] is what login_required/socket_login_required check on every later
        request/event (see TestOidcSessionExpiry below)."""
        self._configure_oidc(web_harness.config)
        expires_at = time.time() + 300

        def fake_fetch_token(self, **kwargs):
            self.token = {"id_token": _make_id_token(_valid_id_token_claims(exp=expires_at))}
            return self.token

        monkeypatch.setattr(web_routes.OAuth2Session, "get", _fake_oauth_get)
        monkeypatch.setattr(web_routes.OAuth2Session, "fetch_token", fake_fetch_token)
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["oauth_state"] = "a-state"
            session["oidc_code_verifier"] = "a-verifier"

        client.get("/login/oidc/callback?state=a-state&code=an-auth-code")

        with client.session_transaction() as session:
            assert session["oidc_exp"] == expires_at


class TestOidcSessionExpiry:
    """Fork: login_required and socket_login_required enforce the stored OIDC id_token exp on
    every request/event, not only at login. Without this, session['authenticated'] alone stays
    valid for as long as the Flask session cookie does, well past the ID token's own expiry -
    so an expired ID token no longer actually ends the session."""

    def test_a_request_is_redirected_to_login_once_the_id_token_has_expired(self, web_harness):
        web_harness.config.oidc_enabled = True
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["username"] = "jared"
            session["oidc_exp"] = time.time() - 1

        response = client.get("/api/browse")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_a_request_before_expiry_stays_authenticated(self, web_harness):
        web_harness.config.oidc_enabled = True
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["username"] = "jared"
            session["oidc_exp"] = time.time() + 300

        response = client.get("/api/browse")

        assert response.status_code == 200
        with client.session_transaction() as session:
            assert session["authenticated"] is True

    def test_a_session_with_no_stored_expiry_is_unaffected(self, web_harness):
        """Basic-auth sessions (and OIDC providers that send no id_token) carry no 'oidc_exp' at
        all; the expiry check must be a no-op for them, not treat a missing claim as expired."""
        web_harness.config.auth_enabled = True
        client = web_harness.http_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["username"] = "jared"

        response = client.get("/api/browse")

        assert response.status_code == 200


@pytest.fixture
def save_config_harness(tmp_path, monkeypatch):
    """Like web_harness, but with a real Config instead of the SimpleNamespace stand-in:
    save_config_web calls config.save() and instantiates a fresh Config() of its own to diff
    against, neither of which the stand-in supports."""
    config_path = str(tmp_path / "config.json")
    config = Config(config_path=config_path)
    config.load()

    app = Flask("web_routes_save_config_test", template_folder=str(SRC_DIR / "templates"))
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["SERVER_NAME"] = "localhost"
    socket = SocketIO(app, async_mode="threading")

    monkeypatch.setattr(app_globals, "config", config)
    monkeypatch.setattr(app_globals, "web_socket", socket)
    monkeypatch.setattr(app_globals, "plex", SimpleNamespace(reconnect=lambda cfg: None))
    # Fork: save_config_web now diffs against the active config.path (config_path=config.path),
    # so the real Config class needs no monkeypatching here any more.

    web_routes.setup_routes(app, config)
    web_routes.setup_socket_handlers(config=config, filename_pattern=re.compile(r".*"))

    return SimpleNamespace(app=app, socket=socket, config=config)


class TestConfigRedaction:
    """Secrets (Plex token, password hash, OIDC client secret, webhook token) are never sent
    to the web UI as-is; redact_config() swaps them for a placeholder the save path treats as
    'leave the stored value alone'."""

    def test_configured_secrets_are_replaced_with_the_placeholder(self):
        config_dict = {
            "token": "real-plex-token",
            "auth_password_hash": "real-password-hash",
            "oidc_client_secret": "real-oidc-secret",
            "webhook_token": "real-webhook-token",
            "base_url": "http://plex:32400",
        }

        redacted = web_routes.redact_config(config_dict)

        assert redacted["token"] == web_routes.SECRET_PLACEHOLDER
        assert redacted["auth_password_hash"] == web_routes.SECRET_PLACEHOLDER
        assert redacted["oidc_client_secret"] == web_routes.SECRET_PLACEHOLDER
        assert redacted["webhook_token"] == web_routes.SECRET_PLACEHOLDER
        # Unrelated settings pass through untouched
        assert redacted["base_url"] == "http://plex:32400"

    def test_radarr_and_sonarr_api_keys_are_redacted_and_kept_unless_changed(self, save_config_harness):
        """Fork: radarr_api_key/sonarr_api_key are sent to a third-party service, not just
        stored locally, so they get the same redact-and-keep treatment as the Plex token."""
        redacted = web_routes.redact_config({
            "radarr_api_key": "real-radarr-key",
            "sonarr_api_key": "real-sonarr-key",
        })
        assert redacted["radarr_api_key"] == web_routes.SECRET_PLACEHOLDER
        assert redacted["sonarr_api_key"] == web_routes.SECRET_PLACEHOLDER

        harness = save_config_harness
        harness.config.radarr_api_key = "real-radarr-key"
        harness.config.sonarr_api_key = "real-sonarr-key"
        harness.config.save()

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""
        payload["base_url"] = "http://plex:32400"  # unrelated change so save proceeds

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert app_globals.config.radarr_api_key == "real-radarr-key"
        assert app_globals.config.sonarr_api_key == "real-sonarr-key"

    def test_an_unset_secret_is_left_blank_not_placeholdered(self):
        redacted = web_routes.redact_config({"token": ""})
        assert redacted["token"] == ""

    def test_the_source_dict_is_not_mutated(self):
        config_dict = {"token": "real-plex-token"}
        web_routes.redact_config(config_dict)
        assert config_dict["token"] == "real-plex-token"

    def test_load_config_sends_the_redacted_form(self, save_config_harness):
        harness = save_config_harness
        harness.config.token = "real-plex-token"
        harness.config.save()
        client = harness.socket.test_client(harness.app)

        client.emit("load_config", {"instance_id": "abc"})

        replies = [m for m in client.get_received() if m["name"] == "load_config"]
        assert replies[0]["args"][0]["config"]["token"] == web_routes.SECRET_PLACEHOLDER


class TestSaveConfigKeepsSecretsUnlessChanged:

    def test_echoed_placeholder_keeps_every_stored_secret(self, save_config_harness):
        harness = save_config_harness
        harness.config.token = "real-plex-token"
        harness.config.webhook_token = "real-webhook-token"
        harness.config.oidc_client_secret = "real-oidc-secret"
        harness.config.save()

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""  # unused here, but save_config_web always expects the key
        payload["base_url"] = "http://plex:32400"  # an unrelated change, so save proceeds

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert app_globals.config.token == "real-plex-token"
        assert app_globals.config.webhook_token == "real-webhook-token"
        assert app_globals.config.oidc_client_secret == "real-oidc-secret"
        assert app_globals.config.base_url == "http://plex:32400"

    def test_an_edited_secret_is_stored(self, save_config_harness):
        harness = save_config_harness
        harness.config.token = "old-plex-token"
        harness.config.save()

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""
        payload["token"] = "new-plex-token"

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert app_globals.config.token == "new-plex-token"

    def test_a_cleared_secret_is_removed(self, save_config_harness):
        harness = save_config_harness
        harness.config.token = "old-plex-token"
        harness.config.save()

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""
        payload["token"] = ""

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert app_globals.config.token == ""


class TestSessionSecretKey:
    """Fork: SESSION_SECRET pins the session signing key across restarts."""

    def test_env_value_is_used_verbatim(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "  pinned-key  ")
        assert web_routes.session_secret_key() == "pinned-key"

    def test_unset_or_blank_env_generates_a_fresh_key(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "   ")
        first = web_routes.session_secret_key()
        monkeypatch.delenv("SESSION_SECRET")
        second = web_routes.session_secret_key()
        assert first != second
        assert len(first) == 64 and len(second) == 64


class TestSaveConfigUsesTheActivePath:
    """Fork: save_config_web diffs against config.path (CONFIG_PATH/--config), not a fresh
    default-path Config, and the config path itself is never settable by the client."""

    def test_save_writes_to_the_active_path_and_the_path_cannot_be_hijacked(self, save_config_harness):
        harness = save_config_harness
        original_path = harness.config.path
        assert original_path != DEFAULT_CONFIG_PATH  # sanity: this harness uses a custom path

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""
        payload["path"] = "/tmp/hijacked-config.json"
        payload["base_url"] = "http://plex:32400"  # unrelated change so save proceeds

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert app_globals.config.path == original_path
        assert os.path.exists(original_path)


class TestSaveConfigReconfiguresArr:
    """Fork: the Radarr/Sonarr clients (ArrService) are rebuilt after a successful save in case
    their URL/API key changed, or the pre-seed fallback keeps using stale clients until a
    restart."""

    def test_arr_is_reconfigured_after_a_successful_save(self, save_config_harness, monkeypatch):
        harness = save_config_harness
        reconfigured_with = []
        monkeypatch.setattr(
            app_globals, "arr", SimpleNamespace(reconfigure=lambda cfg: reconfigured_with.append(cfg)))

        payload = web_routes.redact_config(vars(harness.config))
        payload["auth_password"] = ""
        payload["base_url"] = "http://plex:32400"  # unrelated change so save proceeds

        client = harness.socket.test_client(harness.app)
        client.emit("save_config", {"instance_id": "abc", "config": payload})

        assert reconfigured_with == [app_globals.config]


class TestOidcGroups:
    """Fork: the groups claim may be a dotted path and may arrive as a string."""

    def test_nested_claim_path_is_traversed(self):
        claims = {"resource_access": {"artwork": {"roles": ["media", "admin"]}}}
        assert web_routes.oidc_groups(claims, "resource_access.artwork.roles") == ["media", "admin"]

    def test_space_separated_string_is_split(self):
        assert web_routes.oidc_groups({"groups": "media admin"}, "groups") == ["media", "admin"]

    def test_missing_path_yields_no_groups(self):
        assert web_routes.oidc_groups({"resource_access": {}}, "resource_access.artwork.roles") == []
        assert web_routes.oidc_groups({}, "") == []


class TestOidcScopeList:
    """Fork: older fork configs store oidc_scopes as a space-separated string; upstream's
    handler called .append on it and crashed before redirecting to the provider."""

    def test_string_scopes_are_split_and_openid_added(self):
        assert web_routes.oidc_scope_list("profile email") == ["profile", "email", "openid"]

    def test_list_scopes_are_kept_without_duplicating_openid(self):
        assert web_routes.oidc_scope_list(["openid", "groups"]) == ["openid", "groups"]

    def test_empty_scopes_still_request_openid(self):
        assert web_routes.oidc_scope_list("") == ["openid"]
