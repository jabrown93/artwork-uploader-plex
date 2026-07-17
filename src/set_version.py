#!/usr/bin/env python3
"""
Set an exact version in __version__.py, for semantic-release's prepareCmd
(which computes the target version itself, unlike bump_version.py's relative
major/minor/patch bump).

Usage:
    python set_version.py 1.2.3 minor
"""

import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "core" / "__version__.py"


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <version> <release-type>", file=sys.stderr)
        sys.exit(1)

    new_version, release_type = sys.argv[1], sys.argv[2]

    # A prerelease version (e.g. "0.10.0-beta.1") has more than 3 dot-separated
    # parts, so a plain split(".") unpack fails on beta releases. Pull just the
    # leading major.minor.patch triple for the numeric tuple.
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", new_version)
    if not match:
        print(f"Error: could not parse version {new_version!r}", file=sys.stderr)
        sys.exit(1)
    major, minor, patch = match.groups()

    content = VERSION_FILE.read_text()
    content = re.sub(
        r'__version__ = "[^"]+"',
        f'__version__ = "{new_version}"',
        content,
    )
    content = re.sub(
        r"__version_info__ = \([^)]+\)",
        f'__version_info__ = ({major}, {minor}, {patch}, "{release_type}")',
        content,
    )

    VERSION_FILE.write_text(content)
    print(f"New version: {new_version}", file=sys.stderr)


if __name__ == "__main__":
    main()
