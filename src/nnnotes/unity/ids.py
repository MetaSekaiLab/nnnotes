"""Object ids: an object of a serialized file is `<serialized file>:<pathId>`."""

from __future__ import annotations

__all__ = ["object_id", "parse_object_id"]


def object_id(file: str, path_id: int) -> str:
    return f"{file}:{int(path_id)}"


def parse_object_id(oid: str) -> tuple[str, int]:
    """(serialized file, pathId) of an object id."""
    file, sep, pid = oid.rpartition(":")
    if not sep or not file:
        raise ValueError(f"not an object id: {oid!r}")
    return file, int(pid)
