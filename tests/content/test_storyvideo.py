"""storyvideo: the manifest names and the video.json document of a synthetic CriWare.Assets movie description."""

import pytest

from nnnotes.content import storyvideo


class FakeObjects:
    """An object table: {oid: (info, {role: value})} with catalog keys {key: [oid]}."""

    def __init__(self, objs: dict, keys: dict):
        self._objs = objs
        self._keys = keys
        self.roots: list[str] = []

    def __contains__(self, oid):
        return oid in self._objs

    def ids(self, cls=None):
        return sorted(o for o, (info, _) in self._objs.items() if cls is None or info["class"] == cls)

    def key(self, key):
        return list(self._keys.get(key, []))

    def info(self, oid):
        return self._objs[oid][0]

    def bytes(self, oid, role):
        return self._objs[oid][1][role]

    def json(self, oid, role="json"):
        return self._objs[oid][1][role]


def movie_info(**over) -> dict:
    info = {
        "codecType": 9,
        "width": 1280,
        "height": 720,
        "dispWidth": 1280,
        "dispHeight": 720,
        "framerateN": 30000,
        "framerateD": 1001,
        "totalFrames": 300,
    }
    info.update(over)
    return info


def table(key: str, *behaviours: dict) -> FakeObjects:
    """A table whose key `key` holds one MonoBehaviour per document in `behaviours`."""
    objs, ids = {}, []
    for n, doc in enumerate(behaviours):
        oid = f"globalgamemanagers.assets:{n + 1}"
        objs[oid] = ({"class": "MonoBehaviour", "files": {"json": {}}}, {"json": doc})
        ids.append(oid)
    return FakeObjects(objs, {key: ids})


TRACKS = [{"kind": "video", "channel": 0, "codec": "vp9"}, {"kind": "audio", "channel": 0, "codec": "opus"}]


def test_the_manifest_name_is_the_asset_with_slashes_as_tildes():
    assert storyvideo.manifest_name("Cri/Video/adv/movie_a/movie_a") == "adv~movie_a~movie_a"
    assert storyvideo.manifest_name("Cri/Video/intro.v-2") == "intro.v-2"


@pytest.mark.parametrize("key", ["Cri/Sound/adv/m", "Cri/Video/", "Cri/Video/a b", "Cri/Video//a", "Cri/Video/a/"])
def test_a_key_that_is_not_a_video_key_has_no_manifest_name(key):
    with pytest.raises(ValueError, match="not a video key"):
        storyvideo.manifest_name(key)


def test_video_describes_the_movie_and_carries_the_tracks_of_the_webm():
    key = "Cri/Video/adv/m/m"
    objects = table(key, {"movieInfo": movie_info()})
    doc = storyvideo.video(objects, key, TRACKS)
    assert doc == {
        "format": 1,
        "file": "movie.webm",
        "codec": "vp9",
        "width": 1280,
        "height": 720,
        "displayWidth": 1280,
        "displayHeight": 720,
        "frameRate": [30000, 1001],
        "frames": 300,
        "tracks": TRACKS,
    }


def test_video_names_the_codecs_it_knows_and_keeps_the_others_by_number():
    key = "Cri/Video/adv/m/m"
    for codec, name in ((1, "sofdec.prime"), (5, "h264"), (9, "vp9")):
        doc = storyvideo.video(table(key, {"movieInfo": movie_info(codecType=codec)}), key, [])
        assert doc["codec"] == name
    doc = storyvideo.video(table(key, {"movieInfo": movie_info(codecType=7)}), key, [])
    assert doc["codec"] == "codecType 7"


def test_video_reads_the_one_movie_description_of_the_key():
    key = "Cri/Video/adv/m/m"
    other = {"Other": True}
    assert storyvideo.video(table(key, other, {"movieInfo": movie_info()}), key, [])["frames"] == 300
    with pytest.raises(ValueError, match="0 MonoBehaviours with a movieInfo"):
        storyvideo.video(table(key, other), key, [])
    two = {"movieInfo": movie_info()}
    with pytest.raises(ValueError, match="2 MonoBehaviours with a movieInfo"):
        storyvideo.video(table(key, two, two), key, [])
