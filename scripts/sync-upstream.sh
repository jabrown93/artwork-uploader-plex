#!/usr/bin/env bash
# Merge mscodemonkey/artwork-uploader-plex into this fork with the correct merge base.
#
# main is squash-only and signature-protected, so upstream history never lands
# here and git cannot find the real merge base on its own. .upstream-sync records
# the last upstream commit already synced; this script grafts it in as a
# local-only second parent so `git merge` only sees upstream changes made since.
# The result is committed as one ordinary commit on top of origin/main.
#
# Usage:
#   scripts/sync-upstream.sh [<upstream-ref>]   start a sync (default: upstream/main)
#   scripts/sync-upstream.sh --finish           commit after resolving conflicts
#   scripts/sync-upstream.sh --check            CI: exit 0 clean, 1 conflicts, 3 up to date
#
# See FORK.md for the conflict-resolution policy.
set -euo pipefail

MARKER=.upstream-sync
UPSTREAM_URL=https://github.com/mscodemonkey/artwork-uploader-plex.git
STATE_FILE="$(git rev-parse --git-path upstream-sync-target)"
# Paths the fork owns outright (CI, image, packaging, semantic-release versioning);
# upstream changes there are reported, never taken.
FORK_OWNED=(.github/workflows Dockerfile entrypoint.sh run.sh Makefile .releaserc.js renovate.json
  CHANGELOG.md pyproject.toml requirements.in requirements-dev.in src/core/__version__.py
  AGENTS.md CLAUDE.md GEMINI.md)

die() { echo "sync-upstream: $*" >&2; exit 2; }

require_clean() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree not clean"
  fi
}

fetch() {
  git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
  git fetch -q upstream
  git fetch -q origin
}

restore_fork_owned() {
  local changed f
  changed=$(git diff --cached --name-only HEAD -- "${FORK_OWNED[@]}" || true)
  [[ -n "$changed" ]] || return 0
  echo "Upstream changed fork-owned paths; restored to the fork's version. Review manually:"
  while IFS= read -r f; do
    echo "  $f"
    if git cat-file -e "HEAD:$f" 2>/dev/null; then
      git checkout HEAD -- "$f"
    else
      # Added upstream; the fork does not have it.
      git rm -q -f -- "$f"
    fi
  done <<< "$changed"
}

start() {
  local ref=${1:-upstream/main} last target short branch base out conflicts f
  require_clean
  fetch
  # From the fetched origin/main, not the checkout: a stale local main would graft an
  # older parent and replay upstream changes a merged sync already integrated.
  last=$(git show origin/main:"$MARKER" 2>/dev/null | tr -d '[:space:]') || die "$MARKER missing on origin/main"
  [[ -n $last ]] || die "$MARKER empty on origin/main"
  target=$(git rev-parse --verify "$ref^{commit}")
  if git merge-base --is-ancestor "$target" "$last"; then
    echo "Already synced through $(git rev-parse --short "$last")."
    return 3
  fi
  short=$(git rev-parse --short "$target")
  branch=${SYNC_BRANCH:-upstream-sync-$short}
  git switch -q -C "$branch" origin/main

  base=$(git commit-tree -p HEAD -p "$last" -m "local-only: upstream through $last" "HEAD^{tree}")
  git reset -q --hard "$base"
  printf '%s\n' "$target" > "$STATE_FILE"

  # The fork keeps its code under src/ while upstream is flat; directory-rename
  # detection maps upstream's core/x.py onto src/core/x.py during the merge.
  if out=$(git -c merge.conflictStyle=zdiff3 -c merge.directoryRenames=true merge --no-commit --no-ff "$target" 2>&1); then
    restore_fork_owned
    echo "Merged cleanly. Review, run the tests, then: scripts/sync-upstream.sh --finish"
    return 0
  fi
  # A merge that never started (an untracked file in upstream's way, say) leaves no
  # MERGE_HEAD. Treating it as a conflict would let --finish advance the marker
  # without upstream's changes, dropping them from every later sync.
  if ! git rev-parse -q --verify MERGE_HEAD >/dev/null; then
    rm -f "$STATE_FILE"
    die "merge did not start:
$out"
  fi
  restore_fork_owned
  conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null)
  if [[ -z $conflicts ]]; then
    echo "Merged cleanly; conflicts were only in fork-owned paths. Review, run the tests, then: scripts/sync-upstream.sh --finish"
    return 0
  fi
  echo "Conflicts in:"
  while IFS= read -r f; do echo "  $f"; done <<< "$conflicts"
  echo "Resolve per FORK.md, git add, then: scripts/sync-upstream.sh --finish"
  return 1
}

finish() {
  local target prev tree msg commit
  [[ -s $STATE_FILE ]] || die "no sync in progress"
  target=$(cat "$STATE_FILE")
  [[ -z "$(git diff --name-only --diff-filter=U)" ]] || die "unresolved conflicts remain"
  prev=$(git show origin/main:"$MARKER" 2>/dev/null | tr -d '[:space:]' || true)
  printf '%s\n' "$target" > "$MARKER"
  # Tracked files only: the merge already staged upstream's additions, and -A would
  # sweep unrelated untracked files (an un-ignored .env, say) into the sync commit.
  git add -u
  git add -- "$MARKER"
  tree=$(git write-tree)
  msg="chore(sync): merge upstream mscodemonkey/artwork-uploader-plex through $(git rev-parse --short "$target")

Upstream range: ${prev:-unknown}..$target"
  # Sign when a key is configured; CI branches are squash-merged (and signed) by GitHub.
  if [[ -n "$(git config user.signingkey || true)" ]]; then
    commit=$(git commit-tree -S -p origin/main -m "$msg" "$tree")
  else
    commit=$(git commit-tree -p origin/main -m "$msg" "$tree")
  fi
  git merge --quit 2>/dev/null || true
  git reset -q "$commit"
  rm -f "$STATE_FILE"
  echo "Committed $(git rev-parse --short "$commit") on $(git branch --show-current) (parent: origin/main only)."
}

case "${1:-}" in
  --finish) finish ;;
  --check) start "${2:-upstream/main}" ;;
  -h|--help) sed -n '2,15p' "$0" ;;
  *) start "${1:-}" ;;
esac
