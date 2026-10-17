"""Reusable host-screen products for Overlay stories."""

from ..content import storyhostproduct
from . import contract
from .assemble import MASTER_INPUT, Assembly, ProductStage, master_input
from .spot import RoomStage, SpotStage
from .views import master_rows

HOST = "story.host"


class HostStage(ProductStage):
    name = HOST
    version = 1
    KIND = "StoryHost"
    MASTER = ("MasterHomeSpot",)

    def record(self, subject, env):
        tables = {} if subject == "afterlive" else master_rows(env, self.MASTER)
        return storyhostproduct.record(tables, subject)

    def keys(self, subject, env):
        held = {loc["primaryKey"] for loc in env.fact("index")["locations"]}
        return sorted(storyhostproduct.addresses(self.record(subject, env), held).values())

    def resources(self, subject, env):
        storyhostproduct.parse_subject(subject)
        return ["DOTweenSettings"]

    def builtins(self, subject, env):
        storyhostproduct.parse_subject(subject)
        return ["GraphicsSettings"]

    def parts(self, subject, env):
        record = self.record(subject, env)
        if record["kind"] == "afterlive":
            return {}
        row = record["spot"]
        return {"spot.files": [row["_situationAssetPath"]], "room.gltf": [row["_backgroundAssetPath"]]}

    def more_inputs(self, subject, env):
        record = self.record(subject, env)
        parts = self.parts(subject, env)
        paths = (
            {}
            if not parts
            else {
                "spot": SpotStage().manifest(parts["spot.files"][0]),
                "room": RoomStage().manifest(parts["room.gltf"][0]),
            }
        )
        return [master_input(env, {"host": record, "parts": paths})]

    def manifest(self, subject):
        kind, spot = storyhostproduct.parse_subject(subject)
        return f"storyhosts/{kind}{'' if spot is None else '_' + str(spot)}.json"

    def assemble(self, task, objects):
        record = contract.loads(objects.depot.input_bytes(task.input(MASTER_INPUT)))
        doc, keep, roles = storyhostproduct.host(objects, record["host"], record["parts"])
        return Assembly({"host.json": (contract.encode(doc), "json", HOST)}, keep, roles)
