"""UI products and their library index."""

from ..content import ui
from . import contract
from .assemble import Assembly, ProductStage

UI = "ui.pack"


class UiStage(ProductStage):
    """A catalog key with its prefab, animation, image and font object dependencies."""

    name = UI
    version = 1
    KIND = "UI"
    INCLUDE_FONTS = True

    def select(self, keys: list[str], env) -> list[str]:
        return sorted(k for k in keys if k.startswith("EmbUI/"))

    def keys(self, subject: str, env) -> list[str]:
        return [subject]

    def manifest(self, subject: str) -> str:
        return f"uipacks/{ui.identity(subject)}.json"

    def assemble(self, task, objects) -> Assembly:
        doc, roles = ui.pack(objects, task.subject)
        return Assembly({"ui.json": (contract.encode(doc), "json", UI)}, roles=roles)

    def layout_index(self, results, depot) -> tuple[str, bytes]:
        packs = []
        for subject, result in results:
            artifact = next(a for a in result["artifacts"] if a["id"].endswith("#ui.json"))
            packs.append((self.manifest(subject), contract.loads(depot.read(artifact["content"]["sha256"]))))
        return "index.json", contract.encode(ui.library(packs))
