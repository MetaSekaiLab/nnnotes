import struct
import zipfile

import pytest

import synth
from nnnotes import contract
from nnnotes.addressables import parse, parse_header, parse_keys, parse_locations
from nnnotes.catalog import APK_OFFSET_BASE, Catalog, file_name, location_kind
from nnnotes.catalogdb import (CatalogDB, IndexStage, default_label, diff, fetch, format_diff, index,
                               stable_raw_name, version_id)
from nnnotes.stages import Env, describe, execute, registry
from nnnotes.store import Store

H1, H2, H3, H4 = ("1" * 32, "2" * 32, "3" * 32, "4" * 32)
RAW = "cri_x/sound/bgm_a_" + "a" * 32


def remote_entries(h_a=H1, h_b=H2, raw=RAW, tex_iid="Assets/Game/Char/A.png", extra_b=None):
    b = {"extra": extra_b} if extra_b is not None else {}
    return [
        ("Char/A", tex_iid, [2, 5], {"type": "UnityEngine.Texture2D"}),
        ("Char/B", "Assets/Game/Char/B.prefab", [3], {"type": "UnityEngine.GameObject"}),
        (f"chara_a_{h_a}.bundle", synth.remote(f"chara_a_{h_a}.bundle"), [],
         {"extra": {"crc": 11, "bundleSize": 1000}}),
        (f"chara_b_{h_b}.bundle", synth.remote(f"chara_b_{h_b}.bundle"), [], b),
        ("Sound/bgm_a", "Assets/Game/Sound/bgm_a.acb", [6]),
        ("shared.bundle", synth.local("shared.bundle"), [], {"extra": {"bundleSize": 7, "crc": 5}}),
        (raw, synth.remote(raw), [], {"extra": {"bundleSize": 300}}),
        ("Story/物語", "Assets/Game/Story/物語.asset", [3]),
    ]


APK = [
    ("Shared/S", "Assets/Game/Shared/S.prefab", [1]),
    ("shared.bundle", synth.local("shared.bundle"), [], {"extra": {"bundleSize": 9, "crc": 6}}),
    ("cri_emb/voice_" + H4, synth.local("cri_emb/voice_" + H4), [], {"extra": {"bundleSize": 44}}),
]


def build(entries, **kw):
    return synth.CatalogWriter().build(entries, **kw)


# ---------------------------------------------------------------- the binary layout
def test_header_and_key_table():
    data = build(remote_entries(), build_hash="ab" * 16)
    h = parse_header(data)
    assert (h["magic"], h["version"], h["keysOffset"]) == (0x0DE38942, 2, 36)
    assert h["locatorId"] == "AddressablesMainContentCatalog" and h["buildResultHash"] == "ab" * 16
    assert h["instanceProvider"]["type"].endswith(".InstanceProvider") and h["sceneProvider"]["data"] is None
    assert [o["id"].rsplit(".", 1)[1] for o in h["initObjects"]] == ["AssetBundleProvider", "BundledAssetProvider"]
    keys = parse_keys(data)
    assert [k["key"] for k in keys] == [e[0] for e in remote_entries()]
    assert {k["type"] for k in keys} == {"System.String"}
    assert all(len(k["locations"]) == 1 for k in keys)


def test_records_are_28_bytes_without_prefix():
    data = build(remote_entries())
    locs = parse_locations(data)
    offsets = [e["offset"] for e in locs]
    assert [b - a for a, b in zip(offsets, offsets[1:])] == [28] * (len(offsets) - 1)
    assert all(struct.unpack_from("<I", data, o - 4)[0] != 28 for o in offsets)


