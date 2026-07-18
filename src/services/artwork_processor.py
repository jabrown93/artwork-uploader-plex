"""
Service for coordinating artwork scraping and uploading.

This service handles the business logic of scraping artwork and processing
it for upload to Plex, separating it from UI/notification concerns.
"""

import os
from dataclasses import dataclass
from typing import Callable, Optional

from core.exceptions import (
    CollectionNotFound,
    MovieNotFound,
    NotProcessedByExclusion,
    NotProcessedByFilter,
    PlexConnectorException,
    ScraperException,
    ShowNotFound,
)
from models.options import Options
from plex.plex_connector import PlexConnector
from processors.upload_processor import UploadProcessor
from scrapers.scraper import Scraper


@dataclass
class ProcessingCallbacks:
    """
    Callbacks for UI updates during artwork processing.

    All callbacks are optional and called with appropriate arguments
    when processing events occur.
    """
    on_status_update: Optional[Callable[[str, str, bool, bool], None]] = None  # (message, color, spinner, sticky)
    on_log_update: Optional[Callable[[str], None]] = None  # (message)
    on_progress_update: Optional[Callable[[int, int], None]] = None  # (current, total) - for progress bars
    on_debug: Optional[Callable[[str, str], None]] = None  # (message, context) - for debug messages
    success_counter: Optional[list] = None  # Mutable list to track successful uploads (contains count as single element)
    assets_processed: Optional[list] = None  # Mutable list to track total assets processed (contains count as single element)


