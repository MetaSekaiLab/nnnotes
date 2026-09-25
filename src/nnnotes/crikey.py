"""CRI (ADX2/HCA) decryption key from the game's own boot data.

The key is serialized on the CriWareInitializer component in the APK's
`assets/bin/Data/data.unity3d` as `DecrypterConfig.key` (a decimal string).
The managed side XORs it with a constant before handing it to the native
plugin, which undoes the XOR, so the HCA keycode is the decimal value itself.

This reads it from a user-supplied APK (or an extracted data.unity3d); nothing
is hard-coded here.
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
from pathlib import Path

import UnityPy

DATA_IN_APK = "assets/bin/Data/data.unity3d"


def _load_data(src: Path):
    src = Path(src)
    if src.suffix.lower() in (".apk", ".zip"):
        with zipfile.ZipFile(src) as z:
            return UnityPy.load(io.BytesIO(z.read(DATA_IN_APK)))
    return UnityPy.load(str(src))


def find_key(src: Path) -> int:
    """HCA keycode from a base.apk or data.unity3d."""
    env = _load_data(src)
    init_script = None
    for o in env.objects:
        if o.type.name == "MonoScript":
            d = o.read()
            if d.m_Namespace == "CriWare" and d.m_ClassName == "CriWareInitializer":
                init_script = o.path_id
                break
    if init_script is None:
        raise RuntimeError("CriWareInitializer MonoScript not found")
    # Script typetrees are stripped (IL2CPP), so read m_Script.m_PathID from the
    # raw MonoBehaviour header (GameObject PPtr, m_Enabled+pad, m_Script PPtr).
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        raw = o.get_raw_data()
        if len(raw) < 28 or struct.unpack_from("<q", raw, 20)[0] != init_script:
            continue
        # DecrypterConfig is the component's last field and its first member is
        # the key string; only bools follow it. So the key is the last
        # length-prefixed string in the component.
        last = None
        for m in re.finditer(rb"[\x20-\x7e]+", raw):
            s = m.start()
            if s >= 4:
                n = struct.unpack_from("<i", raw, s - 4)[0]
                if 0 < n <= len(m.group()):
                    last = raw[s:s + n]
        if last is None or not last.isdigit():
            return 0  # empty key: decryption disabled
        return int(last)
    raise RuntimeError("CriWareInitializer instance not found")


def write_hcakey(key: int, directory: Path) -> Path:
    """vgmstream picks up `.hcakey` (8-byte big-endian) next to the input file."""
    p = Path(directory) / ".hcakey"
    p.write_bytes(struct.pack(">Q", key))
    return p
