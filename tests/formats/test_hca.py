import struct

import pytest

from nnnotes.formats.hca import crc16, crypt, inspect, key_table
from tests.helpers.packing import hca_stream


def test_crc_known_polynomial_vector():
    assert crc16(b"123456789") == 0xFEE8


def test_cipher_preserves_framing_and_is_reversible():
    source = hca_stream(4097)
    encrypted = crypt(source, 123456789)
    assert encrypted != source
    assert inspect(encrypted)["cipherType"] == 56
    assert inspect(encrypted)["samples"] == 4097
    assert crypt(encrypted, 123456789, decrypt=True) == source
    assert crypt(encrypted, 987654321, decrypt=True) != source


def test_key_tables_are_inverse_permutations():
    encrypt, decrypt = key_table(123), key_table(123, decrypt=True)
    assert len(set(encrypt)) == 256
    assert bytes(encrypt).translate(decrypt) == bytes(range(256))
    assert encrypt[0] == 0 and encrypt[255] == 255


@pytest.mark.parametrize("key", [0, -1, 2**64, True])
def test_invalid_keys_are_rejected(key):
    with pytest.raises(ValueError, match="key"):
        key_table(key)


@pytest.mark.parametrize("where", [40, 70, -1])
def test_checksum_damage_is_rejected(where):
    data = bytearray(hca_stream())
    data[where] ^= 1
    with pytest.raises(ValueError):
        inspect(bytes(data))


def test_truncated_and_looping_audio_are_rejected():
    with pytest.raises(ValueError, match="truncated"):
        inspect(hca_stream()[:-1])
    data = bytearray(hca_stream())
    data[46:50] = b"loop"
    struct.pack_into(">H", data, 62, crc16(data[:62]))
    with pytest.raises(ValueError, match="finite"):
        inspect(bytes(data))
