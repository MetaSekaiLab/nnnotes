"""Master data views on synthetic master tables and a synthetic address index (no game data)."""
import copy
import json
import pickle
import re
import subprocess
import sys
from pathlib import Path

import pytest

from nnnotes import contract, languages, views
from nnnotes.contract import Input
from nnnotes.orchestrate import Orchestrator
from nnnotes.plan import plan
from nnnotes.stages import Env, Output, Pending, Stage, describe, execute, registry
from nnnotes.store import Store
from nnnotes.views import AddressIndex, Obj, Recorder, ViewError, ViewStage

ROOT = Path(__file__).resolve().parents[1]


def o(n, cls, name, stable=None):
    return Obj(f"CAB-{n:02d}:{n}", cls, name, stable)


def text(tid, base):
    """A MasterText row with a distinct text per language."""
    return {"_id": tid, **{col: f"{base}-{code}" for code, (_, col) in languages.LANGUAGES.items()}}


# ---------------------------------------------------------------- addresses
def test_parse_address():
    assert views.parse_address("MemberCard/1/member_full[member_full]") == ("MemberCard/1/member_full", "member_full")
    assert views.parse_address("MemberCard/1/skill_sprite") == ("MemberCard/1/skill_sprite", None)
    assert views.parse_address("MemberCard/MemberCommon/member_background1[member_background1_0]") == \
        ("MemberCard/MemberCommon/member_background1", "member_background1_0")
    assert views.parse_address("a[b]c") == ("a[b]c", None)
    assert views.parse_address("[x]") == ("[x]", None)


TEX = o(1, "Texture2D", "member_full")
SPR_MAIN = o(2, "Sprite", "member_full")
SPR_PIVOT = o(3, "Sprite", "detail_pivot")


def index(**extra):
    return AddressIndex({"MemberCard/1/member_full": [TEX, SPR_MAIN, SPR_PIVOT],
                         "Card/two": [o(4, "Sprite", "a"), o(5, "Sprite", "b")],
                         "Card/dup": [o(6, "Sprite", "x"), o(7, "Sprite", "x")],
                         "Card/prefab": [o(8, "GameObject", "prefab"), o(9, "Texture2D", "other")],
                         "Card/pair": [o(10, "GameObject", "x"), o(11, "Texture2D", "y")],
                         "Card/empty": [], **extra})


def test_resolve_sub_objects():
    ix = index()
    r = views.resolve(ix, "MemberCard/1/member_full[member_full]", "Sprite")
    assert r == {"address": "MemberCard/1/member_full[member_full]", "status": "ok", "object": SPR_MAIN.id,
                 "class": "Sprite"}
    assert views.resolve(ix, "MemberCard/1/member_full[member_full]", "Texture2D")["object"] == TEX.id
    # with any class, the first object of that name in stored order (here the texture), and how many fit
    anyclass = views.resolve(ix, "MemberCard/1/member_full[member_full]")
    assert (anyclass["object"], anyclass["among"]) == (TEX.id, 2)
    miss = views.resolve(ix, "MemberCard/1/member_full[face_center]", "Sprite")
    assert miss["status"] == "missing-sub"
    assert [a[2] for a in miss["available"]] == ["member_full", "member_full", "detail_pivot"]
    dup = views.resolve(ix, "Card/dup[x]", "Sprite")
    assert (dup["status"], dup["object"], dup["among"]) == ("ok", o(6, "", "").id, 2)


def test_resolve_without_sub():
    ix = index()
    # expected Sprite on a sprite sheet: its first sprite in stored order
    r = views.resolve(ix, "MemberCard/1/member_full", "Sprite")
    assert (r["object"], r["among"]) == (SPR_MAIN.id, 2)
    # no expected class: the first object (the texture, its sheet's main object)
    assert views.resolve(ix, "MemberCard/1/member_full")["object"] == TEX.id
    assert views.resolve(ix, "Card/two", "Sprite")["object"] == o(4, "", "").id
    assert views.resolve(ix, "Card/two", "Texture2D")["status"] == "missing-sub"
    assert views.resolve(ix, "Card/prefab", "GameObject")["object"] == o(8, "", "").id
    assert "among" not in views.resolve(ix, "Card/prefab", "GameObject")
    # a list of classes: the first class that fits
    assert views.resolve(ix, "Card/prefab", ["Texture2D", "GameObject"])["object"] == o(9, "", "").id
    assert views.resolve(ix, "Card/pair", ["GameObject", "Texture2D"])["object"] == o(10, "", "").id
    assert views.resolve(ix, "Card/pair", ["Sprite", "GameObject"])["object"] == o(10, "", "").id


def test_stored_order_decides_not_ids():
    sheet = [o(9, "Texture2D", "s"), o(8, "Sprite", "s_1"), o(7, "Sprite", "s_0")]
    ix = AddressIndex({"S": sheet})
    assert ix.objects("S") == sheet
    assert views.resolve(ix, "S", "Sprite")["object"] == o(8, "", "").id
    doc = {"keys": {"S": [{"location": 1, "type": "UnityEngine.Sprite", "bundle": "b", "internalId": "s.png",
                           "objects": [x.id for x in sheet]}]},
           "objects": {f"b/s.png[{x.name}]": {"object": x.id, "class": x.cls, "name": x.name} for x in sheet}}
    assert [x.id for x in AddressIndex.from_addresses(doc).objects("S")] == [x.id for x in sheet]
    rec = Recorder(ix)
    views.resolve(rec, "S", "Sprite")
    assert [x.id for x in AddressIndex.from_subset(rec.subset()).objects("S")] == [x.id for x in sheet]


