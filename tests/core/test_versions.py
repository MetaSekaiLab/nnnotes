"""Native PNG codec identity participates in image-producing task keys."""

import subprocess
import sys

from PIL import features

from nnnotes.core.versions import PILLOW_ZLIB, pillow_zlib_version
from nnnotes.pipeline import contract, textfonts
from nnnotes.pipeline.stages import library_versions
from nnnotes.unity import atoms


def test_pillow_codec_identity_names_its_backend():
    expected = features.version("zlib")
    if "zlib_ng" in features.get_supported_features():
        expected += "-" + features.version_feature("zlib_ng")
    assert pillow_zlib_version() == expected
    assert library_versions([PILLOW_ZLIB]) == {PILLOW_ZLIB: expected}


def test_png_and_font_task_keys_change_with_native_codec(monkeypatch):
    version = atoms._version
    monkeypatch.setattr(atoms, "_reference", atoms._reference.__wrapped__)

    def keys(native):
        monkeypatch.setattr(atoms, "_version", lambda name: native if name == PILLOW_ZLIB else version(name))
        png = atoms.ids("png.encode")
        glyphs = textfonts.font_atoms()
        return (
            contract.Task("unity.export", 1, "texture", atoms=png).key,
            contract.Task("story.fonts", 1, "1/en", atoms=glyphs).key,
            contract.Task("live.ui", 1, "7/expert/en", atoms=glyphs).key,
        )

    assert all(a != b for a, b in zip(keys("1.2.3"), keys("1.2.3.zlib-ng-2.0"), strict=True))


def test_codec_probe_uses_the_running_process_import_paths(tmp_path):
    package = tmp_path / "PIL"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "features.py").write_text(
        "def version(name): return '7.8.9'\ndef get_supported_features(): return []\n", encoding="utf-8"
    )
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); from PIL import features; "
        "from nnnotes.core.versions import pillow_zlib_version; "
        "assert features.version('zlib') == pillow_zlib_version() == '7.8.9'"
    )
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, capture_output=True, text=True)
