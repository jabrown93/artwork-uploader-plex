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