def test_resolve_missing_keys():
    ix = index()
    assert views.resolve(ix, "Nope/1[a]", "Sprite") == {"address": "Nope/1[a]", "status": "missing-key",
                                                         "detail": "not a catalog key"}
    assert views.resolve(ix, "Card/empty")["detail"] == "no census objects"


def test_stable_address_is_carried():
    stable = contract.stable_address("membercard_1", "assets/x/member_full.png", name="member_full")
    ix = AddressIndex({"K/1": [Obj("CAB-aa:1", "Sprite", "k", stable)]})
    assert views.resolve(ix, "K/1[k]")["stable"] == stable
    assert Obj.from_json(ix.objects("K/1")[0].to_json()) == ix.objects("K/1")[0]


def test_address_index_build_and_prefixes():
    locations = {"MemberCard/1/member_full": "Assets/AddressableResources/MemberCard/1/member_full.png",
                 "MemberCard/1/skill_sprite": "Assets/AddressableResources/MemberCard/1/skill_sprite.png",
                 "Band/1/band_logo": "Assets/AddressableResources/Band/1/band_logo.png",
                 "Band/1/BandItem/101/band_item": "Assets/AddressableResources/Band/1/BandItem/101/band_item.png"}
    containers = {"assets/addressableresources/membercard/1/member_full.png": [TEX, SPR_MAIN]}
    ix = AddressIndex.build(locations, containers)
    assert ix.objects("MemberCard/1/member_full") == [TEX, SPR_MAIN]
    assert ix.objects("MemberCard/1/skill_sprite") == []
    assert ix.objects("Other") is None
    assert ix.keys("MemberCard/") == ["MemberCard/1/member_full", "MemberCard/1/skill_sprite"]
    assert views.prefix_keys(ix, ["Band/"], ["Band/*/BandItem/"]) == ["Band/1/band_logo"]
    assert views.prefix_keys(ix, ["Band/*/BandItem/"]) == ["Band/1/BandItem/101/band_item"]
    again = AddressIndex.from_json(json.loads(json.dumps(ix.to_json())))
    assert again.to_json() == ix.to_json()


def test_address_index_of_an_address_table():
    doc = {"schema": "nnnotes.addresses/1",
           "keys": {"MemberCard/1/member_full": [
                        {"location": 1, "type": "UnityEngine.Texture2D", "bundle": "membercard_1",
                         "internalId": "Assets/MemberCard/1/member_full.png", "objects": ["CAB-a:1", "CAB-a:2"]},
                        {"location": 2, "type": "UnityEngine.Sprite", "bundle": "membercard_1",
                         "internalId": "Assets/MemberCard/1/member_full.png", "objects": ["CAB-a:2", "CAB-a:1"]}],
                    "MemberCard/2/member_full": [{"location": 3, "type": "UnityEngine.Texture2D",
                                                  "bundle": "membercard_2", "internalId": "x", "objects": None}],
                    "Cri/Sound/bgm": [{"location": 4, "type": "System.Object", "raw": "bgm.acb",
                                       "internalId": "y"}]},
           "objects": {"membercard_1/assets/membercard/1/member_full.png":
                           {"object": "CAB-a:1", "class": "Texture2D", "name": "member_full"},
                       "membercard_1/assets/membercard/1/member_full.png[member_full]":
                           {"object": "CAB-a:2", "class": "Sprite", "name": "member_full"}}}
    ix = AddressIndex.from_addresses(doc)
    assert ix.objects("MemberCard/1/member_full") == [
        Obj("CAB-a:1", "Texture2D", "member_full", "membercard_1/assets/membercard/1/member_full.png"),
        Obj("CAB-a:2", "Sprite", "member_full", "membercard_1/assets/membercard/1/member_full.png[member_full]")]
    assert ix.objects("MemberCard/2/member_full") == [] and ix.objects("Cri/Sound/bgm") == []
    r = views.resolve(ix, "MemberCard/1/member_full[member_full]", "Sprite")
    assert (r["object"], r["stable"]) == ("CAB-a:2", "membercard_1/assets/membercard/1/member_full.png[member_full]")


# ---------------------------------------------------------------- rules
def base_rules():
    return {"format": 1, "resolvers": {}, "views": {"things": {
        "version": 1, "table": "MasterThing", "vars": {"id": "_id", "path": "_path"},
        "roles": [{"role": "image", "address": "{path}", "expect": "Sprite", "required": True}]}}}


@pytest.mark.parametrize("edit, message", [
    (lambda r: r.update(format=2), "format"),
    (lambda r: r["views"]["things"].update(colour=1), "unknown keys"),
    (lambda r: r["views"]["things"].update(version=0), "version"),
    (lambda r: r["views"]["things"]["roles"][0].update(address="{nope}"), "not a var"),
    (lambda r: r["views"]["things"]["roles"][0].update(address="{path.x}"), "plain name"),
    (lambda r: r["views"]["things"]["roles"][0].update(when={"field": "id", "op": "like", "value": 1}),
     "unknown operator"),
    (lambda r: r["views"]["things"]["roles"][0].update(when={"field": "colour", "op": "eq", "value": 1}),
     "neither a var nor a column"),
    (lambda r: r["views"]["things"]["roles"][0].update(each={"var": "id", "values": [1]}), "already a var"),
    (lambda r: r["views"]["things"]["roles"][0].update(each={"var": "x", "values": [1], "column": "_c"}),
     "one of values"),
    (lambda r: r["views"]["things"]["roles"].append({"role": "image", "address": "{path}"}), "repeated role"),
    (lambda r: r["views"]["things"]["roles"].append({"role": "icon", "resolver": "Nope"}), "known resolver"),
    (lambda r: r.update(resolvers={"R": {"1": {"name": "X", "address": "A"}}}) or
     r["views"]["things"]["roles"].append({"role": "icon", "resolver": "R", "each": {"var": "x", "values": [1]}}),
     "known resolver"),
    (lambda r: r.update(resolvers={"R": {"x": {"name": "X", "address": "A"}}}), "integers"),
    (lambda r: r.update(resolvers={"R": {"1": {"name": "X", "address": "{a}", "table": "T"}}}), "not a var"),
    (lambda r: r["views"]["things"]["vars"].update(bad=["_a", "NoDot"]), "Table.column"),
    (lambda r: r["views"]["things"].update(sources=[]), "either a table or sources"),
    (lambda r: r["views"]["things"].update(prefixes=["*/x"]), "prefix"),
    (lambda r: r["views"]["things"]["roles"][0].update(expect=["any"]), "expect"),
])
def test_check_rules_refuses(edit, message):
    r = base_rules()
    edit(r)
    with pytest.raises(ViewError, match=message):
        views.check_rules(r)