def test_every_field_decoded():
    data = build(remote_entries())
    by_key = {e["primary_key"]: e for e in parse_locations(data)}
    a = by_key["Char/A"]
    assert a["internal_id"] == "Assets/Game/Char/A.png" and a["resource_type"] == "UnityEngine.Texture2D"
    assert a["provider"] == synth.ASSET_PROVIDER and a["extra_data"] is None and a["dependency_hash"] == 0
    bundle = by_key[f"chara_a_{H1}.bundle"]
    assert a["dependencies"] == [bundle["offset"], by_key["shared.bundle"]["offset"]]
    assert bundle["provider"] == synth.BUNDLE_PROVIDER and bundle["resource_type"] == synth.BUNDLE_TYPE
    x = bundle["extra_data"]
    assert x["type"] == synth.REQUEST_OPTIONS
    assert (x["hash"], x["crc"], x["bundleSize"]) == (H1, 11, 1000)
    assert x["bundleName"] == synth.request_options(bundle["internal_id"])["bundleName"]
    assert len(x["bundleName"].split("_")) == 2
    assert (x["timeout"], x["redirectLimit"], x["retryCount"], x["flags"]) == (0, 32, 0, 0)
    local = by_key["shared.bundle"]["extra_data"]
    assert local["flags"] == 4 and local["useCrcForCachedBundle"] is True and local["chunkedTransfer"] is False
    assert local["assetLoadMode"] == 0 and local["hash"] == synth.request_options(synth.local("shared.bundle"))["hash"]
    raw = by_key[RAW]["extra_data"]
    assert raw["hash"] == "a" * 32 and "_" not in raw["bundleName"] and raw["bundleSize"] == 300
    assert by_key["Story/物語"]["internal_id"] == "Assets/Game/Story/物語.asset"


def test_flags_and_dependency_hash():
    data = build([("b.bundle", synth.remote("b.bundle"), [], {"extra": {"flags": 31, "hash": None},
                                                               "dependency_hash": -7})])
    (e,) = parse_locations(data)
    x = e["extra_data"]
    assert x["hash"] is None and e["dependency_hash"] == -7
    assert (x["assetLoadMode"], x["chunkedTransfer"], x["useCrcForCachedBundle"],
            x["useUnityWebRequestForLocalBundles"], x["clearOtherCachedVersionsWhenLoaded"]) == (1, True, True, True,
                                                                                                True)


def test_other_extra_data_types_keep_their_type():
    data = build([("k", "Assets/k.asset", [], {"extra_object": ("Example.Options", b"")}),
                  ("j", "Assets/j.asset", [])])
    k, j = parse_locations(data)
    assert k["extra_data"] == {"type": "Example.Options"} and j["extra_data"] is None


@pytest.mark.parametrize("path_ids", [False, True])
def test_parse_agrees_with_full_decode(path_ids):
    data = build(remote_entries(), path_ids=path_ids)
    short, full = parse(data), parse_locations(data)
    assert [(e["offset"], e["primary_key"], e["internal_id"], list(e["dependencies"])) for e in short] == \
        [(e["offset"], e["primary_key"], e["internal_id"], e["dependencies"]) for e in full]


def test_rejects_other_formats():
    data = bytearray(build(remote_entries()))
    data[4] = 3
    for f in (parse_header, parse_locations, parse_keys):
        with pytest.raises(ValueError, match="unsupported catalog format"):
            f(bytes(data))


# ---------------------------------------------------------------- Catalog additions
def make_apk(path, entries=APK):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("assets/aa/catalog.bin", build(entries))
    return path


def test_location_kind_and_file_name():
    assert location_kind(synth.remote("x.bundle")) == "bundle"
    assert location_kind(synth.local("x.bundle")) == "bundle"
    assert location_kind(synth.remote("a/b_1")) == "raw" and location_kind(synth.local("a/b")) == "raw"
    assert location_kind("Assets/x.png") == "asset" and location_kind("Packages/p/x.shader") == "asset"
    assert location_kind("something") == "other"
    assert file_name(synth.remote("x.bundle")) == "x.bundle" and file_name(synth.local("x.bundle")) == "x.bundle"
    assert file_name(synth.remote(RAW)) == RAW and file_name(synth.local("cri/v_1")) == "cri/v_1"


