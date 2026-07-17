// semantic-release configuration.
//
// Versioning is automated from Conventional Commits:
//   * push to `main` -> stable release (feat -> minor, fix/perf -> patch, ! -> major)
//   * push to `beta` -> prerelease (vX.Y.Z-beta.N)
//
// Routine runtime dependency bumps (fix(deps), from Renovate via the shared
// preset) do NOT cut a release on ordinary pushes -- they would otherwise
// publish a new image per merged Renovate PR. The weekly scheduled run in
// .github/workflows/release.yml sets RELEASE_DEPS=true, which promotes the
// accumulated bumps into one patch release. Vulnerability fixes are typed
// fix(security) by the preset, not fix(deps), so they are unaffected by the
// suppression and still release immediately. See jabrown93/.github's README,
// "Weekly dependency releases".
//
// This file is CommonJS (there is no root package.json with "type": "module");
// semantic-release loads it via cosmiconfig.

const releaseDeps = process.env.RELEASE_DEPS === "true";

const depReleaseRules = [
  // Required: commit-analyzer evaluates every matching custom rule and keeps
  // the highest release type, so without this a breaking fix(deps)! would
  // match ONLY the suppression rule below and never release. Listed first so
  // the analyzer short-circuits on major.
  { type: "fix", scope: "deps", breaking: true, release: "major" },
  releaseDeps
    ? { type: "fix", scope: "deps", release: "patch" }
    : { type: "fix", scope: "deps", release: false },
];

module.exports = {
  branches: ["main", { name: "beta", prerelease: true }],
  tagFormat: "v${version}",
  plugins: [
    ["@semantic-release/commit-analyzer", { releaseRules: depReleaseRules }],
    "@semantic-release/release-notes-generator",
    ["@semantic-release/changelog", { changelogFile: "CHANGELOG.md" }],
    [
      "@semantic-release/exec",
      {
        // Mirror the computed version into src/core/__version__.py, the file
        // the app reads at runtime.
        prepareCmd:
          "python3 src/set_version.py ${nextRelease.version} ${nextRelease.type}",
      },
    ],
    "@semantic-release/github",
    [
      "@semantic-release/git",
      {
        assets: ["CHANGELOG.md", "src/core/__version__.py"],
        message: "chore(release): v${nextRelease.version} [skip ci]",
      },
    ],
  ],
};
