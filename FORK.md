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

Only what is verified fork-only at the current merge base. Both sides have grown web
authentication (password + OIDC) independently; which implementation survives is decided at the
first catch-up sync (see Decisions).

| Area | Behavior | Where |
| --- | --- | --- |
| Layout | Application code under `src/`; `run.sh` sets `PYTHONPATH`. Dev deps pinned from `requirements*.in`; Ruff config in `pyproject.toml`. | `src/`, `run.sh`, `pyproject.toml` |
| Container | `tini` + `gosu` entrypoint; runs as `PUID`/`PGID`, not root. | `Dockerfile`, `entrypoint.sh` |
| Versioning | semantic-release from Conventional Commits writes `src/core/__version__.py` and `CHANGELOG.md`; `beta` is a prerelease line merged back from `main`. | `.releaserc.js`, `release.yml`, `merge-back.yml` |
| HTTPS / binding | `TLS_CERT_FILE`/`TLS_KEY_FILE` serve HTTPS; bind IPv4, IPv6, or dual-stack. | `src/core/config.py`, `src/web_routes.py` |
| Plex lookups | Title and show/season/episode lookups are memoized per upload run. `PlexLookupError` carries a partial result so a transient library error is never cached as a miss; caches clear when `PlexConnector.state_generation` changes on reconnect. | `src/core/exceptions.py`, `src/plex/plex_connector.py`, `src/processors/upload_processor.py` |
| Auth | Separate `oidc_service.py` (Authlib); saved secrets redacted from settings responses. Upstream has its own OIDC in `authentication_service.py`. | `src/services/oidc_service.py`, `src/services/authentication_service.py`, `src/web_routes.py` |
| Scheduling | APScheduler for periodic jobs. | `src/services/scheduler_service.py` |

## Decisions

Dated so a later sync can revisit them.

- **2026-11 mechanism** (`.upstream-sync` set to the real merge base `c75774d`, 2025-12-12).
  Sync script and weekly workflow as in jabrown93/AURA and jabrown93/CrossWatch. The first catch-up
  to upstream v1.3.3 is a separate PR; its decisions (auth implementation, `PlexLibraryIndex` vs
  the fork's lookup cache, health endpoint, local-assets saver) are recorded here when made.
