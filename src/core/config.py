"""
Application configuration management.
"""

import errno
import json
import os
import shutil
import uuid
from typing import List, Dict, Any, Optional

from core import globals
from core.constants import (
    DEFAULT_CONFIG_PATH, DEFAULT_BULK_IMPORT_FILE, DEFAULT_TV_LIBRARY, DEFAULT_MOVIE_LIBRARY,
    DEFAULT_PLEX_CONNECT_TIMEOUT,
    DEFAULT_KOMETA_DOWNLOAD_TIMEOUT,
    DEFAULT_UPLOAD_RETRY_ATTEMPTS,
    DEFAULT_UPLOAD_RETRY_BACKOFF_SECONDS,
    DEFAULT_NOTIFICATION_EVENTS,
    DEFAULT_IP_BINDING  # Fork: HTTPS / IP binding
)
from core.exceptions import ConfigLoadError, ConfigSaveError, ConfigCreationError
from logging_config import get_logger

logger = get_logger(__name__)


def normalize_notification_channels(apprise_urls: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize the apprise_urls config entry into a list of {"url", "events"} channels.

    Older config files store apprise_urls as a bare list of URL strings, with every
    configured channel receiving every notification. Those are migrated here to
    channels subscribed to DEFAULT_NOTIFICATION_EVENTS, which matches what they
    actually received before per-event routing existed, so upgrading does not
    suddenly start sending more than before.
    """
    channels = []
    for entry in apprise_urls or []:
        if isinstance(entry, dict):
            url = entry.get("url", "")
            if not url:
                continue
            events = entry.get("events", DEFAULT_NOTIFICATION_EVENTS)
            if not isinstance(events, list):
                events = DEFAULT_NOTIFICATION_EVENTS
            channels.append({"url": url, "events": list(events)})
        elif isinstance(entry, str):
            if entry:
                channels.append({"url": entry, "events": list(DEFAULT_NOTIFICATION_EVENTS)})
    return channels


def write_json_atomically(path: str, config_json: Dict[str, Any]) -> None:
    """
    Write the configuration JSON to a temporary file and move it into place.

    Writing over the live config.json means a process stopped part way through the write
    leaves it truncated, and the next load cannot parse it. That file holds the Plex
    token, the webhook token, the OIDC client secret, the basic auth password hash and
    every schedule, so losing it means entering all of that again by hand. os.replace is
    atomic on POSIX and on Windows, so a reader sees either the old file or the new one
    and never a half written one. The temporary file sits in the same directory as the
    target, or the move becomes a cross device copy and stops being atomic.

    Fork: Docker's documented layout (docker-compose.example.yml) bind-mounts the individual
    file at /config/config.json rather than the containing directory. That makes the target
    path itself a mount point, and the kernel refuses to rename anything over an active mount
    point (EBUSY) even though writing into the existing inode is fine. When os.replace hits
    that, fall back to writing the already-rendered temp file's bytes directly into the
    existing file - the same non-atomic in-place write this function replaced, so a
    directory-mounted (or un-mounted) config path keeps full atomicity and only the
    single-file-bind-mount layout loses it, same as before this function existed.
    """
    parent_dir = os.path.dirname(path)
    if parent_dir and not os.path.isdir(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as config_file:
            json.dump(config_json, config_file, indent=4)
        if os.path.isfile(path):
            # The file being moved into place is a new one with whatever permissions the
            # umask gave it, and this one holds credentials, so a config.json an
            # administrator has restricted to its owner stays that way across a save.
            os.chmod(temp_path, os.stat(path).st_mode & 0o777)
        try:
            os.replace(temp_path, path)
        except OSError as e:
            if e.errno != errno.EBUSY:
                raise
            logger.warning(
                f"os.replace() onto {path!r} failed with EBUSY (likely a Docker single-file "
                f"bind mount); falling back to a non-atomic in-place write"
            )
            shutil.copyfile(temp_path, path)
            os.remove(temp_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass  # nothing to tidy up, or the same problem that stopped the write
        raise


class Config:
    """
    Manages application configuration stored in JSON format.

    Attributes:
        path: Path to the configuration file
        base_url: Plex server URL
        token: Plex authentication token
        plex_local_assets: Save assets locally to media folders
        path_mappings: Path mappings for media folders between Plex and Artwork Uploader
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
        local_library_matching: Whether to match scraped artwork against the local libraries before fetching poster pages
        allow_artist_updates: Whether to update locked artwork we applied when the same artist has posted a newer version (requires skip_locked_artwork and track_artwork_ids)
        cache_user_scrapes: Whether to keep a persistent index of ThePosterDB users' uploads so repeat scrapes only fetch new ones
        user_cache_refresh_days: Days between full re-crawls of a cached user's uploads (catches edits and deletions)
        auto_manage_bulk_files: Whether to auto-organize bulk files
        reset_overlay: Whether to reset Kometa overlay labels on upload
        schedules: List of scheduled bulk import jobs
        catch_up_window_minutes: How late a missed scheduled run can be and still run on startup, in minutes (0 disables catch-up)
        auth_enabled: Whether basic authentication is enabled for the web server
        auth_username: Username for web server authentication
        auth_password_hash: Hashed password for web server authentication
        oidc_enabled: Whether OIDC authentication is enabled for the web server
        oidc_label: Name of the OIDC provider, used to customize the login button
        oidc_issuer: Base URL for the OIDC provider (must be https)
        oidc_client_id: Client ID for the app in the OIDC provider
        oidc_client_secret: Client secret for the app in the OIDC provider
        oidc_scopes: List of OIDC scopes for the authentication session
        oidc_groups_claim: OIDC claim containing the group memberships for users
        oidc_allowed_groups: Only authorize login for members of selected groups, or any authenticated user if none specified
        external_url: External URL for the Artwork-Uploader app, used to construct the OIDC callback URI (must be https)
        kometa_library_paths: Dictionary mapping Plex library names to Kometa directory names
        apprise_urls: List of notification channels, each {"url": Apprise URL, "events": list of event names the channel is subscribed to}
        radarr_url: Radarr server URL, used to pre-seed Kometa artwork for movies not yet in Plex
        radarr_api_key: Radarr API key
        sonarr_url: Sonarr server URL, used to pre-seed Kometa artwork for shows/seasons not yet in Plex
        sonarr_api_key: Sonarr API key
        arr_root_folder_library_map: Dictionary mapping Radarr/Sonarr root folder paths to Plex library names
        preseed_arr: Whether to fall back to Radarr/Sonarr to pre-seed Kometa artwork when an item isn't in Plex
        enable_webhooks: Whether the Sonarr/Radarr import webhook endpoint is enabled
        webhook_token: Shared secret required on webhook requests
        webhook_tpdb_users: ThePosterDB users to apply cached artwork from on import, in priority order
        webhook_apply_delay: Seconds to wait after an import before applying artwork (lets Plex scan first)
        plex_connect_timeout: Timeout for connecting to the Plex server (also applies to uploads)
        kometa_download_timeout: Timeout for downloading artwork to save to the Kometa asset directory
        upload_retry_attempts: Total attempts (including the first) made for a transient upload failure
        upload_retry_backoff_seconds: Seconds to wait before the first retry, doubling after each attempt
        tls_cert_file: Path to a PEM certificate (chain) file; with tls_key_file enables HTTPS (env TLS_CERT_FILE wins)
        tls_key_file: Path to the PEM private key file for tls_cert_file (env TLS_KEY_FILE wins)
        ip_binding: IP binding mode for the web server - "auto" (dual-stack), "ipv4", or "ipv6"
    """

    @classmethod
    def at_active_path(cls) -> "Config":
        """
        Fork: a fresh instance pointed at the config the app is running with.

        CONFIG_PATH/--config move the file off the default; code that re-reads the config on
        its own (scrapers, the upload processor) must follow it or it sees a different file.
        """
        from core import globals
        active = getattr(globals.config, "path", None) if globals.config is not None else None
        return cls(config_path=active) if active else cls()

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH) -> None:
        self.path: str = config_path
        self.base_url: str = ""
        self.token: str = ""
        self.plex_local_assets: bool = False
        self.path_mappings: List[Dict[str, str]] = []
        self.bulk_txt: str = DEFAULT_BULK_IMPORT_FILE
        self.tv_library: List[str] = DEFAULT_TV_LIBRARY
        self.movie_library: List[str] = DEFAULT_MOVIE_LIBRARY
        self.mediux_filters: List[str] = ["title_card", "background", "season_cover", "show_cover", "movie_poster",
                                          "collection_poster", "square_art"]
        self.tpdb_filters: List[str] = ["season_cover", "show_cover", "movie_poster",
                                        "collection_poster"]
        self.kometa_base: str = ""
        self.temp_dir: str = ""
        self.save_to_kometa: bool = False
        self.stage_assets: bool = True
        self.stage_specials: bool = True
        self.stage_collections: bool = False
        self.track_artwork_ids: bool = True
        self.skip_locked_artwork: bool = False
        self.local_library_matching: bool = False
        self.allow_artist_updates: bool = False
        self.cache_user_scrapes: bool = False
        self.user_cache_refresh_days: int = 7
        self.auto_manage_bulk_files: bool = True
        self.reset_overlay: bool = False
        self.schedules: List[Dict[str, Any]] = []
        self.catch_up_window_minutes: int = 0
        self.auth_enabled: bool = False
        self.auth_username: str = ""
        self.auth_password_hash: str = ""
        self.oidc_enabled: bool = False
        self.oidc_label: str = ""
        self.oidc_issuer: str = ""
        self.oidc_client_id: str = ""
        self.oidc_client_secret: str = ""
        self.oidc_scopes: str = ""
        self.oidc_groups_claim: str = ""
        self.oidc_allowed_groups: List[str] = []
        self.external_url: str = ""
        self.kometa_library_paths: Dict[str, str] = {}
        self.apprise_urls: List[Dict[str, Any]] = []
        self.radarr_url: str = ""
        self.radarr_api_key: str = ""
        self.sonarr_url: str = ""
        self.sonarr_api_key: str = ""
        self.arr_root_folder_library_map: Dict[str, str] = {}
        self.preseed_arr: bool = False
        self.enable_webhooks: bool = False
        self.webhook_token: str = ""
        self.webhook_tpdb_users: List[str] = []
        self.webhook_apply_delay: int = 30
        self.plex_connect_timeout: int = DEFAULT_PLEX_CONNECT_TIMEOUT
        self.kometa_download_timeout: int = DEFAULT_KOMETA_DOWNLOAD_TIMEOUT
        self.upload_retry_attempts: int = DEFAULT_UPLOAD_RETRY_ATTEMPTS
        self.upload_retry_backoff_seconds: float = DEFAULT_UPLOAD_RETRY_BACKOFF_SECONDS
        # Fork: HTTPS termination and IP binding mode for the web server
        self.tls_cert_file: str = ""
        self.tls_key_file: str = ""
        self.ip_binding: str = DEFAULT_IP_BINDING

    def load(self) -> None:
        """Load the configuration from the JSON file."""
        logger.debug(f"Config path: {self.path}")
        logger.debug(f"File exists: {os.path.isfile(self.path)}")

        # If a config file doesn't exist, create one with default values
        if not os.path.isfile(self.path):
            logger.debug("Config file does not exist, calling create()")
            self.create()
            logger.debug(f"After create(), file exists: {os.path.isfile(self.path)}")

        schedules_migrated = False

        # Load the configuration from the config.json file
        try:
            with open(self.path, "r", encoding="utf-8") as config_file:
                config = json.load(config_file)

            self.base_url = config.get("base_url", "")
            self.token = config.get("token", "")
            self.plex_local_assets = config.get("plex_local_assets", False)
            self.path_mappings = config.get("path_mappings", [])
            self.tv_library = config.get("tv_library", [])
            self.movie_library = config.get("movie_library", [])
            self.mediux_filters = config.get("mediux_filters", [])
            self.tpdb_filters = config.get("tpdb_filters", [])
            if globals.docker:
                from utils.utils import get_host_path  # deferred: utils -> notifications -> services -> arr_service imports Config
                self.kometa_base = get_host_path("/assets")
                self.temp_dir = get_host_path("/temp")
            else:
                self.kometa_base = config.get("kometa_base", "")
                self.temp_dir = config.get("temp_dir", "")
            self.save_to_kometa = config.get("save_to_kometa", False)
            self.stage_assets = config.get("stage_assets", True)
            self.stage_specials = config.get("stage_specials", True)
            self.stage_collections = config.get("stage_collections", False)
            self.bulk_txt = config.get("bulk_txt", "bulk_import.txt")
            self.track_artwork_ids = config.get("track_artwork_ids", True)
            self.skip_locked_artwork = config.get("skip_locked_artwork", False)
            self.local_library_matching = config.get("local_library_matching", False)
            self.allow_artist_updates = config.get("allow_artist_updates", False)
            self.cache_user_scrapes = config.get("cache_user_scrapes", False)
            self.user_cache_refresh_days = config.get("user_cache_refresh_days", 7)
            self.auto_manage_bulk_files = config.get("auto_manage_bulk_files", True)
            self.reset_overlay = config.get("reset_overlay", False)
            self.schedules, schedules_migrated = self._migrate_schedules(config.get("schedules", []))
            self.catch_up_window_minutes = config.get("catch_up_window_minutes", 0)
            self.auth_enabled = config.get("auth_enabled", False)
            self.auth_username = config.get("auth_username", "")
            self.auth_password_hash = config.get("auth_password_hash", "")
            self.oidc_enabled = config.get("oidc_enabled", False)
            self.oidc_label = config.get("oidc_label", "")
            self.oidc_issuer = config.get("oidc_issuer", "")
            self.oidc_client_id = config.get("oidc_client_id", "")
            self.oidc_client_secret = config.get("oidc_client_secret", "")
            self.oidc_scopes = config.get("oidc_scopes", "")
            self.oidc_groups_claim = config.get("oidc_groups_claim", "")
            self.oidc_allowed_groups = config.get("oidc_allowed_groups", [])
            self.external_url = config.get("external_url", "")
            self.kometa_library_paths = config.get("kometa_library_paths", {})
            self.apprise_urls = normalize_notification_channels(config.get("apprise_urls", []))
            self.radarr_url = config.get("radarr_url", "")
            self.radarr_api_key = config.get("radarr_api_key", "")
            self.sonarr_url = config.get("sonarr_url", "")
            self.sonarr_api_key = config.get("sonarr_api_key", "")
            self.arr_root_folder_library_map = config.get("arr_root_folder_library_map", {})
            self.preseed_arr = config.get("preseed_arr", False)
            self.enable_webhooks = config.get("enable_webhooks", False)
            self.webhook_token = config.get("webhook_token", "")
            self.webhook_tpdb_users = config.get("webhook_tpdb_users", [])
            self.webhook_apply_delay = config.get("webhook_apply_delay", 30)
            self.plex_connect_timeout = config.get("plex_connect_timeout", DEFAULT_PLEX_CONNECT_TIMEOUT)
            self.kometa_download_timeout = config.get("kometa_download_timeout", DEFAULT_KOMETA_DOWNLOAD_TIMEOUT)
            self.upload_retry_attempts = config.get("upload_retry_attempts", DEFAULT_UPLOAD_RETRY_ATTEMPTS)
            self.upload_retry_backoff_seconds = config.get(
                "upload_retry_backoff_seconds", DEFAULT_UPLOAD_RETRY_BACKOFF_SECONDS)
            # Fork: HTTPS / IP binding
            self.tls_cert_file = config.get("tls_cert_file", "") or ""
            self.tls_key_file = config.get("tls_key_file", "") or ""
            self.ip_binding = config.get("ip_binding", DEFAULT_IP_BINDING)

        except Exception as e:
            raise ConfigLoadError(f"Failed to load config from {self.path}: {str(e)}") from e

        # Persist any ids minted by the schedule migration above, so they stay stable
        # across reloads instead of being handed out fresh every time (which would
        # break anything that had already matched a schedule by id).
        if schedules_migrated:
            self.save()

    @staticmethod
    def _migrate_schedules(schedules: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], bool]:
        """
        Give every schedule an id, so schedules are addressed by their own
        id rather than by filename.

        Older config.json files stored one schedule per file as
        {"file": ..., "time": ...} with no id. Those entries keep working
        unchanged, they just gain an id here so they can sit alongside
        other schedules on the same file.

        Returns:
            The schedule list, and whether any entry was missing an id
        """
        migrated = False
        for entry in schedules:
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
                migrated = True
        return schedules, migrated

    def create(self) -> None:
        """Create a new configuration file with default values."""
        logger.debug(f"Creating config at: {self.path}")
        logger.debug(f"Path is absolute: {os.path.isabs(self.path)}")

        config_json = {
            "base_url": "",
            "token": "",
            "plex_local_assets": False,
            "path_mappings": [],
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
            "local_library_matching": False,
            "allow_artist_updates": False,
            "cache_user_scrapes": False,
            "user_cache_refresh_days": 7,
            "auto_manage_bulk_files": True,
            "reset_overlay": False,
            "schedules": [],
            "catch_up_window_minutes": 0,
            "auth_enabled": False,
            "auth_username": "",
            "oidc_enabled": False,
            "oidc_label": "",
            "oidc_issuer": "",
            "oidc_client_id": "",
            "oidc_client_secret": "",
            "oidc_scopes": "",
            "oidc_groups_claim": "",
            "oidc_allowed_groups": [],
            "external_url": "",
            "kometa_library_paths": {},
            "apprise_urls": [],
            "radarr_url": "",
            "radarr_api_key": "",
            "sonarr_url": "",
            "sonarr_api_key": "",
            "arr_root_folder_library_map": {},
            "preseed_arr": False,
            "enable_webhooks": False,
            "webhook_token": "",
            "webhook_tpdb_users": [],
            "webhook_apply_delay": 30,
            "plex_connect_timeout": DEFAULT_PLEX_CONNECT_TIMEOUT,
            "kometa_download_timeout": DEFAULT_KOMETA_DOWNLOAD_TIMEOUT,
            "upload_retry_attempts": DEFAULT_UPLOAD_RETRY_ATTEMPTS,
            "upload_retry_backoff_seconds": DEFAULT_UPLOAD_RETRY_BACKOFF_SECONDS,
            "tls_cert_file": "",
            "tls_key_file": "",
            "ip_binding": DEFAULT_IP_BINDING
        }

        if globals.docker:
            from utils.utils import get_host_path  # deferred: see load()
            host_kometa_base = get_host_path("/assets")
            config_json["kometa_base"] = host_kometa_base if host_kometa_base != "(not defined)" else ""
            host_temp_dir = get_host_path("/temp")
            config_json["temp_dir"] = host_temp_dir if host_temp_dir != "(not defined)" else ""

        # Create the config.json file if it doesn't exist
        if not os.path.isfile(self.path):
            logger.debug("File does not exist, attempting to create")
            try:
                write_json_atomically(self.path, config_json)
                logger.info(f"Config file '{self.path}' created with default settings.")
            except Exception as e:
                logger.error(f"Failed to create config file at {self.path}", exc_info=True)
                raise ConfigCreationError(f"Failed to create config file at {self.path}: {str(e)}") from e
        else:
            logger.debug("File already exists, skipping creation")

    def save(self) -> None:
        """Save the current configuration to the file."""

        for schedule in self.schedules:
            schedule.pop("jobReference", None)

        config_json = {
            "base_url": self.base_url,
            "token": self.token,
            "plex_local_assets": self.plex_local_assets,
            "path_mappings": self.path_mappings,
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
            "local_library_matching": self.local_library_matching,
            "allow_artist_updates": self.allow_artist_updates,
            "cache_user_scrapes": self.cache_user_scrapes,
            "user_cache_refresh_days": self.user_cache_refresh_days,
            "auto_manage_bulk_files": self.auto_manage_bulk_files,
            "reset_overlay": self.reset_overlay,
            "schedules": self.schedules,
            "catch_up_window_minutes": self.catch_up_window_minutes,
            "auth_enabled": self.auth_enabled,
            "auth_username": self.auth_username,
            "auth_password_hash": self.auth_password_hash,
            "oidc_enabled": self.oidc_enabled,
            "oidc_label": self.oidc_label,
            "oidc_issuer": self.oidc_issuer,
            "oidc_client_id": self.oidc_client_id,
            "oidc_client_secret": self.oidc_client_secret,
            "oidc_scopes": self.oidc_scopes,
            "oidc_groups_claim": self.oidc_groups_claim,
            "oidc_allowed_groups": self.oidc_allowed_groups,
            "external_url": self.external_url,
            "kometa_library_paths": self.kometa_library_paths,
            "apprise_urls": self.apprise_urls,
            "radarr_url": self.radarr_url,
            "radarr_api_key": self.radarr_api_key,
            "sonarr_url": self.sonarr_url,
            "sonarr_api_key": self.sonarr_api_key,
            "arr_root_folder_library_map": self.arr_root_folder_library_map,
            "preseed_arr": self.preseed_arr,
            "enable_webhooks": self.enable_webhooks,
            "webhook_token": self.webhook_token,
            "webhook_tpdb_users": self.webhook_tpdb_users,
            "webhook_apply_delay": self.webhook_apply_delay,
            "plex_connect_timeout": self.plex_connect_timeout,
            "kometa_download_timeout": self.kometa_download_timeout,
            "upload_retry_attempts": self.upload_retry_attempts,
            "upload_retry_backoff_seconds": self.upload_retry_backoff_seconds,
            "tls_cert_file": self.tls_cert_file,
            "tls_key_file": self.tls_key_file,
            "ip_binding": self.ip_binding
        }

        try:
            write_json_atomically(self.path, config_json)
        except Exception as e:
            raise ConfigSaveError(f"Failed to save config to {self.path}: {str(e)}") from e

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

    # Fork: HTTPS / IP binding

    @staticmethod
    def _env_or(env_name: str, fallback: str) -> str:
        """Return the environment value when set and non-empty, else the config value."""
        return os.environ.get(env_name, "").strip() or fallback

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
