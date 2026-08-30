"""
Application configuration management.
"""

import json
import os
import secrets
from typing import List, Dict, Any, Optional

from core.constants import (
    DEFAULT_CONFIG_PATH, DEFAULT_BULK_IMPORT_FILE, DEFAULT_TV_LIBRARY, DEFAULT_MOVIE_LIBRARY,
    DEFAULT_IP_BINDING, RUNNING_IN_DOCKER, DEFAULT_ZIP_TITLE_STRIP_WORDS,
    AUTH_MODES, AUTH_MODE_NONE, AUTH_MODE_PASSWORD, DEFAULT_AUTH_MODE,
    DEFAULT_OIDC_SCOPES, DEFAULT_OIDC_GROUPS_CLAIM, SECRET_PLACEHOLDER, REDACTED_CONFIG_KEYS
)
from core.exceptions import ConfigLoadError, ConfigSaveError, ConfigCreationError
from logging_config import get_logger

logger = get_logger(__name__)


class Config:
    """
    Manages application configuration stored in JSON format.

    Attributes:
        path: Path to the configuration file
        base_url: Plex server URL
        token: Plex authentication token
        bulk_txt: Default bulk import filename
        tv_library: List of TV library names in Plex
        movie_library: List of movie library names in Plex
        mediux_filters: Default filters for MediUX scraping
        tpdb_filters: Default filters for ThePosterDB scraping
        kometa_base: Base directory for Kometa asset storage
        temp_dir: (Optional) Temporary directory for testing purposes
        save_to_kometa: Whether to save artwork to Kometa
        stage_assets: Whether to download assets for seasons and episodes that are not in Plex yet (except Specials)
        stage_specials: Whether to save specials (season 0) artwork to Kometa even when the season doesn't exist in Plex
        stage_collections: Whether to save collection artwork to Kometa even when the collection doesn't exist in Plex
        track_artwork_ids: Whether to track artwork IDs using Plex labels
        skip_locked_artwork: Whether to skip artwork whose target field is locked in Plex (already set)
        auto_manage_bulk_files: Whether to auto-organize bulk files
        reset_overlay: Whether to reset Kometa overlay labels on upload
        schedules: List of scheduled bulk import jobs
        auth_mode: Authentication mode - "none", "password" or "oidc"
        auth_enabled: Legacy mirror of auth_mode != "none", kept for backward compatibility
        auth_username: Username for web server authentication
        auth_password_hash: Hashed password for web server authentication
        oidc_issuer: OIDC provider issuer URL (discovery document is fetched from it)
        oidc_client_id: OIDC client ID
        oidc_client_secret: OIDC client secret (env OIDC_CLIENT_SECRET takes precedence)
        oidc_scopes: Space separated scopes requested from the provider
        oidc_groups_claim: Claim holding the user's groups; dotted paths are supported
        oidc_allowed_groups: Groups permitted to sign in; empty means any authenticated user
        oidc_allow_password_fallback: Whether /login?local=1 still accepts the local password
        oidc_provider_name: Display name shown on the sign-in button
        session_secret: Flask session signing key (env SESSION_SECRET takes precedence)
        session_cookie_secure: "auto", "always" or "never" - controls the Secure cookie flag
        external_url: Public base URL of this app, used to build the OIDC redirect URI
        trusted_proxy_count: Number of reverse proxies in front of the app; 0 disables ProxyFix
        cors_allowed_origins: Origins allowed for HTTP/Socket.IO; empty means same-origin only
        tls_cert_file: Path to a PEM certificate (chain) file; with tls_key_file enables HTTPS (env TLS_CERT_FILE wins)
        tls_key_file: Path to the PEM private key file for tls_cert_file (env TLS_KEY_FILE wins)
        ip_binding: IP binding mode - "auto" (default), "ipv4", or "ipv6"
        debug: Enable debug logging
        kometa_library_paths: Dictionary mapping Plex library names to Kometa directory names
        apprise_urls: List of Apprise notification URLs
        zip_title_strip_words: Max words to strip from end of ZIP filename titles for progressive title matching
        radarr_url: Radarr server URL, used to pre-seed Kometa artwork for movies not yet in Plex
        radarr_api_key: Radarr API key
        sonarr_url: Sonarr server URL, used to pre-seed Kometa artwork for shows/seasons not yet in Plex
        sonarr_api_key: Sonarr API key
        arr_root_folder_library_map: Dictionary mapping Radarr/Sonarr root folder paths to Plex library names
        preseed_arr: Whether to fall back to Radarr/Sonarr to pre-seed Kometa artwork when an item isn't in Plex
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH) -> None:
        self.path: str = config_path
        self.base_url: str = ""
        self.token: str = ""
        self.bulk_txt: str = DEFAULT_BULK_IMPORT_FILE
        self.tv_library: List[str] = DEFAULT_TV_LIBRARY
        self.movie_library: List[str] = DEFAULT_MOVIE_LIBRARY
        self.mediux_filters: List[str] = ["title_card", "background", "season_cover", "show_cover", "movie_poster",
                                          "collection_poster", "square_art"]
        self.tpdb_filters: List[str] = ["season_cover", "show_cover", "movie_poster",
                                        "collection_poster"]
        self.kometa_base: str = "/assets" if RUNNING_IN_DOCKER else ""
        self.temp_dir: str = "/temp" if RUNNING_IN_DOCKER else ""
        self.save_to_kometa: bool = False
        self.stage_assets: bool = True
        self.stage_specials: bool = True
        self.stage_collections: bool = False
        self.track_artwork_ids: bool = True
        self.skip_locked_artwork: bool = False
        self.auto_manage_bulk_files: bool = True
        self.reset_overlay: bool = False
        self.schedules: List[Dict[str, Any]] = []
        self.auth_mode: str = DEFAULT_AUTH_MODE
        self.auth_enabled: bool = False
        self.auth_username: str = ""
        self.auth_password_hash: str = ""
        self.oidc_issuer: str = ""
        self.oidc_client_id: str = ""
        self.oidc_client_secret: str = ""
        self.oidc_scopes: str = DEFAULT_OIDC_SCOPES
        self.oidc_groups_claim: str = DEFAULT_OIDC_GROUPS_CLAIM
        self.oidc_allowed_groups: List[str] = []
        self.oidc_allow_password_fallback: bool = True
        self.oidc_provider_name: str = "SSO"
        self.session_secret: str = ""
        self.session_cookie_secure: str = "auto"
        self.external_url: str = ""
        self.trusted_proxy_count: int = 1
        self.cors_allowed_origins: List[str] = []
        self.tls_cert_file: str = ""
        self.tls_key_file: str = ""
        self.ip_binding: str = DEFAULT_IP_BINDING
        self.debug: bool = False
        self.kometa_library_paths: Dict[str, str] = {}
        self.apprise_urls: List[str] = []
        self.zip_title_strip_words: int = DEFAULT_ZIP_TITLE_STRIP_WORDS
        self.radarr_url: str = ""
        self.radarr_api_key: str = ""
        self.sonarr_url: str = ""
        self.sonarr_api_key: str = ""
        self.arr_root_folder_library_map: Dict[str, str] = {}
        self.preseed_arr: bool = False

    def load(self) -> None:
        """Load the configuration from the JSON file."""
        logger.debug(f"Config path: {self.path}")
        logger.debug(f"File exists: {os.path.isfile(self.path)}")

        # If a config file doesn't exist, create one with default values
        if not os.path.isfile(self.path):
            logger.debug("Config file does not exist, calling create()")
            self.create()
            logger.debug(
                f"After create(), file exists: {os.path.isfile(self.path)}")

        # Load the configuration from the config.json file
        logger.debug(
            f"Attempting to open config file for reading: {self.path}")
        try:
            with open(self.path, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)

            self.base_url = config.get("base_url", "")
            self.token = config.get("token", "")
            self.tv_library = config.get("tv_library", [])
            self.movie_library = config.get("movie_library", [])
            self.mediux_filters = config.get("mediux_filters", [])
            self.tpdb_filters = config.get("tpdb_filters", [])
            loaded_kometa_base = config.get("kometa_base", None)
            if not loaded_kometa_base or loaded_kometa_base.strip() == "":
                self.kometa_base = "/assets" if RUNNING_IN_DOCKER else ""
            else:
                self.kometa_base = loaded_kometa_base
            loaded_temp_dir = config.get("temp_dir", None)
            if not loaded_temp_dir or loaded_temp_dir.strip() == "":
                self.temp_dir = "/temp" if RUNNING_IN_DOCKER else ""
            else:
                self.temp_dir = loaded_temp_dir
            self.save_to_kometa = config.get("save_to_kometa", False)
            self.stage_assets = config.get("stage_assets", True)
            self.stage_specials = config.get("stage_specials", True)
            self.stage_collections = config.get("stage_collections", False)
            self.bulk_txt = config.get("bulk_txt", "bulk_import.txt")
            self.track_artwork_ids = config.get("track_artwork_ids", True)
            self.skip_locked_artwork = config.get("skip_locked_artwork", False)
            self.auto_manage_bulk_files = config.get(
                "auto_manage_bulk_files", True)
            self.reset_overlay = config.get("reset_overlay", False)
            self.schedules = config.get("schedules", [])
            self.auth_username = config.get("auth_username", "")
            self.auth_password_hash = config.get("auth_password_hash", "")
            self._load_auth_mode(config)
            self.oidc_issuer = config.get("oidc_issuer", "")
            self.oidc_client_id = config.get("oidc_client_id", "")
            self.oidc_client_secret = config.get("oidc_client_secret", "")
            self.oidc_scopes = config.get(
                "oidc_scopes", DEFAULT_OIDC_SCOPES) or DEFAULT_OIDC_SCOPES
            self.oidc_groups_claim = config.get(
                "oidc_groups_claim", DEFAULT_OIDC_GROUPS_CLAIM) or DEFAULT_OIDC_GROUPS_CLAIM
            self.oidc_allowed_groups = config.get("oidc_allowed_groups", [])
            self.oidc_allow_password_fallback = config.get(
                "oidc_allow_password_fallback", True)
            self.oidc_provider_name = config.get(
                "oidc_provider_name", "SSO") or "SSO"
            self.session_secret = config.get("session_secret", "")
            self.session_cookie_secure = config.get(
                "session_cookie_secure", "auto") or "auto"
            self.external_url = config.get("external_url", "")
            try:
                self.trusted_proxy_count = max(
                    0, int(config.get("trusted_proxy_count", 1)))
            except (TypeError, ValueError):
                self.trusted_proxy_count = 1
            self.cors_allowed_origins = config.get("cors_allowed_origins", [])
            self.tls_cert_file = config.get("tls_cert_file", "") or ""
            self.tls_key_file = config.get("tls_key_file", "") or ""
            self.ip_binding = config.get("ip_binding", DEFAULT_IP_BINDING)
            self.debug = config.get("debug", False)
            self.kometa_library_paths = config.get("kometa_library_paths", {})
            self.apprise_urls = config.get("apprise_urls", [])
            raw_zip_title_strip_words = config.get(
                "zip_title_strip_words", DEFAULT_ZIP_TITLE_STRIP_WORDS
            )
            try:
                zip_title_strip_words = int(raw_zip_title_strip_words)
            except (TypeError, ValueError):
                zip_title_strip_words = DEFAULT_ZIP_TITLE_STRIP_WORDS
            if zip_title_strip_words < 0:
                zip_title_strip_words = 0
            self.zip_title_strip_words = zip_title_strip_words
            self.radarr_url = config.get("radarr_url", "")
            self.radarr_api_key = config.get("radarr_api_key", "")
            self.sonarr_url = config.get("sonarr_url", "")
            self.sonarr_api_key = config.get("sonarr_api_key", "")
            self.arr_root_folder_library_map = config.get(
                "arr_root_folder_library_map", {})
            self.preseed_arr = config.get("preseed_arr", False)

        except Exception as e:
            raise ConfigLoadError(
                f"Failed to load config from {self.path}: {str(e)}") from e

    def create(self) -> None:
        """Create a new configuration file with default values."""
        logger.debug(f"Creating config at: {self.path}")
        logger.debug(f"Path is absolute: {os.path.isabs(self.path)}")
        logger.debug(f"Parent directory: {os.path.dirname(self.path)}")
        parent_exists = os.path.isdir(os.path.dirname(self.path)) if os.path.dirname(
            self.path) else 'N/A (current dir)'
        logger.debug(f"Parent dir exists: {parent_exists}")

        config_json = {
            "base_url": "",
            "token": "",
            "bulk_txt": "bulk_import.txt",
            "tv_library": ["TV Shows"],
            "movie_library": ["Movies"],
            "mediux_filters": ["title_card", "background", "season_cover", "show_cover", "movie_poster",
                               "collection_poster", "square_art"],
            "tpdb_filters": ["season_cover", "show_cover", "movie_poster",
                             "collection_poster"],
            "kometa_base": "",
            "temp_dir": "",
            "save_to_kometa": False,
            "stage_assets": False,
            "stage_specials": True,
            "stage_collections": False,
            "track_artwork_ids": True,
            "skip_locked_artwork": False,
            "auto_manage_bulk_files": True,
            "reset_overlay": False,
            "schedules": [],
            "auth_mode": DEFAULT_AUTH_MODE,
            "auth_enabled": False,
            "oidc_issuer": "",
            "oidc_client_id": "",
            "oidc_client_secret": "",
            "oidc_scopes": DEFAULT_OIDC_SCOPES,
            "oidc_groups_claim": DEFAULT_OIDC_GROUPS_CLAIM,
            "oidc_allowed_groups": [],
            "oidc_allow_password_fallback": True,
            "oidc_provider_name": "SSO",
            "session_cookie_secure": "auto",
            "external_url": "",
            "trusted_proxy_count": 1,
            "cors_allowed_origins": [],
            "tls_cert_file": "",
            "tls_key_file": "",
            "debug": False,
            "kometa_library_paths": {},
            "apprise_urls": [],
            "radarr_url": "",
            "radarr_api_key": "",
            "sonarr_url": "",
            "sonarr_api_key": "",
            "arr_root_folder_library_map": {},
            "preseed_arr": False
        }

        # Create the config.json file if it doesn't exist
        if not os.path.isfile(self.path):
            logger.debug("File does not exist, attempting to create")
            try:
                # Ensure parent directory exists
                parent_dir = os.path.dirname(self.path)
                if parent_dir and not os.path.isdir(parent_dir):
                    logger.debug(f"Creating parent directory: {parent_dir}")
                    os.makedirs(parent_dir, exist_ok=True)

                logger.debug(f"Opening file for writing: {self.path}")
                with open(self.path, "w", encoding="utf-8") as config_file:
                    json.dump(config_json, config_file, indent=4)
                logger.debug("File written successfully")
                logger.info(
                    f"Config file '{self.path}' created with default settings.")
            except Exception as e:
                logger.error(
                    f"Failed to create config file at {self.path}", exc_info=True)
                raise ConfigCreationError(
                    f"Failed to create config file at {self.path}: {str(e)}") from e
        else:
            logger.debug("File already exists, skipping creation")

    def save(self) -> None:
        """Save the current configuration to the file."""

        for schedule in self.schedules:
            schedule.pop("jobReference", None)

        config_json = {
            "base_url": self.base_url,
            "token": self.token,
            "tv_library": self.tv_library,
            "movie_library": self.movie_library,
            "mediux_filters": self.mediux_filters,
            "tpdb_filters": self.tpdb_filters,
            "kometa_base": self.kometa_base,
            "temp_dir": self.temp_dir,
            "save_to_kometa": self.save_to_kometa,
            "stage_assets": self.stage_assets,
            "stage_specials": self.stage_specials,
            "stage_collections": self.stage_collections,
            "bulk_txt": self.bulk_txt,
            "track_artwork_ids": self.track_artwork_ids,
            "skip_locked_artwork": self.skip_locked_artwork,
            "auto_manage_bulk_files": self.auto_manage_bulk_files,
            "reset_overlay": self.reset_overlay,
            "schedules": self.schedules,
            "auth_mode": self.auth_mode,
            "auth_enabled": self.auth_enabled,
            "auth_username": self.auth_username,
            "auth_password_hash": self.auth_password_hash,
            "oidc_issuer": self.oidc_issuer,
            "oidc_client_id": self.oidc_client_id,
            "oidc_client_secret": self.oidc_client_secret,
            "oidc_scopes": self.oidc_scopes,
            "oidc_groups_claim": self.oidc_groups_claim,
            "oidc_allowed_groups": self.oidc_allowed_groups,
            "oidc_allow_password_fallback": self.oidc_allow_password_fallback,
            "oidc_provider_name": self.oidc_provider_name,
            "session_secret": self.session_secret,
            "session_cookie_secure": self.session_cookie_secure,
            "external_url": self.external_url,
            "trusted_proxy_count": self.trusted_proxy_count,
            "cors_allowed_origins": self.cors_allowed_origins,
            "tls_cert_file": self.tls_cert_file,
            "tls_key_file": self.tls_key_file,
            "ip_binding": self.ip_binding,
            "debug": self.debug,
            "kometa_library_paths": self.kometa_library_paths,
            "apprise_urls": self.apprise_urls,
            "zip_title_strip_words": self.zip_title_strip_words,
            "radarr_url": self.radarr_url,
            "radarr_api_key": self.radarr_api_key,
            "sonarr_url": self.sonarr_url,
            "sonarr_api_key": self.sonarr_api_key,
            "arr_root_folder_library_map": self.arr_root_folder_library_map,
            "preseed_arr": self.preseed_arr
        }

        try:
            with open(self.path, "w", encoding="utf-8") as config_file:
                json.dump(config_json, config_file, indent=4)
        except Exception as e:
            raise ConfigSaveError(
                f"Failed to save config to {self.path}: {str(e)}") from e

    def _load_auth_mode(self, config: Dict[str, Any]) -> None:
        """
        Resolve auth_mode from the config file.

        Configs written before auth_mode existed only carry auth_enabled, so an
        enabled legacy config is migrated to password mode. auth_enabled is kept
        in sync so a downgrade to an older version still finds authentication on.
        """
        legacy_enabled = bool(config.get("auth_enabled", False))
        mode = config.get("auth_mode")
        if mode not in AUTH_MODES:
            mode = AUTH_MODE_PASSWORD if legacy_enabled else AUTH_MODE_NONE
        self.auth_mode = mode
        self.auth_enabled = mode != AUTH_MODE_NONE

    def set_auth_mode(self, mode: str) -> None:
        """Set the authentication mode, keeping the legacy auth_enabled flag in sync."""
        if mode not in AUTH_MODES:
            raise ValueError(f"Unknown auth mode: {mode}")
        self.auth_mode = mode
        self.auth_enabled = mode != AUTH_MODE_NONE

    @property
    def auth_required(self) -> bool:
        """Whether requests must be authenticated."""
        return self.auth_mode != AUTH_MODE_NONE

    @staticmethod
    def _env_or(env_name: str, fallback: str) -> str:
        """Return the environment value when set and non-empty, else the config value."""
        return os.environ.get(env_name, "").strip() or fallback

    def get_oidc_issuer(self) -> str:
        """OIDC issuer URL, with OIDC_ISSUER taking precedence."""
        return self._env_or("OIDC_ISSUER", self.oidc_issuer).rstrip("/")

    def get_oidc_client_id(self) -> str:
        """OIDC client ID, with OIDC_CLIENT_ID taking precedence."""
        return self._env_or("OIDC_CLIENT_ID", self.oidc_client_id)

    def get_oidc_client_secret(self) -> str:
        """OIDC client secret, with OIDC_CLIENT_SECRET taking precedence."""
        return self._env_or("OIDC_CLIENT_SECRET", self.oidc_client_secret)

    def oidc_is_configured(self) -> bool:
        """Whether enough OIDC settings are present to attempt a login."""
        return bool(self.get_oidc_issuer() and self.get_oidc_client_id()
                    and self.get_oidc_client_secret())

    def get_tls_cert_file(self) -> str:
        """TLS certificate file path, with TLS_CERT_FILE taking precedence."""
        return self._env_or("TLS_CERT_FILE", self.tls_cert_file)

    def get_tls_key_file(self) -> str:
        """TLS private key file path, with TLS_KEY_FILE taking precedence."""
        return self._env_or("TLS_KEY_FILE", self.tls_key_file)

    def tls_is_enabled(self) -> bool:
        """
        Whether the web server terminates TLS itself (both cert and key set).

        A half-configured pair reports False here but still aborts startup in
        start_web_server rather than silently serving plain HTTP.
        """
        return bool(self.get_tls_cert_file() and self.get_tls_key_file())

    def ensure_session_secret(self) -> str:
        """
        Return the Flask session signing key, generating and persisting one if needed.

        A stable key is required so sessions survive restarts; without it every
        container restart silently logs everyone out.
        """
        env_secret = os.environ.get("SESSION_SECRET", "").strip()
        if env_secret:
            return env_secret
        if not self.session_secret:
            self.session_secret = secrets.token_hex(32)
            self.save()
        return self.session_secret

    def session_cookie_is_secure(self) -> bool:
        """
        Whether the session cookie should carry the Secure flag.

        "auto" infers it from external_url so a plain-HTTP LAN deployment keeps
        working while an HTTPS deployment gets the flag without extra config.
        """
        if self.session_cookie_secure == "always":
            return True
        if self.session_cookie_secure == "never":
            return False
        return self.tls_is_enabled() or self.external_url.lower().startswith("https://")

    def to_public_dict(self) -> Dict[str, Any]:
        """
        Return the config as a dict safe to send to the web UI.

        Secrets are replaced with a placeholder that save_config treats as
        "keep the stored value".
        """
        public = dict(vars(self))
        public.pop("session_secret", None)
        for key in REDACTED_CONFIG_KEYS:
            if public.get(key):
                public[key] = SECRET_PLACEHOLDER
        public["secret_placeholder"] = SECRET_PLACEHOLDER
        public["auth_required"] = self.auth_required
        public["oidc_client_secret_from_env"] = bool(
            os.environ.get("OIDC_CLIENT_SECRET", "").strip())
        public["oidc_configured"] = self.oidc_is_configured()
        return public

    def resolve_library_directory(self, library_name: str) -> str:
        """
        Resolve the directory name for a given Plex library.

        If the library is mapped in kometa_library_paths, return the mapped name.
        Otherwise, return the library name as-is (backward compatible).

        Args:
            library_name: The name of the Plex library

        Returns:
            The directory name to use in the Kometa asset structure
        """
        return self.kometa_library_paths.get(library_name, library_name)

    def resolve_arr_library(self, root_folder_path: Optional[str], media_type: str) -> str:
        """
        Resolve the Plex library name for a Radarr/Sonarr root folder path.

        Matches root_folder_path against arr_root_folder_library_map using the
        longest matching prefix (trailing slashes are ignored). Falls back to
        the first configured movie_library/tv_library entry when no mapping matches.

        Args:
            root_folder_path: The root folder path reported by Radarr/Sonarr.
            media_type: "movie" or "tv" - selects the fallback library list.

        Returns:
            The Plex library name to use.
        """
        if root_folder_path:
            normalized_path = root_folder_path.rstrip("/\\")
            best_match_library = None
            best_match_len = -1
            for mapped_path, library_name in self.arr_root_folder_library_map.items():
                normalized_mapped = mapped_path.rstrip("/\\")
                if normalized_path == normalized_mapped or normalized_path.startswith(
                        normalized_mapped + "/") or normalized_path.startswith(normalized_mapped + "\\"):
                    if len(normalized_mapped) > best_match_len:
                        best_match_library = library_name
                        best_match_len = len(normalized_mapped)
            if best_match_library is not None:
                return best_match_library

        fallback = self.movie_library if media_type == "movie" else self.tv_library
        if isinstance(fallback, list):
            return fallback[0] if fallback else ""
        return fallback or ""
