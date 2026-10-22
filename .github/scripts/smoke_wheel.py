"""Install the wheel with the deck model in a fresh environment and exercise its CLI and the deck model."""

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

SMOKE = """
import json
from pathlib import Path
import sys

import nnnotes
import ournotes_sim
from nnnotes import musicdata, schemas
from nnnotes.formats.hca import key_table
from nnnotes.musicdata import deckdata
from nnnotes.packing import AudioOptions
from nnnotes.source.master import MasterKey, decode, encode

assert Path(nnnotes.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
deck = musicdata.Deck(workers=1, aptitude=False)
info = deck.info()
data = {
    "format": ournotes_sim.info()["dataFormat"],
    "master": {name: {"columns": [], "rows": []} for name, _ in deckdata.TABLES},
    "charts": [],
}
stats = json.loads(ournotes_sim.chart_stats(json.dumps(data), workers=1, aptitude=False))
assert stats["format"] == info["format"]
assert stats["charts"] == []
assert AudioOptions().quality == "high"
assert schemas.load("song-package")["title"] == "nnnotes.song-package/1"
assert schemas.load("pack-target")["title"] == "nnnotes.pack-target/1"
assert bytes(key_table(123)).translate(key_table(123, decrypt=True)) == bytes(range(256))
key = MasterKey(bytes(range(32)), bytes(reversed(range(32))))
assert decode(encode(b'{"_allData": []}', key), key) == b'{"_allData": []}'
print(f"nnnotes {nnnotes.__version__}: {info['name']} {info['version']} ({info['commit']})")
"""


def main(directory: str) -> None:
    (wheel,) = Path(directory).resolve().glob("*.whl")
    with tempfile.TemporaryDirectory(prefix="nnnotes-wheel-") as directory:
        root = Path(directory)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        subprocess.run([python, "-I", "-m", "pip", "install", f"{wheel}[deck]"], cwd=root, check=True)
        subprocess.run([python, "-I", "-c", SMOKE], cwd=root, check=True)
        subprocess.run([scripts / ("nnnotes.exe" if os.name == "nt" else "nnnotes"), "--help"], cwd=root, check=True)
        subprocess.run([python, "-I", "-m", "nnnotes", "pack", "audio", "--help"], cwd=root, check=True)


if __name__ == "__main__":
    main(sys.argv[1])
