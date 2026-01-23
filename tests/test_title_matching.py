"""
Unit tests for title matching utilities.

Tests the normalization and fuzzy matching functions used to handle
punctuation differences between artwork provider titles and Plex titles.
"""

import sys
from pathlib import Path

# Add src directory to path for imports
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

from utils.title_matching import (
    normalize_title_for_matching,
    normalize_title_for_comparison,
    fuzzy_title_match
)


class TestTitleNormalizationForMatching:
    """Test title normalization for PlexAPI search (no lowercase)."""

    def test_apostrophe_removal(self):
        """Test that apostrophes are removed."""
        assert normalize_title_for_matching("Bob's Burgers") == "Bobs Burgers"
        assert normalize_title_for_matching("Ocean's Eleven") == "Oceans Eleven"

    def test_hyphen_removal(self):
        """Test that hyphens are removed."""
        assert normalize_title_for_matching("Spider-Man") == "SpiderMan"
        assert normalize_title_for_matching("X-Men") == "XMen"

    def test_case_preservation(self):
        """Test that case is preserved for PlexAPI search."""
        # Should NOT lowercase for PlexAPI search
        assert normalize_title_for_matching("The Matrix") == "The Matrix"
        assert normalize_title_for_matching("THE MATRIX") == "THE MATRIX"
        assert normalize_title_for_matching("the matrix") == "the matrix"

    def test_multiple_punctuation(self):
        """Test removal of multiple types of punctuation."""
        assert normalize_title_for_matching("S.H.I.E.L.D.") == "SHIELD"
        assert normalize_title_for_matching("M*A*S*H") == "MASH"
        assert normalize_title_for_matching("It's Always Sunny in Philadelphia") == "Its Always Sunny in Philadelphia"

    def test_colon_removal(self):
        """Test that colons are removed."""
        assert normalize_title_for_matching("Star Wars: Episode IV") == "Star Wars Episode IV"
        assert normalize_title_for_matching("Mission: Impossible") == "Mission Impossible"

    def test_ellipsis_removal(self):
        """Test that ellipses are removed (backward compatibility)."""
        assert normalize_title_for_matching("And Then There Were None...") == "And Then There Were None"
        assert normalize_title_for_matching("Wait... What?") == "Wait What"

    def test_multiple_spaces(self):
        """Test that multiple spaces are collapsed to single space."""
        assert normalize_title_for_matching("The   Matrix") == "The Matrix"
        assert normalize_title_for_matching("Star    Wars") == "Star Wars"

    def test_leading_trailing_whitespace(self):
        """Test that leading and trailing whitespace is stripped."""
        assert normalize_title_for_matching("  The Matrix  ") == "The Matrix"
        assert normalize_title_for_matching("\tBob's Burgers\n") == "Bobs Burgers"

    def test_empty_string(self):
        """Test handling of empty string."""
        assert normalize_title_for_matching("") == ""

    def test_none_handling(self):
        """Test handling of None input."""
        assert normalize_title_for_matching(None) == ""

    def test_only_punctuation(self):
        """Test titles with only punctuation."""
        assert normalize_title_for_matching("...!!!???") == ""
        assert normalize_title_for_matching("---") == ""

    def test_mixed_punctuation_and_numbers(self):
        """Test titles with numbers and punctuation."""
        assert normalize_title_for_matching("2001: A Space Odyssey") == "2001 A Space Odyssey"
        assert normalize_title_for_matching("9-1-1") == "911"

    def test_special_characters(self):
        """Test various special characters (unicode letters are preserved)."""
        assert normalize_title_for_matching("Thor: Ragnarök") == "Thor Ragnarök"
        assert normalize_title_for_matching("Amélie") == "Amélie"
        assert normalize_title_for_matching("Café") == "Café"

    def test_parentheses_and_brackets(self):
        """Test removal of parentheses and brackets."""
        assert normalize_title_for_matching("The Artist (2011)") == "The Artist 2011"
        assert normalize_title_for_matching("Community [Seasons 1-6]") == "Community Seasons 16"


class TestTitleNormalizationForComparison:
    """Test title normalization for collection comparison (with lowercase)."""

    def test_apostrophe_and_lowercase(self):
        """Test apostrophe removal and lowercasing."""
        assert normalize_title_for_comparison("Bob's Burgers") == "bobs burgers"
        assert normalize_title_for_comparison("Ocean's Eleven") == "oceans eleven"

    def test_hyphen_and_lowercase(self):
        """Test hyphen removal and lowercasing."""
        assert normalize_title_for_comparison("Spider-Man") == "spiderman"
        assert normalize_title_for_comparison("X-Men") == "xmen"

    def test_case_insensitivity(self):
        """Test that all cases produce the same result."""
        assert normalize_title_for_comparison("The Matrix") == "the matrix"
        assert normalize_title_for_comparison("THE MATRIX") == "the matrix"
        assert normalize_title_for_comparison("the matrix") == "the matrix"
        assert normalize_title_for_comparison("tHe MaTrIx") == "the matrix"

    def test_multiple_punctuation(self):
        """Test removal of multiple types of punctuation with lowercase."""
        assert normalize_title_for_comparison("S.H.I.E.L.D.") == "shield"
        assert normalize_title_for_comparison("M*A*S*H") == "mash"

    def test_collection_suffix(self):
        """Test collection titles with 'Collection' suffix."""
        assert normalize_title_for_comparison("Bob's Burgers Collection") == "bobs burgers collection"
        assert normalize_title_for_comparison("Spider-Man Collection") == "spiderman collection"


