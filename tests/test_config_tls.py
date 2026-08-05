import json

import pytest

from core.config import Config
from core.exceptions import ConfigurationError
from web_routes import resolve_tls_files

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


class TestTlsConfig:

    def test_defaults_to_disabled(self, config):
        assert config.tls_cert_file == ""
        assert config.tls_key_file == ""
        assert config.tls_is_enabled() is False

    def test_loads_and_round_trips(self, tmp_path):
        path = write_config(tmp_path, {
            "tls_cert_file": "/certs/tls.crt",
            "tls_key_file": "/certs/tls.key",
        })
        cfg = Config(config_path=path)
        cfg.load()
        assert cfg.get_tls_cert_file() == "/certs/tls.crt"
        assert cfg.get_tls_key_file() == "/certs/tls.key"
        assert cfg.tls_is_enabled() is True

        cfg.save()
        with open(path, encoding="utf-8") as saved_file:
            saved = json.load(saved_file)
        assert saved["tls_cert_file"] == "/certs/tls.crt"
        assert saved["tls_key_file"] == "/certs/tls.key"

    def test_env_overrides_config(self, config, monkeypatch):
        config.tls_cert_file = "/config/tls.crt"
        config.tls_key_file = "/config/tls.key"
        monkeypatch.setenv("TLS_CERT_FILE", "/env/tls.crt")
        monkeypatch.setenv("TLS_KEY_FILE", "/env/tls.key")
        assert config.get_tls_cert_file() == "/env/tls.crt"
        assert config.get_tls_key_file() == "/env/tls.key"

    def test_env_pair_enables_without_config(self, config, monkeypatch):
        monkeypatch.setenv("TLS_CERT_FILE", "/env/tls.crt")
        monkeypatch.setenv("TLS_KEY_FILE", "/env/tls.key")
        assert config.tls_is_enabled() is True

    def test_half_pair_is_not_enabled(self, config, monkeypatch):
        monkeypatch.setenv("TLS_CERT_FILE", "/env/tls.crt")
        assert config.tls_is_enabled() is False


class TestSessionCookieSecureWithTls:

    def test_auto_secure_when_tls_enabled(self, config):
        config.tls_cert_file = "/certs/tls.crt"
        config.tls_key_file = "/certs/tls.key"
        assert config.session_cookie_is_secure() is True

    def test_auto_insecure_without_tls_or_https_url(self, config):
        assert config.session_cookie_is_secure() is False

    def test_never_wins_over_tls(self, config):
        config.tls_cert_file = "/certs/tls.crt"
        config.tls_key_file = "/certs/tls.key"
        config.session_cookie_secure = "never"
        assert config.session_cookie_is_secure() is False


class TestResolveTlsFiles:

    def test_returns_ssl_kwargs_for_valid_pair(self, tmp_path):
        cert = tmp_path / "tls.crt"
        key = tmp_path / "tls.key"
        cert.write_text("cert")
        key.write_text("key")
        assert resolve_tls_files(str(cert), str(key)) == {
            "certfile": str(cert), "keyfile": str(key)}

    def test_rejects_missing_key_path(self, tmp_path):
        cert = tmp_path / "tls.crt"
        cert.write_text("cert")
        with pytest.raises(ConfigurationError, match="both"):
            resolve_tls_files(str(cert), "")

    def test_rejects_nonexistent_cert_file(self, tmp_path):
        key = tmp_path / "tls.key"
        key.write_text("key")
        with pytest.raises(ConfigurationError, match="certificate file not found"):
            resolve_tls_files(str(tmp_path / "missing.crt"), str(key))

    def test_rejects_nonexistent_key_file(self, tmp_path):
        cert = tmp_path / "tls.crt"
        cert.write_text("cert")
        with pytest.raises(ConfigurationError, match="private key file not found"):
            resolve_tls_files(str(cert), str(tmp_path / "missing.key"))
