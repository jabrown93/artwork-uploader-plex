#!/usr/bin/env node
// Creates the semantic-release version-bump commit via GitHub's GraphQL
// createCommitOnBranch instead of local git, so GitHub signs it and the
// commit shows as Verified. Replaces @semantic-release/git, whose local
// commits are unsigned (no key is available to a CI bot identity).
//
// Run from an @semantic-release/exec prepareCmd, ordered after every other
// prepare step so the files it commits are final:
//   node release-commit.mjs --branch <name> --message <headline> -- <path...>
//
// Paths that did not change are skipped; if nothing changed, no commit is
// made. expectedHeadOid pins the mutation to this checkout's HEAD, so a
// concurrent push fails the release loudly instead of being overwritten.
// On success the local checkout is hard-reset to the new commit, because
// semantic-release re-reads HEAD after the prepare phase and tags it — the
// tag must point at the API commit, not the pre-bump checkout.

import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';

const git = (...args) =>
  execFileSync('git', args, { encoding: 'utf8' }).trim();

const argv = process.argv.slice(2);
const flags = {};
const paths = [];
for (let i = 0; i < argv.length; i += 1) {
  if (argv[i] === '--branch' || argv[i] === '--message') {
    flags[argv[i].slice(2)] = argv[i + 1];
    i += 1;
  } else if (argv[i] !== '--') {
    paths.push(argv[i]);
  }
}
if (!flags.branch || !flags.message || paths.length === 0) {
  throw new Error(
    'usage: release-commit.mjs --branch <name> --message <headline> -- <path...>'
  );
}

const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN;
if (!token) throw new Error('GITHUB_TOKEN or GH_TOKEN must be set');
const repository = process.env.GITHUB_REPOSITORY;
if (!repository) throw new Error('GITHUB_REPOSITORY must be set');

// --porcelain covers modified and untracked alike — a repo's first release
// creates CHANGELOG.md rather than modifying it. Listing a path that does
// not exist at all is fine: status prints nothing and it is skipped.
const changed = paths.filter(
  (p) => git('status', '--porcelain', '--', p) !== ''
);
if (changed.length === 0) {
  console.log('release-commit: no listed file changed, skipping commit');
  process.exit(0);
}

const additions = await Promise.all(
  changed.map(async (path) => ({
    path,
    contents: (await readFile(path)).toString('base64'),
  }))
);

const response = await fetch('https://api.github.com/graphql', {
  method: 'POST',
  headers: {
    authorization: `bearer ${token}`,
    'content-type': 'application/json',
  },
  body: JSON.stringify({
    query: `mutation ($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }`,
    variables: {
      input: {
        branch: { repositoryNameWithOwner: repository, branchName: flags.branch },
        message: { headline: flags.message },
        expectedHeadOid: git('rev-parse', 'HEAD'),
        fileChanges: { additions },
      },
    },
  }),
});
const body = await response.json();
if (!response.ok || body.errors) {
  throw new Error(
    `createCommitOnBranch failed: ${JSON.stringify(body.errors ?? body)}`
  );
}
const oid = body.data.createCommitOnBranch.commit.oid;

git('fetch', 'origin', `+refs/heads/${flags.branch}:refs/remotes/origin/${flags.branch}`);
git('reset', '--hard', oid);
console.log(`release-commit: created ${oid} on ${flags.branch} (${changed.join(', ')})`);
