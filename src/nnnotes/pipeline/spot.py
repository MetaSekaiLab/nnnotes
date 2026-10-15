"""The spot products: a spot background as binary glTF (room.gltf) and a spot situation's spot.json with its Spine
files (spot.files).

Both are product stages (pipeline.assemble) whose roots are the objects of their subject's catalog key; they
assemble with content.room and content.spot and keep no objects (their files are all they hold):

room.gltf   subject a background key (Spot/<place>/Background/<place>): "<task id>#room.glb" (kind spot.room) and
            "<task id>#room.json" (kind spot.room-summary); its manifest is rooms/<place>.json
spot.files  subject a situation key (Spot/<place>/<situation>/<situation>): "<task id>#spot.json" (kind spot.situation)
            and "<task id>#spine/<file>" for each Spine file (kind spine.skeleton, spine.atlas or spine.texture); its
            manifest is spots/<situation>.json. Its input "master" is the spot's master record
            (content.spot.master_record) from the master tables of the fact "master", made at planning
"""

from __future__ import annotations

import re

from ..content import room, spot
from . import contract
from .assemble import MASTER_INPUT, Assembly, ProductStage, master_input
from .contract import Input
from .views import master_rows

__all__ = ["ROOM", "SPOT", "RoomStage", "SpotStage", "spine_files"]

ROOM, SPOT = "room.gltf", "spot.files"


def spine_files(doc: dict, spine: dict[str, bytes]) -> dict:
    """{path: (bytes, extension, kind)} of the Spine files of spot.json `doc`: the skeletons (spine.skeleton), the
    atlases (spine.atlas) and the PNG of each atlas page (spine.texture)."""
    skeletons = {f"spine/{s['skeleton']}" for s in doc["skeletons"]}
    atlases = {f"spine/{a}" for s in doc["skeletons"] for a in s["atlases"]}
    out = {}
    for path, data in spine.items():
        if path in skeletons:
            out[path] = (data, path.rsplit(".", 1)[-1], "spine.skeleton")
        elif path in atlases:
            out[path] = (data, "atlas", "spine.atlas")
        else:
            out[path] = (data, "png", "spine.texture")
    return out


class RoomStage(ProductStage):
    """room.gltf: a background key -> room.glb and room.json (module documentation)."""

    name = ROOM
    version = 1
    KIND = "SpotRoom"

    def select(self, keys: list[str], env) -> list[str]:
        return sorted(k for k in keys if re.fullmatch(room.BACKGROUND_KEY, k))

    def keys(self, subject: str, env) -> list[str]:
        room.room_name(subject)
        return [subject]

    def manifest(self, subject: str) -> str:
        return f"rooms/{room.room_name(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        glb, doc = room.room(objects, task.subject)
        files = {
            "room.glb": (glb, "glb", "spot.room"),
            "room.json": (contract.encode(doc), "json", "spot.room-summary"),
        }
        return Assembly(files, set())


class SpotStage(ProductStage):
    """spot.files: a situation key -> spot.json and its Spine files (module documentation)."""

    name = SPOT
    version = 1
    KIND = "SpotSituation"
    MASTER = spot.MASTER_TABLES

    def select(self, keys: list[str], env) -> list[str]:
        out = []
        for k in keys:
            try:
                spot.situation_name(k)
            except ValueError:
                continue
            out.append(k)
        return sorted(out)

    def keys(self, subject: str, env) -> list[str]:
        spot.situation_name(subject)
        return [subject]

    def more_inputs(self, subject: str, env) -> list[Input]:
        return [master_input(env, spot.master_record(master_rows(env, self.MASTER), subject))]

    def manifest(self, subject: str) -> str:
        return f"spots/{spot.situation_name(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        master = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        doc, spine = spot.spot(objects, task.subject, master)
        files = {"spot.json": (contract.encode(doc), "json", "spot.situation"), **spine_files(doc, spine)}
        return Assembly(files, set())
