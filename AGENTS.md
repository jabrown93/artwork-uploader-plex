# AGENTS.md

Instructions for AI agents working on this codebase.

## Project Overview

Flask + SocketIO web app that uploads artwork (posters, backgrounds, title cards) from ThePosterDB and MediUX to Plex Media Server. Forked repo for personal use.

**DO NOT** make PRs to the upstream repo.

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # testing, linting

# Run
python artwork_uploader.py                              # web server on :4567
python artwork_uploader.py https://mediux.pro/sets/9242 # single URL
python artwork_uploader.py bulk bulk_import.txt          # bulk import

# Test
pytest                    # all tests
pytest --cov              # with coverage
pytest -m unit            # unit only
pytest -m "not slow"      # skip slow

# Code quality
black . && isort .        # format
flake8 . && mypy artwork_uploader.py  # lint + types

# Docker (Makefile targets)
make docker-build         # multi-platform build
make docker-release       # build + push
```

## Architecture

```
Web UI (Flask + SocketIO)
    ↓
Application Layer (src/artwork_uploader.py, src/web_routes.py)
    ↓
Service Layer (src/services/)
    ↓
Business Logic (src/scrapers/, src/processors/, src/plex/, src/kometa/)
    ↓
External APIs (PlexAPI, ThePosterDB, MediUX)
```

### Key Patterns

- **Service Layer**: Business logic in service classes, independent of UI.
- **Callbacks**: Services use `ProcessingCallbacks` dataclass for UI communication without tight coupling.
- **Dependency Injection**: Services receive dependencies via constructors, not global state.
- **Scraper Abstraction**: `Scraper` detects provider from URL, delegates to `ThePosterDBScraper` or `MediuxScraper`.

### File Organization

All application code lives under `src/`:

| Directory | Purpose | Key files |
|-----------|---------|-----------|
| `src/core/` | Config, constants, enums, exceptions, globals | `config.py`, `constants.py`, `globals.py`, `__version__.py` |
| `src/services/` | Service layer | `artwork_processor.py`, `bulk_file_service.py`, `image_service.py`, `scheduler_service.py`, `authentication_service.py`, `notify_service.py`, `utility_service.py` |
| `src/scrapers/` | Provider scrapers | `scraper.py`, `theposterdb_scraper.py`, `mediux_scraper.py` |
| `src/processors/` | Processing logic | `upload_processor.py`, `bulk_import.py`, `media_metadata.py` |
| `src/plex/` | Plex integration | `plex_connector.py`, `plex_uploader.py` |
| `src/kometa/` | Kometa asset saving | `kometa_saver.py` |
| `src/models/` | Data models | `options.py`, `instance.py`, `artwork_types.py`, `url_item.py`, `arguments.py` |
| `src/utils/` | Utilities | `notifications.py`, `soup_utils.py`, `utils.py` |
| `src/static/` | Web UI assets | CSS, JS, images |
| `src/templates/` | Flask templates | HTML |
| `bulk_imports/` | User bulk import files | Text files with URLs |

### Critical Components

- **PlexConnector** (`src/plex/plex_connector.py`): Plex server connection with 3s timeout, library detection, media item lookup.
- **UploadProcessor** (`src/processors/upload_processor.py`): Matches scraped artwork to Plex media, handles artwork ID tracking via labels.
- **ArtworkProcessor** (`src/services/artwork_processor.py`): Orchestrates scrape-to-upload flow with callback-based progress.
- **Web Routes** (`src/web_routes.py`): All HTTP routes and Socket.IO handlers.

### Data Flow

1. URL provided (CLI, bulk file, or web UI)
2. `Scraper` detects provider, delegates to specialized scraper
3. Scraper returns structured artwork lists (`MovieArtworkList`, `TVArtworkList`, `CollectionArtworkList`)
4. `UploadProcessor` matches artwork to Plex media by title/year
5. Downloads and uploads to Plex (or saves to Kometa asset directory)
6. Tracks artwork IDs via Plex labels to skip unchanged artwork on re-runs

## Configuration

`config.json` in working directory (override with `--config` flag or `CONFIG_PATH` env var):

| Key | Type | Purpose |
|-----|------|---------|
| `base_url` | string | Plex server URL |
| `token` | string | Plex auth token |
| `tv_library`, `movie_library` | string or array | Library names |
| `track_artwork_ids` | bool | Track artwork IDs via Plex labels (recommended: true) |
| `mediux_filters`, `tpdb_filters` | array | Global artwork type filters |
| `save_to_kometa` | bool | Save to Kometa asset directory instead of Plex |
| `kometa_base` | string | Kometa asset directory path |
| `kometa_library_paths` | dict | Map library names to custom directory names |
| `reset_overlay` | bool | Remove Kometa overlay label on upload |
| `skip_locked_artwork` | bool | Skip artwork whose target field is locked in Plex, unless `--force` |
| `auth_mode` | string | `none`, `password` or `oidc` (legacy `auth_enabled` is migrated to `password`) |
| `oidc_issuer`, `oidc_client_id`, `oidc_client_secret` | string | OIDC client settings; env `OIDC_*` wins |
| `oidc_groups_claim`, `oidc_allowed_groups` | string, array | Group allowlist; dotted claim paths supported |
| `oidc_allow_password_fallback` | bool | Keep `/login?local=1` password sign-in as break-glass |
| `external_url`, `trusted_proxy_count` | string, int | Public URL and proxy hops used to build the redirect URI |
| `session_secret`, `session_cookie_secure` | string | Session signing key (env `SESSION_SECRET` wins) and cookie flag |
| `cors_allowed_origins` | array | Empty means same-origin only for HTTP and Socket.IO |

Docker: `RUNNING_IN_DOCKER=1` hardcodes Kometa base to `/assets` and temp dir to `/temp`.

## Authentication

- Modes live in `auth_mode`; `AuthenticationService` handles local passwords, `OidcService` (`src/services/oidc_service.py`) runs the OIDC authorization code flow with PKCE via Authlib.
- HTTP routes use `@login_required`; **every** Socket.IO handler uses `@socket_login_required` and the `connect` handler refuses unauthenticated clients. All real work happens over Socket.IO, so a new handler without the decorator is an authentication bypass.
- Secrets (`REDACTED_CONFIG_KEYS`: Plex token, Radarr/Sonarr API keys, OIDC client secret) never leave the server: `Config.to_public_dict()` swaps them for `SECRET_PLACEHOLDER`, and `apply_config_updates()` keeps the stored value when the UI echoes that placeholder back. `session_secret` is dropped entirely, and `PROTECTED_CONFIG_KEYS` blocks the UI from writing derived keys. Adding a new secret config key means adding it to `REDACTED_CONFIG_KEYS`.

## Filter Types

Defined in `src/core/constants.py`: `show_cover`, `background`, `season_cover`, `title_card`, `movie_poster`, `collection_poster`, `square_art`.

Set globally in config or per-URL via `--filters`. Exclude by artwork ID or season/episode (`--exclude s01e05`).

## Scraper Details

- **ThePosterDB**: Rate limited to 6s between requests. Supports sets, user uploads, individual posters. HTML scraping with BeautifulSoup.
- **MediUX**: API-based (`https://api.mediux.pro/assets/<id>`). Supports sets and boxsets. Appends `&w=3840&q=80` for high-res.

## Global State

Managed in `src/core/globals.py`. Singletons: `web_socket`, `scheduler_service`, `update_service`, `bulk_file_service`. Avoid adding new globals; use DI or extend existing services.

## Common Tasks

### Adding a New Scraper
1. Create class in `src/scrapers/` following existing patterns
2. Add URL detection in `Scraper.__init__()`
3. Add delegation in `Scraper.scrape()`
4. Add constants to `src/core/constants.py`

### Adding a New Service
1. Create class in `src/services/`
2. Export in `src/services/__init__.py`
3. Use DI for dependencies, `ProcessingCallbacks` for UI updates
4. Add singleton to `src/core/globals.py` only if needed

### Adding a Socket.IO Event
1. Add handler in `src/web_routes.py` inside `setup_socket_handlers()`
2. Emit via `globals.web_socket.emit()`
3. Handle in `src/static/web_interface.js`

## Constraints

- **Python 3.10+** required. Use 3.11 or 3.12 (eventlet issues with 3.13).
- **Port 4567** default (configurable via `DEFAULT_WEB_PORT`).
- **Plex timeout**: 3s connection timeout; web UI starts even if Plex unreachable.
- **Version**: Defined in `src/core/__version__.py`. Use `bump_version.py` for bumps.
