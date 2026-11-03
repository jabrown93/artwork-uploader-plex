import os
from typing import Optional, Literal, TYPE_CHECKING
from core.config import Config
from core.exceptions import CollectionNotFound, CollectionsNotSupported, MovieNotFound, ShowNotFound, PlexConnectorException, PlexLookupError
from core.enums import ScraperSource, MediaType
from core.exceptions import ScraperException
from core.constants import ARTWORK_ID_MAP, ARTWORK_TYPE_MAP, ARTWORK_FILENAME_MAP
from models.options import Options
from plex.plex_connector import PlexConnector
from plex.plex_uploader import PlexUploader
from plexapi.exceptions import NotFound
from kometa.kometa_saver import KometaSaver
from utils import soup_utils
from utils.utils import is_numeric, get_path_parts, normalize_path
from models.artwork_types import MovieArtwork, TVArtwork, CollectionArtwork
from core import globals
from utils.notifications import debug_me

if TYPE_CHECKING:
    from services.arr_service import ArrService

class UploadProcessor:

    def __init__(self, plex: PlexConnector, arr: Optional["ArrService"] = None) -> None:
        self.plex: PlexConnector = plex
        self.options: Options = Options()
        self.config: Config = Config.at_active_path()  # Fork: follow CONFIG_PATH/--config
        self.config.load()
        self._match_confirm_cache: dict = {}
        self.artist_assets: Optional[dict] = None  # {md5(asset url): asset id} for the artist being processed
        # Fork: whether to pre-seed specials (season 0) via Sonarr even though it isn't in Plex.
        self.stage_specials: bool = globals.config.stage_specials
        # Fork: whether to pre-seed a collection's artwork into every configured movie
        # library via Kometa when the collection itself doesn't exist in Plex yet.
        self.stage_collections: bool = globals.config.stage_collections
        # Fork: Radarr/Sonarr pre-seed fallback when an item isn't in Plex yet.
        self.arr: Optional["ArrService"] = arr if arr is not None else globals.arr
        # Fork: per-run memoization. A MediUX full-series set produces one artwork entry per
        # season/episode for the same show, and a tmdb:// GUID lookup has Plex query its remote
        # metadata service, so repeating it (or Show.seasons()/Season.episodes()) per entry is
        # especially costly. Keyed on PlexConnector.state_generation so a reconnect (new server
        # and library handles, and - for the show/season/episode cache - objects whose id() could
        # otherwise collide with an unrelated new object) invalidates both caches.
        self._sonarr_series_cache: dict = {}
        self._plex_item_cache: dict = {}
        self._show_structure_cache: dict = {}
        self._plex_cache_generation = self._current_plex_state_generation()
        # Fork: options-derived state (self.kometa etc) must exist immediately after
        # construction, not only after the caller remembers to call set_options.
        self.set_options(self.options)


    def set_options(self, options: Options) -> None:
        self.options = options
        self.local_assets: bool = self.options.local_assets or globals.config.plex_local_assets
        self.kometa: bool = self.options.kometa or globals.config.save_to_kometa
        self.skip_locked: bool = self.options.skip_locked or globals.config.skip_locked_artwork
        requested_artist_updates: bool = self.options.allow_artist_updates or globals.config.allow_artist_updates
        # Artist updates read the artwork ID labels to tell our own artwork from a hand-set custom.
        # Without track_artwork_ids there are no fresh labels to read, and labels left behind by an
        # earlier tracked run would let a locked field be replaced on stale information.
        self.allow_artist_updates: bool = requested_artist_updates and self.config.track_artwork_ids
        if requested_artist_updates and not self.config.track_artwork_ids:
            debug_me("allow_artist_updates has no effect while track_artwork_ids is off - without "
                     "artwork IDs there's no way to tell artwork we applied from artwork set by "
                     "hand, so locked items are all left alone.", "UploadProcessor/set_options")
        # Fork: depend on self.kometa, which set_options above may just have changed - must be
        # recomputed here, not frozen at construction time (a per-run --kometa option is only
        # known once set_options runs). getattr guards tests that build via __new__ to skip
        # __init__'s network/config-loading side effects, leaving self.arr unset.
        arr = getattr(self, "arr", None)
        self._arr_movie_fallback: bool = bool(self.kometa and arr and arr.movie_fallback_enabled)
        self._arr_tv_fallback: bool = bool(self.kometa and arr and arr.tv_fallback_enabled)

    def _current_plex_state_generation(self) -> int:
        """Fork: return Plex's connection generation, treating lightweight test doubles with no
           state_generation attribute (or a non-int one) as zero."""
        generation = getattr(self.plex, "state_generation", 0)
        return generation if isinstance(generation, int) else 0

    def _invalidate_plex_caches_after_reconnect(self) -> None:
        """Fork: drop cached Plex objects after a reconnect replaces the server and library
           handles - a cached item from the old connection, or a show/season keyed by id(), is no
           longer valid (and a reused id() could even collide with an unrelated new object)."""
        generation = self._current_plex_state_generation()
        if generation == self._plex_cache_generation:
            return
        self._plex_item_cache.clear()
        self._show_structure_cache.clear()
        self._plex_cache_generation = generation

    def _find_in_library_cached(self, media_type, artwork):
        """Fork: looks up (and memoizes, including confirmed misses) the Plex item for this
           artwork's tmdb_id/title/year. A PlexLookupError means at least one library errored
           transiently rather than confirming a miss, so its partial result is returned but
           never cached - the next entry for the same show re-queries."""
        self._invalidate_plex_caches_after_reconnect()
        cache_key = (media_type, artwork.get("tmdb_id"), artwork.get("title"), artwork.get("year"))
        if cache_key not in self._plex_item_cache:
            try:
                self._plex_item_cache[cache_key] = self.plex.find_in_library(media_type, artwork)
            except PlexLookupError as e:
                return e.partial_result
        return self._plex_item_cache[cache_key]

    def _get_show_structure(self, tv_show) -> dict:
        """Fork: lazily caches a show's seasons/episodes by object identity, since one MediUX
           full-series set produces many artwork entries for the same show."""
        key = id(tv_show)
        structure = self._show_structure_cache.get(key)
        if structure is None:
            seasons = tv_show.seasons()
            structure = {
                "seasons": seasons,
                "season_by_index": {s.index: s for s in seasons},
                "episodes_by_season": {},
            }
            self._show_structure_cache[key] = structure
        return structure

    def _get_season_episodes(self, tv_show, season_number: int) -> Optional[list]:
        """Fork: lazily caches and returns a season's episode list, or None if the season isn't
           in Plex."""
        structure = self._get_show_structure(tv_show)
        season = structure["season_by_index"].get(season_number)
        if season is None:
            return None
        if season_number not in structure["episodes_by_season"]:
            structure["episodes_by_season"][season_number] = season.episodes()
        return structure["episodes_by_season"][season_number]

    def _get_season(self, tv_show, season_number: int):
        """Fork: returns the cached Plex Season, raising NotFound like plexapi's Show.season()
           when missing."""
        season = self._get_show_structure(tv_show)["season_by_index"].get(season_number)
        if season is None:
            raise NotFound(f"Season {season_number} not found for show '{tv_show.title}'")
        return season

    def _get_episode(self, tv_show, season_number: int, episode_number: int):
        """Fork: returns the cached Plex Episode, raising NotFound like plexapi's
           Season.episode() when missing."""
        for episode in self._get_season_episodes(tv_show, season_number) or []:
            if episode.index == episode_number:
                return episode
        raise NotFound(
            f"Episode {episode_number} of season {season_number} not found for show '{tv_show.title}'")

    def _season_exists_in_plex(self, tv_show, season_number: int) -> bool:
        """Fork: check if a season exists in the cached Plex show structure."""
        return season_number in self._get_show_structure(tv_show)["season_by_index"]

    def _episode_exists_in_plex(self, tv_show, season_number: int, episode_number: int) -> bool:
        """Fork: check if an episode exists in the cached Plex season."""
        episodes = self._get_season_episodes(tv_show, season_number)
        return episodes is not None and any(E.index == episode_number for E in episodes)

    def _find_sonarr_series(self, artwork: TVArtwork):
        """Fork: looks up (and memoizes) the Sonarr series for this artwork's title/year/tmdb_id."""
        cache_key = (artwork.get("tmdb_id"), artwork.get("title"), artwork.get("year"))
        if cache_key not in self._sonarr_series_cache:
            self._sonarr_series_cache[cache_key] = self.arr.sonarr.find_series(
                artwork.get("tmdb_id"), artwork.get("title"), artwork.get("year"))
        return self._sonarr_series_cache[cache_key]

    def _get_sonarr_seasons_if_needed(self, tv_show, artwork: TVArtwork, season_number: int) -> Optional[set]:
        """Fork: fetches Sonarr's known season numbers for this show, but only when the season is
           missing in Plex and the arr TV fallback is enabled - avoids an HTTP call on the happy
           path."""
        if not self._arr_tv_fallback or self._season_exists_in_plex(tv_show, season_number):
            return None
        arr_series = self._find_sonarr_series(artwork)
        return arr_series.season_numbers if arr_series else None

    def _resolve_tmdb_id(self, artwork, description: str, kind: Literal[MediaType.TV_SHOW, MediaType.MOVIE]) -> bool:
        """
        Resolve the TMDb ID for a TPDb artwork item before matching it to the library.

        With local_library_matching enabled, the title and year are matched against an
        in-memory Plex index first. Ambiguous matches and matches without a TMDb GUID
        fall back to fetching the poster page.

        Returns True when the artwork was matched locally - the caller then attaches a
        confirm_match hook so the poster page is still checked before anything is written.
        """
        if artwork.get("tmdb_id") or artwork.get("source") != ScraperSource.THEPOSTERDB.value or artwork.get("id") == "Upload":
            return False

        if self.config.local_library_matching and artwork.get("title"):
            self.plex._initialize_index()
            status, match = self.plex._index.lookup(artwork.get("title"), artwork.get("year"), kind)
            if status == "not_found":
                # Fork: when the arr pre-seed fallback is enabled for this kind, a local "not
                # found" isn't terminal - the TMDb ID is still needed to look the title up in
                # Radarr/Sonarr, so fall through to the poster page fetch below instead of
                # raising here.
                arr_fallback_enabled = self._arr_movie_fallback if kind == MediaType.MOVIE else self._arr_tv_fallback
                if not arr_fallback_enabled:
                    if kind == MediaType.MOVIE:
                        raise MovieNotFound(f'{description} | Movie not available on Plex')
                    raise ShowNotFound(f'{description} | Show not available on Plex')
            elif status == "matched":
                tmdb_id = match.get("tmdb_id")
                if tmdb_id is not None:
                    debug_me(f"Matched '{artwork.get('title')} ({artwork.get('year')})' locally as TMDb ID '{tmdb_id}'")
                    artwork["tmdb_id"] = tmdb_id
                    return True
                debug_me(f"Local match for '{artwork.get('title')}' has no TMDb GUID, fetching the poster page")
            else:
                debug_me(f"'{artwork.get('title')} ({artwork.get('year')})' is ambiguous locally, fetching the poster page")

        self._fetch_tmdb_id_from_tpdb(artwork, description)
        return False

    def _fetch_tmdb_id_from_tpdb(self, artwork, description: str) -> None:
        """Fetch the poster page from ThePosterDB to read its TMDb ID (data-media-id), falling back
           to a local Plex title/year search if the page doesn't expose one."""
        poster_id = artwork.get("id", None)
        poster_page_url = f"https://theposterdb.com/poster/{poster_id}"
        debug_me(f"Fetching TMDb ID from '{poster_page_url}'")
        try:
            poster_page_soup = soup_utils.cook_soup(poster_page_url)
        except ScraperException as e:
            debug_me(f"Unable to fetch TMDb ID due to error: {str(e)}")
            raise ScraperException(f"{description} | {str(e)}") from None
        try:
            artwork["tmdb_id"] = int(poster_page_soup.find('div', {"data-media-id": True})['data-media-id'])
        except (KeyError, TypeError, ValueError) as e:
            debug_me(f"Failed to extract TMDb ID from poster page, trying another way. Error was: {e}")
            _, artwork["tmdb_id"], _, _ = self.plex.movie_or_show(artwork.get("title"), artwork.get("year"))
            debug_me(f"Found TMDb ID '{artwork['tmdb_id']}' for '{artwork.get('title')}' using Plex search.")

    def _artwork_matches_item(self, artwork, plex_item, kind: Literal[MediaType.TV_SHOW, MediaType.MOVIE]) -> bool:
        """
        Called by the uploader/saver just before artwork is actually written, and only for
        locally-matched items: fetches the poster page once (cached per poster id) and checks the
        TPDb media id against the matched Plex item's guids, so a title-and-year match can never
        write another title's artwork. Skips, locked items and items not in the library never
        trigger this request.
        """
        cache_key = (kind, artwork.get("title"), artwork.get("year"), artwork.get("tmdb_id"), artwork.get("id"))
        cached = self._match_confirm_cache.get(cache_key)
        if cached is not None:
            return cached
        poster_page_url = f"https://theposterdb.com/poster/{artwork.get('id')}"
        debug_me(f"Confirming local match for '{artwork.get('title')}' from '{poster_page_url}'")
        try:
            poster_page_soup = soup_utils.cook_soup(poster_page_url)
        except ScraperException:
            raise
        try:
            tpdb_tmdb_id = int(poster_page_soup.find('div', {"data-media-id": True})['data-media-id'])
            matches = any(guid.id == f"tmdb://{tpdb_tmdb_id}" for guid in plex_item.guids)
        except (KeyError, TypeError, ValueError):
            matches = True  # No media id on the page - trust the local title and year match, same as the existing Plex-search fallback
        self._match_confirm_cache[cache_key] = matches
        return matches

    def process_collection_artwork(self, artwork: CollectionArtwork) -> Optional[str]:

        result = None
        results = []
        description = f"{artwork['title']} • {artwork['author']}"
        artwork_type = ARTWORK_TYPE_MAP.get(artwork.get('file_type'))
        artwork_id = ARTWORK_ID_MAP.get(artwork.get('file_type'))

        if self.local_assets:
            raise CollectionsNotSupported(f'{description} | {artwork_type} not processed (not supported in Plex local assets mode)')
        
        try:
            collection_items, libraries = self.plex.find_collection(artwork['title'])
            if not collection_items:
                collection_items, libraries = self.plex.find_collection(artwork['title'], fuzzy=True)

        except PlexConnectorException as e:
            raise PlexConnectorException(f"Error searching Plex for {artwork['title']}")
        except Exception as e:
            raise Exception from e

        if collection_items:
            debug_me(f"Found collection '{artwork['title']}' in {len(libraries)} libraries.")
            for collection_item, library in zip(collection_items, libraries):
                if self.kometa:
                    asset_folder = collection_item.title.replace("/", "").replace(":", "")
                    saver = KometaSaver(artwork_type, library)
                    saver.download_timeout = self.config.kometa_download_timeout
                    saver.retry_attempts = self.config.upload_retry_attempts
                    saver.retry_backoff = self.config.upload_retry_backoff_seconds
                    saver.set_artwork(artwork)
                    base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, "temp_dir" if self.options.temp else "kometa_base", None)
                    # Fork: kometa_library_paths maps a Plex library name to a different Kometa directory name.
                    saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(library), asset_folder)
                    debug_me(f"Destination directory is {saver.dest_dir}")
                    saver.dest_file_name = ARTWORK_FILENAME_MAP.get(artwork.get('file_type'), 'poster')
                    saver.dest_file_ext = ".jpg"
                    saver.set_description(description)
                    saver.set_options(self.options)
                    result = saver.save_to_kometa()
                    results.append(result)
                else:
                    uploader = PlexUploader(collection_item, artwork_type, artwork_id)
                    uploader.set_artwork(artwork)
                    uploader.track_artwork_ids = self.config.track_artwork_ids
                    uploader.reset_overlay = self.config.reset_overlay
                    uploader.skip_locked = self.skip_locked
                    uploader.allow_artist_updates = self.allow_artist_updates
                    uploader.artist_assets = self.artist_assets
                    uploader.retry_attempts = self.config.upload_retry_attempts
                    uploader.retry_backoff = self.config.upload_retry_backoff_seconds
                    uploader.set_description(description)
                    uploader.set_options(self.options)
                    result = uploader.upload_to_plex()
                    results.append(result)
        elif self.kometa and self.stage_collections:
            # Fork: the collection isn't in Plex yet, but Kometa staging is enabled - save its
            # artwork into every configured movie library so it's ready once Plex (or Kometa
            # itself) creates the collection.
            asset_folder = artwork["title"].replace("/", "").replace(":", "")
            movie_libraries = self.config.movie_library if isinstance(self.config.movie_library, list) else [self.config.movie_library]
            for library in movie_libraries:
                saver = KometaSaver(artwork_type, library)
                saver.download_timeout = self.config.kometa_download_timeout
                saver.retry_attempts = self.config.upload_retry_attempts
                saver.retry_backoff = self.config.upload_retry_backoff_seconds
                saver.set_artwork(artwork)
                base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, "temp_dir" if self.options.temp else "kometa_base", None)
                saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(library), asset_folder)
                debug_me(f"Destination directory is {saver.dest_dir}")
                saver.dest_file_name = ARTWORK_FILENAME_MAP.get(artwork.get('file_type'), 'poster')
                saver.dest_file_ext = ".jpg"
                saver.set_description(description)
                saver.set_options(self.options)
                result = saver.save_to_kometa()
                results.append(result)
        else:
            raise CollectionNotFound(f'{description} | {artwork_type} not processed (Collection not available on Plex)')
        return results

    def process_movie_artwork(self, artwork: MovieArtwork) -> Optional[str]:

        artwork['year'] = self.options.year if self.options.year else artwork['year']
        
        result = None
        results = []
        description = f"{artwork['title']} ({artwork['year']}) • {artwork['author']}"
        artwork_type = ARTWORK_TYPE_MAP.get(artwork.get('file_type'))
        artwork_id = ARTWORK_ID_MAP.get(artwork.get('file_type'))

        # Since the TPDb scraper doesn't fetch the TMDb ID up front for each poster, we resolve it here
        locally_matched = self._resolve_tmdb_id(artwork, description, MediaType.MOVIE)

        try:
            movie_items, libraries = self._find_in_library_cached(MediaType.MOVIE, artwork)
        except PlexConnectorException as e:
            raise PlexConnectorException(str(e))
        except Exception as e:
            raise Exception from e

        if movie_items:
            debug_me(f"Found TMDb ID '{artwork.get('tmdb_id')}' in {len(libraries)} libraries.")
            for movie_item, library in zip(movie_items, libraries):
                # Use the actual movie title from Plex in case it differs from the artwork title (if it's a foreign title, etc.)
                desc = description.replace(artwork["title"], movie_item.title) if movie_item.title != artwork["title"] else description
                item_path = normalize_path(movie_item.media[0].parts[0].file)
                path_parts = []
                path_parts = get_path_parts(item_path)

                if self.local_assets or self.kometa:

                    saver = KometaSaver(artwork_type, library)
                    saver.download_timeout = self.config.kometa_download_timeout
                    saver.retry_attempts = self.config.upload_retry_attempts
                    saver.retry_backoff = self.config.upload_retry_backoff_seconds
                    saver.set_artwork(artwork)

                    if self.local_assets:
                        movie_path = os.path.dirname(item_path)

                        normalized_mappings = [{
                            "plex_path": normalize_path(mapping.get("plex_path", "")),
                            "app_path": normalize_path(mapping.get("app_path", ""))
                        } for mapping in globals.config.path_mappings]
                        
                        remapped = False
                        for mapping in normalized_mappings:
                            if mapping["plex_path"] in movie_path:
                                debug_me(f"Replacing {mapping["plex_path"]} for {mapping["app_path"]}")
                                movie_path = movie_path.replace(mapping["plex_path"], mapping["app_path"])
                                remapped = True

                        if not os.path.exists(movie_path):
                            debug_me(f"ERROR: Path '{movie_path}' does not exist")
                            results.append(f"❌ {desc} | Error saving asset: {movie_path} {f'({os.path.dirname(item_path)}) ' if remapped else ''}does not exist")
                            continue

                        saver.dest_dir = movie_path
                    
                    elif self.kometa:
                        asset_folder = path_parts[-2]
                        base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, "temp_dir" if self.options.temp else "kometa_base", None)
                        # Fork: kometa_library_paths maps a Plex library name to a different Kometa directory name.
                        saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(library), asset_folder)

                    debug_me(f"Destination directory is {saver.dest_dir}")
                    saver.dest_file_name = ARTWORK_FILENAME_MAP.get(artwork.get('file_type'), 'poster')
                    saver.dest_file_ext = ".jpg"
                    saver.set_description(desc)
                    saver.set_options(self.options)
                    if locally_matched:
                        saver.confirm_match = lambda a=artwork, item=movie_item: self._artwork_matches_item(a, item, "movie")
                    result = saver.save_to_kometa()
                    if self.local_assets and (result.startswith("✅") or result.startswith("♻️")):
                        movie_item.refresh()
                    results.append(result)
                else:
                    uploader = PlexUploader(movie_item, artwork_type, artwork_id)
                    uploader.set_artwork(artwork)
                    uploader.track_artwork_ids = self.config.track_artwork_ids
                    uploader.reset_overlay = self.config.reset_overlay
                    uploader.skip_locked = self.skip_locked
                    uploader.allow_artist_updates = self.allow_artist_updates
                    uploader.artist_assets = self.artist_assets
                    uploader.retry_attempts = self.config.upload_retry_attempts
                    uploader.retry_backoff = self.config.upload_retry_backoff_seconds
                    if locally_matched:
                        uploader.confirm_match = lambda a=artwork, item=movie_item: self._artwork_matches_item(a, item, "movie")
                    uploader.set_description(desc)
                    uploader.set_options(self.options)
                    result = uploader.upload_to_plex()
                    results.append(result)
        elif self._arr_movie_fallback:
            # Fork: the movie isn't in Plex yet, but Radarr already knows its folder - pre-seed
            # the Kometa asset directory so the artwork is there the moment Plex imports it.
            arr_movie = self.arr.radarr.find_movie(artwork.get("tmdb_id"), artwork["title"], artwork["year"])
            if not arr_movie:
                raise MovieNotFound(f"{description} | Movie not available on Plex or Radarr")
            arr_library = self.config.resolve_arr_library(arr_movie.root_folder_path, "movie")
            saver = KometaSaver(artwork_type, arr_library)
            saver.download_timeout = self.config.kometa_download_timeout
            saver.retry_attempts = self.config.upload_retry_attempts
            saver.retry_backoff = self.config.upload_retry_backoff_seconds
            saver.set_artwork(artwork)
            base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, "temp_dir" if self.options.temp else "kometa_base", None)
            # Fork: kometa_library_paths maps a Plex library name to a different Kometa directory name.
            saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(arr_library), arr_movie.folder_name)
            debug_me(f"Destination directory is {saver.dest_dir}")
            saver.dest_file_name = ARTWORK_FILENAME_MAP.get(artwork.get('file_type'), 'poster')
            saver.dest_file_ext = ".jpg"
            saver.set_description(f"{description} • pre-seeded via Radarr")
            saver.set_options(self.options)
            result = saver.save_to_kometa()
            results.append(result)
        else:
            raise MovieNotFound(f'{description} | {artwork_type} not processed (Movie not available on Plex)')
        return results


    def process_tv_artwork(self, artwork: TVArtwork) -> Optional[str]:

        description = "Target media"
        upload_target = None
        artwork_type = None
        artwork_id = None
        result = None
        results = []
        self.staging: bool = self.kometa and (globals.config.stage_assets or self.options.stage)

        season = artwork.get('season')
        if is_numeric(season) and season == 0:
            season = "Specials"
        elif season:
            season = f"Season {artwork['season']:02}"
