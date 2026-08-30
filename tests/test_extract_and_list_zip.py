"""
Characterization tests for web_routes.extract_and_list_zip.

These exercise the real ZIP-extraction pipeline end-to-end: real ZIP files on
disk, injected orientation/sort callables, and a stub Plex connector.
"""

import os
import re
import zipfile

import pytest

import web_routes
from core import globals
from core.config import Config
from models.instance import Instance

pytestmark = pytest.mark.unit

FILENAME_PATTERN = re.compile(r'^[^/]+(?:\.jpg|\.jpeg|\.png)$', re.IGNORECASE)


class StubPlexConnector:
    """Stub for globals.plex; movie_or_show responses are queued per-call."""

    def __init__(self, responses_by_title=None, default=(None, None, None, None)):
        self.responses_by_title = responses_by_title or {}
        self.default = default
        self.calls = []

    def movie_or_show(self, title, year=None):
        self.calls.append((title, year))
        return self.responses_by_title.get(title, self.default)


@pytest.fixture(autouse=True)
def _reset_globals():
    globals.config = Config()
    globals.debug = False
    original_plex = globals.plex
    yield
    globals.plex = original_plex


def _make_zip(tmp_path, name, files):
    """Create a ZIP at tmp_path/name containing {filename: bytes} entries."""
    zip_path = os.path.join(tmp_path, name)
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    return zip_path


def _extract(tmp_path, zip_name, files, plex_stub=None, filters=None, plex_title=None, plex_year=None,
             orientation="portrait"):
    zip_path = _make_zip(tmp_path, zip_name, files)
    globals.plex = plex_stub or StubPlexConnector()
    instance = Instance(id=None, mode="cli")
    return web_routes.extract_and_list_zip(
        instance,
        zip_path,
        FILENAME_PATTERN,
        filters or [],
        plex_title,
        plex_year,
        check_image_orientation_func=lambda path: orientation,
        sort_key_func=lambda item: (item.get("title") or "", item.get("season") or 0),
    )


class TestSourceDetection:
    def test_tpdb_filename_pattern_sets_source_and_title_author(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 42, "Some Movie", 2020))
        file_list, skipped, zip_title, zip_author, zip_source = _extract(
            tmp_path, "Some Movie set by CoolAuthor - abc123.zip",
            {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub,
        )
        assert zip_source == "theposterdb"
        assert zip_title == "Some Movie"
        assert zip_author == "CoolAuthor"
        assert len(file_list) == 1
        assert file_list[0]["media"] == "Movie"
        assert file_list[0]["tmdb_id"] == 42

    def test_mediux_source_txt_overrides_title_and_author(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, skipped, zip_title, zip_author, zip_source = _extract(
            tmp_path, "random_export.zip",
            {
                "source.txt": b"Title: Some Movie\nAuthor: MediuxUser\n",
                "Some Movie (2020).jpg": b"fake",
            },
            plex_stub=plex_stub,
        )
        assert zip_source == "mediux"
        assert zip_title == "Some Movie"
        assert zip_author == "MediuxUser"
        assert len(file_list) == 1


class TestPlexTitleResolutionCascade:
    def test_direct_match_used_when_available(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"}, plex_stub=plex_stub,
        )
        assert file_list[0]["media"] == "Movie"
        assert plex_stub.calls[0] == ("Some Movie", 2020)

    def test_underscore_colon_substitution_fallback(self, tmp_path):
        # ZIP filenames replace ':' with '_' before a space; original lookup fails,
        # the colon-substituted title should be tried next and succeed.
        plex_stub = StubPlexConnector(
            responses_by_title={"Show_ Subtitle": (None, None, None, None),
                                 "Show: Subtitle": ("TV Show", 7, "Show: Subtitle", 2019)}
        )
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Show_ Subtitle (2019).jpg": b"fake"}, plex_stub=plex_stub,
        )
        assert file_list[0]["media"] == "TV Show"
        assert file_list[0]["title"] == "Show: Subtitle"
        titles_tried = [c[0] for c in plex_stub.calls]
        assert "Show_ Subtitle" in titles_tried
        assert "Show: Subtitle" in titles_tried

    def test_accent_folding_fallback(self, tmp_path):
        # Simulate a ZIP filename that stripped the accent; only the accented
        # original matches in Plex.
        plex_stub = StubPlexConnector(
            responses_by_title={"Pokemon": (None, None, None, None),
                                 "Pokémon": ("TV Show", 99, "Pokémon", 1998)}
        )
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Pokemon (1998).jpg": b"fake"}, plex_stub=plex_stub,
        )
        # NFKD-fold of "Pokemon" == "Pokemon" (no accent to strip), so this
        # particular fallback can't fire for an already-unaccented candidate;
        # confirms the direct lookup miss falls through without a false match.
        assert file_list[0]["media"] == "unavailable"

    def test_progressive_title_shortening_fallback(self, tmp_path):
        plex_stub = StubPlexConnector(
            responses_by_title={
                "Worlds End Extended Cut": (None, None, None, None),
                "Worlds End Extended": (None, None, None, None),
                "Worlds End": ("Movie", 5, "World's End", 2013),
            }
        )
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Worlds End Extended Cut (2013).jpg": b"fake"}, plex_stub=plex_stub,
        )
        assert file_list[0]["media"] == "Movie"
        assert file_list[0]["title"] == "World's End"

    def test_no_match_marks_unavailable(self, tmp_path):
        plex_stub = StubPlexConnector(default=(None, None, None, None))
        file_list, skipped, *_ = _extract(
            tmp_path, "export.zip", {"Totally Unknown Movie (2020).jpg": b"fake"}, plex_stub=plex_stub,
        )
        assert file_list[0]["media"] == "unavailable"

    def test_collection_skips_plex_lookup_entirely(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "wrong", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Saga Collection.jpg": b"fake"}, plex_stub=plex_stub,
        )
        assert file_list[0]["media"] == "Collection"
        assert plex_stub.calls == []