def test_catalog_bundles_raw_files_locations(tmp_path):
    remote = build(remote_entries())
    cat = Catalog(remote, tmp_path / "cache")
    assert [b.name for b in cat.bundles()] == [f"chara_a_{H1}.bundle", f"chara_b_{H2}.bundle", "shared.bundle"]
    assert [b.remote for b in cat.bundles()] == [True, True, False]
    assert [file_name(e["internal_id"]) for e in cat.raw_files()] == [RAW]
    assert cat.cached_raw(cat.raw_files()[0]) is None
    assert cat.sources() == {"remote": remote}
    locs = cat.locations()
    assert [e["offset"] for e in locs] == [e["offset"] for e in cat.entries]
    assert {e["catalog"] for e in locs} == {"remote"}
    assert [e["kind"] for e in locs] == ["asset", "asset", "bundle", "bundle", "asset", "bundle", "raw", "asset"]

    with_apk = Catalog(remote, tmp_path / "cache", apk=make_apk(tmp_path / "base.apk"))
    shared = [b for b in with_apk.bundles() if b.name == "shared.bundle"]
    assert len(shared) == 1 and shared[0].offset >= APK_OFFSET_BASE        # the APK catalog's location wins
    assert [file_name(e["internal_id"]) for e in with_apk.raw_files()] == ["cri_emb/voice_" + H4, RAW]
    locs = with_apk.locations()
    apk_locs = [e for e in locs if e["catalog"] == "apk"]
    assert len(apk_locs) == 3 and all(e["offset"] >= APK_OFFSET_BASE for e in apk_locs)
    assert all(d >= APK_OFFSET_BASE for e in apk_locs for d in e["dependencies"])
    assert set(with_apk.sources()) == {"remote", "apk"}
    locs[0]["kind"] = "changed"
    assert with_apk.locations()[0]["kind"] == "asset"                      # callers get copies


# ---------------------------------------------------------------- index
def test_index():
    remote, apk = build(remote_entries(), build_hash="cd" * 16), build(APK)
    doc = index(remote, apk)
    assert doc["schema"] == contract.CATALOG_INDEX
    assert doc["catalogs"]["remote"]["sha256"] == contract.sha256(remote)
    assert doc["catalogs"]["remote"]["header"]["buildResultHash"] == "cd" * 16
    assert (doc["catalogs"]["remote"]["locations"], doc["catalogs"]["apk"]["locations"]) == (8, 3)
    assert doc["catalogs"]["apk"]["keys"] == 3
    ids = [loc["id"] for loc in doc["locations"]]
    assert ids[0].startswith("remote:") and ids[-1].startswith("apk:")
    a = doc["locations"][0]
    assert a["primaryKey"] == "Char/A" and a["kind"] == "asset" and all(d.startswith("remote:")
                                                                         for d in a["dependencies"])
    bundles = {b["stable"]: b for b in doc["bundles"]}
    assert set(bundles) == {"chara_a", "chara_b", "shared"}
    a = bundles["chara_a"]
    assert (a["name"], a["hash"], a["crc"], a["size"], a["remote"]) == (f"chara_a_{H1}.bundle", H1, 11, 1000, True)
    shared = bundles["shared"]
    assert shared["location"].startswith("apk:") and len(shared["locations"]) == 2
    assert (shared["size"], shared["crc"], shared["remote"], shared["flags"]) == (9, 6, False, 4)
    raws = {r["stable"]: r for r in doc["rawFiles"]}
    assert set(raws) == {"cri_x/sound/bgm_a", "cri_emb/voice"} and raws["cri_x/sound/bgm_a"]["size"] == 300
    assert doc["collisions"] == {"bundles": [], "rawFiles": []}
    assert contract.encode(index(remote, apk)) == contract.encode(doc)           # deterministic
    assert "apk" not in index(remote)["catalogs"]


