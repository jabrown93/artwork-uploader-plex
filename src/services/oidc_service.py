"""
OpenID Connect authentication service.

Wraps Authlib's Flask OAuth client to run the Authorization Code flow with PKCE
against any OIDC provider that publishes a discovery document.
"""

from typing import Any, Dict, List, Optional

from authlib.integrations.flask_client import OAuth

from core.config import Config
from core.constants import OIDC_DISCOVERY_PATH
from core.exceptions import ArtworkUploaderException
from logging_config import get_logger

logger = get_logger(__name__)

CLIENT_NAME = "oidc"


class OidcError(ArtworkUploaderException):
    """Raised when an OIDC login cannot be completed."""


class OidcService:
    """
    Runs OIDC logins for the web UI.

    The Authlib client is rebuilt whenever the relevant configuration changes so
    that edits made in the settings tab take effect without a restart.
    """

    def __init__(self, config: Config, app=None) -> None:
        self._config = config
        self._app = app
        self._client = None
        self._signature: Optional[tuple] = None

    def init_app(self, app) -> None:
        """Bind the service to the Flask app (Authlib's registry requires one)."""
        self._app = app
        self._client = None
        self._signature = None

    def reconfigure(self, config: Config) -> None:
        """Point the service at a new config object and drop the cached client."""
        self._config = config
        self._client = None
        self._signature = None

    @property
    def is_configured(self) -> bool:
        """Whether issuer, client ID and client secret are all present."""
        return self._config.oidc_is_configured()

    @property
    def provider_name(self) -> str:
        """Display name for the sign-in button."""
        return self._config.oidc_provider_name or "SSO"

    def _current_signature(self) -> tuple:
        return (
            self._config.get_oidc_issuer(),
            self._config.get_oidc_client_id(),
            self._config.get_oidc_client_secret(),
            self._config.oidc_scopes,
        )

    def _get_client(self):
        """Return the Authlib client, building it if config changed since last use."""
        if not self.is_configured:
            raise OidcError(
                "OIDC is not configured - issuer, client ID and client secret are all required")
        if self._app is None:
            raise OidcError("OIDC service was not bound to the Flask app")

        signature = self._current_signature()
        if self._client is not None and signature == self._signature:
            return self._client

        issuer, client_id, client_secret, scopes = signature
        oauth = OAuth(self._app)
        oauth.register(
            name=CLIENT_NAME,
            server_metadata_url=f"{issuer}{OIDC_DISCOVERY_PATH}",
            client_id=client_id,
            client_secret=client_secret,
            client_kwargs={
                "scope": scopes,
                # PKCE protects the code exchange even if the redirect is intercepted
                "code_challenge_method": "S256",
            },
        )
        self._client = getattr(oauth, CLIENT_NAME)
        self._signature = signature
        logger.debug(f"Built OIDC client for issuer {issuer}")
        return self._client

    def authorize_redirect(self, redirect_uri: str):
        """
        Start the login by redirecting the browser to the provider.

        Args:
            redirect_uri: Absolute callback URL registered with the provider

        Returns:
            A Flask redirect response
        """
        return self._get_client().authorize_redirect(redirect_uri)

    def handle_callback(self) -> Dict[str, Any]:
        """
        Complete the login from the provider's redirect back to us.

        Returns:
            The user's claims, merged from the ID token and the userinfo endpoint.

        Raises:
            OidcError: If the exchange fails or no claims could be resolved.
        """
        client = self._get_client()
        try:
            token = client.authorize_access_token()
        except Exception as e:
            raise OidcError(f"Token exchange failed: {e}") from e

        claims: Dict[str, Any] = dict(token.get("userinfo") or {})

        # Group memberships are frequently omitted from the ID token, so always
        # merge in the userinfo endpoint's view of the user
        try:
            userinfo = client.userinfo(token=token)
            if userinfo:
                claims.update(dict(userinfo))
        except Exception as e:
            logger.debug(f"Could not fetch OIDC userinfo: {e}")

        if not claims:
            raise OidcError("Provider returned no claims for this user")

        claims["_id_token"] = token.get("id_token", "")
        return claims

    def get_groups(self, claims: Dict[str, Any]) -> List[str]:
        """
        Extract the user's groups from their claims.

        Supports dotted claim paths (e.g. "resource_access.artwork.roles") and
        providers that return a space separated string instead of a list.
        """
        value: Any = claims
        for part in (self._config.oidc_groups_claim or "").split("."):
            if not isinstance(value, dict):
                return []
            value = value.get(part)
            if value is None:
                return []

        if isinstance(value, str):
            return value.split()
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return []

    def is_authorized(self, claims: Dict[str, Any]) -> bool:
        """Whether the user's groups satisfy the configured allowlist."""
        allowed = [group.strip() for group in (
            self._config.oidc_allowed_groups or []) if group and group.strip()]
        if not allowed:
            return True
        return bool(set(self.get_groups(claims)) & set(allowed))

    @staticmethod
    def get_username(claims: Dict[str, Any]) -> str:
        """Best-effort display name for the signed-in user."""
        for key in ("preferred_username", "email", "name", "sub"):
            value = claims.get(key)
            if value:
                return str(value)
        return "unknown"

    def logout_url(self, id_token: str, post_logout_redirect: str) -> Optional[str]:
        """
        Build the provider's RP-initiated logout URL.

        Returns None when the provider does not advertise an end_session_endpoint,
        in which case clearing the local session is all we can do.
        """
        if not self.is_configured:
            return None
        try:
            metadata = self._get_client().load_server_metadata()
        except Exception as e:
            logger.debug(f"Could not load OIDC metadata for logout: {e}")
            return None

        endpoint = metadata.get("end_session_endpoint")
        if not endpoint:
            return None

        from urllib.parse import urlencode

        params = {"post_logout_redirect_uri": post_logout_redirect}
        if id_token:
            params["id_token_hint"] = id_token
        params["client_id"] = self._config.get_oidc_client_id()
        return f"{endpoint}?{urlencode(params)}"