def test_package_rules_load_and_every_view_builds_on_empty_tables():
    rules = views.load_rules()
    assert rules["format"] == views.FORMAT
    names = views.view_names(rules)
    for required in ("cards", "support_cards", "characters", "bands", "band_items", "stamps", "items", "degrees",
                     "jackets", "gacha", "home_banners", "login_bonuses", "story_chapters", "story_episodes",
                     "shops", "season_passes", "comics", "skill_icons", "rewards"):
        assert required in names
    tables = {t: [] for n in names for t in views.view_tables(rules, n)}
    docs = views.build_all(rules, tables, AddressIndex({}))
    assert sorted(docs) == names
    for n, d in docs.items():
        assert d["schema"] == contract.VIEW and d["view"] == n and d["rows"] == []
        assert d["rules"] == views.rules_digest(rules, n)
    assert views.view_tables(rules, "cards") == ["MasterBand", "MasterCharacter", "MasterMemberCard", "MasterText"]
    assert "MasterItem" in views.view_tables(rules, "rewards")


def test_a_missing_table_is_an_error():
    with pytest.raises(ViewError, match="MasterThing is not in the master data"):
        views.build(base_rules(), "things", {}, AddressIndex({}))
    with pytest.raises(ViewError, match="no view"):
        views.build(base_rules(), "nope", {}, AddressIndex({}))


def test_rules_digest_covers_only_the_view():
    r = base_rules()
    r["views"]["other"] = copy.deepcopy(r["views"]["things"])
    d = views.rules_digest(r, "things")
    r["views"]["other"]["version"] = 2
    assert views.rules_digest(r, "things") == d
    r["views"]["things"]["roles"][0]["expect"] = "Texture2D"
    assert views.rules_digest(r, "things") != d


# ---------------------------------------------------------------- the cards view of the package rules
def card(cid, rarity, character):
    return {"_id": cid, "_assetID": cid, "_rarity": rarity, "_characterID": character,
            "_nameTextID": f"Card_Name_{cid}", "_subtitleTextID": f"Card_Sub_{cid}"}


CARD_TABLES = {
    "MasterMemberCard": [card(20, 4, 1), card(10, 2, 1), card(11, 3, 2)],
    "MasterCharacter": [{"_id": 1, "_bandID": 1}, {"_id": 2, "_bandID": 7}],
    "MasterBand": [{"_id": 1, "_memberRarityRBackgroundAssetPath": "MemberCard/MemberCommon/member_background1[formation]"}],
    "MasterText": [text("Card_Name_10", "ten"), text("Card_Name_11", "eleven"), text("Card_Name_20", "twenty"),
                   text("Card_Sub_10", "sub")],
}


def card_entries():
    n = iter(range(10, 100))

    def sprite_sheet(key, *subs):
        base = key.rpartition("/")[2]
        return {key: [o(next(n), "Texture2D", base)] + [o(next(n), "Sprite", s) for s in subs]}
    e = {}
    for a in (10, 11, 20):
        e.update(sprite_sheet(f"MemberCard/{a}/member_full", "member_full", "detail_pivot"))
        e.update(sprite_sheet(f"MemberCard/{a}/member_thumbnail", "member_thumbnail", "square"))
        e.update(sprite_sheet(f"MemberCard/{a}/member_character", "main"))
    e.update(sprite_sheet("MemberCard/11/member_background", "member_background", "formation"))
    e.update(sprite_sheet("MemberCard/MemberCommon/member_background1", "formation"))
    for part in ("anime_part1", "anime_part2", "live2d_in"):
        e[f"MemberCard/20/movie/{part}"] = [o(next(n), "MonoBehaviour", part)]
    e["MemberCard/README"] = [o(next(n), "TextAsset", "README")]
    return e


def card_index():
    return AddressIndex(card_entries())


def cards():
    return views.build(views.load_rules(), "cards", CARD_TABLES, card_index())


def test_cards_rows_names_and_roles():
    doc = cards()
    assert [r["id"] for r in doc["rows"]] == [10, 11, 20]
    r10, r11, r20 = doc["rows"]
    assert r10["vars"] == {"id": 10, "asset": 10, "rarity": 2, "band": 1,
                           "r_background": "MemberCard/MemberCommon/member_background1[formation]"}
    assert r10["names"]["name"] == {code: f"ten-{code}" for code in languages.LANGUAGES}
    assert r10["names"]["subtitle"]["ko"] == "sub-ko"
    assert r11["names"]["subtitle"] is None                      # a text id with no MasterText row
    assert r10["roles"]["member_full"]["status"] == "ok"
    assert r10["roles"]["member_full"]["class"] == "Sprite"
    assert r10["roles"]["member_full_detail_pivot"]["status"] == "ok"
    assert r10["roles"]["member_full_face_center"]["status"] == "missing-sub"
    assert r10["roles"]["skill_sprite"]["status"] == "missing-key"
    # R cards: the band's background, not their own
    assert r10["roles"]["member_background"] == {"status": "not-applicable"}
    assert r10["roles"]["member_background_r"]["status"] == "ok"
    assert r11["roles"]["member_background"]["status"] == "ok"
    assert r11["roles"]["member_background_r"] == {"status": "not-applicable"}
    # the band of the character: its common background in the gacha
    assert r11["roles"]["gacha_band_background"]["address"] == \
        "MemberCard/MemberCommon/member_background7[member_background7_0]"


