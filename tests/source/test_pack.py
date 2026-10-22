import json

import numpy as np
import pytest

from nnnotes.source import addressables, master
from nnnotes.source.addressables import BundleKey
from nnnotes.source.master import MasterKey
from tests.helpers import synth

KEY = MasterKey(synth.MASTER_KEY, synth.MASTER_IV)
BUNDLE_KEY = BundleKey(synth.BUNDLE_KEY, synth.BUNDLE_SEED)
TABLE = synth.MASTER_TABLE
JSON = json.dumps(TABLE, ensure_ascii=False).encode("utf-8")

# the same known answer tests/source/test_master.py decrypts: the all-zero block under the all-zero key
KAT_C1 = bytes.fromhex("C6227E7740B7E53B5CB77865278EAB0726F62366D9AABAD908936123A1FC8AF3")


def test_encrypt_known_answer():
    rk = master.round_keys(bytes(32))
    out = master.encrypt_blocks(np.zeros((1, 32), dtype=np.uint8), rk)
    assert out.tobytes() == KAT_C1


def test_encode_matches_the_test_encryptor():
    # the independent pure-Python encryptor of the tests builds the same file, byte for byte
    assert master.encode(JSON, KEY, prefix=bytes(range(64))) == synth.master_file(TABLE, prefix=bytes(range(64)))


def test_encode_decode_round_trip():
    for prefix in (b"", bytes(range(64))):
        raw = master.encode(JSON, KEY, prefix=prefix)
        assert raw[:64] == (prefix or bytes(64))
        assert master.decode(raw, KEY) == JSON


def test_encode_pads_to_a_block():
    raw = master.encode(b"{}", KEY)
    assert (len(raw) - master.PREFIX) % master.BLOCK == 0


def test_encode_rejects_a_short_prefix():
    with pytest.raises(ValueError, match="64 bytes"):
        master.encode(JSON, KEY, prefix=bytes(10))


def test_pack_bundle_round_trip():
    plain = synth.fake_bundle("a_01.bundle")
    stored = addressables.pack(plain, "a_01.bundle", BUNDLE_KEY)
    assert not stored.startswith(b"UnityFS") and stored[addressables.ENCRYPTED_BYTES :] == plain[16384:]
    assert addressables.decrypt(stored, "a_01.bundle", BUNDLE_KEY) == plain


def test_pack_bundle_matches_the_test_encryptor():
    plain = synth.fake_bundle("a_01.bundle")
    assert addressables.pack(plain, "a_01.bundle", BUNDLE_KEY) == synth.encrypt_bundle(plain, "a_01.bundle")


def test_pack_bundle_requires_unityfs():
    with pytest.raises(ValueError, match="UnityFS"):
        addressables.pack(b"not a bundle", "a.bundle", BUNDLE_KEY)


def test_bundle_name_seeds_the_nonce():
    plain = synth.fake_bundle("a_01.bundle")
    stored = addressables.pack(plain, "a_01.bundle", BUNDLE_KEY)
    assert addressables.decrypt(stored, "b_02.bundle", BUNDLE_KEY) != plain


def test_pack_is_the_inverse_of_a_decrypted_file(tmp_path):
    # what `bundle pull` writes (UnityFS) goes back to what the CDN serves
    p = tmp_path / "b_02.bundle"
    p.write_bytes(synth.fake_bundle("b_02.bundle"))
    stored = addressables.pack(p.read_bytes(), p.name, BUNDLE_KEY)
    assert addressables.decrypt(stored, p.name, BUNDLE_KEY) == p.read_bytes()
