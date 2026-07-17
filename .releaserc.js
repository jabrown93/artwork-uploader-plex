// semantic-release configuration.
//
// Versioning is automated from Conventional Commits:
//   * push to `main` -> stable release (feat -> minor, fix/perf -> patch, ! -> major)
//   * push to `beta` -> prerelease (vX.Y.Z-beta.N)
//
// This file is CommonJS (there is no root package.json with "type": "module");
// semantic-release loads it via cosmiconfig.

module.exports = {
  branches: ["main", { name: "beta", prerelease: true }],
  tagFormat: "v${version}",
  plugins: [
    "@semantic-release/commit-analyzer",
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
