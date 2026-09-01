"""
Web routes and Socket.IO handlers for the Flask application.

This module contains all Flask routes and Socket.IO event handlers,
extracted from artwork_uploader.py for better organization and maintainability.

The routes are organized into:
- HTTP routes (Flask @app.route)
- Socket.IO event handlers (@socket.on)
- Helper functions for file uploads and processing
"""
import shutil
from typing import Optional

from utils.notifications import update_log, update_status, notify_web, debug_me, send_notification
from utils import utils
from services import UtilityService, AuthenticationService
from services import NotifyService
from processors.media_metadata import parse_title
from models.instance import Instance
from core.constants import (
    SOURCE_MEDIUX, SOURCE_THEPOSTERDB, SEASON_SQUARE_ART,
    AUTH_MODE_NONE, AUTH_MODE_OIDC, AUTH_MODE_PASSWORD, AUTH_MODES,
    SECRET_PLACEHOLDER, REDACTED_CONFIG_KEYS
)
from core.config import Config
from core.enums import FilterType
from core.exceptions import InvalidUrl, InvalidFlag, ConfigurationError
from core import globals
import base64
import os
import pprint
import re
import socket
import unicodedata
import subprocess
import sys
import tempfile
import time
import zipfile
from functools import wraps

from flask import render_template, send_from_directory, request, redirect, url_for, session
from flask_socketio import ConnectionRefusedError, disconnect
from eventlet.semaphore import Semaphore
from packaging import version
from plexapi.server import PlexServer
from logging_config import get_logger

logger = get_logger(__name__)


SOURCE_TXT = "source.txt"

# Config keys the web UI must never overwrite directly: secrets, derived values,
# and the config file path itself
PROTECTED_CONFIG_KEYS = frozenset({
    "auth_password_hash", "auth_enabled", "auth_mode", "session_secret", "path"
})


def is_ipv6_available():
    """
    Check if IPv6 is available on the system.

    Returns:
        bool: True if IPv6 is available, False otherwise
    """
    try:
        # Try to create an IPv6 socket and bind to the IPv6 loopback address
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as test_socket:
            try:
                test_socket.bind(('::1', 0))
                return True
            except OSError:
                return False
    except (OSError, AttributeError):
        # AF_INET6 not available or socket creation failed
        return False


