#!/usr/bin/env python3
"""
Bump version in __version__.py and print the new version to stdout.

Usage:
    python bump_version.py patch   # 0.5.1 -> 0.5.2
    python bump_version.py minor   # 0.5.1 -> 0.6.0
    python bump_version.py major   # 0.5.1 -> 1.0.0
"""

import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "core" / "__version__.py"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("major", "minor", "patch"):
        print(f"Usage: {sys.argv[0]} patch|minor|major", file=sys.stderr)
        sys.exit(1)

    bump_type = sys.argv[1]
    content = VERSION_FILE.read_text()

    match = re.search(r'__version__ = "([^"]+)"', content)
    if not match:
        print("Error: could not parse __version__", file=sys.stderr)
        sys.exit(1)

    parts = list(map(int, match.group(1).split(".")))
    idx = {"major": 0, "minor": 1, "patch": 2}[bump_type]
    parts[idx] += 1
    for i in range(idx + 1, 3):
        parts[i] = 0

    new_version = ".".join(map(str, parts))

    content = re.sub(
        r'__version__ = "[^"]+"',
        f'__version__ = "{new_version}"',
        content,
    )
    content = re.sub(
        r"__version_info__ = \([^)]+\)",
        f'__version_info__ = ({parts[0]}, {parts[1]}, {parts[2]}, "patch")',
        content,
    )

    VERSION_FILE.write_text(content)

    # Print to stderr for human, stdout for Make to capture
    print(f"Current version: {match.group(1)}", file=sys.stderr)
    print(f"New version:     {new_version}", file=sys.stderr)
    print(new_version)


if __name__ == "__main__":
    main()
