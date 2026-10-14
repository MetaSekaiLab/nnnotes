"""MonoBehaviour (mono.json), with the cue sheets (ACB) and movies (USM) its assets hold.

A cue sheet held in a bundle is a MonoBehaviour in one of two layouts (formats/cri.py): a SplitAcbData
(`_cueSheetName`, `_chunks`: TextAssets of this bundle, joined and unmasked into the ACB) or a CriWare.Assets asset
named like the sheet whose `implementation` managed reference is a CriSerializedBytesAssetImpl (its `data.data`
bytes are the ACB: the field's {"$blob"} names the `acb` artifact, whatever its size). The artifact's facts name the
cue sheet. A movie asset (CriWare.Assets CriManaUsmAsset) whose `implementation` is a CriSerializedBytesAssetImpl
holds its USM the same way; with converter cri.usm its `data.data` bytes are the `usm` artifact, whatever their size,
the facts naming the asset.
"""

from __future__ import annotations

from ..atoms import Unsupported
from ..ids import object_id
from . import USM_ASSET_SCRIPT
from .objects import NO_SCRIPT, BundleObjects, Capture, encode_object, label

__all__ = ["ACB_KIND", "EMBEDDED_ACB", "SPLIT_ACB", "USM_KIND", "acb_asset", "embedded_bytes", "mono"]

ACB_KIND, USM_KIND = "cri.acb", "cri.usm"
SPLIT_ACB, EMBEDDED_ACB = "split", "embedded"
EMBEDDED_ACB_IMPL = "CriSerializedBytesAssetImpl"


def acb_asset(tt: dict) -> dict | None:
    """Which cue sheet layout a MonoBehaviour's typetree holds (cri.py): {"layout": "split", "cueSheet", "chunks"}
    for a SplitAcbData, {"layout": "embedded", "cueSheet", "ref": index in references.RefIds, "impl"} for an asset
    whose `implementation` is a CriSerializedBytesAssetImpl; None for any other. SplitAcbData first."""
    sheet = tt.get("_cueSheetName")
    if isinstance(sheet, str) and isinstance(tt.get("_chunks"), list):
        return {"layout": SPLIT_ACB, "cueSheet": sheet, "chunks": tt["_chunks"]}
    ref = embedded_bytes(tt)
    return (
        None if ref is None else {"layout": EMBEDDED_ACB, "cueSheet": tt.get("m_Name"), "ref": ref[0], "impl": ref[1]}
    )


def embedded_bytes(tt: dict) -> tuple[int, dict] | None:
    """(index in references.RefIds, the reference) of a CriWare.Assets asset's `implementation` when it is a
    CriSerializedBytesAssetImpl (bytes held in the asset), else None."""
    impl = tt.get("implementation")
    if not isinstance(impl, dict) or "rid" not in impl:
        return None
    ids = ((tt.get("references") or {}).get("RefIds")) or []
    for i, r in enumerate(ids):
        typ = r.get("type") if isinstance(r, dict) else None
        if isinstance(typ, dict) and r.get("rid") == impl["rid"] and typ.get("class") == EMBEDDED_ACB_IMPL:
            return i, r
    return None


def mono(ctx: BundleObjects, info, *, usm_assets: bool) -> None:
    """`json`: {"$script": the script table entry, ...the typetree JSON}; a cue sheet asset also `acb`, and with
    `usm_assets` (converter cri.usm) a movie asset holding its USM also `usm`."""
    key = (info.file, info.path_id)
    tt = ctx.tt(key)
    entry = ctx.mono_script(key, tt)
    if entry == NO_SCRIPT:
        raise Unsupported("script.missing", "m_Script is null")
    if entry is None:
        s = tt.get("m_Script") or {}
        raise Unsupported(
            "script.unresolved", f"script {s.get('m_FileID')}:{s.get('m_PathID')} is not in the task's script table"
        )
    movie = embedded_bytes(tt) if usm_assets and entry == USM_ASSET_SCRIPT else None
    acb = acb_asset(tt) if movie is None else None
    captures = {}
    if movie is not None:
        captures[f"references/RefIds/{movie[0]}/data/data"] = Capture(
            "usm",
            USM_KIND,
            b"CRID",
            "the movie data is not a USM (CRID)",
            f"movie {tt.get('m_Name')}: no USM bytes in its {EMBEDDED_ACB_IMPL}",
            {"movie": label(tt.get("m_Name"))},
        )
    if acb is not None and acb["layout"] == EMBEDDED_ACB:
        if (tt.get("awb") or {}).get("m_PathID"):
            raise Unsupported("unsupported.cri.awb_external", f"cue sheet {acb['cueSheet']}: its AWB is another object")
        from ...formats import cri

        captures[f"references/RefIds/{acb['ref']}/data/data"] = Capture(
            "acb",
            ACB_KIND,
            cri.ACB_SIGNATURE,
            "the cue sheet data is not an ACB (@UTF)",
            f"cue sheet {acb['cueSheet']}: no ACB bytes in its {EMBEDDED_ACB_IMPL}",
            {"cueSheet": label(acb["cueSheet"]), "layout": EMBEDDED_ACB},
        )
    doc = {"$script": entry, **ctx.tree(ctx.readers[key], tt, captures=captures)}
    ctx.emit(key, "json", encode_object(doc), "json", "mono.json", "json", {"script": entry})
    if acb is not None and acb["layout"] == SPLIT_ACB:
        _split_acb(ctx, key, acb)


def _split_acb(ctx: BundleObjects, key: tuple, acb: dict) -> None:
    """The `acb` artifact of a SplitAcbData: its chunk TextAssets (of this bundle) joined and unmasked."""
    from ...formats import cri

    owner, parts, chunks = ctx.readers[key], [], []
    for c in acb["chunks"]:
        k = ctx.target(owner, c) if isinstance(c, dict) else None
        if k is None or k not in ctx.info or ctx.info[k].class_name != "TextAsset":
            raise ValueError(f"cue sheet {acb['cueSheet']}: chunk {c} is not a TextAsset of the bundle")
        parts.append(cri.text_bytes(ctx.tt(k).get("m_Script", "")))
        chunks.append(k)
    data = cri.split_acb(parts, acb["cueSheet"])
    facts = {"cueSheet": label(acb["cueSheet"]), "layout": SPLIT_ACB, "chunks": len(parts)}
    refs = [{"rel": "chunk", "object": object_id(*k)} for k in chunks]
    ctx.emit(key, "acb", data, "acb", ACB_KIND, "acb", facts, refs=refs)
