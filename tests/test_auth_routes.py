import os

import pytest
from flask import Flask, redirect

import web_routes
from core import globals
from core.config import Config
from core.constants import AUTH_MODE_NONE, AUTH_MODE_OIDC, AUTH_MODE_PASSWORD
from services.authentication_service import AuthenticationService
from services.oidc_service import OidcError, OidcService

pytestmark = pytest.mark.unit

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "templates")

IDP_AUTHORIZE_URL = "https://idp.example.com/authorize"


class StubOidcService(OidcService):
    """OidcService with the network-facing steps replaced by canned results."""

    def __init__(self, config, claims=None, error=None):
        super().__init__(config)
        self.claims = claims or {}
        self.error = error

    @property
    def is_configured(self):
        return True

    def authorize_redirect(self, redirect_uri):
        return redirect(f"{IDP_AUTHORIZE_URL}?redirect_uri={redirect_uri}")

    def handle_callback(self):
        if self.error:
            raise self.error
        return self.claims

    def logout_url(self, id_token, post_logout_redirect):
        return None


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=str(tmp_path / "config.json"))
    cfg.load()
    return cfg


@pytest.fixture
def app(config):
    flask_app = Flask(__name__, template_folder=TEMPLATE_DIR)
    flask_app.config["SECRET_KEY"] = "test-secret"
    flask_app.config["TESTING"] = True
    globals.config = config
    globals.oidc_service = StubOidcService(config)
    web_routes.setup_routes(flask_app, config)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def set_oidc_mode(config, allowed_groups=None):
    config.oidc_issuer = "https://idp.example.com"
    config.oidc_client_id = "artwork"
    config.oidc_client_secret = "secret"
    config.oidc_allowed_groups = allowed_groups or []
    config.set_auth_mode(AUTH_MODE_OIDC)


def set_password_mode(config, username="jared", password="hunter2"):
    config.auth_username = username
    config.auth_password_hash = AuthenticationService.hash_password(password)
    config.set_auth_mode(AUTH_MODE_PASSWORD)


class TestAuthDisabled:

    def test_home_is_open_when_auth_is_off(self, client, config):
        assert config.auth_mode == AUTH_MODE_NONE
        assert client.get("/").status_code == 200

    def test_login_redirects_home_when_auth_is_off(self, client):
        response = client.get("/login")
        assert response.status_code == 302
        assert response.headers["Location"] == "/"


class TestPasswordLogin:

    def test_valid_credentials_start_a_session(self, client, config):
        set_password_mode(config)
        response = client.post(
            "/login", data={"username": "jared", "password": "hunter2"})
        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        with client.session_transaction() as session:
            assert session["authenticated"] is True
            assert session["auth_via"] == "password"

    def test_invalid_credentials_are_rejected(self, client, config):
        set_password_mode(config)
        response = client.post(
            "/login", data={"username": "jared", "password": "wrong"})
        assert response.status_code == 401
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_protected_route_redirects_to_login(self, client, config):
        set_password_mode(config)
        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")