def test_cards_movies_are_enumerated_for_ssr_only():
    doc = cards()
    r10, _, r20 = doc["rows"]
    parts = ["anime_part1", "anime_part2", "live2d_in", "live2d_loop"]
    assert [e["each"] for e in r20["roles"]["movie"]] == [{"part": p} for p in parts]
    assert [e["status"] for e in r20["roles"]["movie"]] == ["ok", "ok", "ok", "missing-key"]
    assert r20["roles"]["movie"][0]["address"] == "MemberCard/20/movie/anime_part1"
    assert [e["status"] for e in r10["roles"]["movie"]] == ["not-applicable"] * 4


def test_cards_coverage_both_ways():
    doc = cards()
    cov = doc["coverage"]
    assert cov["rows"] == 3
    assert cov["roles"]["member_full"] == {"required": True, "counts": {"ok": 3}, "gaps": []}
    assert cov["roles"]["movie"]["counts"] == {"missing-key": 1, "not-applicable": 8, "ok": 3}
    assert cov["roles"]["movie"]["required"] is False and cov["roles"]["movie"]["gaps"] == []
    assert cov["roles"]["member_background"]["counts"] == {"missing-key": 1, "not-applicable": 1, "ok": 1}
    assert cov["reverse"]["prefixes"] == ["MemberCard/"]
    assert cov["reverse"]["unreferenced"] == ["MemberCard/README"]
    assert views.gaps(doc) == 0
    # a required role that does not resolve is a gap of its row
    tables = copy.deepcopy(CARD_TABLES)
    tables["MasterMemberCard"].append(card(30, 3, 1))
    doc = views.build(views.load_rules(), "cards", tables, card_index())
    assert doc["coverage"]["roles"]["member_full"]["gaps"] == [30]
    assert views.gaps(doc) == 4                                  # member_full, member_character, 2 thumbnails


def test_view_documents_are_deterministic_and_canonical():
    a, b = cards(), cards()
    assert contract.encode(a) == contract.encode(b)
    assert contract.loads(contract.encode(a)) == a
    shuffled = copy.deepcopy(CARD_TABLES)
    shuffled["MasterMemberCard"].reverse()
    assert contract.encode(views.build(views.load_rules(), "cards", shuffled, card_index())) == contract.encode(a)


def test_recorded_subset_is_all_a_view_reads():
    rec = Recorder(card_index())
    doc = views.build(views.load_rules(), "cards", CARD_TABLES, rec)
    sub = rec.subset()
    assert "MemberCard/20/movie/live2d_loop" in sub["keys"] and sub["keys"]["MemberCard/20/movie/live2d_loop"] is None
    assert sub["prefixes"]["MemberCard/"][-1] == "MemberCard/README"
    assert list(sub["keys"]) == sorted(sub["keys"])
    # the subset alone gives the same view: nothing else of the index enters it
    only = AddressIndex.from_subset(json.loads(json.dumps(sub)))
    assert views.build(views.load_rules(), "cards", CARD_TABLES, only) == doc
    assert only.keys("MemberCard/") == sub["prefixes"]["MemberCard/"]
    again = Recorder(card_index())
    views.build(views.load_rules(), "cards", CARD_TABLES, again)
    assert contract.digest(again.subset()) == contract.digest(sub)


# ---------------------------------------------------------------- vars, conditions, enumerations
def thing_rules(**view):
    v = {"version": 1, "table": "MasterThing", "vars": {"id": "_id", "path": "_path"},
         "roles": [{"role": "image", "address": "{path}", "expect": "Sprite", "required": True}]}
    v.update(view)
    return {"format": 1, "resolvers": {}, "views": {"things": v}}


def test_no_value_and_broken_chains():
    rules = thing_rules(vars={"id": "_id", "path": "_path", "band": ["_char", "MasterChar._band"]},
                        roles=[{"role": "image", "address": "{path}"},
                               {"role": "logo", "address": "Band/{band}/logo"},
                               {"role": "pad", "address": "Pass/{id:05d}"}])
    tables = {"MasterThing": [{"_id": 1, "_path": "", "_char": 5}, {"_id": 2, "_path": None, "_char": 9},
                              {"_id": 3, "_path": "P/3", "_char": 0}],
              "MasterChar": [{"_id": 5, "_band": 2}]}
    doc = views.build(rules, "things", tables, AddressIndex({"Band/2/logo": [o(1, "Sprite", "logo")]}))
    r1, r2, r3 = doc["rows"]
    assert r1["roles"]["image"] == {"status": "no-value", "detail": "path is empty"}
    assert r1["roles"]["logo"]["status"] == "ok"
    assert r2["roles"]["logo"] == {"status": "no-value", "detail": "MasterChar has no row 9"}
    assert r3["roles"]["image"]["status"] == "missing-key"
    assert r3["roles"]["pad"]["address"] == "Pass/00003"
    assert doc["coverage"]["roles"]["image"]["counts"] == {"missing-key": 1, "no-value": 2}


