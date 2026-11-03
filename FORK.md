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
| Plex lookups | On top of upstream's `PlexLibraryIndex`: a transient per-library error raises `PlexLookupError(message, partial_result)` instead of being cached as a miss; a hit found after an earlier error is partial; `PlexConnector.state_generation` bumps on reconnect and every per-run cache (title, show structure, season/episode) clears with it; `set_tv_libraries`/`set_movie_libraries` snapshot that generation before resolving and discard a resolution a concurrent reconnect() made stale instead of publishing it over the newer reconnect's handles. ZIP imports resolve titles through the same memoized, error-aware cascade (underscore/colon, accent folding, progressive shortening). | `src/core/exceptions.py`, `src/plex/plex_connector.py`, `src/plex/library_index.py`, `src/processors/upload_processor.py`, `src/web_routes.py` (`_resolve_plex_title`), `tests/test_plex_connector_recovery.py`, `tests/test_upload_processor_plex_memoization.py`, `tests/test_extract_and_list_zip.py` |
| TPDb identity | With local library matching enabled, a title match without a Plex TMDb GUID resolves the poster-page identity before the normal Plex lookup; it does not reject the match solely for lacking that GUID. | `src/processors/upload_processor.py`, `tests/test_upload_processor_preseed.py` |
| Auth | Upstream's password + OIDC (`requests-oauthlib`) plus: PKCE S256 on the authorization-code flow; ID-token claims (`iss`/`aud`/`exp` checked) are the base for the identity and group checks with userinfo merged over them; `oidc_groups_claim` may be a dotted path and the value a space-separated string; `oidc_scopes` accepts a list or a space-separated string; a login POST only reaches a method whose flag is on (a stale password hash cannot bypass OIDC); every Socket.IO handler carries `@socket_login_required` so enabling auth also cuts off already-connected clients; Socket.IO accepts same-origin connections only (upstream opens `cors_allowed_origins="*"`); saved secrets (`token`, `auth_password_hash`, `oidc_client_secret`, `webhook_token`, `radarr_api_key`, `sonarr_api_key`) are redacted in settings responses and kept unless the client sends a new value; settings save to the active `config.path` (never the default) and a client payload cannot change it; the run-log viewer only serves `*.log` files inside the log directory; `SESSION_SECRET` env pins the session signing key so restarts don't log users out; the OIDC ID token's own `exp` is stored at login (`session['oidc_exp']`) and enforced by `login_required`/`socket_login_required` on every later request/event, not only at login, so an expired ID token actually ends the session instead of staying valid for the life of the Flask session cookie. | `src/web_routes.py`, `tests/test_auth_routes.py`, `tests/test_socket_authentication.py` |
| Uploads | Chunked uploads are isolated per Socket.IO client (`(sid, file_name)` keyed, locked, cleaned on disconnect, run label/log file carried on the entry); completed uploads release their run state even on processing errors; upload filenames are basename-contained; bulk-import failures are reported. | `src/web_routes.py`, `tests/test_socket_uploads.py`, `tests/test_bulk_file_path_traversal.py`, `tests/test_bulk_import_failure_reporting.py` |
| Run logs | Run labels are sanitized for log filenames without changing their display titles, so names with path separators remain readable in run history. | `src/utils/notifications.py`, `tests/test_run_log_file.py`, `tests/test_webhook_service.py` |
| Run history | Writers in separate CLI and web processes serialize history updates using a persistent sidecar lock; atomic replacement keeps readers from seeing partial JSON. | `src/services/run_history.py`, `tests/test_run_history.py` |
| Import webhooks | Radarr import retries are deduplicated by movie, folder, and imported file, so a replacement arriving while an earlier file waits for Plex gets its own retry chain. Transient Plex connection failures use the same bounded retry ladder. | `src/services/webhook_service.py`, `tests/test_webhook_service.py` |
| Sonarr/Radarr pre-seed | Kometa assets for items not yet in Plex are pre-seeded from Sonarr/Radarr (`radarr_*`, `sonarr_*`, `arr_root_folder_library_map`, `preseed_arr`); `ArrService` is reconfigured on settings save. Distinct from upstream's import webhooks. | `src/services/arr_service.py`, `src/processors/upload_processor.py`, settings UI, `tests/test_arr_service.py`, `tests/test_upload_processor_preseed.py` |
| Kometa staging | `kometa_library_paths` maps a Plex library to its Kometa directory on every save path; `stage_collections` stages artwork for a collection not yet in Plex into each movie library; `stage_specials` controls staging Specials for a show whose season 0 Plex lacks, including when Sonarr knows the season; a forced save removes the stale copies with other extensions once the new file has landed. | `src/processors/upload_processor.py`, `src/kometa/kometa_saver.py`, `src/core/config.py` (`resolve_library_directory`), `tests/test_upload_processor_kometa_staging.py`, `tests/test_kometa_saver_retry.py`, `tests/test_upload_processor_tv_artwork.py` |
| Scraping | MediUX `movie_poster` files in a collection set without `movie_id` count as the collection poster; title cards and season covers fall back to per-file metadata when the show omits episode/season lists. URL validation (IPv6, IDNA, label rules) in `utils.py` instead of the `validators` package. | `src/scrapers/mediux_scraper.py`, `src/utils/utils.py`, `tests/test_mediux_scraper_process_set.py`, `tests/test_utils_urls.py` |
| Config paths | `CONFIG_PATH`/`LOGS_DIR` env and `--config`/`--logs` CLI overrides; code that re-reads the config (scrapers, upload processor, settings save) uses `Config.at_active_path()` so it follows them. The CLI's default bulk-file argument resolves through `BulkFileService` (honors the Docker `/bulk_imports` mount and `BULK_IMPORTS_DIR`). | `src/artwork_uploader.py`, `src/models/arguments.py`, `src/core/config.py`, `tests/test_config_save.py`, `tests/test_cli_bulk_default_path.py` |

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
    to the next future occurrence; `get_missed_run` likewise judges the most recent missed
    interval, not the first (`# Fork:` in `scheduler_service.py`); both worth offering upstream.
  - `local_library_matching` (upstream) treats a local index miss as terminal; with the arr
    pre-seed enabled for that kind it falls through to the TPDb lookup instead, so the later Plex
    miss reaches the Radarr/Sonarr branch.
  - ZIP square art is numbered per show (`SquareArt_0` is a show's primary asset); upstream's
    archive-wide counter demoted every show's first asset but one.
  - `ensure_libraries` retries each configured library type independently, so a type that failed
    at start-up recovers while the other is already held.
    The setters publish handles only after every name resolved.
    `reconnect` drops the old handles before connecting, and the webhook path calls
    `ensure_libraries` so a webhook-only install recovers without a scrape.
  - Run-history labels are rendered with `textContent` and listener closures (upstream built an
    `html_label` string with the label inside inline `onclick`), closing stored HTML injection
    and the broken delete button on labels with an apostrophe.
  - `find_in_library` keeps the fork's title/year fallback after a TMDb GUID miss (uploads can
    carry no `tmdb_id`), with the same partial-result semantics.
  - An item whose processing raises counts as a failure (`callbacks.failed(1)`), so the summary,
    notifications and run history don't report a clean run.
  - `PlexLibraryIndex.normalize_title` and `AssetIndex`'s key normalizer keep non-Latin letters
    (upstream folds to ASCII, which erases e.g. Japanese titles and makes them unmatchable); the ZIP resolver treats upstream's
    `"unavailable"` miss sentinel as a miss so the title-variant cascade still runs.
  - The CLI's default bulk-file argument (no filename given on `bulk`) now resolves through
    `globals.bulk_file_service.get_bulk_file_path(config.bulk_txt)` instead of a bare
    `os.path.join("bulk_imports", ...)` relative to the process cwd, so it honors the Docker
    `/bulk_imports` mount and the `BULK_IMPORTS_DIR` override like every other bulk-file read/write.
  - `AssetIndex.__init__` only quarantines the SQLite file for confirmed corruption
    (`sqlite3.DatabaseError`, not its `sqlite3.OperationalError` subclass); a lock-contention
    timeout past `busy_timeout` now propagates instead of renaming aside and emptying a healthy
    index, so the caller's existing cache-miss/full-crawl fallback handles it.
  - Upstream's `write_json_atomically` (`core/config.py`) moves a temp file into place with
    `os.replace`, which the fork's documented Docker layout breaks: `docker-compose.example.yml`
    bind-mounts the individual file (`./config.json:/config/config.json`), making the target
    itself a mount point the kernel refuses to rename over (`EBUSY`), so every save raised
    `ConfigSaveError` under that deployment. Patched to fall back to a non-atomic in-place write
    only on `EBUSY`; a directory-mounted (or un-mounted) config path keeps the atomic swap.
    Worth offering upstream. `src/core/config.py`, `tests/test_config_save.py`.
  - The "`UploadProcessor` filter/exclusion checks... dropped" decision above only replaced them
    with scraper-layer checks (`mediux_scraper.py`, `theposterdb_scraper.py`); ZIP uploads
    (`web_routes.py extract_and_list_zip`) go through neither, so an empty per-request filter
    list (the UI's "As set by global filters" option) was treating "no restriction" as "allow
    every type" instead of falling back to `mediux_filters`/`tpdb_filters`. Patched
    `extract_and_list_zip` with the same `has_no_filters`-style fallback the scrapers use,
    keyed on the ZIP's detected source; the "unavailable"-media placeholder type (`"poster"`,
    assigned when Plex can't classify the item) is exempt, since it was never a real filter
    value and must keep surfacing in the results instead of being silently dropped.
    `src/web_routes.py`, `tests/test_extract_and_list_zip.py`.
  - `UploadProcessor._artwork_matches_item`'s confirmation cache was keyed on
    `(kind, title, year, tmdb_id)`. With `local_library_matching` on, two TPDb poster entries
    sharing a display title/year get the same locally-inferred `tmdb_id`, so they collided on one
    cache entry: the second poster's confirmation was skipped and it silently inherited the
    first poster's matched/rejected result. Added the poster's own `id` to the cache key so each
    poster is confirmed independently. `src/processors/upload_processor.py`,
    `tests/test_upload_processor_match_confirm_cache.py`.
  - The arr webhook route (`web_routes.py arr_webhook`) gated on `PlexConnector.has_libraries()`
    before enqueueing an import. A Plex that was down at start-up left library handles empty
    forever on a webhook-only install (no scrape ever calls `ensure_libraries()` either), so
    every future webhook kept 503'ing even after Plex recovered and the right library names
    were configured. Switched the route's own gate to `ensure_libraries()`, matching the pattern
    already used by `process_scrape_url_from_web` and `WebhookService._attempt()`.
    `src/web_routes.py`, `tests/test_arr_webhook_library_recovery.py`.
  - `process_scrape_url_from_web`'s cleanup (`scrapes_running` decrement, `scrape_state`
    broadcast) lived in the same `finally` block as `RunHistory().add_run()`. A history
    persistence failure (full disk, unwritable config dir) raised past the decrement, leaving
    `scrapes_running` stuck positive and the frontend's scrape/upload controls disabled until
    the process restarted. Moved the run-state cleanup into its own inner `finally` so a
    persistence failure cannot block it. `src/artwork_uploader.py`,
    `tests/test_run_history_scrape.py`.
