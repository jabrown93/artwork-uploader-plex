import json

import pytest

from core.config import Config
from core.constants import (
    AUTH_MODE_NONE, AUTH_MODE_OIDC, AUTH_MODE_PASSWORD, SECRET_PLACEHOLDER
)

pytestmark = pytest.mark.unit


def write_config(tmp_path, payload):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=str(tmp_path / "config.json"))
    cfg.load()
    return cfg


class TestAuthModeMigration:

    def test_fresh_config_defaults_to_no_auth(self, config):
        assert config.auth_mode == AUTH_MODE_NONE
        assert config.auth_enabled is False
        assert config.auth_required is False

    def test_legacy_enabled_config_migrates_to_password(self, tmp_path):
        cfg = Config(config_path=write_config(
            tmp_path, {"auth_enabled": True, "auth_username": "jared"}))
        cfg.load()
        assert cfg.auth_mode == AUTH_MODE_PASSWORD
        assert cfg.auth_required is True

    def test_legacy_disabled_config_stays_off(self, tmp_path):
        cfg = Config(config_path=write_config(tmp_path, {"auth_enabled": False}))
        cfg.load()
        assert cfg.auth_mode == AUTH_MODE_NONE

    def test_unknown_mode_falls_back_to_legacy_flag(self, tmp_path):
        cfg = Config(config_path=write_config(
            tmp_path, {"auth_mode": "kerberos", "auth_enabled": True}))
        cfg.load()
        assert cfg.auth_mode == AUTH_MODE_PASSWORD

    def test_oidc_mode_round_trips_and_keeps_legacy_flag(self, tmp_path):
        path = str(tmp_path / "config.json")
        cfg = Config(config_path=path)
        cfg.load()
        cfg.set_auth_mode(AUTH_MODE_OIDC)
        cfg.save()

        saved = json.loads(open(path, encoding="utf-8").read())
        assert saved["auth_mode"] == AUTH_MODE_OIDC
        assert saved["auth_enabled"] is True

        reloaded = Config(config_path=path)
        reloaded.load()
        assert reloaded.auth_mode == AUTH_MODE_OIDC

    def test_set_auth_mode_rejects_unknown_mode(self, config):
        with pytest.raises(ValueError):
            config.set_auth_mode("kerberos")


class TestOidcEnvOverrides:

    def test_env_takes_precedence_over_file(self, config, monkeypatch):
        config.oidc_issuer = "https://file.example.com"
        config.oidc_client_id = "file-id"
        config.oidc_client_secret = "file-secret"

        monkeypatch.setenv("OIDC_ISSUER", "https://env.example.com")
        monkeypatch.setenv("OIDC_CLIENT_ID", "env-id")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-secret")

        assert config.get_oidc_issuer() == "https://env.example.com"
        assert config.get_oidc_client_id() == "env-id"
        assert config.get_oidc_client_secret() == "env-secret"

    def test_blank_env_falls_back_to_file(self, config, monkeypatch):
        config.oidc_issuer = "https://file.example.com"
        monkeypatch.setenv("OIDC_ISSUER", "   ")
        assert config.get_oidc_issuer() == "https://file.example.com"

    def test_issuer_trailing_slash_is_stripped(self, config):
        config.oidc_issuer = "https://idp.example.com/application/o/app/"
        assert config.get_oidc_issuer() == "https://idp.example.com/application/o/app"

    def test_is_configured_requires_all_three_settings(self, config):
        assert config.oidc_is_configured() is False
        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "id"
        assert config.oidc_is_configured() is False
        config.oidc_client_secret = "secret"
        assert config.oidc_is_configured() is True


class TestSessionSecret:

    def test_generated_secret_is_persisted(self, tmp_path):
        path = str(tmp_path / "config.json")
        cfg = Config(config_path=path)
        cfg.load()

        secret = cfg.ensure_session_secret()
        assert len(secret) >= 32

        reloaded = Config(config_path=path)
        reloaded.load()
        assert reloaded.ensure_session_secret() == secret

    def test_env_secret_is_not_persisted(self, tmp_path, monkeypatch):
        path = str(tmp_path / "config.json")
        cfg = Config(config_path=path)
        cfg.load()
        monkeypatch.setenv("SESSION_SECRET", "from-the-environment")

        assert cfg.ensure_session_secret() == "from-the-environment"
        assert cfg.session_secret == ""


class TestCookieSecure:

    @pytest.mark.parametrize("setting,external_url,expected", [
        ("auto", "https://artwork.example.com", True),
        ("auto", "http://artwork.example.com", False),
        ("auto", "", False),
        ("always", "http://artwork.example.com", True),
        ("never", "https://artwork.example.com", False),
    ])
    def test_cookie_secure_resolution(self, config, setting, external_url, expected):
        config.session_cookie_secure = setting
        config.external_url = external_url
        assert config.session_cookie_is_secure() is expected


class TestPublicDict:

    def test_secrets_are_redacted(self, config):
        config.token = "plex-token"
        config.radarr_api_key = "radarr-key"
        config.sonarr_api_key = "sonarr-key"
        config.oidc_client_secret = "super-secret"
        config.session_secret = "signing-key"

        public = config.to_public_dict()

        assert public["token"] == SECRET_PLACEHOLDER
        assert public["radarr_api_key"] == SECRET_PLACEHOLDER
        assert public["sonarr_api_key"] == SECRET_PLACEHOLDER
        assert public["oidc_client_secret"] == SECRET_PLACEHOLDER
        assert "session_secret" not in public

    def test_no_secret_value_survives_redaction(self, config):
        config.token = "plex-token"
        config.radarr_api_key = "radarr-key"
        config.sonarr_api_key = "sonarr-key"
        config.oidc_client_secret = "super-secret"
        config.session_secret = "signing-key"

        serialized = json.dumps(config.to_public_dict())

        for secret in ("plex-token", "radarr-key", "sonarr-key", "super-secret", "signing-key"):
            assert secret not in serialized

    def test_absent_secret_is_not_placeholdered(self, config):
        public = config.to_public_dict()
        assert public["oidc_client_secret"] == ""
        assert public["token"] == ""

    def test_placeholder_satisfies_the_plex_token_input_pattern(self):
        """The settings form validates the token field, so the placeholder must pass."""
        import re

        assert re.fullmatch(r"[A-Za-z0-9_\-]{20,40}", SECRET_PLACEHOLDER)

    def test_reports_env_managed_secret(self, config, monkeypatch):
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-secret")
        assert config.to_public_dict()["oidc_client_secret_from_env"] is True
