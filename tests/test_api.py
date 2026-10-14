"""The public Python API: the top-level names and the `__all__` of the public modules."""

import importlib
import importlib.resources
import subprocess
import sys

import pytest

import nnnotes

PUBLIC_MODULES = [
    "nnnotes.musicdata",
    "nnnotes.musicdata.deckdata",
    "nnnotes.schemas",
    "nnnotes.site.webmodel",
    "nnnotes.source.addressables",
    "nnnotes.source.catalog",
    "nnnotes.source.config",
    "nnnotes.source.gameapi",
    "nnnotes.source.jp",
    "nnnotes.source.master",
    "nnnotes.source.session",
]


def test_the_top_level_names_are_the_modules_own():
    assert sorted(nnnotes.__all__) == sorted([*nnnotes._LAZY, "__version__"])
    for name in nnnotes.__all__:
        value = getattr(nnnotes, name)
        if name != "__version__":
            module = importlib.import_module(nnnotes._LAZY[name])
            assert value is getattr(module, name)
    assert set(nnnotes.__all__) <= set(dir(nnnotes))
    with pytest.raises(AttributeError):
        nnnotes.cli_main  # noqa: B018


def test_importing_the_package_imports_no_subpackage():
    code = "import sys, nnnotes; print(sorted(m for m in sys.modules if m.startswith('nnnotes.')))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


@pytest.mark.parametrize("name", PUBLIC_MODULES)
def test_every_public_name_is_defined_and_documented(name):
    module = importlib.import_module(name)
    assert module.__all__ == sorted(module.__all__)
    for attr in module.__all__:
        value = getattr(module, attr)
        assert value.__doc__ and value.__module__ == name, attr


def test_the_package_is_typed():
    assert (importlib.resources.files("nnnotes") / "py.typed").is_file()