def test_index_stable_name_collisions():
    entries = [(f"x_{h}.bundle", synth.remote(f"x_{h}.bundle"), []) for h in (H1, H2)]
    doc = index(build(entries))
    assert [b["stable"] for b in doc["bundles"]] == [f"x_{H1}.bundle", f"x_{H2}.bundle"]
    assert doc["collisions"]["bundles"] == [[f"x_{H1}.bundle", f"x_{H2}.bundle"]]
    assert stable_raw_name("a/b_" + H1) == "a/b" and stable_raw_name("a/b") == "a/b"


# ---------------------------------------------------------------- diff
def test_diff_identical_is_empty():
    doc = index(build(remote_entries()))
    d = diff(doc, doc)
    assert d["summary"]["keys"] == {"added": 0, "removed": 0, "changed": 0}
    assert d["affectedKeys"] == [] and d["bundles"]["unchanged"] == 3 and d["rawFiles"]["unchanged"] == 1


def test_diff_synthetic_mutation():
    old = index(build(remote_entries()))
    new_entries = remote_entries(h_b=H3, raw="cri_x/sound/bgm_a_" + "b" * 32,
                                 tex_iid="Assets/Game/Char/A2.png")[:-1]                 # Story/物語 removed
    new_entries.append(("Char/C", "Assets/Game/Char/C.prefab", [8]))
    new_entries.append((f"chara_c_{H4}.bundle", synth.remote(f"chara_c_{H4}.bundle"), []))
    new = index(build(new_entries))
    d = diff(old, new)
    assert d["keys"]["added"] == ["Char/C"] and d["keys"]["removed"] == ["Story/物語"]
    (changed,) = d["keys"]["changed"]
    assert changed["key"] == "Char/A" and changed["fields"] == ["internalId"]
    assert changed["old"][0]["dependencies"] == ["bundle:chara_a", "bundle:shared"]
    b = d["bundles"]
    assert [f["stable"] for f in b["added"]] == ["chara_c"] and b["removed"] == []
    (cb,) = b["changed"]
    assert cb["stable"] == "chara_b" and cb["old"]["name"] == f"chara_b_{H2}.bundle"
    assert cb["new"]["name"] == f"chara_b_{H3}.bundle" and "hash" in cb["fields"] and b["unchanged"] == 2
    (cr,) = d["rawFiles"]["changed"]
    assert cr["stable"] == "cri_x/sound/bgm_a" and cr["fields"][:2] == ["name", "hash"]
    # Char/B and Story/物語 reach chara_b, Sound/bgm_a the raw file, Char/A and Char/C changed / were added
    assert d["affectedKeys"] == ["Char/A", "Char/B", "Char/C", "Sound/bgm_a", "Story/物語"]
    assert d["old"]["remote"] == old["catalogs"]["remote"]["sha256"] and d["new"]["apk"] is None
    assert contract.encode(diff(old, new)) == contract.encode(d)
    text = format_diff(d)
    assert "~ bundle chara_b" in text and "+ key Char/C" in text and text.endswith("affected keys 5\n")


def test_diff_crc_only_change_affects_dependents():
    old = index(build(remote_entries()))
    new = index(build(remote_entries(extra_b={"crc": 99})))
    d = diff(old, new)
    (cb,) = d["bundles"]["changed"]
    assert cb["fields"] == ["crc"] and d["affectedKeys"] == ["Char/B", "Story/物語"]


def test_diff_with_addresses():
    doc = index(build(remote_entries()))
    addr = {"objects": {"chara_a/Assets/Game/Char/A.png": {"object": "CAB-1:5", "class": "Texture2D", "name": "A",
                                                           "byteSize": 10, "typeHash": "00"}}}
    moved = {"objects": {"chara_a/Assets/Game/Char/A.png": {"object": "CAB-2:5", "class": "Texture2D", "name": "A",
                                                            "byteSize": 12, "typeHash": "00"}}}
    d = diff(doc, doc, addr, moved)
    assert d["summary"]["objects"]["changed"] == 1
    assert "~ object chara_a/Assets/Game/Char/A.png (object, byteSize)" in format_diff(d)