def test_conditions_on_vars_and_columns():
    rules = thing_rules(roles=[
        {"role": "a", "address": "{path}", "when": {"field": "id", "op": "ge", "value": 2}},
        {"role": "b", "address": "{path}", "when": [{"field": "_kind", "op": "in", "value": ["x", "y"]},
                                                    {"field": "_flag", "op": "nonempty"}]},
        {"role": "c", "address": "{path}", "when": {"field": "_flag", "op": "empty"}}])
    tables = {"MasterThing": [{"_id": 1, "_path": "P", "_kind": "x", "_flag": "on"},
                              {"_id": 2, "_path": "P", "_kind": "z", "_flag": ""}]}
    doc = views.build(rules, "things", tables, AddressIndex({"P": [o(1, "Sprite", "P")]}))
    r1, r2 = doc["rows"]
    assert (r1["roles"]["a"]["status"], r2["roles"]["a"]["status"]) == ("not-applicable", "ok")
    assert (r1["roles"]["b"]["status"], r2["roles"]["b"]["status"]) == ("ok", "not-applicable")
    assert (r1["roles"]["c"]["status"], r2["roles"]["c"]["status"]) == ("not-applicable", "ok")


def test_each_over_a_column_and_the_catalog():
    rules = thing_rules(roles=[
        {"role": "chara", "address": "Chara/{c}/icon", "each": {"var": "c", "column": "_chars"}},
        {"role": "part", "address": "Movie/{id}/{p}", "each": {"var": "p", "catalogPrefix": "Movie/{id}/"},
         "when": {"field": "p", "op": "ne", "value": "skip"}}])
    ix = AddressIndex({"Chara/1/icon": [o(1, "Sprite", "icon")], "Movie/7/b": [o(2, "MonoBehaviour", "b")],
                       "Movie/7/a[x]": [], "Movie/7/skip": [], "Movie/70/c": [o(3, "MonoBehaviour", "c")]})
    doc = views.build(rules, "things", {"MasterThing": [{"_id": 7, "_chars": [2, 1]}]}, ix)
    row = doc["rows"][0]
    assert [(e["each"], e["status"]) for e in row["roles"]["chara"]] == [({"c": 2}, "missing-key"),
                                                                           ({"c": 1}, "ok")]
    assert [(e["each"]["p"], e["status"]) for e in row["roles"]["part"]] == \
        [("a", "missing-key"), ("b", "ok"), ("skip", "not-applicable")]


# ---------------------------------------------------------------- polymorphic references
REWARD_RULES = {"format": 1, "resolvers": {"GameResourceType": {
    "1": {"name": "Item", "table": "MasterItem", "vars": {"path": "_imagePath"}, "address": "{path}",
          "expect": "Sprite", "names": {"name": "_nameTextId"}},
    "7": {"name": "GachaPoint", "address": "Item/common/seal", "expect": "Sprite"}}},
    "views": {"rewards": {"version": 1, "sources": [{"table": "MasterReward", "type": "_resourceType",
                                                     "id": "_resourceId"},
                                                    {"table": "MasterPay", "type": "_payType", "id": "_payId"}],
                          "roles": [{"role": "icon", "resolver": "GameResourceType", "required": True}]}}}


def test_rewards_resolve_through_the_resource_type():
    tables = {"MasterReward": [{"_id": 1, "_resourceType": 1, "_resourceId": 5},
                               {"_id": 2, "_resourceType": 1, "_resourceId": 5},
                               {"_id": 3, "_resourceType": 1, "_resourceId": 6},
                               {"_id": 4, "_resourceType": 4, "_resourceId": 1},
                               {"_id": 5, "_resourceType": 7, "_resourceId": 0}],
              "MasterPay": [{"_id": 1, "_payType": 1, "_payId": 5}, {"_id": 2, "_payType": None, "_payId": 0}],
              "MasterItem": [{"_id": 5, "_imagePath": "Item/common/star", "_nameTextId": "Item_5"}],
              "MasterText": [text("Item_5", "star")]}
    ix = AddressIndex({"Item/common/star": [o(1, "Texture2D", "star"), o(2, "Sprite", "star")],
                       "Item/common/seal": [o(3, "Sprite", "seal")]})
    doc = views.build(REWARD_RULES, "rewards", tables, ix)
    rows = {r["id"]: r for r in doc["rows"]}
    assert list(rows) == ["1:5", "1:6", "4:1", "7:0"]
    assert rows["1:5"]["from"] == ["MasterPay", "MasterReward"]
    assert rows["1:5"]["vars"] == {"type": 1, "id": 5}
    assert rows["1:5"]["roles"]["icon"] == {"resource": "Item", "address": "Item/common/star", "status": "ok",
                                            "object": o(2, "", "").id, "class": "Sprite"}
    assert rows["1:5"]["names"]["name"]["en"] == "star-en"
    assert rows["1:6"]["roles"]["icon"] == {"resource": "Item", "status": "no-value",
                                            "detail": "MasterItem has no row 6"}
    assert rows["4:1"]["roles"]["icon"]["status"] == "not-applicable"
    assert rows["7:0"]["roles"]["icon"]["object"] == o(3, "", "").id
    assert doc["coverage"]["roles"]["icon"]["counts"] == {"no-value": 1, "not-applicable": 1, "ok": 2}
    assert views.view_tables(REWARD_RULES, "rewards") == ["MasterItem", "MasterPay", "MasterReward", "MasterText"]


def test_package_resolvers_cover_the_icon_types():
    res = views.load_rules()["resolvers"]["GameResourceType"]
    assert {int(c) for c in res} >= {1, 2, 3, 8, 9, 17, 18, 19, 1001, 1002, 1003}