def is_dual_stack_supported():
    """
    Test if binding to :: actually enables dual-stack (IPv4 + IPv6) listening.

    This is important for Windows compatibility where the IPV6_V6ONLY socket
    option might prevent dual-stack behavior.

    Returns:
        bool: True if :: binding supports both IPv4 and IPv6, False otherwise
    """
    # First check if IPv6 is available at all
    if not is_ipv6_available():
        return False

    try:
        # Create a test server socket bound to :: (all interfaces, IPv6)
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as server_socket:

            # Set socket options to allow reuse
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Try to disable IPV6_V6ONLY if possible (enables dual-stack)
            # This might not be available on all platforms
            try:
                server_socket.setsockopt(
                    socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (OSError, AttributeError):
                # IPV6_V6ONLY not available or can't be set
                pass

            # Bind to :: on a random port
            server_socket.bind(('::', 0))
            server_socket.listen(1)

            # Get the port that was assigned
            port = server_socket.getsockname()[1]

            # Test results
            ipv4_works = False
            ipv6_works = False

            # Test IPv6 connection
            try:
                with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as ipv6_client:
                    ipv6_client.settimeout(1)
                    ipv6_client.connect(('::1', port))
                    ipv6_works = True
            except OSError:
                pass

            # Test IPv4 connection (this is the key test for dual-stack)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4_client:
                    ipv4_client.settimeout(1)
                    ipv4_client.connect(('127.0.0.1', port))
                    ipv4_works = True
            except OSError:
                pass

            # Dual-stack works if both IPv4 and IPv6 connections succeeded
            return ipv4_works and ipv6_works
    except Exception as e:
        debug_me(
            f"Error testing dual-stack support: {e}", "is_dual_stack_supported")
        return False


def is_session_authenticated(config: Optional[Config]) -> bool:
    """
    Whether the current Flask session is a valid, still-live login.

    Sessions are invalidated when the identity provider's token has expired, and
    when a password session survives a switch to OIDC-only authentication.
    """
    if not config or not config.auth_required:
        return True

    if not session.get('authenticated'):
        return False

    expires_at = session.get('idp_exp')
    if expires_at:
        try:
            if time.time() >= float(expires_at):
                return False
        except (TypeError, ValueError):
            return False

    if (config.auth_mode == AUTH_MODE_OIDC
            and session.get('auth_via') == 'password'
            and not config.oidc_allow_password_fallback):
        return False

    return True


def oidc_available(config: Config) -> bool:
    """Whether an OIDC login can actually be started right now."""
    return bool(config.auth_mode == AUTH_MODE_OIDC
                and globals.oidc_service is not None
                and globals.oidc_service.is_configured)


def safe_next(target: Optional[str]) -> str:
    """Return target if it is a same-site relative path, else the app root."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def external_base_url(config: Config) -> str:
    """
    Public base URL of this app.

    Prefers the configured external_url; otherwise relies on the request host,
    which is only correct behind a proxy when trusted_proxy_count is set.
    """
    if config.external_url:
        return config.external_url.rstrip("/")
    return request.host_url.rstrip("/")


def oidc_redirect_uri(config: Config) -> str:
    """Absolute callback URL that must be registered with the provider."""
    return f"{external_base_url(config)}{url_for('oidc_callback')}"


def apply_config_updates(config: Config, incoming: dict) -> None:
    """
    Merge a save_config payload into the config object.

    Protected keys are ignored, and a redacted secret that comes back as the
    placeholder leaves the stored value alone - the web UI never receives the
    real one, so echoing the placeholder back must not overwrite it.
    """
    for key, value in incoming.items():
        if key in PROTECTED_CONFIG_KEYS:
            continue
        if key in REDACTED_CONFIG_KEYS and value == SECRET_PLACEHOLDER:
            continue
        # Only real settings are writable; to_public_dict also carries computed
        # keys and read-only properties that setattr would reject
        if key not in vars(config):
            continue
        setattr(config, key, value)


def apply_auth_mode(config: Config, incoming: dict) -> None:
    """
    Apply the auth mode from a save_config payload.

    Clients written before auth_mode existed only send auth_enabled, so that is
    honoured as a request for password mode.
    """
    requested = incoming.get("auth_mode")
    if requested in AUTH_MODES:
        config.set_auth_mode(requested)
    elif "auth_enabled" in incoming:
        config.set_auth_mode(
            AUTH_MODE_PASSWORD if incoming["auth_enabled"] else AUTH_MODE_NONE)


def validate_auth_config(config: Config) -> None:
    """
    Reject auth settings that would lock everyone out.

    Raises:
        ConfigurationError: If the selected mode cannot actually authenticate anyone.
    """
    if config.auth_mode == AUTH_MODE_PASSWORD and not config.auth_password_hash:
        raise ConfigurationError(
            "Set a username and password before enabling password protection")
    if config.auth_mode == AUTH_MODE_OIDC and not config.oidc_is_configured():
        raise ConfigurationError(
            "Issuer, client ID and client secret are required before enabling single sign-on")


def socket_login_required(f):
    """
    Decorator to require authentication for Socket.IO events.

    The connect handler already refuses unauthenticated clients; this re-checks on
    every event so a session that expires mid-connection stops being honoured.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = globals.config if hasattr(
            globals, 'config') and globals.config else None

        if not is_session_authenticated(config):
            logger.warning(
                f"Rejected unauthenticated Socket.IO event '{f.__name__}'")
            disconnect()
            return None

        return f(*args, **kwargs)

    return decorated_function


def login_required(f):
    """Decorator to require authentication for routes."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get config from globals
        config = globals.config if hasattr(
            globals, 'config') and globals.config else None

        # If auth not enabled, allow access
        if not config or not config.auth_required:
            return f(*args, **kwargs)

        if not is_session_authenticated(config):
            session.clear()
            if oidc_available(config):
                return redirect(url_for('oidc_login', next=request.path))
            return redirect(url_for('login'))

        return f(*args, **kwargs)

    return decorated_function


def setup_routes(web_app, config: Config):
    """
    Set up Flask HTTP routes.

    Args:
        web_app: Flask application instance
        config: Configuration object
    """

    def render_login(error: Optional[str] = None, status: int = 200):
        """Render the login page with the options available for the current mode."""
        oidc_ready = oidc_available(config)
        password_allowed = (config.auth_mode == AUTH_MODE_PASSWORD
                            or (config.auth_mode == AUTH_MODE_OIDC
                                and config.oidc_allow_password_fallback
                                and bool(config.auth_password_hash)))
        return render_template(
            "login.html",
            error=error,
            oidc_enabled=oidc_ready,
            oidc_provider=config.oidc_provider_name or "SSO",
            show_password_form=password_allowed and (
                config.auth_mode == AUTH_MODE_PASSWORD
                or request.args.get('local') == '1'),
            password_fallback_available=password_allowed and config.auth_mode == AUTH_MODE_OIDC
        ), status

    @web_app.route("/login", methods=["GET", "POST"])
    def login():
        """Handle local password login and render the sign-in page."""
        # If auth not enabled, redirect to home
        if not config.auth_required:
            return redirect(url_for('home'))

        # Already logged in
        if is_session_authenticated(config):
            return redirect(url_for('home'))

        password_allowed = (config.auth_mode == AUTH_MODE_PASSWORD
                            or (config.oidc_allow_password_fallback
                                and bool(config.auth_password_hash)))

        # In OIDC mode the login page is just a pass-through, unless the user
        # explicitly asked for the break-glass local form
        if (request.method == "GET" and oidc_available(config)
                and request.args.get('local') != '1'):
            return redirect(url_for('oidc_login', next=safe_next(request.args.get('next'))))

        if request.method == "POST":
            if not password_allowed:
                logger.warning(
                    "Rejected password login attempt while password auth is disabled")
                return render_login("Password sign-in is disabled", 403)

            username = request.form.get('username', '')
            password = request.form.get('password', '')
            remember = request.form.get('remember') == 'on'

            # Authenticate
            if AuthenticationService.authenticate(username, password, config.auth_username, config.auth_password_hash):
                session.clear()
                session['authenticated'] = True
                session['auth_via'] = 'password'
                session['user'] = username
                session.permanent = remember  # Set to 7 days if remember is checked
                return redirect(url_for('home'))

            logger.warning(f"Failed password login for user '{username}'")
            return render_login("Invalid username or password", 401)

        if config.auth_mode == AUTH_MODE_OIDC and not oidc_available(config):
            return render_login(
                "Single sign-on is enabled but not fully configured", 200)

        return render_login()

    @web_app.route("/auth/oidc/login")
    def oidc_login():
        """Start the OIDC authorization code flow."""
        if not config.auth_required:
            return redirect(url_for('home'))

        if not oidc_available(config):
            return render_login("Single sign-on is enabled but not fully configured", 200)

        session['oidc_next'] = safe_next(request.args.get('next'))

        try:
            return globals.oidc_service.authorize_redirect(oidc_redirect_uri(config))
        except Exception as e:
            logger.error(f"Could not start OIDC login: {e}", exc_info=True)
            return render_login(f"Could not reach the sign-in provider: {e}", 502)

    @web_app.route("/auth/oidc/callback")
    def oidc_callback():
        """Complete the OIDC login and establish the session."""
        if not oidc_available(config):
            return render_login("Single sign-on is not enabled", 400)

        service = globals.oidc_service

        try:
            claims = service.handle_callback()
        except Exception as e:
            logger.error(f"OIDC callback failed: {e}", exc_info=True)
            return render_login(f"Sign-in failed: {e}", 401)

        username = service.get_username(claims)
        groups = service.get_groups(claims)

        if not service.is_authorized(claims):
            logger.warning(
                f"OIDC sign-in denied for '{username}': groups {groups} do not match "
                f"allowed groups {config.oidc_allowed_groups}")
            return render_login(
                "Your account is not a member of a group allowed to use this app", 403)

        target = safe_next(session.get('oidc_next'))
        session.clear()
        session['authenticated'] = True
        session['auth_via'] = 'oidc'
        session['user'] = username
        session['groups'] = groups
        session['id_token'] = claims.get('_id_token', '')
        if claims.get('exp'):
            session['idp_exp'] = claims['exp']
        session.permanent = True

        logger.info(f"OIDC sign-in for '{username}'")
        return redirect(target)

    @web_app.route("/logout")
    def logout():
        """Handle user logout, including RP-initiated logout at the provider."""
        auth_via = session.get('auth_via')
        id_token = session.get('id_token', '')
        session.clear()

        if auth_via == 'oidc' and globals.oidc_service is not None:
            try:
                provider_logout = globals.oidc_service.logout_url(
                    id_token, f"{external_base_url(config)}{url_for('login')}")
            except Exception as e:
                logger.debug(f"Could not build provider logout URL: {e}")
                provider_logout = None
            if provider_logout:
                return redirect(provider_logout)

        return redirect(url_for('login'))

    @web_app.route("/")
    @login_required
    def home():
        """Render the main web interface."""
        return render_template("web_interface.html", config=config)

    @web_app.route('/downloads/<path:filename>')
    @login_required
    def download_file(filename):
        """Serve files from the downloads directory."""
        downloads_path = os.path.join(
            UtilityService.get_exe_dir(), 'downloads')
        return send_from_directory(downloads_path, filename, as_attachment=True)

    @web_app.route('/uploads/<path:filename>')
    @login_required
    def uploaded_file(filename):
        """Serve files from the uploads directory."""
        uploads_path = os.path.join(UtilityService.get_exe_dir(), 'uploads')
        return send_from_directory(uploads_path, filename)


def setup_socket_handlers(
        config: Config,
        filename_pattern: re.Pattern
):
    """
    Set up Socket.IO event handlers.

    Args:
        config: Configuration object
        filename_pattern: Regex pattern for validating filenames

    Note: This function imports from artwork_uploader to avoid circular dependencies.
          It uses globals.web_socket which must be initialized before calling.
          Scheduled jobs are now managed through globals.scheduler_service.
    """
    # Import functions from artwork_uploader (to avoid circular imports at module level)
    from artwork_uploader import (
        process_scrape_url_from_web,
        run_bulk_import_scrape_in_thread,
        save_bulk_import_file,
        load_bulk_import_file,
        rename_bulk_import_file,
        delete_bulk_import_file,
        add_file_to_schedule_thread,
        update_scheduled_jobs,
        current_version,
        check_image_orientation,
        sort_key
    )

    # Temporary storage for chunked uploads, isolated by Socket.IO connection.
    upload_chunks = {}
    upload_chunks_lock = Semaphore()

    def cleanup_upload_file(upload, file_name):
        """Close and remove one upload's temporary file."""
        try:
            upload["temp_file"].close()
        except Exception as close_err:
            logger.warning(
                f"Error closing temp file for {file_name}: {close_err}",
                exc_info=True,
            )

        temp_path = upload.get("temp_path")
        if temp_path:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
            except OSError as remove_err:
                logger.warning(
                    f"Error removing temp file {temp_path} for {file_name}: {remove_err}",
                    exc_info=True,
                )

    def cleanup_upload(upload_key):
        """Discard one pending upload. Caller must hold upload_chunks_lock."""
        upload = upload_chunks.pop(upload_key, None)
        if upload is not None:
            cleanup_upload_file(upload, upload_key[1])
        return upload

    @globals.web_socket.on("connect")
    def handle_connect(auth=None):
        """
        Refuse Socket.IO connections from unauthenticated clients.

        Every meaningful action in this app is a Socket.IO event, so without this
        check the HTTP login only protects the page shell, not the API.
        """
        if not is_session_authenticated(config):
            logger.warning("Refused unauthenticated Socket.IO connection")
            raise ConnectionRefusedError("Authentication required")

    # Cleanup must run even if authentication expired before disconnect.
    @globals.web_socket.on("disconnect")
    def handle_disconnect(reason=None):
        """Discard uploads owned by the disconnecting Socket.IO client."""
        sid = request.sid
        with upload_chunks_lock:
            for upload_key in [key for key in upload_chunks if key[0] == sid]:
                cleanup_upload(upload_key)

    @globals.web_socket.on("debug_mode")
    @socket_login_required
    def debug_mode(data):
        """Report on debug mode status and toggle debug mode."""
        instance = Instance(data.get("instance_id"), "web")
        if data.get("action") == "get":
            logger.info(f"Reporting current debug mode: {'On' if globals.debug else 'Off'}")
            debug_me(f"Reporting current debug mode: {'On' if globals.debug else 'Off'}", "debug_mode")
            notify_web(instance, "debug_mode", {"debug": globals.debug})
        elif data.get("action") == "toggle":
            logger.info(f"Turning debug mode {'Off' if globals.debug else 'On'}")
            debug_me(f"Turning debug mode {'Off' if globals.debug else 'On'}", "debug_mode")
            notify_web(instance, "debug_mode", {"debug": not globals.debug})
            if globals.debug:
                globals.debug = False
            else:
                globals.debug = True

    @globals.web_socket.on("update_app")
    @socket_login_required
    def update_app(data):
        """Pull updates from GitHub and restart the app."""
        instance = Instance(data.get("instance_id"), "web")

        try:
            update_status(
                Instance(broadcast=True),
                "Updating to the latest version, please wait...",
                "info",
                sticky=True,
                spinner=True
            )

            # Detect platform
            python_cmd = "python3" if sys.platform == "darwin" else "python"

            # Pull latest changes
            subprocess.run(["git", "pull"], check=True)

            # Install dependencies
            subprocess.run([python_cmd, "-m", "pip", "install",
                           "-r", "requirements.txt"], check=True)

            # Trigger the front-end to restart
            update_status(
                Instance(broadcast=True),
                "Update complete, restarting the app...",
                "success",
                sticky=True,
                spinner=True
            )
            notify_web(Instance(broadcast=True), "backend_restarting", {})

            # Restart the app
            os.execlp(python_cmd, python_cmd, "artwork_uploader.py")

        except Exception as e:
            update_status(Instance(broadcast=True),
                          "Update failed, restarting the app...", "danger")
            notify_web(instance, "update_failed", {"error": str(e)})

    @globals.web_socket.on("start_scrape")
    @socket_login_required
    def handle_scrape_from_web(data):
        """Handle scraping request from web UI."""
        instance = Instance(data.get("instance_id"), "web")
        url = data.get("url").lower()
        options = data.get("options")
        filters = data.get("filters")
        year = data.get("year")

        if url:
            if year:
                url = url + f" --year {year}"
            if options:
                url = url + " " + " ".join(options)
            if filters and len(filters) < 6:
                url = url + " --filters " + " ".join(filters)
            notify_web(
                instance,
                "element_disable",
                {"element": ["scrape_url", "scrape_button",
                             "bulk_button"], "mode": True}
            )
            process_scrape_url_from_web(instance, url)

    @globals.web_socket.on("start_bulk_import")
    @socket_login_required
    def handle_bulk_import_from_web(data):
        """Handle bulk import request from web UI."""
        instance = Instance(data.get("instance_id"), "web")
        bulk_list = data.get("bulk_list").lower()
        filename = data.get("filename", "bulk_import.txt")
        scheduled = data.get("scheduled", False)
        run_bulk_import_scrape_in_thread(instance, bulk_list, filename, scheduled=scheduled)

    @globals.web_socket.on("save_bulk_import")
    @socket_login_required
    def handle_bulk_import(data):
        """Save bulk import file from web UI."""
        instance = Instance(data.get("instance_id"), "web")
        content = data.get("content")
        filename = data.get("filename")
        now_load = data.get("now_load")
        if content:
            save_bulk_import_file(instance, content, filename, now_load)

    @globals.web_socket.on("load_config")
    @socket_login_required
    def load_config_web(data):
        """Load configuration from web UI."""
        instance = Instance(data.get("instance_id"), "web")
        config.load()
        update_scheduled_jobs()
        globals.oidc_service.reconfigure(config)
        notify_web(instance, "load_config", {"config": config.to_public_dict()})

    @globals.web_socket.on("load_bulk_filelist")
    @socket_login_required
    def load_bulk_filelist(data):
        """Load list of bulk import files."""
        instance = Instance(data.get("instance_id"), "web")
        bulk_files = None
        try:
            folder_path = globals.bulk_file_service.get_bulk_imports_directory()
            bulk_files = [f.name for f in folder_path.iterdir() if f.is_file()]
        except (FileNotFoundError, PermissionError) as e:
            debug_me(
                f"Error loading bulk file list: {e}", "load_bulk_filelist")
        notify_web(instance, "load_bulk_filelist", {"bulk_files": bulk_files})

    @globals.web_socket.on("load_bulk_import")
    @socket_login_required
    def load_bulk_import(data):
        """Load a specific bulk import file."""
        instance = Instance(data.get("instance_id"), "web")
        load_bulk_import_file(instance, data.get("filename"))

    @globals.web_socket.on("rename_bulk_file")
    @socket_login_required
    def rename_bulk_file(data):
        """Rename a bulk import file."""
        instance = Instance(data.get("instance_id"), "web")
        old_name = data.get("old_filename")
        new_name = data.get("new_filename")
        time = globals.scheduler_service.run_times_by_file.get(old_name)
        rename_bulk_import_file(instance, old_name, new_name)
        if time:
            delete_task_from_scheduler({"instance_id": instance.id, "file": old_name})
            add_tasks_to_scheduler({"instance_id": instance.id, "file": new_name, "time": time})

    @globals.web_socket.on("delete_bulk_file")
    @socket_login_required
    def delete_bulk_file(data):
        """Delete a bulk import file."""
        instance = Instance(data.get("instance_id"), "web")
        delete_bulk_import_file(instance, data.get("filename"))

    @globals.web_socket.on("create_bulk_file")
    @socket_login_required
    def create_bulk_file(data):
        """Create a new bulk import file."""
        instance = Instance(data.get("instance_id"), "web")

        from datetime import datetime
        timestamp = datetime.now().strftime("%d %b %Y %H:%M:%S")

        # Generate a unique filename
        base_name = "bulk_import_new"
        extension = ".txt"
        counter = 1
        filename = f"{base_name}{extension}"

        # Check if file exists and increment counter
        while globals.bulk_file_service.file_exists(filename):
            filename = f"{base_name}_{counter}{extension}"
            counter += 1

        # Create file with comment header
        content = f"# Bulk import file created {timestamp}\n"

        try:
            globals.bulk_file_service.write_file(content, filename)
            update_log(instance, f"Created new bulk file: {filename}")
            notify_web(instance, "create_bulk_file", {
                       "created": True, "filename": filename})
            # Reload the file list
            folder_path = globals.bulk_file_service.get_bulk_imports_directory()
            bulk_files = [f.name for f in folder_path.iterdir() if f.is_file()]
            notify_web(instance, "load_bulk_filelist",
                       {"bulk_files": bulk_files})
        except Exception as e:
            update_status(instance, f"Error creating file: {str(e)}", "danger")
            notify_web(instance, "create_bulk_file", {
                       "created": False, "error": str(e)})

    @globals.web_socket.on("display_message")
    @socket_login_required
    def display_message(data):
        """Log a debug message from the frontend."""
        instance = Instance(data.get("instance_id"), "web")
        debug_me(f"Received message from frontend: '{data.get('message')}' - Log level: '{data.get('level')}'", "display_message")
        if data.get("level") == "debug":
            debug_me(data.get("message"), data.get("title", "web_message"))
        elif data.get("level") == "log":
            update_log(instance, data.get("message"))

    @globals.web_socket.on("test_plex_connect")
    @socket_login_required
    def test_plex_connect(data):
        """Test connectivity to Plex server."""

        def fail(status, log):
            update_log(instance, log)
            update_status(instance, status, "danger", False, False, "x-circle")
            notify_web(instance, "test_plex_connect", {"success": False, "status": status, "log": log})
            notify_web(instance, "element_disable", {"element": ["test_plex_btn"], "mode": False})

        instance = Instance(data.get("instance_id"), "web")
        instance.broadcast = True
        update_status(instance, "Testing connection to Plex server", "info", False, True)

        # Disable the test button to prevent multiple clicks
        notify_web(instance, "element_disable", {"element": ["test_plex_btn"], "mode": True})

        # Capture Plex settings form parameters
        url = data.get("url", "")
        debug_me(f"Obtained Plex URL: {url}", "test_plex_connect")
        token = data.get("token", "")
        # The UI never receives the stored token, so an untouched field means "use it"
        if not token or token == SECRET_PLACEHOLDER:
            token = config.token
        debug_me(f"Obtained Plex token: {'*' * min(len(token), 8) if token else '(not set)'}", "test_plex_connect")
        tv_libs = data.get("tv_libs", "")
        debug_me(f"Obtained {len(tv_libs)} TV libraries: {tv_libs}", "test_plex_connect")
        movie_libs = data.get("movie_libs", "")
        debug_me(f"Obtained {len(movie_libs)} Movie libraries: {movie_libs}", "test_plex_connect")

        # Check for a valid Plex server URL and token
        url_pattern = r"^https?:\/\/((([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})|(\d{1,3}(\.\d{1,3}){3}))(:\d+)?(\/.*)?$"
        token_pattern = r"^[A-Za-z0-9_-]{20,}$"
        if not re.fullmatch(url_pattern, url):
            fail("Invalid Plex URL", f"Invalid Plex URL: {url}")
            return
        if not re.fullmatch(token_pattern, token):
            fail("Invalid Plex token", f"Invalid Plex token: {token}")
            return

        # Check connectivity to server
        try:
            plex_server = PlexServer(url, token, timeout=5)
        except Exception as e:
            if "NewConnectionError" in str(e):
                log = "Error connecting to Plex - Connection refused"
            elif "ConnectTimeoutError" in str(e) or "timed out" in str(e):
                log = "Error connecting to Plex - Timed out"
            elif "unauthorized" in str(e):
                log = "Error connecting to Plex - Invalid token"
            elif "NameResolutionError" in str(e):
                log = "Error connecting to Plex - Cannot resolve server name"
            elif "SSLError" in str(e):
                log = "Error connecting to Plex - SSL certificate validation failed"
            else:
                log = f"Unknown error connecting to Plex: {str(e)}"
            fail("Error connecting to Plex, check log for details", log)
            return

        # Check that the provided libraries exist in the server
        all_libs = list(tv_libs) + list(movie_libs)
        invalid_libs = []
        for lib in all_libs:
            try:
                plex_server.library.section(lib)
            except Exception:
                invalid_libs.append(lib)
        if invalid_libs:
            fail("Some libraries not found, check log for details.",
                 f"The following libraries could not be found: {', '.join(invalid_libs)}")
            return

        update_log(instance, "Successfully connected to Plex server")
        update_status(instance, "Successfully connected to Plex server", "success", False, False, "check2-circle")
        notify_web(instance, "element_disable", {"element": ["test_plex_btn"], "mode": False})
        notify_web(instance, "test_plex_connect", {"success": True})

    @globals.web_socket.on("test_notifications")
    @socket_login_required
    def test_notifications(data):
        """Send a test notification."""
        instance = Instance(data.get("instance_id"), "web")
        instance.broadcast = True
        # Disable the test button to prevent multiple clicks
        notify_web(instance, "element_disable", {"element": ["test_notif_btn"], "mode": True})
        urls = data.get("urls", [])
        notification_title = "Test Notification from Artwork Uploader"
        notification_message = "This is a test notification to verify your notification settings are working correctly."
        test_notification = NotifyService()
        success = True
        failed = 0
        for idx, url in enumerate(urls):
            test_notification.add_url(url)
            debug_me(f"Sending test notification to URL #{idx + 1}", "test_notifications")
            url_success = test_notification.send_notification(notification_title, notification_message)
            success = success and url_success
            if url_success:
                debug_me(f"Test notification sent successfully to URL #{idx + 1}", "test_notifications")
                update_log(instance, f"Test notification sent successfully to URL #{idx + 1}")
                if len(urls) == 1:
                    update_status(instance, "Test notification sent successfully", "success", False, False, "check2-circle")
            else:
                failed += 1
                debug_me(f"Test notification failed to send to URL #{idx + 1}.", "test_notifications")
                update_log(instance, f"Test notification failed to send to URL #{idx + 1}")
                if len(urls) == 1:
                    update_status(instance, "Test notification failed to send", "danger", False, False, "x-circle")
            test_notification.clear_urls()
        if len(urls) > 1:
            if success and len(urls) > 1:
                debug_me("All test notifications sent successfully", "test_notifications")
                update_log(instance, "All test notifications sent successfully")
                update_status(instance, "All test notifications sent successfully", "success", False, False, "check2-circle")
            elif failed < len(urls):
                debug_me("Some test notifications failed to send", "test_notifications")
                update_log(instance, "Some test notifications failed to send")
                update_status(instance, "Some test notifications failed to send. Check logs for details.", "warning", False, False, "exclamation-triangle")
            else:
                debug_me("All test notifications failed to send", "test_notifications")
                update_log(instance, "All test notifications failed to send")
                update_status(instance, "All test notifications failed to send", "danger", False, False, "x-circle")
        notify_web(instance, "element_disable", {"element": ["test_notif_btn"], "mode": False})

    @globals.web_socket.on("set_password")
    @socket_login_required
    def set_password_web(data):
        """Set a new password for authentication."""
        instance = Instance(data.get("instance_id"), "web")

        try:
            username = data.get("username", "")
            password = data.get("password", "")

            if not username or not password:
                notify_web(instance, "set_password", {
                           "success": False, "error": "Username and password required"})
                return

            # Hash the password
            password_hash = AuthenticationService.hash_password(password)

            # Update config
            config.auth_username = username
            config.auth_password_hash = password_hash
            # Setting a password must not downgrade an OIDC deployment
            if config.auth_mode == AUTH_MODE_NONE:
                config.set_auth_mode(AUTH_MODE_PASSWORD)
            config.save()

            # Also update globals
            globals.config = config

            notify_web(instance, "set_password", {"success": True})
            update_log(
                instance, f"Authentication enabled for user '{username}'")
        except Exception as e:
            notify_web(instance, "set_password", {
                       "success": False, "error": str(e)})

    @globals.web_socket.on("save_config")
    @socket_login_required
    def save_config_web(data):
        """Save configuration from web UI."""
        instance = Instance(data.get("instance_id"), "web")

        incoming = data.get("config") or {}

        try:
            apply_config_updates(config, incoming)
            apply_auth_mode(config, incoming)
            validate_auth_config(config)
            config.save()

            # Also update globals
            globals.config = config

            # Rebuild the Radarr/Sonarr clients in case their URL/API key changed
            globals.arr.reconfigure(config)

            # Pick up issuer/client changes without a restart
            globals.oidc_service.reconfigure(config)
        except Exception as config_error:
            logger.error(
                f"Could not save configuration: {config_error}", exc_info=True)
            # Discard the partially applied in-memory changes
            config.load()
            globals.oidc_service.reconfigure(config)
            notify_web(instance, "save_config", {"saved": False})
            update_status(instance, str(config_error), color="danger")
            return

        # Reconnect to Plex because the Plex server or token might have changed.
        # An unreachable Plex server does not undo the settings we just stored.
        update_log(
            instance, "Saving updated configuration and reconnecting to Plex")
        try:
            globals.plex.reconnect(config)
        except Exception as plex_error:
            logger.warning(
                f"Configuration saved but reconnecting to Plex failed: {plex_error}")
            update_status(
                instance, f"Configuration saved, but connecting to Plex failed: {plex_error}",
                color="warning")

        notify_web(instance, "save_config", {
                   "saved": True, "config": config.to_public_dict()})

    @globals.web_socket.on("delete_schedule")
    @socket_login_required
    def delete_task_from_scheduler(data):
        """Delete a scheduled task."""
        if data.get("instance_id"):
            instance = Instance(data.get("instance_id"), "web")
            schedule_file = data.get("file")

            if schedule_file:
                # Get job ID from scheduler service
                job_id = globals.scheduler_service.get_job_id_by_file(
                    schedule_file)

                if job_id:
                    # Remove from scheduler service
                    globals.scheduler_service.remove_schedule(job_id)

                    # Make sure it's also removed from the config file
                    config.load()
                    config.schedules = [
                        each_schedule
                        for each_schedule in config.schedules
                        if each_schedule["file"] != schedule_file
                    ]
                    config.save()

                    # And update the front-end
                    notify_web(
                        instance,
                        "delete_schedule",
                        {"file": schedule_file,
                            "job_reference": job_id, "deleted": True}
                    )
                else:
                    notify_web(instance, "delete_schedule", {
                               "deleted": False, "job_id": job_id})

    @globals.web_socket.on("add_schedule")
    @socket_login_required
    def add_tasks_to_scheduler(data):
        """Add a new scheduled task."""
        try:
            # Schedule bulk import task
            if data.get("instance_id"):
                instance = Instance(data.get("instance_id"), "web")
                schedule_file = data.get("file")
                schedule_time = data.get("time")

                # Make sure the schedule is saved as part of the config
                config.load()
                update_or_add_schedule(schedule_file, schedule_time)
                config.save()

                try:
                    # Create the callback for this schedule
                    def schedule_callback(filename=schedule_file):
                        add_file_to_schedule_thread(instance, filename)

                    # Add to scheduler service
                    job_id = globals.scheduler_service.add_schedule(
                        schedule_file,
                        schedule_time,
                        schedule_callback
                    )

                    notify_web(
                        instance,
                        "add_schedule",
                        {
                            "added": True,
                            "file": schedule_file,
                            "time": schedule_time,
                            "jobReference": job_id
                        }
                    )
                except Exception as e:
                    debug_me(
                        f"Error adding schedule: {e}", "add_tasks_to_scheduler")
                    raise

                # Start the scheduler if it's not already started
                globals.scheduler_service.start()

        except Exception as e:
            if globals.debug:
                debug_me(
                    f"Error in scheduler setup: {e}", "add_tasks_to_scheduler")
                raise

    def update_or_add_schedule(file_name, new_time):
        """Helper function to update or add a schedule in config."""
        for each_schedule in config.schedules:
            if each_schedule["file"] == file_name:
                # Update existing schedule
                each_schedule["time"] = new_time
                return

        # Add new schedule if not found
        config.schedules.append({"file": file_name, "time": new_time})

    @globals.web_socket.on("upload_artwork_chunk")
    @socket_login_required
    def handle_upload_chunk(data):
        """Handle chunked file upload - writes directly to temp file for memory efficiency."""
        instance = Instance(data.get("instance_id"), "web")

        # basename: fileName is client-supplied and later used as a path component
        # in save_uploaded_file, so a ../ sequence would escape the temp directory.
        file_name = os.path.basename(data["fileName"])
        upload_key = (request.sid, file_name)
        chunk_index = data.get("chunkIndex")

        try:
            chunk_data = data["chunkData"]
            chunk_index = int(data["chunkIndex"])
            total_chunks = int(data["totalChunks"])

            with upload_chunks_lock:
                # A new first chunk replaces any abandoned upload with the same client/name.
                if chunk_index == 0:
                    cleanup_upload(upload_key)

                if upload_key not in upload_chunks:
                    temp_file = tempfile.NamedTemporaryFile(
                        mode='wb', delete=False, suffix='.upload')
                    upload_chunks[upload_key] = {
                        "temp_file": temp_file,
                        "temp_path": temp_file.name,
                        "chunks_received": 0,
                        "total_chunks": total_chunks,
                        "instance": instance
                    }

                decoded_chunk = base64.b64decode(chunk_data)
                upload_chunks[upload_key]["temp_file"].write(decoded_chunk)
                upload_chunks[upload_key]["chunks_received"] += 1
        except Exception as e:
            logger.error(
                f"Error decoding/writing chunk {chunk_index}: {e}", exc_info=True)
            with upload_chunks_lock:
                cleanup_upload(upload_key)

    @globals.web_socket.on("upload_complete")
    @socket_login_required
    def handle_upload_complete(data):
        """Finalize the upload once all chunks are received."""
        file_name = os.path.basename(data.get("fileName") or "")
        upload_key = (request.sid, file_name)
        filters = data.get("filters")
        plex_year = data.get("plex_year")
        plex_title = data.get("plex_title")
        options = data.get("options")
        debug_me(
            f"Obtained options from web form: {options}", "handle_upload_complete")

        instance = Instance(data.get("instance_id"), "web")

        with upload_chunks_lock:
            upload = upload_chunks.get(upload_key)
            chunks_received = upload["chunks_received"] if upload else 0
            expected_chunks = upload["total_chunks"] if upload else 0
            if not upload or chunks_received != expected_chunks:
                cleanup_upload(upload_key)
                upload = None
            else:
                upload_chunks.pop(upload_key)

        if upload is None:
            debug_me(
                f'Upload complete event received for {file_name}, but with '
                f'{chunks_received} of {expected_chunks}, some chunks are missing.',
                "handle_upload_complete"
            )
            return

        debug_me(
            f"Upload complete for {file_name}, processing file...", "handle_upload_complete")
        try:
            upload["temp_file"].close()
            save_uploaded_file(
                instance,
                file_name,
                options,
                filters,
                plex_title,
                plex_year,
                upload["temp_path"],
                filename_pattern,
                check_image_orientation,
                sort_key
            )
        finally:
            cleanup_upload_file(upload, file_name)


def save_uploaded_file(
        instance: Instance,
        file_name: str,
        options: list,
        filters: list,
        plex_title: str,
        plex_year: int,
        temp_upload_path: str,
        filename_pattern: re.Pattern,
        check_image_orientation_func,
        sort_key_func
):
    """
    Process the uploaded file from temp storage.

    Args:
        instance: Instance object for web notifications
        file_name: Name of the uploaded file
        options: List of options to apply
        filters: List of filters to apply
        plex_title: Optional title override
        plex_year: Optional year override
        temp_upload_path: Path to the completed temporary upload
        filename_pattern: Regex pattern for validating filenames
        check_image_orientation_func: Function to check image orientation
        sort_key_func: Function to generate sort keys
    """
    from artwork_uploader import process_uploaded_artwork

    debug_me(
        f"Processing uploaded file {file_name} from temp path: {temp_upload_path}", "save_uploaded_file")

    # Move to a proper temp location with correct filename for processing
    temp_zip_folder = tempfile.mkdtemp()
    temp_zip_path = os.path.join(temp_zip_folder, file_name)

    try:
        shutil.move(temp_upload_path, temp_zip_path)
        debug_me(f"Moved uploaded file to: {temp_zip_path}", "save_uploaded_file")

        extracted_files, skipped, zip_title, zip_author, zip_source = extract_and_list_zip(
            instance,
            temp_zip_path,
            filename_pattern,
            filters,
            plex_title,
            plex_year,
            check_image_orientation_func,
            sort_key_func
        )
    finally:
        shutil.rmtree(temp_zip_folder, ignore_errors=True)
        debug_me(
            f"Deleted temporary ZIP file: {temp_zip_path}", "save_uploaded_file")

    process_uploaded_artwork(instance, extracted_files, skipped, zip_title, zip_author, zip_source,
                             options, filters, plex_title, plex_year)

    notify_web(instance, "upload_complete", {"files": extracted_files})
    update_status(instance, "Finished processing uploaded file.",
                  color="success")


def _detect_zip_source_from_filename(zip_path: str) -> tuple[str, Optional[str], Optional[str]]:
    """
    Detect a ZIP's source from its filename. ThePosterDB ZIPs encode
    "<title> set by <author> - ..."; anything else is assumed to be MediUX
    (confirmed or overridden later if a source.txt entry is found inside).
    """
    pattern = r"^(?P<title>.+?)\s+set by\s+(?P<author>.+?)\s*-"
    match = re.search(pattern, os.path.basename(zip_path), re.IGNORECASE)
    if not match:
        return SOURCE_MEDIUX, None, None

    title = match.group("title").strip()
    author = match.group("author").strip()
    debug_me(f"Detected ThePosterDB source", "extract_and_list_zip")
    debug_me(f"Detected ZIP title: {title}", "extract_and_list_zip")
    debug_me(f"Detected ZIP author: {author}", "extract_and_list_zip")
    return SOURCE_THEPOSTERDB, title, author


def _extract_mediux_source_metadata(
        zip_ref: zipfile.ZipFile, zip_info: zipfile.ZipInfo, extract_dir: str
) -> tuple[Optional[str], Optional[str]]:
    """Extract and parse a MediUX ZIP's source.txt for the set title/author, then remove it."""
    debug_me("Detected Mediux source", "extract_and_list_zip")
    source_txt_path = os.path.join(extract_dir, SOURCE_TXT)
    with zip_ref.open(zip_info) as source, open(source_txt_path, "wb") as target:
        target.write(source.read())

    zip_title = None
    zip_author = None
    with open(source_txt_path, "r", encoding="utf-8") as source_file:
        for line in source_file:
            if line.startswith("Title:"):
                zip_title = line.split("Title:")[1].strip()
                debug_me(f"Detected ZIP title: {zip_title}", "extract_and_list_zip")
            if line.startswith("Author:"):
                zip_author = line.split("Author:")[1].strip()
                debug_me(f"Detected ZIP author: {zip_author}", "extract_and_list_zip")
                break
    os.remove(source_txt_path)
    return zip_title, zip_author


def _resolve_plex_title(
        original_title: str, lookup_year: Optional[int]
) -> tuple[Optional[str], Optional[int], Optional[str], Optional[int], str]:
    """
    Look up a ZIP-derived title in Plex, trying progressively looser matches:
    the literal title, colon/hyphen restoration, accent-folding, and (if a
    year is known) progressively shorter titles.

    Returns (media_type, tmdb_id, found_title, found_year, resolved_title),
    where resolved_title is the last candidate string actually tried
    (i.e. the one that matched, once media_type is not None).
    """
    media_type, tmdb_id, title, year = globals.plex.movie_or_show(original_title, lookup_year)
    candidate_title = original_title

    if media_type is None:
        # ZIP filenames replace colons with underscores or hyphens, and drop apostrophes/ellipses
        candidate_title = re.sub(r'_(?=\s)', ':', original_title)
        candidate_title = re.sub(r'\s-\s', ': ', candidate_title).replace('...', '').strip()
        media_type, tmdb_id, title, year = globals.plex.movie_or_show(candidate_title, lookup_year)

    if media_type is None:
        # ZIP filenames may strip accented chars (e.g. "Pokémon" → "Pokemon" or "Pokmon")
        # ASCII-fold the title so Plex can match against the accented original
        folded = ''.join(
            c for c in unicodedata.normalize('NFKD', candidate_title)
            if not unicodedata.combining(c)
        )
        if folded != candidate_title:
            candidate_title = folded
            media_type, tmdb_id, title, year = globals.plex.movie_or_show(candidate_title, lookup_year)

    if media_type is None and lookup_year is not None:
        # Fallback: try progressively shorter titles for any remaining mismatch
        # (e.g. stripped subtitles or missing apostrophes: "Worlds End" vs "World's End")
        words = re.sub(r'_(?=\s)|\s-\s', ' ', original_title).split()
        max_strip = globals.config.zip_title_strip_words if globals.config else 3
        min_words = max(2, len(words) - max_strip)
        for end in range(len(words) - 1, min_words - 1, -1):
            short_title = ' '.join(words[:end])
            media_type, tmdb_id, title, year = globals.plex.movie_or_show(short_title, lookup_year)
            if media_type is not None:
                candidate_title = short_title
                break

    return media_type, tmdb_id, title, year, candidate_title


def _reclassify_artwork_type(artwork: dict, check_image_orientation_func) -> None:
    """Refine artwork['type']/['season']/['media'] using image orientation, in place."""
    if artwork['media'] == "TV Show":
        if artwork['type'] == FilterType.SQUARE_ART.value:
            # Square art is identified by its filename OST suffix; don't let the
            # orientation checks below reclassify it as a cover or backdrop
            artwork['season'] = SEASON_SQUARE_ART
        else:
            if artwork['season'] is None:
                artwork['season'] = "Cover"
                artwork['type'] = FilterType.SHOW_COVER.value
            if artwork['season'] == "Cover" and check_image_orientation_func(artwork["path"]) == "landscape":
                artwork['season'] = "Backdrop"
                artwork['type'] = FilterType.BACKGROUND.value
    if artwork['media'] == "Movie" and artwork['type'] != FilterType.SQUARE_ART.value:
        if check_image_orientation_func(artwork["path"]) == "landscape":
            artwork['type'] = FilterType.BACKGROUND.value
        else:
            artwork['type'] = FilterType.MOVIE_POSTER.value
    if artwork['media'] == "Collection":
        if check_image_orientation_func(artwork["path"]) == "landscape":
            artwork['type'] = FilterType.BACKGROUND.value
    if artwork['media'] == "unavailable":
        if check_image_orientation_func(artwork["path"]) == "landscape":
            artwork['type'] = FilterType.BACKGROUND.value
        if artwork['type'] == FilterType.SEASON_COVER.value:
            artwork['media'] = "TV Show"
        else:
            # Intentional fallback: "poster" doesn't match any FilterType, so this
            # unclassifiable artwork won't pass filters and won't be processed further
            artwork['type'] = "poster"


def _artwork_log_label(artwork: dict) -> str:
    """Build the "'{title} (YYYY)', Season N, Episode M" fragment shared by include/skip log lines."""
    year_suffix = f" ({artwork['year']})" if artwork['year'] is not None else ""
    season_suffix = f", Season {artwork['season']}" if isinstance(artwork['season'], int) else ""
    episode_suffix = f", Episode {artwork['episode']}" if isinstance(artwork['episode'], int) else ""
    return f"'{artwork['title']}{year_suffix}'{season_suffix}{episode_suffix}"


def extract_and_list_zip(
        instance: Instance,
        zip_path: str,
        filename_pattern: re.Pattern,
        filters: list,
        plex_title: str,
        plex_year: int,
        check_image_orientation_func,
        sort_key_func
) -> tuple[list, int, str, str, str]:
    """
    Extract a ZIP file, flatten directories, and return a list of valid image files.

    Args:
        instance: Instance object for web notifications
        zip_path: Path to the ZIP file
        filename_pattern: Regex pattern for validating filenames
        filters: List of artwork type filters to apply
        plex_title: Optional title override
        plex_year: Optional year override
        check_image_orientation_func: Function to check image orientation
        sort_key_func: Function to generate sort keys

    Returns:
        Tuple of (artwork list, skipped count, zip title, zip author, zip source)
    """
    extract_dir = tempfile.mkdtemp()
    file_list = []
    filtered_files = 0

    debug_me(
        f"Extracting ZIP file: {zip_path} to {extract_dir}", "extract_and_list_zip")

    zip_source, zip_title, zip_author = _detect_zip_source_from_filename(zip_path)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Pre-process the file list to determine source and extract valid files
        zip_infos = [
            zip_info for zip_info in zip_ref.infolist()
            if os.path.basename(zip_info.filename)
            and not os.path.basename(zip_info.filename).startswith(".")
            and os.path.basename(zip_info.filename).lower() not in {"ds_store", "__macosx"}
        ]
        total_files_in_zip = len(zip_infos)

        update_status(instance, "Extracting ZIP file...", "info", sticky=True, spinner=True)
        for n, zip_info in enumerate(zip_infos, 1):
            filename = os.path.basename(zip_info.filename)
            debug_me(f"{n} / {total_files_in_zip} - Processing '{filename}'", "extract_and_list_zip")
            percent = (n / total_files_in_zip) * 100 if total_files_in_zip > 0 else 0
            message = f"{n} / {total_files_in_zip} ({round(percent)}%)"
            notify_web(instance, "progress_bar", {"percent": percent, "message": message})

            # Mediux ZIP files contain a source.txt file with metadata
            if filename == SOURCE_TXT:
                zip_source = SOURCE_MEDIUX
                zip_title, zip_author = _extract_mediux_source_metadata(zip_ref, zip_info, extract_dir)

            elif filename_pattern.match(filename):
                full_path = os.path.join(extract_dir, filename)

                with zip_ref.open(zip_info) as source, open(full_path, "wb") as target:
                    target.write(source.read())

                md5 = utils.calculate_file_md5(full_path)

                # Obtain artwork title, year, media type, season, episode and artwork type by parsing the filename
                debug_me(f"Parsing artwork metadata from filename: {filename}", "extract_and_list_zip")
                artwork = parse_title(os.path.splitext(filename)[0])
                # Override title and year if provided
                artwork["title"] = plex_title if plex_title else artwork["title"]
                artwork["year"] = plex_year if plex_year else artwork.get("year")
                # Add additional metadata
                artwork["source"] = zip_source
                artwork["path"] = full_path
                artwork["checksum"] = md5
                artwork["id"] = "Upload"
                artwork["author"] = zip_author
                # Determine media type via Plex lookup if not a collection
                if artwork["media"] != "Collection":
                    original_title = artwork.get('title')
                    if not original_title:
                        artwork["media"] = "unavailable"
                        artwork["tmdb_id"] = None
                    else:
                        lookup_year = int(artwork.get('year')) if artwork.get('year') is not None else None
                        media_type, tmdb_id, title, year, candidate_title = _resolve_plex_title(
                            original_title, lookup_year)
                        artwork["media"] = media_type if media_type else "unavailable"
                        artwork["title"] = title if title and title != candidate_title else candidate_title
                        artwork["tmdb_id"] = tmdb_id
                        if artwork.get('year') is None and year is not None:
                            artwork['year'] = year

                _reclassify_artwork_type(artwork, check_image_orientation_func)

                # Check for filters and exclusions
                label = _artwork_log_label(artwork)
                if not filters or artwork["type"] in filters:
                    debug_me(
                        f"Including {artwork['type'].replace('_', ' ')} for {label}. Type is {artwork['type']}.",
                        "extract_and_list_zip"
                    )
                    file_list.append(artwork)
                else:
                    debug_me(
                        f"Skipping {artwork['type'].replace('_', ' ')} for {label} based on filters. "
                        f"Type is {artwork['type']} and filters are {filters}.", "extract_and_list_zip"
                    )
                    filtered_files += 1

    total_files = len(os.listdir(extract_dir))

    sorted_data = sorted(file_list, key=sort_key_func)

    debug_me(f"Skipped {filtered_files} asset(s) out of {total_files} based on filters.", "extract_and_list_zip")
    debug_me(f"Included {len(sorted_data)} assets:", "extract_and_list_zip")
    if globals.debug:
        pprint.pprint(sorted_data)

    return sorted_data, filtered_files, zip_title, zip_author, zip_source


def resolve_tls_files(tls_cert_file: str, tls_key_file: str) -> dict:
    """
    Validate the TLS file pair and return the SSL kwargs for SocketIO.run.

    Failing fast beats silently serving plain HTTP when the user asked for
    HTTPS - a half-configured or missing file would otherwise only surface as
    browser connection errors.

    Raises:
        ConfigurationError: If only one of the pair is set or a file is missing.
    """
    if not tls_cert_file or not tls_key_file:
        raise ConfigurationError(
            "HTTPS needs both tls_cert_file and tls_key_file (or TLS_CERT_FILE/TLS_KEY_FILE); "
            f"got cert='{tls_cert_file}', key='{tls_key_file}'")
    for label, path in (("certificate", tls_cert_file), ("private key", tls_key_file)):
        if not os.path.isfile(path):
            raise ConfigurationError(
                f"TLS {label} file not found: {path}")
    return {"certfile": tls_cert_file, "keyfile": tls_key_file}


def start_web_server(web_app, web_port: int, debug: bool = False, ip_binding: str = "auto",
                     tls_cert_file: str = "", tls_key_file: str = ""):
    """
    Start the Flask web server with support for IPv4, IPv6, or dual-stack.

    Args:
        web_app: Flask application instance
        web_port: Port to bind to
        debug: Whether to run in debug mode
        ip_binding: IP binding mode - "auto" (dual-stack), "ipv4", or "ipv6"
        tls_cert_file: PEM certificate (chain) file; with tls_key_file serves HTTPS
        tls_key_file: PEM private key file for tls_cert_file
    """
    ssl_kwargs = {}
    scheme = "http"
    if tls_cert_file or tls_key_file:
        ssl_kwargs = resolve_tls_files(tls_cert_file, tls_key_file)
        scheme = "https"
        logger.info(
            f"TLS enabled: serving HTTPS with certificate {tls_cert_file}")

    # Determine the binding address based on ip_binding configuration
    ipv6_available = is_ipv6_available()

    if ip_binding == "auto":
        # Dual-stack: Listen on both IPv4 and IPv6
        if ipv6_available:
            logger.info("Checking dual-stack support...")
            dual_stack_supported = is_dual_stack_supported()
            if dual_stack_supported:
                # "::" enables both IPv4 and IPv6
                binding_host = "::"
                logger.info(
                    f"Starting web server on dual-stack (IPv4 and IPv6) at port {web_port}\n"
                    f"  - IPv4: {scheme}://127.0.0.1:{web_port}\n"
                    f"  - IPv6: {scheme}://[::1]:{web_port}")
            else:
                # Dual-stack not supported, fall back to IPv4 only
                binding_host = "0.0.0.0"
                logger.info(
                    f"Dual-stack not supported on this system, using IPv4 only at port {web_port}\n"
                    f"  - IPv4: {scheme}://127.0.0.1:{web_port}")
        else:
            # IPv6 not available, fall back to IPv4 only
            binding_host = "0.0.0.0"
            logger.info(
                f"IPv6 not available, using IPv4 only at port {web_port}\n"
                f"  - IPv4: {scheme}://127.0.0.1:{web_port}")
    elif ip_binding == "ipv6":
        # Prefer IPv6; may also accept IPv4 connections on dual-stack systems
        if ipv6_available:
            binding_host = "::"
            logger.info(
                f"Starting web server with IPv6 binding at port {web_port}\n"
                f"  - IPv6: {scheme}://[::1]:{web_port}\n"
                "    Note: On some systems this binding may also accept IPv4 connections due to dual-stack behavior.")
        else:
            # IPv6 requested but not available, fall back to IPv4
            binding_host = "0.0.0.0"
            logger.info(
                f"IPv6 requested but not available, falling back to IPv4 at port {web_port}\n"
                f"  - IPv4: {scheme}://127.0.0.1:{web_port}")
    else:
        # IPv4 only (default fallback)
        binding_host = "0.0.0.0"
        logger.info(
            f"Starting web server on IPv4 only at port {web_port}\n"
            f"  - IPv4: {scheme}://127.0.0.1:{web_port}")

    globals.web_socket.run(web_app, host=binding_host,
                           port=web_port, debug=debug, **ssl_kwargs)
