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
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask
from flask_socketio import SocketIO

import core.globals as app_globals
import web_routes
from core.config import Config
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
}
USER_INFO = {"preferred_username": "jared", "groups": ["media"]}


def _fake_oauth_get(self, url, **kwargs):
    return _Response(USER_INFO if url.endswith("/userinfo") else DISCOVERY)


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
            self.token = {"id_token": "an-id-token"}
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
            self.token = {"id_token": "an-id-token"}
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
    # save_config_web builds its own Config() to diff against; point it at the same file.
    monkeypatch.setattr(web_routes, "Config", lambda: Config(config_path=config_path))

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