# ---------------------------------------------------------------- several views, versions
def test_unreferenced_across_views_sharing_a_prefix():
    rules = thing_rules(prefixes=["Img/"])
    rules["views"]["others"] = dict(copy.deepcopy(rules["views"]["things"]), table="MasterOther")
    tables = {"MasterThing": [{"_id": 1, "_path": "Img/a"}], "MasterOther": [{"_id": 1, "_path": "Img/b"}]}
    ix = AddressIndex({"Img/a": [o(1, "Sprite", "a")], "Img/b": [o(2, "Sprite", "b")], "Img/c": []})
    docs = views.build_all(rules, tables, ix)
    assert docs["things"]["coverage"]["reverse"]["unreferenced"] == ["Img/b", "Img/c"]
    assert views.unreferenced(docs.values(), rules, ix) == ["Img/c"]


def test_diff_between_versions():
    rules = thing_rules()
    s1 = contract.stable_address("img_a", "assets/a.png", name="a")
    s2 = contract.stable_address("img_b", "assets/a.png", name="a")
    t1 = {"MasterThing": [{"_id": 1, "_path": "A"}, {"_id": 2, "_path": "B"}, {"_id": 3, "_path": "C"},
                          {"_id": 4, "_path": "D"}]}
    t2 = {"MasterThing": [{"_id": 1, "_path": "A"}, {"_id": 2, "_path": "B"}, {"_id": 3, "_path": "C2"},
                          {"_id": 5, "_path": "E"}]}
    old = views.build(rules, "things", t1, AddressIndex({"A": [Obj("CAB-1:1", "Sprite", "A", s1)],
                                                         "B": [Obj("CAB-1:2", "Sprite", "B", s1)]}))
    new = views.build(rules, "things", t2, AddressIndex({"A": [Obj("CAB-2:1", "Sprite", "A", s1)],
                                                         "B": [Obj("CAB-3:2", "Sprite", "B", s2)],
                                                         "C2": [Obj("CAB-3:3", "Sprite", "C2")]}))
    d = views.diff(old, new)
    assert d["rows"] == {"added": [5], "removed": [4]}
    assert [(c["row"], c["kind"]) for c in d["changes"]] == [(1, "object"), (2, "moved"), (3, "status")]
    assert views.diff(new, new)["changes"] == []
    with pytest.raises(ViewError, match="different views"):
        views.diff(old, dict(new, view="other"))


def test_master_tables_from_a_directory(tmp_path):
    (tmp_path / "MasterThing.json").write_text(json.dumps({"_allData": [{"_id": 1, "_path": "A"}]}),
                                               encoding="utf-8")
    t = views.MasterTables(tmp_path)
    assert t["MasterThing"] == [{"_id": 1, "_path": "A"}]
    assert t.sha256("MasterThing") == contract.sha256((tmp_path / "MasterThing.json").read_bytes())
    with pytest.raises(KeyError):
        t["MasterNope"]
    doc = views.build(thing_rules(), "things", t, AddressIndex({"A": [o(1, "Sprite", "A")]}))
    assert doc["rows"][0]["roles"]["image"]["status"] == "ok"


# ---------------------------------------------------------------- the view stages
def addresses_doc(entries: dict) -> dict:
    """A synthetic address table (nnnotes.addresses/1) of {catalog key: objects}: one location per key in bundle
    `b`, every object listed under a stable address of its own."""
    keys, objects = {}, {}
    for n, (key, objs) in enumerate(sorted(entries.items())):
        keys[key] = [{"location": f"remote:{n}", "type": "UnityEngine.Object", "internalId": f"Assets/{key}.png",
                      "bundle": "b", "objects": [x.id for x in objs]}]
        for x in objs:
            objects[f"b:{x.id.rpartition(':')[2]}"] = {"object": x.id, "class": x.cls, "name": x.name,
                                                       "byteSize": 1, "typeHash": None}
    return {"schema": "nnnotes.addresses/1", "catalog": {"remote": None, "apk": None}, "bundles": {}, "files": {},
            "keys": keys, "objects": objects, "problems": {"files": [], "bundleNames": [], "keys": []}}


class FakeAddresses(Stage):
    """link.addresses standing in for the real stage: its result is the given address table."""
    name = "link.addresses"
    version = 1

    def __init__(self, doc: dict):
        self.data = contract.encode(doc)

    def subjects(self, env):
        return sorted(env.fact("catalogs"))

    def inputs(self, subject, env):
        return [Input("doc", env.store.put(self.data), len(self.data), None, ({"kind": "store"},))]

    def run(self, task, store):
        rec = contract.artifact(contract.artifact_id(task.id, "addresses"),
                                store.add(store.input_bytes(task.input("doc")), "json"), contract.provenance(task),
                                {"kind": "link.addresses", "format": "json"})
        return Output([rec], [])


STAGE_TABLES = {**CARD_TABLES,
                "MasterStamp": [{"_id": 1, "_stampAsset": "Stamp/s1", "_nameTextId": "Stamp_1"}],
                "MasterText": CARD_TABLES["MasterText"] + [text("Stamp_1", "stamp")]}


def stage_entries() -> dict:
    return {**card_entries(), "Stamp/s1": [o(95, "Sprite", "s1")]}


