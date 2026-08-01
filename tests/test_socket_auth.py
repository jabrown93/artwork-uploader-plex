import pytest
from flask import Flask, session

import web_routes
from core import globals
from core.config import Config
from core.constants import (
    AUTH_MODE_NONE, AUTH_MODE_OIDC, AUTH_MODE_PASSWORD, SECRET_PLACEHOLDER
)
from core.exceptions import ConfigurationError

pytestmark = pytest.mark.unit


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=str(tmp_path / "config.json"))
    cfg.load()
    globals.config = cfg
    return cfg


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test-secret"
    return flask_app


@pytest.fixture
def disconnects(monkeypatch):
    """Record disconnect() calls instead of touching a real Socket.IO context."""
    calls = []
    monkeypatch.setattr(web_routes, "disconnect", lambda *a, **kw: calls.append(True))
    return calls


class TestSocketLoginRequired:

    def test_event_runs_when_auth_is_off(self, app, config, disconnects):
        handled = []

        @web_routes.socket_login_required
        def handler(data):
            handled.append(data)

        with app.test_request_context("/socket.io/"):
            handler({"instance_id": "1"})

        assert handled == [{"instance_id": "1"}]
        assert disconnects == []

    def test_event_rejected_without_a_session(self, app, config, disconnects):
        config.auth_username = "jared"
        config.auth_password_hash = "hash"
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        handled = []

        @web_routes.socket_login_required
        def handler(data):
            handled.append(data)

        with app.test_request_context("/socket.io/"):
            handler({"instance_id": "1"})

        assert handled == []
        assert disconnects == [True]

    def test_event_runs_for_an_authenticated_session(self, app, config, disconnects):
        config.auth_username = "jared"
        config.auth_password_hash = "hash"
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        handled = []

        @web_routes.socket_login_required
        def handler(data):
            handled.append(data)

        with app.test_request_context("/socket.io/"):
            session["authenticated"] = True
            session["auth_via"] = "password"
            handler({"instance_id": "1"})

        assert handled == [{"instance_id": "1"}]
        assert disconnects == []

    def test_event_rejected_once_the_idp_token_expired(self, app, config, disconnects):
        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "id"
        config.oidc_client_secret = "secret"
        config.set_auth_mode(AUTH_MODE_OIDC)
        handled = []

        @web_routes.socket_login_required
        def handler(data):
            handled.append(data)

        with app.test_request_context("/socket.io/"):
            session["authenticated"] = True
            session["auth_via"] = "oidc"
            session["idp_exp"] = 1000  # 1970
            handler({"instance_id": "1"})

        assert handled == []
        assert disconnects == [True]


class TestApplyAuthMode:

    def test_explicit_mode_is_applied(self, config):
        web_routes.apply_auth_mode(config, {"auth_mode": AUTH_MODE_OIDC})
        assert config.auth_mode == AUTH_MODE_OIDC

    def test_legacy_client_flag_maps_to_password_mode(self, config):
        web_routes.apply_auth_mode(config, {"auth_enabled": True})
        assert config.auth_mode == AUTH_MODE_PASSWORD

    def test_legacy_client_flag_can_disable_auth(self, config):
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        web_routes.apply_auth_mode(config, {"auth_enabled": False})
        assert config.auth_mode == AUTH_MODE_NONE

    def test_unknown_mode_is_ignored(self, config):
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        web_routes.apply_auth_mode(config, {"auth_mode": "kerberos"})
        assert config.auth_mode == AUTH_MODE_PASSWORD


class TestValidateAuthConfig:

    def test_password_mode_without_a_password_is_rejected(self, config):
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        with pytest.raises(ConfigurationError):
            web_routes.validate_auth_config(config)

    def test_oidc_mode_without_client_details_is_rejected(self, config):
        config.set_auth_mode(AUTH_MODE_OIDC)
        with pytest.raises(ConfigurationError):
            web_routes.validate_auth_config(config)

    def test_configured_modes_pass(self, config):
        config.auth_password_hash = "hash"
        config.set_auth_mode(AUTH_MODE_PASSWORD)
        web_routes.validate_auth_config(config)

        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "id"
        config.oidc_client_secret = "secret"
        config.set_auth_mode(AUTH_MODE_OIDC)
        web_routes.validate_auth_config(config)


class TestProtectedConfigKeys:

    def test_secrets_and_derived_keys_are_protected(self):
        assert "auth_password_hash" in web_routes.PROTECTED_CONFIG_KEYS
        assert "session_secret" in web_routes.PROTECTED_CONFIG_KEYS
        assert "auth_mode" in web_routes.PROTECTED_CONFIG_KEYS
        assert "path" in web_routes.PROTECTED_CONFIG_KEYS

    def test_placeholder_secret_is_never_stored(self, config):
        """The UI echoes back a placeholder; storing it would destroy the secret."""
        config.oidc_client_secret = "real-secret"
        public = config.to_public_dict()
        assert public["oidc_client_secret"] == SECRET_PLACEHOLDER