class TestOrientationReclassification:
    def test_tv_show_cover_flips_to_backdrop_on_landscape(self, tmp_path):
        plex_stub = StubPlexConnector(default=("TV Show", 1, "Some Show", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Show (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="landscape",
        )
        assert file_list[0]["season"] == "Backdrop"
        assert file_list[0]["type"] == "background"

    def test_tv_show_cover_stays_cover_on_portrait(self, tmp_path):
        plex_stub = StubPlexConnector(default=("TV Show", 1, "Some Show", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Show (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait",
        )
        assert file_list[0]["season"] == "Cover"
        assert file_list[0]["type"] == "show_cover"

    def test_movie_landscape_becomes_background(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="landscape",
        )
        assert file_list[0]["type"] == "background"

    def test_movie_portrait_becomes_poster(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait",
        )
        assert file_list[0]["type"] == "movie_poster"

    def test_unavailable_season_cover_becomes_tv_show(self, tmp_path):
        plex_stub = StubPlexConnector(default=(None, None, None, None))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Show - Season 2.jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait",
        )
        assert file_list[0]["media"] == "TV Show"
        assert file_list[0]["season"] == 2

    def test_unavailable_non_season_cover_becomes_poster_type(self, tmp_path):
        plex_stub = StubPlexConnector(default=(None, None, None, None))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Totally Unknown Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait",
        )
        assert file_list[0]["type"] == "poster"


class TestFilters:
    def test_filtered_file_excluded_from_list_and_counted(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, skipped, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait", filters=["background"],
        )
        assert file_list == []
        assert skipped == 1

    def test_no_filters_includes_everything(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, skipped, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, orientation="portrait", filters=[],
        )
        assert len(file_list) == 1
        assert skipped == 0


class TestPlexTitleYearOverride:
    def test_plex_title_and_year_override_parsed_values(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Override Title", 1999))
        file_list, *_ = _extract(
            tmp_path, "export.zip", {"Some Movie (2020).jpg": b"fake"},
            plex_stub=plex_stub, plex_title="Override Title", plex_year=1999,
        )
        assert file_list[0]["title"] == "Override Title"
        assert file_list[0]["year"] == 1999
        assert plex_stub.calls[0] == ("Override Title", 1999)


class TestNonMatchingFilesIgnored:
    def test_hidden_and_macosx_files_skipped(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip",
            {
                "Some Movie (2020).jpg": b"fake",
                "__MACOSX/._Some Movie (2020).jpg": b"junk",
                ".DS_Store": b"junk",
            },
            plex_stub=plex_stub,
        )
        assert len(file_list) == 1

    def test_non_matching_extension_ignored(self, tmp_path):
        plex_stub = StubPlexConnector(default=("Movie", 1, "Some Movie", 2020))
        file_list, *_ = _extract(
            tmp_path, "export.zip",
            {"Some Movie (2020).jpg": b"fake", "readme.txt": b"notes"},
            plex_stub=plex_stub,
        )
        assert len(file_list) == 1