def write_master(d: Path, tables: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        (d / f"{name}.json").write_text(json.dumps({"_allData": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def env_with(store, doc: dict, master, **facts) -> Env:
    """An environment where link.addresses:main has run with the address table `doc`."""
    link = FakeAddresses(doc)
    env = Env(store, {"catalogs": {"main": []}, "master": master, **facts})
    t = describe(link, "main", None, env)
    execute(t, store, {link.name: link})
    env.done(t.id, t.key)
    return env


def test_one_stage_per_view():
    rules = views.load_rules()
    st = views.stages()
    assert [s.name for s in st] == [f"view.{n}" for n in views.view_names(rules)]
    assert all(s.version == rules["views"][s.view]["version"] and s.after == ("link.addresses",) for s in st)
    assert sorted(registry(*st)) == [s.name for s in st]
    again = pickle.loads(pickle.dumps(st[0]))                     # stages cross to worker processes
    assert (again.name, again.rules_bytes) == (st[0].name, st[0].rules_bytes)
    with pytest.raises(ViewError, match="no view"):
        ViewStage("nope", rules)


def test_view_stage_subjects_and_dependencies(tmp_path):
    store, mdir = Store(tmp_path / "s"), write_master(tmp_path / "m", STAGE_TABLES)
    stage = ViewStage("cards", views.load_rules())
    assert stage.subjects(Env(store, {"catalogs": {"main": []}, "master": None})) == []
    assert stage.subjects(Env(store, {"catalogs": {"main": []}, "master": mdir, "views": ["stamps"]})) == []
    assert stage.subjects(Env(store, {"catalogs": {"main": [], "b": []}, "master": mdir, "views": ["cards"]})) == \
        ["b", "main"]
    env = Env(store, {"catalogs": {"main": []}, "master": mdir})
    assert stage.subjects(env) == ["main"] and stage.depends("main", env) == ["link.addresses:main"]
    with pytest.raises(Pending) as e:
        describe(stage, "main", None, env)
    assert e.value.task == "link.addresses:main"
    (mdir / "MasterBand.json").unlink()
    with pytest.raises(ViewError, match="MasterBand is not in the master data"):
        describe(stage, "main", None, env_with(store, addresses_doc(stage_entries()), mdir))


def test_view_stage_runs_on_its_inputs_alone(tmp_path):
    rules = views.load_rules()
    stage = ViewStage("cards", rules)
    mdir, doc = write_master(tmp_path / "m", STAGE_TABLES), addresses_doc(stage_entries())
    a = Store(tmp_path / "a")
    task = describe(stage, "main", None, env_with(a, doc, mdir))
    assert (task.id, task.version, task.atoms, task.context) == ("view.cards:main", 1, {}, {})
    assert [i.role for i in task.inputs] == ["addresses", "master:MasterBand", "master:MasterCharacter",
                                             "master:MasterMemberCard", "master:MasterText", "rules"]
    ex = execute(task, a, {stage.name: stage})
    assert (ex.status, ex.result_status) == ("ran", "ok")
    res = a.result(task.key)
    assert [x["id"] for x in res["artifacts"]] == ["view.cards:main#view"] and res["items"] == []
    view = contract.loads(a.read(res["artifacts"][0]["content"]["sha256"]))
    assert view == views.build(rules, "cards", views.MasterTables(mdir), AddressIndex.from_addresses(doc))
    assert res["artifacts"][0]["semantics"]["facts"]["rows"] == 3
    assert res["artifacts"][0]["semantics"]["facts"]["gaps"] == 0
    # the task description runs in another store holding only its stored inputs: the same result
    b = Store(tmp_path / "b")
    for i in task.inputs:
        if i.locators[0]["kind"] == "store":
            b.put(a.read(i.sha256))
    again = contract.Task.from_json(json.loads(json.dumps(task.to_json())))
    execute(again, b, {stage.name: ViewStage("cards", rules)})
    assert contract.encode(b.result(task.key)) == contract.encode(res)
    # described again elsewhere: the same key; run again: the same output
    assert describe(stage, "main", None, env_with(Store(tmp_path / "c"), doc, mdir)).key == task.key
    assert execute(task, a, {stage.name: stage}, force=True).status == "ran"


def test_view_keys_follow_only_their_own_inputs(tmp_path):
    store, mdir = Store(tmp_path / "s"), write_master(tmp_path / "m", STAGE_TABLES)
    rules = views.load_rules()
    entries = stage_entries()

    def keys(entries=entries, rules=rules):
        env = env_with(store, addresses_doc(entries), mdir)
        return [describe(ViewStage(n, rules), "main", None, env).key for n in ("cards", "stamps")]

    cards, stamps = keys()
    assert keys() == [cards, stamps]
    # a table only the stamps read
    write_master(mdir, {"MasterStamp": STAGE_TABLES["MasterStamp"] + [{"_id": 2, "_stampAsset": "Stamp/s2"}]})
    k = keys()
    assert k[0] == cards and k[1] != stamps
    stamps = k[1]
    # a catalog key under the stamps' prefix; a key no view reads or lists
    k = keys({**entries, "Stamp/s3": [o(96, "Sprite", "s3")]})
    assert k[0] == cards and k[1] != stamps
    assert keys({**entries, "Other/x": [o(97, "Sprite", "x")]}) == [cards, stamps]
    # new object ids under a key the cards read (its bundle was rebuilt)
    moved = {**entries, "MemberCard/10/member_full": [Obj(f"CAB-new:{i}", x.cls, x.name)
                                                      for i, x in enumerate(entries["MemberCard/10/member_full"])]}
    k = keys(moved)
    assert k[0] != cards and k[1] == stamps
    # the rule of another view
    other = copy.deepcopy(rules)
    other["views"]["stamps"]["roles"][0]["expect"] = "Texture2D"
    k = keys(rules=other)
    assert k[0] == cards and k[1] != stamps
    # a table both read
    write_master(mdir, {"MasterText": STAGE_TABLES["MasterText"] + [text("Extra", "extra")]})
    k = keys()
    assert k[0] != cards and k[1] != stamps


def test_png_encoding_never_reaches_view_keys(tmp_path):
    store, mdir = Store(tmp_path / "s"), write_master(tmp_path / "m", STAGE_TABLES)
    stage = ViewStage("cards", views.load_rules())
    assert "png" not in stage.PARAMS
    with pytest.raises(ValueError, match="unknown parameters png"):
        stage.normalize({"png": {"level": 9}})
    env = env_with(store, addresses_doc(stage_entries()), mdir)
    key = describe(stage, "main", None, env).key
    oid = card_entries()["MemberCard/10/member_full"][1].id
    for level in (6, 9):                                          # exports of the objects with other PNG bytes
        t = contract.Task("unity.export", 1, "b", {"png": {"encoder": "pillow", "level": level}})
        rec = contract.artifact(f"{oid}#image", store.add(b"png %d" % level, "png"), contract.provenance(t),
                                {"kind": "texture.image"})
        store.commit(contract.result(t, [rec], [contract.item(oid, "exported", artifacts=[rec["id"]])]))
        env.done(t.id, t.key)
        assert describe(stage, "main", None, env).key == key


class Paths:
    """Layout paths of a synthetic `original` layout."""

    def __init__(self, placed: dict):
        self.placed = placed

    def objects(self, oid):
        return sorted(a for a in self.placed if a.startswith(oid + "#")) + [f"{oid}#unplaced"]

    def artifact(self, aid):
        return self.placed.get(aid)


def test_derived_view_document_has_layout_paths(tmp_path):
    store, mdir = Store(tmp_path / "s"), write_master(tmp_path / "m", STAGE_TABLES)
    stage = ViewStage("cards", views.load_rules())
    task = describe(stage, "main", None, env_with(store, addresses_doc(stage_entries()), mdir))
    execute(task, store, {stage.name: stage})
    res = store.result(task.key)
    full = next(x.id for x in card_entries()["MemberCard/10/member_full"] if x.name == "member_full" and
                x.cls == "Sprite")
    placed = {f"{full}#meta": "MemberCard/10/member_full[member_full].png.meta.json",
              f"{full}#image": "MemberCard/10/member_full[member_full].png"}
    out = stage.derived(task.id, res, store, Paths(placed))
    assert [p for p, _ in out] == ["views/cards.json"]
    doc = contract.loads(out[0][1])
    assert out[0][1] == contract.encode(doc) and doc["layout"] == "original"
    r10 = doc["rows"][0]
    assert r10["roles"]["member_full"]["path"] == "MemberCard/10/member_full[member_full].png"
    assert r10["roles"]["member_full"]["files"] == {"image": placed[f"{full}#image"], "meta": placed[f"{full}#meta"]}
    assert r10["roles"]["member_character"]["path"] is None and r10["roles"]["member_character"]["files"] == {}
    assert "path" not in r10["roles"]["skill_sprite"]                         # missing-key: no object
    assert "path" not in r10["roles"]["movie"][0]
    # everything else is the view document
    view = contract.loads(store.read(res["artifacts"][0]["content"]["sha256"]))
    for row in doc["rows"]:
        for e in (x for v in row["roles"].values() for x in (v if isinstance(v, list) else [v])):
            e.pop("path", None), e.pop("files", None)
    assert {k: v for k, v in doc.items() if k != "layout"} == view
    assert views.view_path("cards", "main") == "views/cards.json"
    assert views.view_path("cards", "zh-Hans") == "views/zh-Hans/cards.json"


def test_views_plan_and_run_after_the_address_table(tmp_path):
    store, mdir = Store(tmp_path / "s"), write_master(tmp_path / "m", STAGE_TABLES)
    vs = views.stages()
    reg = registry(FakeAddresses(addresses_doc(stage_entries())), *vs)
    pipeline = ["link.addresses"] + [s.name for s in vs]
    facts = {"catalogs": {"main": []}, "master": mdir, "views": ["cards", "stamps"]}
    nodes = {n["id"]: n for n in plan(reg, pipeline, store, facts=facts).doc["nodes"]}
    assert sorted(nodes) == ["link.addresses:main", "view.cards:main", "view.stamps:main"]
    assert nodes["view.cards:main"]["status"] == "unknown"
    assert nodes["view.cards:main"]["reasons"] == [{"code": "pending", "task": "link.addresses:main"}]
    first = Orchestrator(store, reg, log=lambda m: None).run(pipeline, facts=facts)
    assert {t: v["status"] for t, v in first.tasks.items()} == dict.fromkeys(nodes, "ran")
    second = Orchestrator(store, reg, log=lambda m: None).run(pipeline, facts=facts)
    assert {t: v["status"] for t, v in second.tasks.items()} == dict.fromkeys(nodes, "hit")
    assert plan(reg, pipeline, store, facts=facts).check() == 0


def test_views_import_neither_unitypy_nor_numpy():
    code = ("import sys, nnnotes.views; "
            "print(sorted(m for m in ('UnityPy', 'numpy', 'PIL', 'nnnotes.export') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    assert out.strip() == "[]"


# ---------------------------------------------------------------- documentation
def test_every_view_role_and_status_is_documented():
    doc = (ROOT / "docs" / "views.md").read_text(encoding="utf-8")
    rules = views.load_rules()
    for name, v in rules["views"].items():
        section = re.search(rf"^### `{name}`\n(.*?)(?=^### |\Z)", doc, re.S | re.M)
        assert section, f"docs/views.md has no section for {name}"
        for role in v.get("roles", []):
            assert f"`{role['role']}`" in section.group(1), f"{name}.{role['role']} is not documented"
    for status in views.STATUSES:
        assert f"`{status}`" in doc
    for op in views.OPS:
        assert f"`{op}`" in doc
