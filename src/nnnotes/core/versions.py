"""Native codec identities used by content-producing tasks."""

from __future__ import annotations

import json
import subprocess
import sys
from functools import cache
from typing import cast

PILLOW_ZLIB = "Pillow.zlib"
_PROBE = (
    "import json,sys; sys.path=json.loads(sys.argv[1]); from PIL import features; "
    "print(json.dumps([features.version('zlib'), "
    "features.version_feature('zlib_ng') if 'zlib_ng' in features.get_supported_features() else None]))"
)


@cache
def pillow_zlib_version() -> str:
    """Pillow's zlib backend and version, probed without loading image codecs into the planning process."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _PROBE,
            json.dumps([p for p in cast(list[object], sys.path) if isinstance(p, str)]),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    zlib, zlib_ng = json.loads(result.stdout)
    if not isinstance(zlib, str) or not zlib:
        raise RuntimeError("Pillow has no zlib codec")
    return zlib if zlib_ng is None else f"{zlib}-{zlib_ng}"