class ArtworkProcessor:
    """Coordinates scraping and uploading of artwork."""

    def __init__(self, plex: PlexConnector) -> None:
        self.plex = plex

    @staticmethod
    def _emit_debug(callbacks: Optional[ProcessingCallbacks], message: str,
                     context: str = "process_uploaded_artwork") -> None:
        if callbacks and callbacks.on_debug:
            callbacks.on_debug(message, context)

    @staticmethod
    def _emit_log(callbacks: Optional[ProcessingCallbacks], message: str) -> None:
        if callbacks and callbacks.on_log_update:
            callbacks.on_log_update(message)

    @staticmethod
    def _emit_progress(callbacks: Optional[ProcessingCallbacks], current: int, total: int) -> None:
        if callbacks and callbacks.on_progress_update:
            callbacks.on_progress_update(current, total)

    @staticmethod
    def _emit_status(callbacks: Optional[ProcessingCallbacks], message: str, color: str,
                      spinner: bool, sticky: bool) -> None:
        if callbacks and callbacks.on_status_update:
            callbacks.on_status_update(message, color, spinner, sticky)

    @classmethod
    def _cleanup_temp_artwork_file(cls, artwork: dict, callbacks: Optional[ProcessingCallbacks]) -> None:
        """Delete an uploaded artwork's temp file and its containing temp directory, if now empty."""
        path = artwork['path']
        try:
            os.remove(path)
            cls._emit_debug(callbacks, f"Deleted temporary file: {path}")
        except OSError as e:
            cls._emit_debug(callbacks, f"Failed to delete temporary file: {path} - {str(e)}")
        try:
            os.rmdir(os.path.dirname(path))
            cls._emit_debug(callbacks, f"Deleted temporary directory: {os.path.dirname(path)}")
        except OSError as e:
            cls._emit_debug(callbacks, f"Error deleting temporary directory: {os.path.dirname(path)} - {str(e)}")

    def scrape_and_process(
        self,
        url: str,
        options: Options,
        callbacks: Optional[ProcessingCallbacks] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Optional[str]:
        """
        Scrape artwork from a URL and process it for upload to Plex.

        Args:
            url: URL to scrape
            options: Processing options
            callbacks: Optional callbacks for UI updates
            progress_callback: Optional callback for sub-item scrape progress (current, total),
                e.g. "n of N sets" while scraping a MediUX boxset. Passed through to the
                scraper layer; a no-op for scrapers/URLs that don't have sub-items to report on.

        Returns:
            Title of the scraped content, or None if no title found

        Raises:
            PlexConnectorException: If Plex connection fails
            ScraperException: If scraping fails
        """
        # Check Plex connection
        try:
            self.plex.connect()
        except PlexConnectorException as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"❌ Plex connection error: {str(e)}")
            raise PlexConnectorException(f"Plex connection error: {str(e)}") from e

        # Scrape the artwork
        scraper = Scraper(url, progress_callback=progress_callback)
        scraper.set_options(options)

        try:
            if callbacks and callbacks.on_status_update:
                if "/boxsets/" in url:
                    callbacks.on_status_update(f"Scraping Mediux Boxset from {url}, this may take a while...", "info", True, True)
                else:
                    callbacks.on_status_update(f"Scraping {url}", "info", True, True)
            scraper.scrape()
            title = scraper.title
        except ScraperException as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"❌ Scraper error: {str(e)}")
            raise ScraperException(f"Scraper error: {str(e)}") from e

        # Process the scraped artwork
        processor = UploadProcessor(self.plex)
        processor.set_options(options)

        if callbacks and callbacks.on_log_update:
            callbacks.on_log_update(f"🔍 {title} • {scraper.author} | Obtained {scraper.total} asset(s) from {f"ThePosterDB" if scraper.source == "theposterdb" else "Mediux"}")
            if scraper.skipped > 0:
                callbacks.on_log_update(f"⏩ {title} • {scraper.author} | Skipping {scraper.skipped} asset(s) based on exclusions ({scraper.exclusions}) or filters ({scraper.filtered}). Processing {scraper.total - scraper.skipped} asset(s).")

        # Update total assets processed
        if callbacks and callbacks.assets_processed is not None:
            callbacks.assets_processed[0] += scraper.total

        # Process collections
        for artwork in scraper.collection_artwork:
            self._process_single_artwork(
                artwork,
                processor.process_collection_artwork,
                callbacks
            )

        # Process movies
        for artwork in scraper.movie_artwork:
            self._process_single_artwork(
                artwork,
                processor.process_movie_artwork,
                callbacks
            )

        # Process TV shows
        for artwork in scraper.tv_artwork:
            self._process_single_artwork(
                artwork,
                processor.process_tv_artwork,
                callbacks
            )
        callbacks.on_log_update(f"✔️ {title} • {scraper.author} | {scraper.total - scraper.skipped} asset(s) processed • {callbacks.success_counter[0]} asset(s) updated")
        return title

    def _process_single_artwork(
        self,
        artwork: dict,
        process_func: Callable[[dict], str],
        callbacks: Optional[ProcessingCallbacks]
    ) -> None:
        """
        Process a single piece of artwork with error handling.

        Args:
            artwork: Artwork dictionary
            process_func: Function to process the artwork (from UploadProcessor)
            callbacks: Optional callbacks for UI updates
        """
        try:
            # Update status if callback provided
            if callbacks and callbacks.on_status_update:
                callbacks.on_status_update(
                    f'Processing artwork for {artwork["title"]}',
                    "info",
                    True,   # spinner
                    True    # sticky
                )

            # Process the artwork
            results = process_func(artwork)

            for result in results:
                # Track successful uploads (those starting with ✅ or ♻️)
                if callbacks and callbacks.success_counter is not None and (result.startswith('✅') or result.startswith('♻️')):
                    callbacks.success_counter[0] += 1

                # Log the result
                if callbacks and callbacks.on_log_update:
                    callbacks.on_log_update(result)

        except CollectionNotFound as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"⚠️ {str(e)}")

        except MovieNotFound as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"⚠️ {str(e)}")

        except ShowNotFound as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"⚠️ {str(e)}")

        except NotProcessedByExclusion as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"⏩ {str(e)}")

        except NotProcessedByFilter as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"⏩ {str(e)}")

        except Exception as e:
            if callbacks and callbacks.on_log_update:
                callbacks.on_log_update(f"❌ {str(e)}")
            if callbacks and callbacks.on_status_update:
                callbacks.on_status_update(
                    f"Error: {str(e)}",
                    "danger",
                    False,  # no spinner
                    False   # not sticky
                )

    def process_uploaded_files(
        self,
        file_list: list[dict],
        skipped: int,
        zip_title: Optional[str],
        zip_author: Optional[str],
        zip_source: Optional[str],
        options: Options,
        callbacks: Optional[ProcessingCallbacks] = None,
        override_title: Optional[str] = None
    ) -> None:
        """
        Process a list of uploaded artwork files.

        Args:
            file_list: List of artwork dictionaries with 'media', 'title', etc.
            skipped: Number of skipped assets from filtering
            zip_title: Title from the ZIP file metadata
            zip_author: Author from the ZIP file metadata
            zip_source: Source from the ZIP file metadata
            options: Processing options
            callbacks: Optional callbacks for UI updates
            override_title: Optional title to override in all files
        """
        processor = UploadProcessor(self.plex)
        processor.set_options(options)

        total_files = len(file_list)
        title = override_title if override_title else zip_title if zip_title else "Unknown"
        author = zip_author if zip_author else "Unknown"
        source = zip_source if zip_source else "Unknown"

        if total_files > 0:
            # ZIP file titles don't contain year info, even for ZIPs of single movies or TV shows
            # In that case, we try to infer the year from the first file's metadata
            # We determine if it's a single movie/TV ZIP by checking if the title of the ZIP file is part of the title of the first file
            # Otherwise we assume it's a ZIP file containing artwork for multiple shows/movies/collections and leave year as None
            year = file_list[0].get('year', 'unknown') if title in file_list[0].get('title', 'unknown') else None
            self._emit_debug(callbacks, f"Processing {total_files} files from {source} ZIP file for {title}{f' ({year})' if year else ''}")
        else:
            year = None
            self._emit_debug(callbacks, "No files to process in uploaded ZIP file")

        success_counter = 0  # Mutable counter to track successful uploads

        # Initial progress update
        self._emit_debug(callbacks, "Processing uploaded file...")
        self._emit_progress(callbacks, 0, total_files)

        self._emit_log(
            callbacks,
            f"⚙️ {title}{f' ({year})' if year else ''} • {author} | Obtained {total_files + skipped} asset(s) "
            f"from uploaded {'MediUX' if source == 'mediux' else 'TPDb'} ZIP file."
        )
        if skipped > 0:
            self._emit_log(
                callbacks,
                f"⏩ {title}{f' ({year})' if year else ''} • {author} | Skipping {skipped} asset(s) based on "
                f"filters. Processing {total_files} asset(s)."
            )

        for index, artwork in enumerate(file_list, start=1):
            self._emit_progress(callbacks, index, total_files)
            # Override title if provided
            if override_title:
                artwork['title'] = override_title

            media_type = artwork.get('media')

            if media_type == "unavailable":
                year_suffix = f" ({artwork['year']})" if artwork.get('year') else ''
                self._emit_log(callbacks, f"⚠️ {artwork['title']}{year_suffix} : {artwork['author']} | Not available on Plex.")
                self._cleanup_temp_artwork_file(artwork, callbacks)
                continue

            # Call the appropriate processor method based on media type
            if media_type == "Collection":
                process_func = processor.process_collection_artwork
            elif media_type == "Movie":
                process_func = processor.process_movie_artwork
            elif media_type == "TV Show":
                process_func = processor.process_tv_artwork
            else:
                self._emit_log(callbacks, f"❌ Unknown media type: {media_type}")
                continue

            # Build status message
            season_info = f" - Season {artwork['season']}" if artwork.get('season') else ""
            episode_info = f", Episode {artwork['episode']}" if artwork.get('episode') else ""
            status_msg = f'Processing artwork for {media_type.lower()} "{artwork["title"]}"{season_info}{episode_info}'

            self._emit_debug(callbacks, status_msg)
            self._emit_status(callbacks, status_msg, "info", True, True)  # spinner, sticky

            # Process the artwork
            try:
                results = process_func(artwork)

                for result in results:
                    # Track successful uploads (those starting with ✅ or ♻️)
                    if result.startswith('✅') or result.startswith('♻️'):
                        success_counter += 1
                    self._emit_log(callbacks, result)

            except (CollectionNotFound, MovieNotFound, ShowNotFound) as e:
                self._emit_log(callbacks, f"⚠️ {str(e)}")

            except (NotProcessedByExclusion, NotProcessedByFilter) as e:
                self._emit_log(callbacks, f"⏩ {str(e)}")

            except Exception as e:
                self._emit_log(callbacks, f"❌ Unexpected during process_uploaded_artwork: {str(e)}")
                self._emit_status(callbacks, f"Error: {str(e)}", "danger", False, False)  # no spinner, not sticky

            self._cleanup_temp_artwork_file(artwork, callbacks)

        # Final progress update
        self._emit_log(
            callbacks,
            f"✔️ {title}{f' ({year})' if year else ''} • {author} | {total_files} file(s) processed • "
            f"{success_counter} asset(s) updated."
        )
        self._emit_progress(callbacks, total_files, total_files)
