"""Split a billing line into fields.

The seed implementation is deliberately naive: the live cycle fixture exists to
make a worker modify an existing file, not create a new one.
"""

from __future__ import annotations


def split_line(line: str) -> list[str]:
    return line.split(",")
