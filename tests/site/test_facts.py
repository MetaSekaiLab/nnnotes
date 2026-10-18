"""Model names and story groups from synthetic master tables."""

import json

from nnnotes.content import sitefacts

KEY_A = "Character/Live2D/001_adv/adv_model_a/model/adv_model_a"
KEY_B = "Character/Live2D/sub_x/adv_model_b/model/adv_model_b"
PATH_A = KEY_A.removeprefix("Character/Live2D/")
PATH_B = KEY_B.removeprefix("Character/Live2D/")


def name_row(tid, ja, en, zh=None):
    return {
        "_id": tid,
        "_japanese": ja,
        "_english": en,
        "_traditionalChinese": zh or ja,
        "_simplifiedChinese": zh or ja,
        "_korean": "",
    }


def write_master(md, costumes, characters=None, texts=None):
    md.mkdir(parents=True, exist_ok=True)
    tables = {
        "MasterCharacterCostume": costumes,
        "MasterCharacter": characters
        if characters is not None
        else [
            {"_id": 1, "_nameTextID": "Name_A"},
            {"_id": 2, "_nameTextID": "Name_B"},
            {"_id": 3, "_nameTextID": "Gone"},
        ],
        "MasterText": texts
        if texts is not None
        else [name_row("Name_A", "エー", "Ay", "艾"), name_row("Name_B", "ビー", "Bee")],
    }
    for name, rows in tables.items():
        (md / f"{name}.json").write_text(json.dumps({"_allData": rows}, ensure_ascii=False), encoding="utf-8")
    return md


def costume(cid, path, char):
    return {"_id": cid, "_characterID": char, "_costumeID": cid % 1000, "_isDefault": False, "_live2dPath": path}


def test_model_names_from_the_costume_rows(tmp_path):
    md = write_master(
        tmp_path / "m",
        [
            costume(1001, PATH_A, 1),
            costume(1002, PATH_A, 1),  # two costumes, one model
            costume(2001, PATH_B, 2),
            costume(2002, "002_adv/shared/model/shared", 2),
            costume(3001, "002_adv/shared/model/shared", 1),
            costume(3002, "003_adv/c/model/c", 3),  # no name text
            {"_id": 9, "_characterID": 1, "_live2dPath": ""},
        ],
    )
    names = sitefacts.model_names(md, "zh-Hant")
    assert names == {
        KEY_A: {"character": 1, "names": {"ja": "エー", "en": "Ay", "zh-Hant": "艾", "zh-Hans": "艾"}, "label": "艾"},
        KEY_B: {
            "character": 2,
            "names": {"ja": "ビー", "en": "Bee", "zh-Hant": "ビー", "zh-Hans": "ビー"},
            "label": "ビー",
        },
    }
    assert "label" not in sitefacts.model_names(md)[KEY_A]
    assert "label" not in sitefacts.model_names(md, "ko")[KEY_A]  # no Korean text


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        data
        if isinstance(data, bytes)
        else (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)).encode("utf-8")
    )


def test_story_groups(tmp_path):
    md = tmp_path / "master"

    def table(name, rows):
        write(md / f"{name}.json", {"_allData": rows})

    def text(i, ja, en):
        return {
            "_id": i,
            "_japanese": ja,
            "_english": en,
            "_traditionalChinese": "",
            "_simplifiedChinese": None,
            "_korean": None,
        }

    table(
        "MasterText",
        [text("C1", "章", "Chapter"), text("N1", "あ", "A"), text("N2", "い", "B"), text("S1", "店", "Shop")],
    )
    table("MasterCharacter", [{"_id": 1, "_nameTextID": "N1"}, {"_id": 2, "_nameTextID": "N2"}])
    table("MasterStoryChapter", [{"_id": 10, "_nameTextId": "C1", "_bandId": 3, "_isSpecialStory": False}])
    table("MasterStoryEpisode", [{"_id": 101, "_advId": 7, "_chapterId": 10, "_episodeNumber": 2, "_characterId": 0}])
    table("MasterCharacterFriendship", [{"_id": 12, "_masterCharacterIdA": 1, "_masterCharacterIdB": 2}])
    table("MasterStoryFriendshipEpisode", [{"_id": 1, "_advId": 8, "_characterFriendshipId": 12, "_episodeNumber": 1}])
    table("MasterHomeSpot", [{"_id": 50, "_nameTextId": "S1", "_advId": 9}])
    table("MasterStoryHomeSpotTapTalkEpisode", [{"_id": 5001, "_advId": 7, "_spotId": 50, "_characterId": 2}])
    g = sitefacts.story_groups(md)
    assert g[7] == [
        {
            "kind": "chapter",
            "id": 101,
            "episodeNumber": 2,
            "chapter": {"id": 10, "names": {"ja": "章", "en": "Chapter"}, "bandId": 3, "special": False},
            "characters": [],
        },
        {
            "kind": "spotTalk",
            "id": 5001,
            "spot": {"id": 50, "names": {"ja": "店", "en": "Shop"}},
            "characters": [{"id": 2, "names": {"ja": "い", "en": "B"}}],
        },
    ]
    assert [c["id"] for c in g[8][0]["characters"]] == [1, 2] and g[8][0]["kind"] == "friendship"
    assert g[9] == [
        {"kind": "spot", "id": 50, "spot": {"id": 50, "names": {"ja": "店", "en": "Shop"}}, "characters": []}
    ]
