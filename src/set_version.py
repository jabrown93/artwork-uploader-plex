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
    major, minor, patch = new_version.split(".")

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
