"""The depot: the files a configuration downloads, the results of the stages by content, and the record of runs.

    <depot>/downloads/                   the game files as downloaded: catalogs, decrypted bundles (bundles/<name>),
                                         raw files (raw/<CDN path>), files read from the APK, each source in its own
                                         directory (international/<CDN>/<version>/, jp/<site>/<version>/<hash>/)
    <depot>/cas/sha256/ab/<sha>          bytes by content id, write-once (no extension: the records carry it)
    <depot>/ac/ab/<key>.json             task results (nnnotes.result/1) by task key; written after every byte they
                                         name is in cas/, so a result present is a complete result (the commit)
    <depot>/inputs/<kind>/ab/<digest>.json
                                         the identity recorded for an input name: {"name", "sha256", "size"} (kind:
                                         bundles, raw or files; digest: the sha256 of the name)
    <depot>/inputs/paths/ab/<digest>.json
                                         the identity of a file as last hashed: {"path", "fileSize", "mtimeNs",
                                         "sha256", "size"} (a memo: a file seen unchanged is not hashed again;
                                         digest: the sha256 of the path)
    <depot>/catalogs/                    versioned catalogs (catalogdb)
    <depot>/runs/<run>.json              run manifests (nnnotes.run/1), deterministic
    <depot>/runs/<run>.log.jsonl         the log of the run's last execution (timings, workers, errors)
    <depot>/runs/selections/<digest>     the id of the latest run of a selection (digest of its selection)
    <depot>/costs/<key>.json             the measured cost of a task's last run (nnnotes.cost/1)

Every file is written through a temporary file and a rename, so concurrent writers (threads, processes, machines on
a shared file system) never see or leave a partial file, and a crash before a result's entry leaves no false hit.
Records that change (input identities, the latest run of a selection) are one file each, replaced whole.
The files of cas/ are read-only (a layout may hard-link them: an edit of such a file fails instead of changing the
depot).
Only costs/, runs/ and inputs/ hold non-deterministic data; cas/ and ac/ are a function of the tasks run.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from ..core.cache import temp_path
from ..core.downloads import touch
from . import contract
from .contract import Input

INPUT_KINDS = ("bundles", "raw", "files")


class InputMissing(FileNotFoundError):
    """No locator of an input gives a file with its content id."""


class DepotConflict(RuntimeError):
    """A result already in the depot differs from the one being committed for the same key: the stage's output is
    not a function of its key (a nondeterministic stage, or a change without a version bump)."""


def _replace(tmp: Path, dst: Path, same) -> bool:
    """Rename `tmp` onto `dst`; False when another writer put `dst` first (`same(dst)` true) and `tmp` was dropped.
    On some systems a rename onto (or a read of) a file another process is replacing or reading fails for a
    moment: retried, as sitefiles.Depot does."""
    try:
        for i in range(20):
            try:
                os.replace(tmp, dst)
                return True
            except PermissionError:
                try:
                    if dst.exists() and same(dst):
                        _drop(tmp)
                        return False
                except PermissionError:
                    pass
                time.sleep(0.1 * (i + 1))
        raise RuntimeError(f"could not depot {dst}")
    except BaseException:
        _drop(tmp)
        raise


def _drop(tmp: Path) -> None:
    """Remove a temporary file, read-only or not (Windows refuses to delete a read-only file)."""
    try:
        tmp.unlink(missing_ok=True)
    except PermissionError:
        os.chmod(tmp, 0o666)
        tmp.unlink(missing_ok=True)


def _read(p: Path) -> bytes | None:
    """A file's bytes, None when it does not exist (retried while another process is replacing it)."""
    for i in range(20):
        try:
            return p.read_bytes()
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    return p.read_bytes()


def _write(dst: Path, data: bytes, same=None, read_only: bool = False) -> bool:
    """Write `data` to `dst` atomically (creating the directory); with `same`, keep an existing `dst` for which
    same(dst) holds; `read_only`: the file is read-only (depot objects). True when this call wrote the file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path(dst)
    try:
        with open(tmp, "xb") as f:
            f.write(data)
        if read_only:
            os.chmod(tmp, 0o444)
    except BaseException:
        _drop(tmp)
        raise
    return _replace(tmp, dst, same or (lambda p: False))


def write_file(dst, data: bytes) -> None:
    """Write (or overwrite) a file atomically."""
    _write(Path(dst), data)


def write_doc(dst, doc) -> str:
    """Write a document's canonical bytes (contract.encode) atomically, encoded as they are written; their sha256."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path(dst)
    try:
        with open(tmp, "xb") as f:
            h = hashlib.sha256()
            try:
                for b in contract.encode_chunks(doc):
                    h.update(b)
                    f.write(b)
            except ValueError:  # a non-finite number: encode() writes those
                f.seek(0)
                f.truncate()
                data = contract.encode(doc)
                h = hashlib.sha256(data)
                f.write(data)
        _replace(tmp, dst, lambda p: False)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return h.hexdigest()


