"""
Title matching utilities for handling punctuation differences in Plex media titles.

This module provides normalization and fuzzy matching functions to handle cases where
artwork provider titles differ from Plex titles due to punctuation (e.g., "Bobs Burgers"
vs "Bob's Burgers").
"""

from typing import Optional


def normalize_title_for_matching(title: Optional[str]) -> str:
    """
    Normalize title for fuzzy matching by removing all punctuation.

    This replaces the hardcoded normalization in web_routes.py that only
    removed hyphens and ellipses. Now handles all punctuation including
    apostrophes, periods, colons, etc.

    Process:
    - Remove all punctuation (apostrophes, hyphens, periods, colons, etc.)
    - Collapse multiple spaces to single space
    - Strip leading/trailing whitespace
    - Keep case intact (PlexAPI search is case-insensitive)

    Examples:
        "Bob's Burgers" -> "Bobs Burgers"
        "Spider-Man" -> "SpiderMan"
        "S.H.I.E.L.D." -> "SHIELD"
        "Star Wars: Episode IV" -> "Star Wars Episode IV"

    Args:
        title: The title string to normalize

    Returns:
        Normalized title with punctuation removed

    Note:
        We don't lowercase because PlexAPI's search handles case-insensitivity,
        and keeping original case may help with exact match attempts.
    """
    if not title:
        return ""

    # Remove all punctuation except spaces and alphanumeric
    # This handles apostrophes, hyphens, periods, colons, underscores, etc.
    # Existing spaces are preserved; Unicode letters/digits are kept
    normalized = ''.join(ch for ch in title if ch.isalnum() or ch.isspace())

    # Collapse multiple spaces to single space and strip
    normalized = " ".join(normalized.split())

    return normalized


def normalize_title_for_comparison(title: Optional[str]) -> str:
    """
    Normalize title for comparison (used in collection matching).

    Same as normalize_title_for_matching but also lowercases for
    case-insensitive exact matching.

    Examples:
        "Bob's Burgers" -> "bobs burgers"
        "Spider-Man" -> "spiderman"

    Args:
        title: The title string to normalize

    Returns:
        Normalized title with punctuation removed and lowercased
    """
    return normalize_title_for_matching(title).lower()


def fuzzy_title_match(title1: Optional[str], title2: Optional[str]) -> bool:
    """
    Compare two titles using fuzzy matching (ignoring punctuation and case).

    Args:
        title1: First title to compare
        title2: Second title to compare

    Returns:
        True if titles match after normalization, False otherwise

    Examples:
        fuzzy_title_match("Bob's Burgers", "Bobs Burgers") -> True
        fuzzy_title_match("Spider-Man", "SpiderMan") -> True
        fuzzy_title_match("The Matrix", "the matrix") -> True
        fuzzy_title_match("Bob's Burgers", "Breaking Bad") -> False
    """
    return normalize_title_for_comparison(title1) == normalize_title_for_comparison(title2)