#
        if artwork['season'] is None and artwork['episode'] is None:
            raise ShowNotFound(f"{artwork['title']} ({artwork['year']}) • {artwork['author']} | Not available on Plex")
        elif is_numeric(artwork['season']) and is_numeric(artwork['episode']):
            description = f"{artwork['title']} ({artwork['year']}) • {artwork['author']} • {season} • Episode {artwork['episode']:02}"
        elif (artwork['episode'] is None or artwork['episode'] == "Cover") and is_numeric(artwork['season']):
            description = f"{artwork['title']} ({artwork['year']}) • {artwork['author']} • {season}"
        elif artwork['season'] is None or artwork['season'] == "Cover" or artwork['season'] == "Backdrop" or artwork['season'].startswith("SquareArt"):
            description = f"{artwork['title']} ({artwork['year']}) • {artwork['author']}"

        artwork['year'] = self.options.year if self.options.year else artwork['year']
        artwork_type = ARTWORK_TYPE_MAP.get(artwork.get('file_type'))
        
        # Since the TPDb scraper doesn't fetch the TMDb ID up front for each poster, we resolve it here
        locally_matched = self._resolve_tmdb_id(artwork, description, MediaType.TV_SHOW)

        try:
            tv_show_items, libraries = self._find_in_library_cached(MediaType.TV_SHOW, artwork)
        except PlexConnectorException as e:
            raise PlexConnectorException(str(e))
        except Exception as e:
            raise Exception from e

        if not tv_show_items:
            if self._arr_tv_fallback:
                # Fork: the show isn't in Plex yet, but Sonarr already knows its folder - pre-seed
                # the Kometa asset directory so the artwork is there the moment Plex imports it.
                return self._preseed_tv_artwork(artwork, description, season, artwork_type)
            raise ShowNotFound(f"{description} | {artwork_type} not processed (Show not available on Plex)")

        debug_me(f"Found TMDb ID '{artwork.get('tmdb_id')}' in {len(libraries)} libraries.")
        for tv_show, library in zip(tv_show_items, libraries):
            # Use the actual TV show title from Plex in case it differs from the artwork title (if it's a foreign title, etc.)
            desc = description.replace(artwork['title'], tv_show.title.split(' (')[0]) if tv_show.title.split(' (')[0] != artwork['title'] else description
            # Use the year from Plex if it differs
            desc = desc.replace(f"({artwork['year']})", f"({tv_show.year})") if tv_show.year and artwork['year'] != tv_show.year else desc
            first_season = self._get_show_structure(tv_show)["seasons"][0]
            item_path = normalize_path(self._get_season_episodes(tv_show, first_season.index)[0].media[0].parts[0].file)
            path_parts = []
            path_parts = get_path_parts(item_path)
            has_season_folders = path_parts[-2].lower().startswith("season") or path_parts[-2].lower().startswith("specials")
            asset_folder = path_parts[-3] if has_season_folders else path_parts[-2]
            show_path = os.path.dirname(os.path.dirname(item_path)) if has_season_folders else os.path.dirname(item_path)

            try:
                if isinstance(artwork['season'], str):
                    # This is a show-level asset, it could be a show cover, background or square art
                    final_path = show_path
                    if artwork['season'] == "Cover":
                        upload_target = tv_show
                        file_name = "poster"
                    elif artwork['season'] == "Backdrop":
                        upload_target = tv_show
                        file_name = "background"
                    elif "SquareArt" in artwork['season']:
                        sq = artwork['season'].split("_")[-1]
                        upload_target = tv_show
                        if sq == "0":
                            # For the first square art asset processed, we set the upload target (for Plex uploads)
                            # and the file_name to 'square.ext' for Kometa asset directory
                            file_name = "square"
                        elif self.kometa:
                            # If there's more than one square art asset in the set and we're saving to Kometa asset directory,
                            # we save the additional assets as 'square_alt_#.ext' so the user can rename the one they want to use
                            file_name = f"square_alt_{sq}"
                        else:
                            # If we're applying directly to a Plex server, only process the first one and ignore the rest
                            result = f"⚠️ {desc} | Ignoring additional square art asset"
                            results.append(result)
                            continue
                elif is_numeric(artwork['season']):
                    # This is either a season asset (season cover) or an episode asset (title card)
                    if artwork['season'] >= 0:
                        is_season_available = self._season_exists_in_plex(tv_show, artwork['season'])
                        # Fork: only consult Sonarr when the season is missing in Plex and the arr
                        # TV fallback is enabled - a season Sonarr knows about but Plex hasn't
                        # imported yet is still processed (in Kometa mode) rather than skipped.
                        sonarr_seasons = self._get_sonarr_seasons_if_needed(tv_show, artwork, artwork['season'])
                        # Fork: Sonarr knowledge alone must not bypass stage_specials for season 0 -
                        # the show may lack season 0 in both Plex and the staged state, so the
                        # "stage specials" toggle still governs whether Specials are eligible here.
                        via_sonarr = (
                            sonarr_seasons is not None
                            and artwork['season'] in sonarr_seasons
                            and (season != "Specials" or self.stage_specials)
                        )

                        if self.local_assets and is_season_available:
                            item_path = normalize_path(self._get_season_episodes(tv_show, artwork['season'])[0].media[0].parts[0].file)
                            season_folder = os.path.basename(os.path.dirname(item_path)) if has_season_folders else ""
                            final_path = os.path.join(show_path, season_folder) if has_season_folders else show_path

                        if artwork['episode'] == "Cover" or artwork['episode'] is None:
                            # This is a season cover
                            # Fork: stage specials (season 0) in Kometa mode even when Sonarr
                            # hasn't reported them yet.
                            if is_season_available or (self.staging and season != "Specials") or via_sonarr or (self.kometa and self.stage_specials and season == "Specials"):
                                debug_me(f"Staging is {'enabled' if self.staging else 'disabled'}")
                                file_name = f"Season{artwork['season']:02}"

                                if artwork['season'] == 0 and self.local_assets:
                                    file_name = "season-specials-poster"

                                if not self.kometa:
                                    upload_target = self._get_season(tv_show, artwork['season'])
                                if via_sonarr and not is_season_available:
                                    desc = f"{desc} • pre-seeded via Sonarr"
                            else:
                                result = f"⚠️ {desc} | {season} not available in {library}"
                                results.append(result)
                                continue
                        elif is_numeric(artwork['episode']) and artwork['episode'] >= 0:
                            # This is a title card
                            # Fork: season gate mirrors the season-cover check above - stage
                            # specials (season 0) in Kometa mode even without Sonarr.
                            if is_season_available or (self.staging and season != "Specials") or via_sonarr or (self.kometa and self.stage_specials and season == "Specials"):
                                is_episode_available = self._episode_exists_in_plex(tv_show, artwork['season'], artwork['episode'])

                                if (is_season_available and is_episode_available) or self.staging or via_sonarr:
                                    file_name = f"S{artwork['season']:02}E{artwork['episode']:02}"
                                    if self.local_assets and is_episode_available:
                                        # To save as local asset the filename has to match the episode filename
                                        ep_path = self._get_episode(tv_show, artwork['season'], artwork['episode']).media[0].parts[0].file
                                        file_name = os.path.splitext(get_path_parts(ep_path)[-1])[0]
                                    if not self.kometa:
                                        upload_target = self._get_episode(tv_show, artwork['season'], artwork['episode'])
                                    if via_sonarr and not (is_season_available and is_episode_available):
                                        desc = f"{desc} • pre-seeded via Sonarr"
                                else:
                                    result = f"⚠️ {desc} | {season}, Episode {artwork['episode']:02} not available in {library}"
                                    results.append(result)
                                    continue
                            else:
                                result = f"⚠️ {desc} | {season} not available in {library}"
                                results.append(result)
                                continue

            except (AttributeError, KeyError, NotFound) as e:
                raise ShowNotFound(f"{desc} | Not available on Plex in {library}: {e}") from e
                
            try:
                if self.local_assets or self.kometa:
                    saver = KometaSaver(artwork_type, library)
                    saver.download_timeout = self.config.kometa_download_timeout
                    saver.retry_attempts = self.config.upload_retry_attempts
                    saver.retry_backoff = self.config.upload_retry_backoff_seconds
                    saver.set_artwork(artwork)

                    if self.local_assets and upload_target:
                        normalized_mappings = [{
                            "plex_path": normalize_path(mapping.get("plex_path", "")),
                            "app_path": normalize_path(mapping.get("app_path", ""))
                        } for mapping in globals.config.path_mappings]

                        remapped = False
                        for mapping in normalized_mappings:
                            if mapping["plex_path"] in final_path:
                                debug_me(f"Replacing {mapping["plex_path"]} for {mapping["app_path"]}")
                                final_path = final_path.replace(mapping["plex_path"], mapping["app_path"])
                                remapped = True

                        if not os.path.exists(final_path):
                            debug_me(f"ERROR: Path '{final_path}' does not exist")
                            results.append(f"❌ {desc} | Error saving asset: {final_path} {f'({os.path.dirname(item_path)}) ' if remapped else ''}does not exist")
                            continue

                        saver.dest_dir = final_path

                    elif self.kometa:
                        base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, 'temp_dir' if self.options.temp else 'kometa_base', None)
                        # Fork: kometa_library_paths maps a Plex library name to a different Kometa directory name.
                        saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(library), asset_folder)

                    debug_me(f"Destination directory is {saver.dest_dir}")
                    saver.dest_file_name = file_name
                    saver.dest_file_ext = ".jpg"
                    saver.set_description(desc)
                    saver.set_options(self.options)

                    if locally_matched:
                        saver.confirm_match = lambda a=artwork, item=tv_show: self._artwork_matches_item(a, item, "tv")
                    result = saver.save_to_kometa()

                    if self.local_assets and upload_target and (result.startswith("✅") or result.startswith("♻️")):
                        upload_target.refresh()

                    results.append(result)

                elif upload_target:
                    artwork_id = ARTWORK_ID_MAP.get(artwork.get('file_type'))
                    uploader = PlexUploader(upload_target, artwork_type, artwork_id)
                    uploader.set_artwork(artwork)
                    uploader.track_artwork_ids = self.config.track_artwork_ids
                    uploader.reset_overlay = self.config.reset_overlay
                    uploader.skip_locked = self.skip_locked
                    uploader.allow_artist_updates = self.allow_artist_updates
                    uploader.artist_assets = self.artist_assets
                    uploader.retry_attempts = self.config.upload_retry_attempts
                    uploader.retry_backoff = self.config.upload_retry_backoff_seconds
                    if locally_matched:
                        uploader.confirm_match = lambda a=artwork, item=tv_show: self._artwork_matches_item(a, item, "tv")
                    uploader.set_description(desc)
                    uploader.set_options(self.options)
                    result = uploader.upload_to_plex()
                    results.append(result)
            except Exception:
                raise

        return results

    def _preseed_tv_artwork(self, artwork: TVArtwork, description: str, season: str, artwork_type: str) -> list:
        """Fork: pre-seeds Kometa artwork for a show that isn't in Plex yet, using Sonarr to
           locate the show's asset folder and confirm which seasons it knows about."""
        results = []

        arr_series = self._find_sonarr_series(artwork)
        if not arr_series:
            raise ShowNotFound(f"{description} | Show not available on Plex or Sonarr")

        if isinstance(artwork['season'], str):
            if artwork['season'] == "Cover":
                file_name = "poster"
            elif artwork['season'] == "Backdrop":
                file_name = "background"
            elif "SquareArt" in artwork['season']:
                sq = artwork['season'].split("_")[-1]
                file_name = "square" if sq == "0" else f"square_alt_{sq}"
            else:
                file_name = "poster"
        else:
            is_specials = (season == "Specials")
            if artwork['season'] not in arr_series.season_numbers or (is_specials and not self.stage_specials):
                results.append(f"⚠️ {description} | {season} not known to Sonarr")
                return results
            if artwork['episode'] == "Cover" or artwork['episode'] is None:
                file_name = f"Season{artwork['season']:02}"
            elif is_numeric(artwork['episode']):
                file_name = f"S{artwork['season']:02}E{artwork['episode']:02}"
            else:
                file_name = f"Season{artwork['season']:02}"

        library = self.config.resolve_arr_library(arr_series.root_folder_path, "tv")
        saver = KometaSaver(artwork_type, library)
        saver.download_timeout = self.config.kometa_download_timeout
        saver.retry_attempts = self.config.upload_retry_attempts
        saver.retry_backoff = self.config.upload_retry_backoff_seconds
        saver.set_artwork(artwork)
        base_dir = ("/temp" if self.options.temp else "/assets") if globals.docker else getattr(globals.config, "temp_dir" if self.options.temp else "kometa_base", None)
        # Fork: kometa_library_paths maps a Plex library name to a different Kometa directory name.
        saver.dest_dir = os.path.join(base_dir, globals.config.resolve_library_directory(library), arr_series.folder_name)
        debug_me(f"Destination directory is {saver.dest_dir}")
        saver.dest_file_name = file_name
        saver.dest_file_ext = ".jpg"
        saver.set_description(f"{description} • pre-seeded via Sonarr")
        saver.set_options(self.options)
        result = saver.save_to_kometa()
        results.append(result)
        return results