class TestOidcLogin:

    def test_protected_route_starts_sso(self, client, config):
        set_oidc_mode(config)
        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/auth/oidc/login")

    def test_login_page_redirects_to_provider(self, client, config):
        set_oidc_mode(config)
        response = client.get("/login", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/auth/oidc/login")

    def test_authorize_redirect_uses_external_url(self, client, config):
        set_oidc_mode(config)
        config.external_url = "https://artwork.example.com/"
        response = client.get("/auth/oidc/login")
        assert response.status_code == 302
        assert response.headers["Location"] == (
            f"{IDP_AUTHORIZE_URL}?redirect_uri="
            "https://artwork.example.com/auth/oidc/callback")

    def test_callback_establishes_session(self, client, config):
        set_oidc_mode(config)
        globals.oidc_service.claims = {
            "preferred_username": "jared", "groups": ["media"],
            "exp": 4102444800, "_id_token": "an-id-token"}

        response = client.get("/auth/oidc/callback?code=abc&state=xyz")

        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        with client.session_transaction() as session:
            assert session["authenticated"] is True
            assert session["auth_via"] == "oidc"
            assert session["user"] == "jared"
            assert session["groups"] == ["media"]
            assert session["id_token"] == "an-id-token"

    def test_callback_returns_to_the_requested_page(self, client, config):
        set_oidc_mode(config)
        globals.oidc_service.claims = {"preferred_username": "jared"}
        with client.session_transaction() as session:
            session["oidc_next"] = "/downloads/poster.jpg"

        response = client.get("/auth/oidc/callback?code=abc")
        assert response.headers["Location"] == "/downloads/poster.jpg"

    def test_callback_ignores_offsite_next_target(self, client, config):
        set_oidc_mode(config)
        globals.oidc_service.claims = {"preferred_username": "jared"}
        with client.session_transaction() as session:
            session["oidc_next"] = "//evil.example.com/steal"

        response = client.get("/auth/oidc/callback?code=abc")
        assert response.headers["Location"] == "/"

    def test_callback_denies_user_outside_allowed_groups(self, client, config):
        set_oidc_mode(config, allowed_groups=["media-admins"])
        globals.oidc_service.claims = {
            "preferred_username": "jared", "groups": ["users"]}

        response = client.get("/auth/oidc/callback?code=abc")

        assert response.status_code == 403
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_callback_reports_exchange_failure(self, client, config):
        set_oidc_mode(config)
        globals.oidc_service.error = OidcError("token exchange failed")

        response = client.get("/auth/oidc/callback?code=abc")

        assert response.status_code == 401
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_callback_rejected_when_sso_is_not_enabled(self, client, config):
        assert config.auth_mode == AUTH_MODE_NONE
        assert client.get("/auth/oidc/callback?code=abc").status_code == 400


class TestSessionValidity:

    def test_expired_idp_token_invalidates_the_session(self, client, config):
        set_oidc_mode(config)
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["auth_via"] = "oidc"
            session["idp_exp"] = 1000  # 1970

        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/auth/oidc/login")

    def test_password_session_survives_when_fallback_is_allowed(self, client, config):
        set_password_mode(config)
        set_oidc_mode(config)
        config.oidc_allow_password_fallback = True
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["auth_via"] = "password"

        assert client.get("/").status_code == 200

    def test_password_session_dropped_when_fallback_is_disabled(self, client, config):
        set_password_mode(config)
        set_oidc_mode(config)
        config.oidc_allow_password_fallback = False
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["auth_via"] = "password"

        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/auth/oidc/login")

    def test_password_post_rejected_when_fallback_is_disabled(self, client, config):
        set_password_mode(config)
        set_oidc_mode(config)
        config.oidc_allow_password_fallback = False

        response = client.post(
            "/login", data={"username": "jared", "password": "hunter2"})
        assert response.status_code == 403


class TestBreakGlassLogin:

    def test_local_login_page_is_reachable_in_oidc_mode(self, client, config):
        set_password_mode(config)
        set_oidc_mode(config)

        response = client.get("/login?local=1")
        assert response.status_code == 200
        assert b'name="password"' in response.data

    def test_local_password_still_works_in_oidc_mode(self, client, config):
        set_password_mode(config)
        set_oidc_mode(config)

        response = client.post(
            "/login?local=1", data={"username": "jared", "password": "hunter2"})
        assert response.status_code == 302
        with client.session_transaction() as session:
            assert session["auth_via"] == "password"


class TestLogout:

    def test_logout_clears_the_session(self, client, config):
        set_password_mode(config)
        client.post("/login", data={"username": "jared", "password": "hunter2"})

        response = client.get("/logout")

        assert response.status_code == 302
        assert response.headers["Location"] == "/login"
        with client.session_transaction() as session:
            assert "authenticated" not in session

    def test_logout_uses_provider_endpoint_when_available(self, client, config, monkeypatch):
        set_oidc_mode(config)
        monkeypatch.setattr(
            globals.oidc_service, "logout_url",
            lambda id_token, post_logout_redirect: "https://idp.example.com/end-session")
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["auth_via"] = "oidc"

        response = client.get("/logout")
        assert response.headers["Location"] == "https://idp.example.com/end-session"


class TestSafeNext:

    @pytest.mark.parametrize("target,expected", [
        ("/settings", "/settings"),
        ("//evil.example.com", "/"),
        ("https://evil.example.com", "/"),
        ("", "/"),
        (None, "/"),
    ])
    def test_only_same_site_paths_are_kept(self, target, expected):
        assert web_routes.safe_next(target) == expected