# ---------------------------------------------------------------- versions in the store
def test_catalog_db(tmp_path):
    db = CatalogDB(tmp_path / "store")
    assert db.versions() == []
    remote, apk = build(remote_entries()), build(APK)
    v = db.add(remote, apk, region="r1", language="xx", hash_text="00ff" * 8)
    rsha = contract.sha256(remote)
    assert v["id"] == version_id(rsha, contract.sha256(apk)) and v["labels"] == [rsha[:12]] and v["seq"] == 1
    assert v["hash"] == "00ff" * 8 and v["remote"]["buildResultHash"] == "0" * 32
    assert db.path(rsha).read_bytes() == remote and db.catalog_bytes(v) == (remote, apk)
    again = db.add(remote, apk, label="1.2.3", region="r1", language="xx", resource_version="1.2.3")
    assert again["id"] == v["id"] and again["labels"] == [rsha[:12], "1.2.3"] and again["resourceVersion"] == "1.2.3"
    assert len(db.versions()) == 1
    other = build(remote_entries(h_a=H3))
    w = db.add(other, None, region="r1", language="yy", resource_version="1.2.3")
    assert w["labels"] == ["1.2.3"] and w["seq"] == 2 and w["apk"] is None
    assert db.get("latest")["id"] == w["id"]
    assert db.get("1.2.3", language="xx")["id"] == v["id"]
    with pytest.raises(ValueError, match="names 2 catalog versions"):
        db.get("1.2.3")
    assert db.get(v["id"][:8])["id"] == v["id"] and db.get(rsha[:10])["id"] == v["id"]
    with pytest.raises(KeyError):
        db.get("nothing")
    with pytest.raises(ValueError, match="already names"):
        db.add(build(remote_entries(h_a=H4)), None, label="1.2.3", region="r1", language="xx")
    assert db.index(v) == index(remote, apk)
    ins = db.inputs(v)
    assert [i.role for i in ins] == ["remote", "apk"] and ins[0].locators[0]["path"] == str(db.path(rsha))
    assert (tmp_path / "store" / "catalogs" / "index.json").read_bytes() == contract.encode(
        {"schema": "nnnotes.catalogs/1", "versions": db.versions()})
    assert default_label("ab" * 32) == "ab" * 6 and default_label("ab" * 32, "9.9") == "9.9"


def test_fetch_with_and_without_hash(tmp_path):
    root = tmp_path / "cdn"
    (root / "asset" / "Android").mkdir(parents=True)
    data = build(remote_entries())
    (root / "asset" / "Android" / "catalog_main_xx.bin").write_bytes(data)
    assert fetch(root.as_uri(), "xx") == (data, None)
    (root / "asset" / "Android" / "catalog_main_xx.hash").write_bytes(b"0123abcd\n")
    assert fetch(root.as_uri() + "/", "xx") == (data, "0123abcd")


# ---------------------------------------------------------------- stage
def test_index_stage(tmp_path):
    db = CatalogDB(tmp_path / "store")
    remote, apk = build(remote_entries()), build(APK)
    v = db.add(remote, apk)
    store = Store(tmp_path / "store")
    stage = IndexStage()
    env = Env(store, {"catalogs": {"main": db.inputs(v)}})
    assert stage.subjects(env) == ["main"]
    task = describe(stage, "main", None, env)
    assert task.id == "catalog.index:main"
    ex = execute(task, store, registry(stage))
    assert ex.status == "ran" and ex.result_status == "ok"
    (rec,) = store.result(task.key)["artifacts"]
    assert rec["id"] == "catalog.index:main#index" and rec["semantics"]["facts"]["bundles"] == 3
    assert contract.loads(store.read(rec["content"]["sha256"])) == index(remote, apk)
    assert execute(task, store, registry(stage)).status == "hit"
    only_remote = Env(store, {"catalogs": {"main": db.inputs(v)[:1]}})
    assert describe(stage, "main", None, only_remote).key != task.key
    with pytest.raises(ValueError, match="expected remote"):
        describe(stage, "main", None, Env(store, {"catalogs": {"main": db.inputs(v)[1:]}}))