class TestFuzzyMatching:
    """Test fuzzy title matching comparisons."""

    def test_fuzzy_match_apostrophe(self):
        """Test matching titles with apostrophe differences."""
        assert fuzzy_title_match("Bob's Burgers", "Bobs Burgers") == True
        assert fuzzy_title_match("Ocean's Eleven", "Oceans Eleven") == True

    def test_fuzzy_match_hyphen(self):
        """Test matching titles with hyphen differences."""
        assert fuzzy_title_match("Spider-Man", "SpiderMan") == True
        assert fuzzy_title_match("X-Men", "XMen") == True

    def test_fuzzy_match_case(self):
        """Test case-insensitive matching."""
        assert fuzzy_title_match("The Matrix", "the matrix") == True
        assert fuzzy_title_match("THE MATRIX", "The Matrix") == True
        assert fuzzy_title_match("tHe MaTrIx", "THE MATRIX") == True

    def test_fuzzy_match_combined(self):
        """Test matching with multiple differences."""
        assert fuzzy_title_match("Bob's Burgers", "BOBS BURGERS") == True
        assert fuzzy_title_match("Spider-Man: Far From Home", "SpiderMan Far From Home") == True

    def test_fuzzy_match_failure(self):
        """Test that different titles don't match."""
        assert fuzzy_title_match("Bob's Burgers", "Breaking Bad") == False
        assert fuzzy_title_match("Spider-Man", "Iron Man") == False
        assert fuzzy_title_match("The Matrix", "The Matrix Reloaded") == False

    def test_fuzzy_match_empty(self):
        """Test matching with empty strings."""
        assert fuzzy_title_match("", "") == True
        assert fuzzy_title_match("Something", "") == False
        assert fuzzy_title_match("", "Something") == False

    def test_fuzzy_match_none(self):
        """Test matching with None values."""
        assert fuzzy_title_match(None, None) == True
        assert fuzzy_title_match("Something", None) == False
        assert fuzzy_title_match(None, "Something") == False

    def test_fuzzy_match_collection_suffix(self):
        """Test matching collection titles with suffix differences."""
        # This tests the fuzzy matching part; suffix removal is handled elsewhere
        assert fuzzy_title_match("Bob's Burgers Collection", "Bobs Burgers Collection") == True
        assert fuzzy_title_match("Spider-Man Collection", "SpiderMan Collection") == True

    def test_fuzzy_match_special_characters(self):
        """Test matching with special characters (unicode preserved)."""
        assert fuzzy_title_match("Thor: Ragnarök", "Thor Ragnarök") == True
        assert fuzzy_title_match("Amélie", "Amélie") == True

    def test_fuzzy_match_numbers_and_punctuation(self):
        """Test matching with numbers and punctuation."""
        assert fuzzy_title_match("2001: A Space Odyssey", "2001 A Space Odyssey") == True
        assert fuzzy_title_match("9-1-1", "911") == True


class TestRealWorldExamples:
    """Test real-world examples from artwork providers and Plex."""

    def test_bobs_burgers(self):
        """Test the original issue: Bob's Burgers."""
        # Artwork provider might have "Bobs Burgers" without apostrophe
        # Plex has "Bob's Burgers" with apostrophe
        assert fuzzy_title_match("Bobs Burgers", "Bob's Burgers") == True
        assert normalize_title_for_matching("Bob's Burgers") == "Bobs Burgers"

    def test_star_wars_colon(self):
        """Test Star Wars titles with colons."""
        # Providers might replace colons with hyphens or remove them
        assert fuzzy_title_match("Star Wars: The Empire Strikes Back", "Star Wars The Empire Strikes Back") == True
        assert fuzzy_title_match("Star Wars - The Empire Strikes Back", "Star Wars The Empire Strikes Back") == True

    def test_marvel_movies(self):
        """Test Marvel movie titles with hyphens."""
        assert fuzzy_title_match("Spider-Man", "SpiderMan") == True
        assert fuzzy_title_match("Spider-Man: Homecoming", "SpiderMan Homecoming") == True
        assert fuzzy_title_match("Ant-Man", "AntMan") == True

    def test_tv_shows_with_punctuation(self):
        """Test TV shows with various punctuation."""
        assert fuzzy_title_match("It's Always Sunny in Philadelphia", "Its Always Sunny in Philadelphia") == True
        assert fuzzy_title_match("Grey's Anatomy", "Greys Anatomy") == True
        assert fuzzy_title_match("How I Met Your Mother", "How I Met Your Mother") == True

    def test_collections(self):
        """Test collection titles."""
        assert fuzzy_title_match("Bob's Burgers Collection", "Bobs Burgers Collection") == True
        assert fuzzy_title_match("Spider-Man Collection", "SpiderMan Collection") == True
        assert fuzzy_title_match("The Lord of the Rings Collection", "The Lord of the Rings Collection") == True
