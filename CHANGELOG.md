## [1.1.7](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.6...v1.1.7) (2026-09-01)


### Bug Fixes

* **plex:** recover libraries after startup outage ([#137](https://github.com/jabrown93/artwork-uploader-plex/issues/137)) ([8396c2f](https://github.com/jabrown93/artwork-uploader-plex/commit/8396c2f49246416ddc44d2d34ab47c66bd0cfa99))

## [1.1.6](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.5...v1.1.6) (2026-09-01)


### Bug Fixes

* isolate chunked uploads by socket client ([#140](https://github.com/jabrown93/artwork-uploader-plex/issues/140)) ([2db9c9a](https://github.com/jabrown93/artwork-uploader-plex/commit/2db9c9a0029976862ca0801b4c528ccd503e8d47))

## [1.1.5](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.4...v1.1.5) (2026-09-01)


### Bug Fixes

* report bulk import upload failures ([#139](https://github.com/jabrown93/artwork-uploader-plex/issues/139)) ([eb50aad](https://github.com/jabrown93/artwork-uploader-plex/commit/eb50aadf711de8761de41525b6ef351e6b2ef5c0))

## [1.1.4](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.3...v1.1.4) (2026-09-01)


### Bug Fixes

* **deps:** update python:3.14.7 docker digest to 8edbf9e ([228471d](https://github.com/jabrown93/artwork-uploader-plex/commit/228471d96803de98e6bd6a0848bb39017ae4012d))
* **docker:** restore runtime user and signal handling ([#138](https://github.com/jabrown93/artwork-uploader-plex/issues/138)) ([6d76252](https://github.com/jabrown93/artwork-uploader-plex/commit/6d762528a71e2ac8bdc4bd68241e171523350927))

## [1.1.3](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.2...v1.1.3) (2026-08-30)


### Bug Fixes

* **deps:** update python:3.14.7 docker digest to 1b3f778 ([a84d28d](https://github.com/jabrown93/artwork-uploader-plex/commit/a84d28db4d763c36313799dc536ece89784b0c22))
* **deps:** update python:3.14.7 docker digest to 93e0cf8 ([383c4a0](https://github.com/jabrown93/artwork-uploader-plex/commit/383c4a030ab12e9df73d1f1ac499f395b575e51d))
* **deps:** update python:3.14.7 docker digest to b0aed0e ([a1f5ee7](https://github.com/jabrown93/artwork-uploader-plex/commit/a1f5ee746894a27aaf429d7a0ca2f1cfd20d61e9))
* **security:** contain bulk import and upload filenames to their directories ([#134](https://github.com/jabrown93/artwork-uploader-plex/issues/134)) ([13a756c](https://github.com/jabrown93/artwork-uploader-plex/commit/13a756c1941e5894c9981ed5ca8720268b3521f1))

## [1.1.2](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.1...v1.1.2) (2026-08-17)


### Bug Fixes

* **deps:** update python:3.14.7 docker digest to 4fad234 ([caf889e](https://github.com/jabrown93/artwork-uploader-plex/commit/caf889eff1068fd30d87eee0f1312c406f1b4656))
* **deps:** update python:3.14.7 docker digest to 5ef1a8c ([e37ea56](https://github.com/jabrown93/artwork-uploader-plex/commit/e37ea561fcd80ba85939cbf2fc13271fafe56986))

## [1.1.1](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.1.0...v1.1.1) (2026-08-10)


### Bug Fixes

* **deps:** update python docker tag to v3.14.7 ([50c45df](https://github.com/jabrown93/artwork-uploader-plex/commit/50c45df0e6f414ff733c3b1c339087185422f8ff))
* **deps:** update python:3.14.6 docker digest to 004e5a1 ([09ab827](https://github.com/jabrown93/artwork-uploader-plex/commit/09ab8274ced9dfbfd141d36344f21ee5703339c5))
* **deps:** update python:3.14.6 docker digest to 51570a5 ([b49f368](https://github.com/jabrown93/artwork-uploader-plex/commit/b49f3688eecd9bac3d60924adf309c3a45b486c9))

# [1.1.0](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.0.1...v1.1.0) (2026-08-05)


### Features

* serve HTTPS with user-provided TLS cert and key ([#118](https://github.com/jabrown93/artwork-uploader-plex/issues/118)) ([6a92693](https://github.com/jabrown93/artwork-uploader-plex/commit/6a9269349b9759571ba9e56ba43c675a1d088652))

## [1.0.1](https://github.com/jabrown93/artwork-uploader-plex/compare/v1.0.0...v1.0.1) (2026-08-03)


### Bug Fixes

* **deps:** pin python docker tag to 5f1cdbc ([a0fc7f4](https://github.com/jabrown93/artwork-uploader-plex/commit/a0fc7f4f46b4de7d6abd921067fb2b16dfa3b7e2))

# [1.0.0](https://github.com/jabrown93/artwork-uploader-plex/compare/v0.10.1...v1.0.0) (2026-08-01)


* feat(auth)!: OIDC single sign-on, Socket.IO authentication, secret redaction ([#116](https://github.com/jabrown93/artwork-uploader-plex/issues/116)) ([3bd99d6](https://github.com/jabrown93/artwork-uploader-plex/commit/3bd99d6e380a621458ab2de22b17381b3b351a4a))


### BREAKING CHANGES

* cross-origin HTTP and Socket.IO requests are rejected unless
the origin is listed in `cors_allowed_origins`, and Socket.IO clients must
carry an authenticated session whenever authentication is enabled.

Claude-Session: https://claude.ai/code/session_01L36Toh7ZeuvkytjGaAuXnB

* fix(security): stop sending stored secrets to the web UI

The settings payload carried the Plex token and the Radarr/Sonarr API keys to
the browser in cleartext on every config load. They are now replaced with a
placeholder, alongside the OIDC client secret that already was.

Echoing the placeholder back keeps the stored value, typing over it sets a new
one, and clearing the field removes it. "Test Plex connection" falls back to the
stored token when the field is untouched. The placeholder deliberately matches
the token input's validation pattern so the settings form still submits.

Saving no longer reports failure when only the Plex reconnect fails: the
settings are already stored at that point, so an unreachable server now
produces a warning instead of a misleading "could not be saved", and the UI
gets its save_config acknowledgement either way.

Claude-Session: https://claude.ai/code/session_01L36Toh7ZeuvkytjGaAuXnB

## [0.10.1](https://github.com/jabrown93/artwork-uploader-plex/compare/v0.10.0...v0.10.1) (2026-07-25)


### Bug Fixes

* **renovate:** actually enable branch automerge ([#115](https://github.com/jabrown93/artwork-uploader-plex/issues/115)) ([45c6827](https://github.com/jabrown93/artwork-uploader-plex/commit/45c6827409b6452568ea65b9b33142e4587b58a3))

# [0.10.0](https://github.com/jabrown93/artwork-uploader-plex/compare/v0.9.1...v0.10.0) (2026-07-20)


### Bug Fixes

* **ci:** install @semantic-release/exec for the release workflow ([#112](https://github.com/jabrown93/artwork-uploader-plex/issues/112)) ([4db1e89](https://github.com/jabrown93/artwork-uploader-plex/commit/4db1e89f5c643d4067bee3b39df6a1d8553fc188))


### Features

* add skip_locked_artwork option to skip locked Plex fields ([#94](https://github.com/jabrown93/artwork-uploader-plex/issues/94)) ([57c118a](https://github.com/jabrown93/artwork-uploader-plex/commit/57c118ab8c26447a8cb184fe190b609c745ac459)), closes [#55](https://github.com/jabrown93/artwork-uploader-plex/issues/55)
* automate versioning and releases via semantic-release ([#91](https://github.com/jabrown93/artwork-uploader-plex/issues/91)) ([e8a0cd6](https://github.com/jabrown93/artwork-uploader-plex/commit/e8a0cd6717bc028cffae4d3c828876d76b5acf43))
