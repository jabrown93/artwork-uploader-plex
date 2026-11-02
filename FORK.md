# Fork notes

Fork of [mscodemonkey/artwork-uploader-plex](https://github.com/mscodemonkey/artwork-uploader-plex).
This file is the contract for keeping it close to upstream: what differs, why, and how to sync.

## Syncing upstream

`main` is squash-only with signed commits, so upstream history never lands here and git cannot
find the merge base by itself. `.upstream-sync` records the last upstream commit already merged.

```bash
scripts/sync-upstream.sh            # branch from origin/main, merge upstream/main
# resolve conflicts (policy below), run the checks, git add
scripts/sync-upstream.sh --finish   # one commit on top of origin/main; updates .upstream-sync
```

Open a PR from the branch. `.github/workflows/upstream-sync.yml` does this weekly: a clean merge
becomes a PR, a conflicting one becomes a tracking issue.

Never edit `.upstream-sync` by hand to a commit that has not actually been merged; the next sync
would silently drop everything before it.

Checks (CI runs both):

```bash
python -m ruff check .
python -m pytest
```

To tell merge regressions from upstream's own failures, run upstream's tests on a pristine checkout
of the target upstream commit and compare.

### Conflict policy

1. **Upstream's code shape wins.** Fork-only refactors of upstream code are not kept. Re-apply the
   fork *behavior* as the smallest patch on upstream's version, marked with a `# Fork:` comment,
   or in a `fork_*.py` module so the next conflict is obvious.
2. **Keep fork behavior listed below.** Anything not listed is upstream's call.
3. **Layout:** the fork keeps application code under `src/`; upstream is flat. The merge maps
   upstream's `core/x.py` onto `src/core/x.py` by directory-rename detection. A conflict reported at
   a flat path (e.g. `plex/plex_connector.py`) is the same file as `src/plex/plex_connector.py`:
   resolve into the `src/` copy and `git rm` the flat one. Never let a flat copy land.
4. **Fork-owned paths** keep the fork version and the script restores them automatically:
   `.github/workflows/`, `Dockerfile`, `entrypoint.sh`, `run.sh`, `Makefile`, `.releaserc.js`,
   `renovate.json`, `CHANGELOG.md`, `pyproject.toml`, `requirements*.in`,
   `src/core/__version__.py`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`. Port upstream changes there
   deliberately (e.g. a new `HEALTHCHECK`).
5. **Dependencies:** `requirements.in` / `requirements-dev.in` are the source of truth. Add
   upstream's new packages there, then regenerate `requirements.txt` / `requirements-dev.txt`
   (`uv pip compile`, command in each `.in` header). Upstream's `requirements.txt` edits are never taken verbatim. Upstream's
   `bump_version.py` and the version string in `core/__version__.py` are not taken: releases are
   cut by semantic-release.
6. New behavioral conflicts the policy does not cover: record a decision here before merging.

## Fork differences

Verified against upstream v1.3.3 (`517eed0`). Each kept behavior has a test; `# Fork:` marks the
patch in code.

| Area | Behavior | Where |
| --- | --- | --- |
| Layout | Application code under `src/`; `run.sh` sets `PYTHONPATH`; `tests/conftest.py` puts `src/` on `sys.path`. Deps pinned from `requirements*.in` with `uv pip compile`; Ruff config in `pyproject.toml`. | `src/`, `run.sh`, `pyproject.toml` |
| Container | `tini` + `gosu` entrypoint; runs as `PUID`/`PGID`, not root. Upstream's `HEALTHCHECK` ported. | `Dockerfile`, `entrypoint.sh` |
| Versioning | semantic-release from Conventional Commits writes `src/core/__version__.py` and `CHANGELOG.md`; `beta` is a prerelease line merged back from `main`. Upstream's `bump_version.py` and version strings are not taken. | `.releaserc.js`, `release.yml`, `merge-back.yml` |
| HTTPS / binding | `tls_cert_file`/`tls_key_file` (env `TLS_CERT_FILE`/`TLS_KEY_FILE`) serve HTTPS; `ip_binding` = `auto` (dual-stack), `ipv4`, `ipv6`. | `src/core/config.py`, `src/web_routes.py` (`start_web_server`), `tests/test_config_tls.py` |
| Plex lookups | On top of upstream's `PlexLibraryIndex`: a transient per-library error raises `PlexLookupError(message, partial_result)` instead of being cached as a miss; a hit found after an earlier error is partial; `PlexConnector.state_generation` bumps on reconnect and every per-run cache (title, show structure, season/episode) clears with it. ZIP imports resolve titles through the same memoized, error-aware cascade (underscore/colon, accent folding, progressive shortening). | `src/core/exceptions.py`, `src/plex/plex_connector.py`, `src/plex/library_index.py`, `src/processors/upload_processor.py`, `src/web_routes.py` (`_resolve_plex_title`), `tests/test_plex_connector_recovery.py`, `tests/test_upload_processor_plex_memoization.py`, `tests/test_extract_and_list_zip.py` |
| Auth | Upstream's password + OIDC (`requests-oauthlib`) plus: PKCE S256 on the authorization-code flow; a login POST only reaches a method whose flag is on (a stale password hash cannot bypass OIDC); every Socket.IO handler carries `@socket_login_required` so enabling auth also cuts off already-connected clients; saved secrets (`token`, `auth_password_hash`, `oidc_client_secret`, `webhook_token`, `radarr_api_key`, `sonarr_api_key`) are redacted in settings responses and kept unless the client sends a new value; settings save to the active `config.path` (never the default) and a client payload cannot change it; `SESSION_SECRET` env pins the session signing key so restarts don't log users out. | `src/web_routes.py`, `tests/test_auth_routes.py`, `tests/test_socket_authentication.py` |
| Uploads | Chunked uploads are isolated per Socket.IO client (`(sid, file_name)` keyed, locked, cleaned on disconnect); upload filenames are basename-contained; bulk-import failures are reported. | `src/web_routes.py`, `tests/test_socket_uploads.py`, `tests/test_bulk_file_path_traversal.py`, `tests/test_bulk_import_failure_reporting.py` |
| Sonarr/Radarr pre-seed | Kometa assets for items not yet in Plex are pre-seeded from Sonarr/Radarr (`radarr_*`, `sonarr_*`, `arr_root_folder_library_map`, `preseed_arr`); `ArrService` is reconfigured on settings save. Distinct from upstream's import webhooks. | `src/services/arr_service.py`, `src/processors/upload_processor.py`, settings UI, `tests/test_arr_service.py`, `tests/test_upload_processor_preseed.py` |
| Kometa staging | `kometa_library_paths` maps a Plex library to its Kometa directory on every save path; `stage_collections` stages artwork for a collection not yet in Plex into each movie library; `stage_specials` stages Specials for a show whose season 0 Plex lacks; a forced save removes the stale copies with other extensions once the new file has landed. | `src/processors/upload_processor.py`, `src/kometa/kometa_saver.py`, `src/core/config.py` (`resolve_library_directory`), `tests/test_upload_processor_kometa_staging.py`, `tests/test_kometa_saver_retry.py`, `tests/test_upload_processor_tv_artwork.py` |
| Scraping | MediUX `movie_poster` files in a collection set without `movie_id` count as the collection poster. URL validation (IPv6, IDNA, label rules) in `utils.py` instead of the `validators` package. | `src/scrapers/mediux_scraper.py`, `src/utils/utils.py`, `tests/test_mediux_scraper_process_set.py`, `tests/test_utils_urls.py` |
| Config paths | `CONFIG_PATH`/`LOGS_DIR` env and `--config`/`--logs` CLI overrides. | `src/artwork_uploader.py`, `src/models/arguments.py` |

## Decisions

Dated so a later sync can revisit them.

- **2026-11 mechanism** (`.upstream-sync` set to the real merge base `c75774d`, 2025-12-12).
  Sync script and weekly workflow as in jabrown93/AURA and jabrown93/CrossWatch. The first catch-up
  to upstream v1.3.3 is a separate PR; its decisions (auth implementation, `PlexLibraryIndex` vs
  the fork's lookup cache, health endpoint, local-assets saver) are recorded here when made.
- **2026-11 catch-up (upstream `c75774d` → v1.3.3 `517eed0`)**
  - The two sides diverged past git's rename detection on most large files; those were rebuilt
    from upstream's version with the table above re-applied as `# Fork:` patches. Fork-only
    refactors with "characterization" tests that pinned the refactor (not behavior) were dropped
    with their tests: the fork's `_process_set` MediUX scraper shape, `PlexConnector` socket probe
    and name-tracking re-resolution, `UploadProcessor` filter/exclusion checks (upstream does this
    in the scraper layer), the Authlib `oidc_service.py` and its `auth_mode` config keys.
  - Auth: upstream's implementation and config keys (`auth_enabled`, `oidc_*`, `external_url`).
    Dropped fork keys: `auth_mode`, `oidc_provider_name`, `oidc_allow_password_fallback`,
    `session_secret`, `session_cookie_secure`, `trusted_proxy_count`, `cors_allowed_origins`
    (Flask-CORS removed), `zip_title_strip_words`. Kept: PKCE, secret redaction, groups allowlist
    (upstream has its own `oidc_allowed_groups`).
  - Server: upstream's `async_mode="threading"`; eventlet removed. Scheduler: upstream's
    `schedule`-based service; APScheduler removed (its fork tests re-expressed against `schedule`).
  - Plex: upstream's `PlexLibraryIndex` is the single cache; the fork's invariants attach to it
    rather than adding a second layer. `services/update_service.py` restored (upstream's call).
  - Upstream's `save_uploaded_file` takes the finished temp path instead of the shared
    `upload_chunks` dict (consequence of per-client isolation); its `test_upload_temp_cleanup`
    adapted.
  - Kometa: the fork's multi-extension stale-asset cleanup in `kometa_saver.py` was first dropped
    as untested, then restored with a test once review showed a forced PNG over a JPG left both.
  - Scheduler: upstream restores a persisted interval `next_run` verbatim, so a stale one fires
    on the first tick after restart and bypasses `catch_up_window_minutes`. Patched to advance it
    to the next future occurrence (`# Fork:` in `scheduler_service.py`); worth offering upstream.
  - `local_library_matching` (upstream) treats a local index miss as terminal; with the arr
    pre-seed enabled for that kind it falls through to the TPDb lookup instead, so the later Plex
    miss reaches the Radarr/Sonarr branch.
  - `find_in_library` keeps the fork's title/year fallback after a TMDb GUID miss (uploads can
    carry no `tmdb_id`), with the same partial-result semantics.
