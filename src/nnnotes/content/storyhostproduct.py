"""Host-screen objects and initial state for Overlay stories."""

from __future__ import annotations

from ..source import languages
from . import storyui
from .livenotes import animator_bindings
from .prefabs import (
    Prefab,
    builtin_root,
    component_class,
    component_ref,
    has_json,
    key_object,
    mesh_roles,
    ref_id,
    shader_table,
)

FORMAT = "nnnotes.story-host/1"
OVERLAY = 1
TABLES = ("MasterHomeSpot", "MasterStoryHomeSpotTapTalkEpisode", "MasterStoryLiveResultEpisode", "MasterText")
ADDRESSES = {
    "cameraTarget": "Common/Prefab/CameraTargetRenderer",
    "spotWidget": "UI/Prefab/UISpotWidget",
    "spotLetterbox": "UI/Prefab/Parts/Spot/UISpotLetterbox",
    "spotScene": "Scene/Spot",
    "resultWidget": "UI/Prefab/UILiveResultWidget",
    "resultBackground": "UI/Prefab/UILiveResultBackgroundWidget",
    "resultBackgroundSprite": "LowImage/LiveResult/live_result_background",
}
KIND_KEYS = {
    "home": ("spotWidget", "spotLetterbox", "spotScene"),
    "afterlive": ("resultWidget", "resultBackground", "resultBackgroundSprite"),
}
SUBSTITUTES = ("musicResultPanel", "rewardItems", "memberCard", "navigation", "gradeUpEffect")


def episode_host(tables: dict, episode: dict) -> dict | None:
    """The host selected by an Overlay episode's unique home or live-result master row."""
    if episode.get("_playbackMode") != OVERLAY:
        return None
    adv_id = episode["_id"]
    hits = []
    for row in tables["MasterStoryHomeSpotTapTalkEpisode"]:
        if row["_advId"] == adv_id:
            hits.append(
                {
                    "subject": f"home/{row['_spotId']}",
                    "kind": "home",
                    "talk": "tap",
                    "spotId": row["_spotId"],
                    "characterId": row.get("_characterId") or None,
                    "group": {"kind": "spotTalk", "id": row["_id"]},
                }
            )
    for row in tables["MasterHomeSpot"]:
        if row.get("_advId") == adv_id:
            texts = [r for r in tables["MasterText"] if r["_id"] == row.get("_advNameTextId")]
            if not texts:
                raise ValueError(f"home spot {row['_id']}: area title is absent from MasterText")
            hits.append(
                {
                    "subject": f"home/{row['_id']}",
                    "kind": "home",
                    "talk": "area",
                    "spotId": row["_id"],
                    "characterId": None,
                    "titleTextId": row["_advNameTextId"],
                    "title": languages.texts(texts[0]),
                    "group": {"kind": "spot", "id": row["_id"]},
                }
            )
    for row in tables["MasterStoryLiveResultEpisode"]:
        if row["_advId"] == adv_id:
            hits.append(
                {"subject": "afterlive", "kind": "afterlive", "group": {"kind": "liveResult", "id": row["_id"]}}
            )
    if len(hits) != 1:
        raise ValueError(f"Overlay episode {adv_id}: expected one host, found {len(hits)}")
    host = hits[0]
    if host["kind"] == "home":
        row = record(tables, host["subject"])["spot"]
        host.update(spotKey=row["_situationAssetPath"], roomKey=row["_backgroundAssetPath"])
    return host


def parse_subject(subject: str) -> tuple[str, int | None]:
    if subject == "afterlive":
        return subject, None
    kind, sep, number = subject.partition("/")
    if kind != "home" or not sep or not number.isdigit() or str(int(number)) != number:
        raise ValueError(f"{subject!r}: expected afterlive or home/<spot id>")
    return kind, int(number)