def _record_path(directory: Path, key: str) -> Path:
    """The file of a record by key (a name, a path) in a directory of records: ab/<sha256 of the key>.json."""
    d = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return directory / d[:2] / f"{d}.json"


class Depot:
    """A depot directory. `fetch(input, locator) -> Path`: how a "catalog" locator is fetched (none: such locators
    are skipped)."""

    def __init__(self, root, *, fetch=None):
        self.root = Path(root)
        self.fetch = fetch
        self.written = self.reused = 0
        self._have: set[str] = set()  # content ids this depot object wrote or found
        self._hashed: dict[str, dict] = {}  # path -> its record (inputs/paths)
        self._names: dict[tuple[str, str], tuple[str, int]] = {}  # (kind, name) -> (sha256, size)
        self._docs: dict[str, dict] = {}  # the documents document() decoded last
        self._lock = threading.Lock()

    def __getstate__(self):  # a depot crosses to worker processes as its settings
        return {"root": self.root, "fetch": self.fetch}

    def __setstate__(self, state):
        self.__init__(state["root"], fetch=state["fetch"])

    def __repr__(self) -> str:
        return f"Depot({str(self.root)!r})"

    @property
    def downloads(self) -> Path:
        """The directory of downloaded game files ("download" locators are relative to it)."""
        return self.root / "downloads"

    @property
    def catalogs(self) -> Path:
        """The directory of versioned catalogs (catalogdb)."""
        return self.root / "catalogs"

    # ---------------------------------------------------------------- content
    def path(self, sha: str) -> Path:
        if not contract.is_sha256(sha):
            raise ValueError(f"not a sha256: {sha!r}")
        return self.root / "cas" / "sha256" / sha[:2] / sha

    def has(self, sha: str, size: int | None = None) -> bool:
        if sha in self._have:
            return True
        p = self.path(sha)
        try:
            n = p.stat().st_size
        except OSError:
            return False
        if size is not None and n != size:
            return False
        self._have.add(sha)
        return True

    def put(self, data) -> str:
        """Store bytes; their content id. Write-once: bytes already stored are not written again."""
        data = bytes(data)
        sha = contract.sha256(data)
        if self.has(sha, len(data)):
            self.reused += 1
            return sha
        if _write(self.path(sha), data, lambda p: p.stat().st_size == len(data), read_only=True):
            self.written += 1
        else:
            self.reused += 1
        self._have.add(sha)
        return sha

    def put_file(self, src) -> tuple[str, int]:
        """Store a file's bytes (hashed and copied in blocks); (content id, size)."""
        src = Path(src)
        sha, size = self.identify(src)
        if self.has(sha, size):
            self.reused += 1
            return sha, size
        dst = self.path(sha)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_path(dst)
        h = hashlib.sha256()
        try:
            with open(src, "rb") as f, open(tmp, "xb") as g:
                while block := f.read(1 << 20):
                    h.update(block)
                    g.write(block)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if h.hexdigest() != sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{src} changed while it was stored")
        os.chmod(tmp, 0o444)
        if _replace(tmp, dst, lambda p: p.stat().st_size == size):
            self.written += 1
        self._have.add(sha)
        return sha, size

    def add(self, data, ext: str, media: str | None = None) -> dict:
        """Store bytes; the content part of their artifact record."""
        c = contract.content(data, ext, media)
        self.put(data)
        return c

    def add_chunks(self, chunks, ext: str, media: str | None = None) -> dict:
        """Store the bytes of a sequence of byte strings, written and hashed as they come (never held whole); the
        content part of their artifact record, as add() gives it for the joined bytes."""
        tmp = temp_path(self.root / "cas" / "sha256" / "chunks")  # the content id is known at the end
        tmp.parent.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        try:
            with open(tmp, "xb") as f:
                for b in chunks:
                    h.update(b)
                    f.write(b)
                    size += len(b)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        sha = h.hexdigest()
        if self.has(sha, size):
            tmp.unlink(missing_ok=True)
            self.reused += 1
        else:
            dst = self.path(sha)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(tmp, 0o444)
            if _replace(tmp, dst, lambda p: p.stat().st_size == size):
                self.written += 1
            else:
                self.reused += 1
            self._have.add(sha)
        ext = ext.lower().lstrip(".")
        return {"sha256": sha, "size": size, "mediaType": media or contract.media_type(ext), "ext": ext}

    def read(self, sha: str) -> bytes:
        return self.path(sha).read_bytes()

    def document(self, sha: str) -> dict:
        """The JSON document stored as `sha`, decoded; the last two decoded are kept (a catalog index is read at
        planning and again by the stages it describes) and shared: callers must not change them."""
        with self._lock:
            doc = self._docs.get(sha)
        if doc is None:
            doc = contract.loads(self.read(sha))
            with self._lock:
                self._docs[sha] = doc
                while len(self._docs) > 2:
                    del self._docs[next(iter(self._docs))]
        return doc

    # ---------------------------------------------------------------- results (the action cache)
    def result_path(self, key: str) -> Path:
        if not contract.is_sha256(key):
            raise ValueError(f"not a task key: {key!r}")
        return self.root / "ac" / key[:2] / f"{key}.json"

    def result(self, key: str) -> dict | None:
        """The committed result of a task key, or None."""
        data = _read(self.result_path(key))
        return None if data is None else contract.loads(data)

    def committed(self, key: str, task: str) -> dict:
        """The committed result of a task key that has one (a FileNotFoundError naming `task` when it is missing)."""
        doc = self.result(key)
        if doc is None:
            raise FileNotFoundError(f"{task}: its result {key} is not in the depot")
        return doc

    def has_result(self, key: str) -> bool:
        return self.result_path(key).is_file()

    def commit(self, doc: dict) -> bool:
        """Write a result entry, after checking that the bytes of every artifact it names are stored. Write-once: an
        entry already there must be identical (else DepotConflict). True when this call wrote it."""
        if doc.get("schema") != contract.RESULT:
            raise ValueError("not a result document")
        for a in doc["artifacts"]:
            c = a["content"]
            if not self.has(c["sha256"], c["size"]):
                raise RuntimeError(f"result {doc['key']}: bytes of {a['id']} ({c['sha256']}) are not stored")
        data = contract.encode(doc)
        dst = self.result_path(doc["key"])

        def same(p: Path) -> bool:
            old = _read(p)
            if old is None:
                return False
            if old != data:
                raise DepotConflict(f"result {doc['key']} differs from the one in the depot ({dst})")
            return True

        if same(dst):
            return False
        return _write(dst, data, same)

    # ---------------------------------------------------------------- inputs
    def identify(self, path, kind: str = "files", name: str | None = None) -> tuple[str, int]:
        """(sha256, size) of a file: from its record when the file's path, size and mtime are unchanged, else hashed
        and recorded (inputs/paths); with `name`, also recorded as the identity of that name (inputs/<kind>)."""
        if kind not in INPUT_KINDS:
            raise ValueError(f"unknown input kind {kind!r}")
        p = Path(path).resolve()
        st = p.stat()
        key = str(p)
        with self._lock:
            rec = self._hashed.get(key)
        if rec is None:
            data = _read(_record_path(self.root / "inputs" / "paths", key))
            rec = json.loads(data) if data is not None else None
        if rec is None or (rec["fileSize"], rec["mtimeNs"]) != (st.st_size, st.st_mtime_ns):
            with open(p, "rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            rec = {"path": key, "fileSize": st.st_size, "mtimeNs": st.st_mtime_ns, "sha256": sha, "size": st.st_size}
            _write(_record_path(self.root / "inputs" / "paths", key), contract.encode(rec))
        with self._lock:
            self._hashed[key] = rec
        hit = (rec["sha256"], rec["size"])
        if name is not None and self.named(kind, name) != hit:
            doc = {"name": name, "sha256": hit[0], "size": hit[1]}
            _write(_record_path(self.root / "inputs" / kind, name), contract.encode(doc))
            with self._lock:
                self._names[(kind, name)] = hit
        return hit

    def named(self, kind: str, name: str) -> tuple[str, int] | None:
        """The identity recorded for an input name, or None."""
        with self._lock:
            hit = self._names.get((kind, name))
        if hit is None:
            data = _read(_record_path(self.root / "inputs" / kind, name))
            if data is None:
                return None
            doc = json.loads(data)
            hit = (doc["sha256"], int(doc["size"]))
            with self._lock:
                self._names[(kind, name)] = hit
        return hit

    def open_input(self, inp: Input) -> Path:
        """A file holding the bytes of `inp`: its locators in order (none: the depot), each checked against the
        content id (depot: by name and size; files: hashed, memoized). Raises InputMissing."""
        tried = []
        for loc in inp.locators or ({"kind": "depot"},):
            kind = loc["kind"]
            try:
                if kind == "depot":
                    if self.has(inp.sha256, inp.size):
                        return self.path(inp.sha256)
                    tried.append("depot: absent")
                    continue
                if kind == "catalog":
                    if self.fetch is None:
                        tried.append("catalog: no fetcher")
                        continue
                    p = Path(self.fetch(inp, loc))
                elif kind == "download":
                    p = self.downloads / loc["path"]
                    touch(p)
                else:
                    p = Path(loc["path"])
                if not p.is_file() or p.stat().st_size != inp.size:
                    tried.append(f"{kind}: absent")
                    continue
                if self.identify(p)[0] != inp.sha256:
                    tried.append(f"{kind}: other content")
                    continue
                return p
            except OSError as e:
                tried.append(f"{kind}: {type(e).__name__}")
        raise InputMissing(f"input {inp.role} ({inp.name or inp.sha256}): {'; '.join(tried)}")

    def input_bytes(self, inp: Input) -> bytes:
        return self.open_input(inp).read_bytes()

    # ---------------------------------------------------------------- verification
    def verify(self, *, quick: bool = False, workers: int = 8, limit: int = 100) -> dict:
        """Check the depot: every cas/ file has the content its name says (`quick`: only that it is a file of a
        well-formed name; its size is checked against the results naming it), every ac/ entry is a result whose
        name is its key, whose key is the key of its parts and whose artifacts' bytes are stored with their size.
        -> {"contents", "results", "problems": [{path, problem}] (the first `limit`, sorted), "problemCount"}."""
        from concurrent.futures import ThreadPoolExecutor

        problems = []
        top = self.root / "cas" / "sha256"
        cas = sorted([*top.glob("*/*"), *top.glob("*.part")])  # add_chunks writes its temporary file in top

        def content(p: Path):
            name = p.name
            if not contract.is_sha256(name) or p.parent.name != name[:2]:
                return p, "not a content id" if not name.endswith(".part") else "temporary file left"
            if not quick:
                with open(p, "rb") as f:
                    if hashlib.file_digest(f, "sha256").hexdigest() != name:
                        return p, "content differs from its id"
            return None

        with ThreadPoolExecutor(max(1, workers)) as pool:
            problems += [r for r in pool.map(content, cas) if r is not None]
        sizes = {}
        results = sorted((self.root / "ac").glob("*/*.json"))
        for p in results:
            try:
                doc = contract.loads(p.read_bytes())
            except (OSError, ValueError) as e:
                problems.append((p, f"unreadable: {type(e).__name__}"))
                continue
            key = p.name[: -len(".json")]
            if doc.get("schema") != contract.RESULT or doc.get("key") != key:
                problems.append((p, "not the result of its key"))
                continue
            if contract.digest(doc.get("keyParts")) != key:
                problems.append((p, "key is not the key of its parts"))
            for a in doc.get("artifacts", ()):
                c = a["content"]
                if c["sha256"] not in sizes:
                    try:
                        sizes[c["sha256"]] = self.path(c["sha256"]).stat().st_size
                    except (OSError, ValueError):
                        sizes[c["sha256"]] = None
                if sizes[c["sha256"]] != c["size"]:
                    problems.append(
                        (
                            p,
                            f"artifact {a['id']}: bytes "
                            f"{'missing' if sizes[c['sha256']] is None else 'of another size'}",
                        )
                    )
        rows = sorted({(p.relative_to(self.root).as_posix(), m) for p, m in problems})
        return {
            "contents": len(cas),
            "results": len(results),
            "problemCount": len(rows),
            "problems": [{"path": a, "problem": b} for a, b in rows[:limit]],
        }

    # ---------------------------------------------------------------- runs and costs
    def run_path(self, run_id: str) -> Path:
        return self.root / "runs" / f"{run_id}.json"

    def write_run(self, doc: dict, log: list[dict] | None = None) -> Path:
        """Write a run manifest (and the log of this execution), and make it the latest run of its selection."""
        if doc.get("schema") != contract.RUN:
            raise ValueError("not a run document")
        p = self.run_path(doc["id"])
        write_file(p, contract.encode(doc))
        if log is not None:
            text = "".join(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n" for line in log)
            write_file(p.with_name(f"{doc['id']}.log.jsonl"), text.encode("utf-8"))
        write_file(self._selection_path(doc["selection"]), (doc["id"] + "\n").encode("ascii"))
        return p

    def _selection_path(self, selection) -> Path:
        return self.root / "runs" / "selections" / contract.digest(list(selection))

    def run(self, run_id: str) -> dict | None:
        try:
            return contract.loads(self.run_path(run_id).read_bytes())
        except OSError:
            return None

    def latest_run(self, selection) -> dict | None:
        """The latest run manifest written for `selection`, or None."""
        try:
            rid = self._selection_path(selection).read_text(encoding="ascii").strip()
        except OSError:
            return None
        return self.run(rid)

    def write_cost(self, key: str, cost: dict) -> None:
        write_file(self.root / "costs" / f"{key}.json", contract.encode(cost))

    def cost(self, key: str) -> dict | None:
        try:
            return contract.loads((self.root / "costs" / f"{key}.json").read_bytes())
        except OSError:
            return None

    # ---------------------------------------------------------------- collection
    def gc(self, *, keep: int | None = None, dry_run: bool = False) -> dict:
        """Remove what no kept run reaches. The kept runs: of each selection the `keep` latest by the time their
        manifest was written (None: every run), and always the run the selection names as its latest. A kept run
        reaches the results of its tasks, the contents those results name (their artifacts, and the inputs their key
        parts name when the depot holds them) and the layout manifests it names with the contents they list.
        Removed: the other runs (manifest and log), results and their measured costs, and contents. `dry_run`: count
        only. -> {"runs", "results", "contents": {"kept", "removed"}, "bytesRemoved"}."""
        runs_dir = self.root / "runs"
        manifests = sorted(p for p in runs_dir.glob("*.json") if p.is_file())
        latest = set()
        for p in (runs_dir / "selections").glob("*"):
            try:
                latest.add(p.read_text(encoding="ascii").strip())
            except OSError:
                continue
        by_selection: dict[str, list[tuple[int, Path, dict]]] = {}
        for p in manifests:
            try:
                doc = contract.loads(p.read_bytes())
            except (OSError, ValueError):
                continue
            sel = contract.digest(list(doc.get("selection") or ()))
            by_selection.setdefault(sel, []).append((p.stat().st_mtime_ns, p, doc))
        kept, dropped = [], []
        for runs in by_selection.values():
            runs.sort(key=lambda r: r[0], reverse=True)
            for i, (_, p, doc) in enumerate(runs):
                (kept if keep is None or i < keep or doc.get("id") in latest else dropped).append((p, doc))
        keys: set[str] = set()
        shas: set[str] = set()
        for _, doc in kept:
            for t in doc.get("tasks") or ():
                if t.get("key"):
                    keys.add(t["key"])
            for sha in (doc.get("layouts") or {}).values():
                shas.add(sha)
                if self.has(sha):
                    _collect_shas(contract.loads(self.read(sha)), shas)
        for key in keys:
            res = self.result(key)
            if res is None:
                continue
            shas.update(a["content"]["sha256"] for a in res.get("artifacts", ()))
            shas.update(sha for _, sha in (res.get("keyParts") or {}).get("inputs", ()))
        removed_bytes = 0
        out: dict = {"runs": {"kept": len(kept), "removed": len(dropped)}}
        for p, _ in dropped:
            if not dry_run:
                p.unlink(missing_ok=True)
                p.with_name(p.name[: -len(".json")] + ".log.jsonl").unlink(missing_ok=True)
        n_kept = n_removed = 0
        for p in sorted((self.root / "ac").glob("*/*.json")):
            key = p.name[: -len(".json")]
            if key in keys:
                n_kept += 1
                continue
            n_removed += 1
            if not dry_run:
                p.unlink(missing_ok=True)
                (self.root / "costs" / f"{key}.json").unlink(missing_ok=True)
        out["results"] = {"kept": n_kept, "removed": n_removed}
        n_kept = n_removed = 0
        for p in sorted((self.root / "cas" / "sha256").glob("*/*")):
            if p.name in shas:
                n_kept += 1
                continue
            n_removed += 1
            removed_bytes += p.stat().st_size
            if not dry_run:
                _drop(p)
                self._have.discard(p.name)
        out["contents"] = {"kept": n_kept, "removed": n_removed}
        out["bytesRemoved"] = removed_bytes
        return out


def _collect_shas(doc, out: set[str]) -> None:
    """Add every "sha256" value of a document (a layout manifest names its files' contents so)."""
    if isinstance(doc, dict):
        sha = doc.get("sha256")
        if isinstance(sha, str) and contract.is_sha256(sha):
            out.add(sha)
        for v in doc.values():
            _collect_shas(v, out)
    elif isinstance(doc, list):
        for v in doc:
            _collect_shas(v, out)
