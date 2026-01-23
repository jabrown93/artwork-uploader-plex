"""Utility functions for artwork uploader."""

from .title_matching import (
    normalize_title_for_matching,
    normalize_title_for_comparison,
    fuzzy_title_match
)

__all__ = [
    'normalize_title_for_matching',
    'normalize_title_for_comparison',
    'fuzzy_title_match'
]