def record(tables: dict, subject: str) -> dict:
    kind, spot_id = parse_subject(subject)
    if kind == "afterlive":
        return {"kind": kind}
    rows = [r for r in tables["MasterHomeSpot"] if r["_id"] == spot_id]
    if len(rows) != 1:
        raise ValueError(f"home spot {spot_id}: expected one MasterHomeSpot row")
    return {"kind": kind, "spot": rows[0]}


def addresses(record: dict, catalog) -> dict[str, str]:
    held, out = set(catalog), {}
    for name in ("cameraTarget", *KIND_KEYS[record["kind"]]):
        address = ADDRESSES[name]
        key = next((k for k in (address, f"Emb{address}") if k in held), None)
        if key is None:
            if name == "spotScene":
                continue
            raise ValueError(f"{address}: no plain or embedded asset in the catalog")
        out[name] = key
    if record["kind"] == "home":
        out.update(background=record["spot"]["_backgroundAssetPath"], situation=record["spot"]["_situationAssetPath"])
    return out


def _component(objects, prefab: str, cls: str, *, path: str | None = None):
    hits = [
        (i, c)
        for i, n in enumerate(objects.json(prefab)["nodes"])
        for c in n["components"]
        if (path is None or n["path"] == path) and component_class(c) == cls
    ]
    if len(hits) != 1:
        raise ValueError(f"{prefab}: expected one {cls} at {path!r}, found {len(hits)}")
    return hits[0]


def _target(objects, prefab: str, ref) -> dict:
    if ref_id(ref) is not None:
        hit = component_ref(objects, prefab, ref)
        if hit is None:
            raise ValueError("host component target is null")
        holder, index, _ = hit
    else:
        holder = prefab
        index = Prefab(objects.json(prefab)).node_of(ref)
        if index is None:
            raise ValueError("host node target is null")
    return {"prefab": holder, "path": objects.json(holder)["nodes"][index]["path"]}


def background_state(doc: dict, situation_name: str) -> dict:
    """Background activation and the floor renderer's queue after the host places a situation."""
    prefab = Prefab(doc)
    root = prefab.nodes[0]
    overrides, activated, hidden = {}, [], []
    name = situation_name.replace("(Clone)", "")
    matches = [c for c in root["components"] if component_class(c) == "SpotBackground"]
    matched = False
    if matches:
        children = [i for i, p in enumerate(prefab.parent) if p == 0]
        if not children:
            raise ValueError("SpotBackground needs a child container")
        for entry in matches[0]["fields"].get("_situationObjects") or []:
            if entry.get("SituationName") == name:
                matched = True
                activated = [i for i, p in enumerate(prefab.parent) if p == children[0]]
                hidden = [prefab.node_of(r) for r in entry.get("HideObjects") or []]
                overrides.update({i: True for i in activated})
                overrides.update({i: False for i in hidden if i is not None})
                break

    def active(index):
        while index is not None:
            if not overrides.get(index, prefab.nodes[index].get("active", True)):
                return False
            index = prefab.parent[index]
        return True

    floor = next(
        (i for i, n in enumerate(prefab.nodes) if i and "floor" in n["name"] and "shadow" not in n["name"]), None
    )
    renderer = None
    if floor is not None:
        path = prefab.nodes[floor]["path"]
        renderer = next(
            (
                i
                for i, n in enumerate(prefab.nodes)
                if (n["path"] == path or n["path"].startswith(path + "/"))
                and active(i)
                and any(component_class(c) == "MeshRenderer" for c in n["components"])
            ),
            None,
        )
    overrides[0] = True
    return {
        "situation": {
            "name": name,
            "matched": matched,
            "activate": [prefab.nodes[i]["path"] for i in activated],
            "hide": [None if i is None else prefab.nodes[i]["path"] for i in hidden],
        },
        "nodes": {
            n["path"]: {"active": active(i), "renderQueue": 2000 if i == renderer else None}
            for i, n in enumerate(prefab.nodes)
        },
        "floor": {
            "object": None if floor is None else prefab.nodes[floor]["path"],
            "renderer": None if renderer is None else prefab.nodes[renderer]["path"],
            "renderQueue": 2000,
        },
    }


