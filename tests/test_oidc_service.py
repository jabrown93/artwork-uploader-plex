import pytest

from core.config import Config
from services.oidc_service import OidcError, OidcService

pytestmark = pytest.mark.unit


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=str(tmp_path / "config.json"))
    cfg.load()
    return cfg


@pytest.fixture
def service(config):
    return OidcService(config)


class TestGetGroups:

    def test_list_claim(self, service):
        assert service.get_groups({"groups": ["admins", "media"]}) == ["admins", "media"]

    def test_space_separated_string_claim(self, service):
        assert service.get_groups({"groups": "admins media"}) == ["admins", "media"]

    def test_missing_claim(self, service):
        assert service.get_groups({"sub": "abc"}) == []

    def test_dotted_claim_path(self, config, service):
        config.oidc_groups_claim = "resource_access.artwork.roles"
        claims = {"resource_access": {"artwork": {"roles": ["uploader"]}}}
        assert service.get_groups(claims) == ["uploader"]

    def test_dotted_path_missing_intermediate_key(self, config, service):
        config.oidc_groups_claim = "resource_access.artwork.roles"
        assert service.get_groups({"resource_access": {}}) == []

    def test_dotted_path_hits_non_dict(self, config, service):
        config.oidc_groups_claim = "resource_access.artwork.roles"
        assert service.get_groups({"resource_access": "nope"}) == []

    def test_unexpected_claim_type(self, service):
        assert service.get_groups({"groups": 42}) == []


class TestIsAuthorized:

    def test_empty_allowlist_permits_any_user(self, service):
        assert service.is_authorized({"sub": "abc"}) is True

    def test_matching_group_is_allowed(self, config, service):
        config.oidc_allowed_groups = ["media-admins"]
        assert service.is_authorized({"groups": ["users", "media-admins"]}) is True

    def test_non_matching_group_is_denied(self, config, service):
        config.oidc_allowed_groups = ["media-admins"]
        assert service.is_authorized({"groups": ["users"]}) is False

    def test_missing_groups_claim_is_denied(self, config, service):
        config.oidc_allowed_groups = ["media-admins"]
        assert service.is_authorized({"sub": "abc"}) is False

    def test_blank_entries_in_allowlist_are_ignored(self, config, service):
        config.oidc_allowed_groups = ["", "  "]
        # A list of empty strings must not become an impossible-to-satisfy allowlist
        assert service.is_authorized({"sub": "abc"}) is True

    def test_group_match_is_case_sensitive(self, config, service):
        config.oidc_allowed_groups = ["Media-Admins"]
        assert service.is_authorized({"groups": ["media-admins"]}) is False


class TestGetUsername:

    def test_prefers_preferred_username(self):
        assert OidcService.get_username(
            {"preferred_username": "jared", "email": "j@example.com"}) == "jared"

    def test_falls_back_through_claims(self):
        assert OidcService.get_username({"email": "j@example.com"}) == "j@example.com"
        assert OidcService.get_username({"name": "Jared"}) == "Jared"
        assert OidcService.get_username({"sub": "abc-123"}) == "abc-123"

    def test_unknown_when_no_identifying_claim(self):
        assert OidcService.get_username({}) == "unknown"


class TestClientConstruction:

    def test_unconfigured_service_refuses_to_build_a_client(self, service):
        with pytest.raises(OidcError):
            service.authorize_redirect("https://artwork.example.com/auth/oidc/callback")

    def test_configured_service_without_app_refuses(self, config, service):
        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "id"
        config.oidc_client_secret = "secret"
        with pytest.raises(OidcError):
            service.authorize_redirect("https://artwork.example.com/auth/oidc/callback")

    def test_logout_url_is_none_when_unconfigured(self, service):
        assert service.logout_url("token", "https://artwork.example.com/login") is None

    def test_client_requests_pkce_and_discovery(self, config):
        from flask import Flask

        flask_app = Flask(__name__)
        flask_app.config["SECRET_KEY"] = "test-secret"
        config.oidc_issuer = "https://idp.example.com/application/o/artwork/"
        config.oidc_client_id = "artwork"
        config.oidc_client_secret = "secret"
        service = OidcService(config, flask_app)

        client = service._get_client()

        assert client.client_kwargs["code_challenge_method"] == "S256"
        assert client.client_kwargs["scope"] == config.oidc_scopes
        assert client._server_metadata_url == (
            "https://idp.example.com/application/o/artwork/.well-known/openid-configuration")

    def test_client_is_rebuilt_when_config_changes(self, config):
        from flask import Flask

        flask_app = Flask(__name__)
        flask_app.config["SECRET_KEY"] = "test-secret"
        config.oidc_issuer = "https://idp.example.com"
        config.oidc_client_id = "artwork"
        config.oidc_client_secret = "secret"
        service = OidcService(config, flask_app)

        first = service._get_client()
        assert service._get_client() is first

        config.oidc_client_id = "artwork-2"
        assert service._get_client() is not first
