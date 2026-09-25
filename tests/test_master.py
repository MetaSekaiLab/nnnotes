import gzip
import hashlib
import json

import numpy as np
import pytest

import synth
from nnnotes import master
from nnnotes.master import MasterKey

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
TABLE = {"_allData": [{"_id": 1, "_name": "テスト", "_value": 0.5}, {"_id": 2, "_name": "test", "_value": -3}]}

# Rijndael (256-bit block, 256-bit key) known answer: the designers' reference implementation encrypts the all-zero
# block under the all-zero key twice ("The Design of Rijndael", reference code test driver).
KAT_C1 = bytes.fromhex("C6227E7740B7E53B5CB77865278EAB0726F62366D9AABAD908936123A1FC8AF3")
KAT_C2 = bytes.fromhex("9843E807319C32AD1EA3935EF56A2BA96E4BF19C30E47D88A2B97CBBF2E159E7")


def test_known_answer_decrypt():
    rk = master.round_keys(bytes(32))
    out = master.decrypt_blocks(np.frombuffer(KAT_C2 + KAT_C1, dtype=np.uint8).reshape(-1, 32), rk)
    assert out.tobytes() == KAT_C1 + bytes(32)


def test_test_encryptor_known_answer():
    assert synth.rijndael_encrypt_block(bytes(32), bytes(32)) == KAT_C1
    assert synth.rijndael_encrypt_block(KAT_C1, bytes(32)) == KAT_C2


def test_cbc_round_trip():
    data = bytes(range(256)) * 3 + b"tail"
    ct = synth.rijndael256_cbc_encrypt(data, synth.MASTER_KEY, synth.MASTER_IV)
    pt = master.decrypt_cbc(ct, synth.MASTER_KEY, synth.MASTER_IV)
    assert pt[:len(data)] == data and pt[len(data):] == bytes([pt[-1]]) * pt[-1]
    with pytest.raises(ValueError, match="multiple of 32"):
        master.decrypt_cbc(ct[:-1], synth.MASTER_KEY, synth.MASTER_IV)


def test_decode_file():
    raw = synth.master_file(TABLE, prefix=bytes(range(64)))
    assert json.loads(master.decode(raw, KEY)) == TABLE


def test_decode_wrong_key():
    raw = synth.master_file(TABLE)
    with pytest.raises((ValueError, OSError, EOFError, gzip.BadGzipFile)):
        master.decode(raw, MasterKey(bytes(32), synth.MASTER_IV))


def test_master_key_hides_values():
    assert synth.MASTER_KEY.hex() not in repr(KEY) and repr(synth.MASTER_IV) not in repr(KEY)
    with pytest.raises(ValueError):
        MasterKey(bytes(16), bytes(32))


def test_decode_files(tmp_path):
    src = tmp_path / "bin"
    src.mkdir()
    (src / "MasterB.bin").write_bytes(synth.master_file(TABLE))
    (src / "MasterA.bin").write_bytes(synth.master_file({"_allData": []}))
    (src / "MasterBad.bin").write_bytes(synth.master_file(TABLE, key=bytes(32)))
    (src / "notes.txt").write_text("not a table")
    files = master.input_files([src])
    assert [f.name for f in files] == ["MasterA.bin", "MasterB.bin", "MasterBad.bin"]
    r = master.decode_files(files, tmp_path / "json", KEY, workers=2)
    assert r["decoded"] == 2 and [f["file"] for f in r["failed"]] == ["MasterBad.bin"]
    data = (tmp_path / "json" / "MasterB.json").read_bytes()
    assert json.loads(data) == TABLE
    assert b"\r" not in data and "テスト".encode("utf-8") in data           # UTF-8, LF, not escaped
    assert not (tmp_path / "json" / "MasterBad.json").exists()
    with pytest.raises(FileNotFoundError):
        master.input_files([tmp_path / "absent"])


def serve_version(root, version, files, bad_hash=()):
    """A CDN tree on disk: <root>/master/<version>/MasterManifest.json and the files it lists."""
    d = root / "master" / version
    d.mkdir(parents=True)
    listed = []
    for name, data in files.items():
        (d / name).write_bytes(data)
        sha = hashlib.sha256(b"other" if name in bad_hash else data).hexdigest()
        listed.append({"name": name, "hash": sha, "size": len(data)})
    (d / "MasterManifest.json").write_text(json.dumps({"version": version, "files": listed}))
    return root.as_uri()


def test_download(tmp_path):
    files = {"MasterA.bin": synth.master_file(TABLE), "MasterB.bin": b"x" * 100, "MasterC.bin": b"y"}
    cdn = serve_version(tmp_path / "cdn", "1.2.3", files, bad_hash={"MasterC.bin"})
    out = tmp_path / "out"
    r = master.download(cdn, "1.2.3", out, workers=2)
    assert (r["version"], r["files"], r["downloaded"], r["kept"]) == ("1.2.3", 3, 2, 0)
    assert r["failed"] == [{"file": "MasterC.bin", "error": "sha256 differs from the manifest"}]
    assert (out / "MasterA.bin").read_bytes() == files["MasterA.bin"] and not (out / "MasterC.bin").exists()
    assert (out / "MasterManifest.json").is_file()
    r = master.download(cdn, "1.2.3", out)
    assert (r["downloaded"], r["kept"]) == (0, 2)


def test_download_rejects_path_names(tmp_path):
    d = tmp_path / "cdn" / "master" / "v"
    d.mkdir(parents=True)
    (d / "MasterManifest.json").write_text(json.dumps({"files": [{"name": "../evil.bin", "hash": ""}]}))
    r = master.download((tmp_path / "cdn").as_uri(), "v", tmp_path / "out")
    assert r["failed"] == [{"file": "../evil.bin", "error": "unexpected file name"}]
    assert not (tmp_path / "evil.bin").exists()


def test_table_rows(tmp_path):
    (tmp_path / "MasterX.json").write_text(json.dumps(TABLE, ensure_ascii=False), encoding="utf-8")
    assert master.table(tmp_path, "MasterX") == TABLE["_allData"]
    assert master.has_row(tmp_path, "MasterX", 2) and not master.has_row(tmp_path, "MasterX", 3)
    with pytest.raises(FileNotFoundError):
        master.table(tmp_path, "MasterY")