def host(objects, record: dict, parts: dict) -> tuple[dict, set, dict]:
    """host.json, retained object ids and graphics-API roles from object data."""
    keys = addresses(record, objects.keys())
    roots = {
        name: key_object(objects, key, "GameObject")
        for name, key in keys.items()
        if name not in ("resultBackgroundSprite", "spotScene")
    }
    kind = record["kind"]
    ui_name, field = ("spotWidget", "_afterTalkContents") if kind == "home" else ("resultWidget", "_advSimpleContents")
    prefab = roots[ui_name]
    cls = "UISpotWidget" if kind == "home" else "UILiveResultWidget"
    _, widget = _component(objects, prefab, cls)
    overlay = _target(objects, prefab, widget["fields"][field])
    _, layout = _component(objects, overlay["prefab"], "SimpleAdvLayoutRoot", path=overlay["path"])
    opened = [_target(objects, prefab, widget["fields"]["_fadeIn" if kind == "home" else "_inAnimation"])]
    home, afterlive = None, None
    if kind == "home":
        scene = keys.get("spotScene")
        scene_roots = [oid for oid in objects.key(scene) if oid in objects and has_json(objects, oid)] if scene else []
        home = {
            "spotId": record["spot"]["_id"],
            "background": roots["background"],
            "situation": roots["situation"],
            "scene": scene_roots,
            "parts": parts,
            "backgroundState": background_state(
                objects.json(roots["background"]), objects.json(roots["situation"])["name"]
            ),
        }
    else:
        background = roots["resultBackground"]
        _, bg = _component(objects, background, "UILiveResultBackgroundWidget")
        opened.insert(0, _target(objects, background, bg["fields"]["_inAnimation"]))
        ancestors = [
            (i, c)
            for i, n in enumerate(objects.json(prefab)["nodes"])
            for c in n["components"]
            if component_class(c) == "UISoloLiveMusicResultView" and overlay["path"].startswith(n["path"] + "/")
        ]
        if len(ancestors) != 1:
            raise ValueError("live result overlay needs one UISoloLiveMusicResultView ancestor")
        opened.append(_target(objects, prefab, ancestors[0][1]["fields"]["_inAnimation"]))
        path = overlay["path"].split("/")
        reward = next(("/".join(path[: i + 1]) for i in range(len(path) - 2, 0, -1) if path[i] == "RewardPanel"), None)
        if reward is None:
            raise ValueError("live result overlay needs a RewardPanel ancestor")
        afterlive = {
            "backgroundImage": _target(objects, background, bg["fields"]["_backgroundImage"]),
            "backgroundSprite": key_object(objects, keys["resultBackgroundSprite"], "Sprite"),
            "rewardPanel": {"prefab": prefab, "path": reward},
            "substitutes": list(SUBSTITUTES),
        }
    graphics = builtin_root(objects, "GraphicsSettings")
    included = [ref_id(r) for r in objects.json(graphics).get("m_AlwaysIncludedShaders", ())]
    shaders, roles = shader_table(objects, found=[oid for oid in included if oid is not None])
    keep = {oid for oid in objects.ids() if objects.info(oid).get("class") != "MonoScript"}
    roles.update(mesh_roles(objects, keep))
    doc = {
        "format": FORMAT,
        "kind": kind,
        "prefabs": roots,
        "cameraTarget": roots["cameraTarget"],
        "overlayRoot": overlay,
        "layoutRoot": {**overlay, "fields": layout["fields"]},
        "openedSequences": opened,
        "home": home,
        "afterlive": afterlive,
        "graphics": {"graphics": graphics},
        "dotween": storyui.script_root(objects, "DOTweenSettings"),
        "bindings": animator_bindings(objects),
        "shaders": shaders,
    }
    return doc, keep, roles
