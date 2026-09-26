"""Master data views: which catalog objects the rows of a master table name, role by role.

A view is one master table read through its rules (package data `viewrules.json`, "format": 1, a version per view;
docs/views.md): every row gives values (`vars`, columns or chains of columns through other tables), names in the five
text languages (`names`: MasterText id columns, languages.texts), and per role an address the game formats from
those values. An address is a catalog key, optionally with an Addressables sub-object: `key[sub]`. Resolving it
(`resolve`) looks the key up in an address index (catalog key -> the objects of its container path in the order the
bundle stores them, from the censuses) and picks the object the Addressables loader returns for a load of the
expected class:

    [sub] given       the first object named `sub` of the expected class
    no [sub]          the first object of the expected class (a Sprite load of a sprite sheet: its first sprite)

With no expected class (`any`), the first object. A list of classes tries them in order. When several objects fit,
the entry says how many (`among`).

Statuses: ok (an object fits), missing-key (not a catalog key, or no census object under it), missing-sub (no object
fits), not-applicable (the role's `when` condition is false), no-value (a value the address needs is empty or
absent). A role can be enumerated (`each`: fixed values, a list column, or the catalog keys under a formatted
prefix) and polymorphic references (a GameResourceType and an id) are resolved through `resolvers`.

Coverage both ways: forward per role (counts per status; the rows where a required role is missing-key or
missing-sub are its gaps) and reverse (catalog keys under the view's prefixes that no row references). Gaps are
data, not errors.

A view document (nnnotes.view/1) holds object ids, never content hashes, so its task depends on the master tables it
reads, the digest of its rules and the part of the address index it read (`Recorder.subset`), not on how objects
are encoded: new PNG bytes never change a view, a master data update changes only views.

The stages (`stages()`: one `view.<name>` per view, subjects = the catalog subjects of link.addresses) take exactly
those inputs: "master:<Table>" (the decoded table files), "rules" (the view's rule and the resolvers it uses) and
"addresses" (the recorded part of the address table of link.addresses:<subject>), and write the view document as
the artifact "<task id>#view". `derived` adds the layout paths of the objects for the `original` layout
(views/<name>.json).
"""
from __future__ import annotations

import bisect
import copy
import functools
import json
import re
import string
from collections import OrderedDict
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from . import contract, languages
from .contract import Cost, Input
from .stages import Output, Stage

FORMAT = 1
RULES_FILE = "viewrules.json"
TEXT_TABLE = "MasterText"
STATUSES = ("ok", "missing-key", "missing-sub", "not-applicable", "no-value")
GAP_STATUSES = ("missing-key", "missing-sub")
ANY = "any"
OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and a < b, "le": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b, "ge": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b, "notin": lambda a, b: a not in b,
    "empty": lambda a, b: _empty(a), "nonempty": lambda a, b: not _empty(a),
}
_SUB = re.compile(r"(?P<key>.*[^\]])\[(?P<sub>[^\[\]]+)\]")
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_VIEW_KEYS = {"version", "table", "sources", "id", "vars", "names", "prefixes", "exclude", "roles"}
_ROLE_KEYS = {"role", "address", "expect", "required", "when", "each", "resolver", "type", "id"}
_RESOLVER_KEYS = {"name", "table", "vars", "names", "address", "expect"}


class ViewError(ValueError):
    """Rules that cannot be used as written, or master data a view cannot read."""


def _empty(v) -> bool:
    return v is None or v == "" or v == []


# ---------------------------------------------------------------- the address index
@dataclass(frozen=True, order=True)
class Obj:
    """A census object under a catalog key: its object id (contract.object_id), Unity class, m_Name and, when known,
    its stable address (contract.stable_address: the same in another catalog version while the object id changes
    with its bundle's content)."""
    id: str
    cls: str
    name: str
    stable: str | None = None

    def to_json(self) -> list:
        return [self.id, self.cls, self.name] + ([self.stable] if self.stable is not None else [])

    @classmethod
    def from_json(cls, v) -> "Obj":
        return cls(str(v[0]), str(v[1]), str(v[2]), None if len(v) < 4 or v[3] is None else str(v[3]))


class Addresses(Protocol):
    """What a view reads of the catalog and the censuses."""

    def keys(self, prefix: str = "") -> list[str]:
        """The catalog keys starting with `prefix`, sorted."""

    def objects(self, key: str) -> list[Obj] | None:
        """The objects under `key` in the order its bundle stores them (None: not a catalog key; []: a key whose
        container has no census object)."""


class AddressIndex:
    """Addresses from {catalog key: objects in stored order} (a key mapped to None or [] has no census object)."""

    def __init__(self, entries: Mapping[str, Iterable[Obj] | None]):
        self._objs = {k: list(v or ()) for k, v in entries.items()}
        self._keys = sorted(self._objs)

    def keys(self, prefix: str = "") -> list[str]:
        i = bisect.bisect_left(self._keys, prefix)
        out = []
        for k in self._keys[i:]:
            if not k.startswith(prefix):
                break
            out.append(k)
        return out

    def objects(self, key: str) -> list[Obj] | None:
        objs = self._objs.get(key)
        return None if objs is None else list(objs)

    @classmethod
    def build(cls, locations: Mapping[str, str], containers: Mapping[str, Iterable[Obj]]) -> "AddressIndex":
        """The index of a catalog and its censuses: `locations` {key: internal id} (the asset path a key loads),
        `containers` {container path: objects} (census container entries, in stored order). Unity writes container
        paths in lower case, so an internal id matches its container path case-insensitively."""
        by_path: dict[str, list[Obj]] = {}
        for path, objs in containers.items():
            by_path.setdefault(path.lower(), []).extend(objs)
        return cls({key: by_path.get(internal.lower(), []) for key, internal in locations.items()})

    @classmethod
    def from_addresses(cls, doc: dict) -> "AddressIndex":
        """The index of an address table (nnnotes.addresses/1, the link.addresses stage): a key's objects are those
        of all its locations (in the table's order, the order of the bundle's container), with class, name and
        stable address from the table's `objects`; a location whose bundle has no census yet contributes none."""
        where: dict[str, str] = {}
        for addr, info in doc["objects"].items():
            where.setdefault(info["object"], addr)
        entries = {}
        for key, locs in doc["keys"].items():
            objs = {}
            for loc in locs:
                for oid in loc.get("objects") or ():
                    addr = where.get(oid)
                    info = doc["objects"][addr] if addr is not None else {}
                    objs[oid] = Obj(oid, info.get("class") or "", info.get("name") or "", addr)
            entries[key] = list(objs.values())
        return cls(entries)

    def to_json(self) -> dict:
        return {"keys": {k: [o.to_json() for o in self._objs[k]] for k in self._keys}}

    @classmethod
    def from_json(cls, doc: dict) -> "AddressIndex":
        return cls({k: [Obj.from_json(o) for o in (v or ())] for k, v in doc["keys"].items()})

    @classmethod
    def from_subset(cls, subset: dict) -> "AddressIndex":
        """The index a recorded part of an index stands for (Recorder.subset): its keys with their objects, and the
        keys its prefixes listed. A view built on it reads exactly what it read when the part was recorded."""
        entries = {k: [Obj.from_json(o) for o in v] for k, v in subset["keys"].items() if v is not None}
        for keys in subset["prefixes"].values():
            for k in keys:
                entries.setdefault(k, [])
        return cls(entries)


class Recorder:
    """Addresses that remember what was read: `subset()` is the part of the index a view depends on."""

    def __init__(self, inner: Addresses):
        self.inner = inner
        self._keys: dict[str, list[Obj] | None] = {}
        self._prefixes: dict[str, list[str]] = {}

    def keys(self, prefix: str = "") -> list[str]:
        if prefix not in self._prefixes:
            self._prefixes[prefix] = list(self.inner.keys(prefix))
        return list(self._prefixes[prefix])

    def objects(self, key: str) -> list[Obj] | None:
        if key not in self._keys:
            objs = self.inner.objects(key)
            self._keys[key] = None if objs is None else list(objs)
        objs = self._keys[key]
        return None if objs is None else list(objs)

    def subset(self) -> dict:
        """{"keys": {key: objects or null}, "prefixes": {prefix: keys}}, sorted."""
        return {"keys": {k: None if v is None else [o.to_json() for o in v] for k, v in sorted(self._keys.items())},
                "prefixes": {p: v for p, v in sorted(self._prefixes.items())}}


# ---------------------------------------------------------------- master tables
class Tables(Protocol):
    def __getitem__(self, name: str) -> list[dict]:
        """The rows of a master table (KeyError when there is no such table)."""


def table_rows(data: bytes) -> list[dict]:
    """The rows of a decoded master table file (`_allData`, as master.table reads it)."""
    return json.loads(bytes(data).decode("utf-8"))["_allData"]


class MasterTables:
    """The decoded master tables of a directory (<name>.json), read once each."""

    def __init__(self, master_dir):
        self.dir = Path(master_dir)
        self._rows: dict[str, list[dict]] = {}

    def __getitem__(self, name: str) -> list[dict]:
        if name not in self._rows:
            try:
                self._rows[name] = table_rows((self.dir / f"{name}.json").read_bytes())
            except FileNotFoundError:
                raise KeyError(name) from None
        return self._rows[name]

    def sha256(self, name: str) -> str:
        """The content id of a table's file."""
        return contract.sha256((self.dir / f"{name}.json").read_bytes())


# ---------------------------------------------------------------- rules
def load_rules(path=None) -> dict:
    """The view rules: `path`, else the package's viewrules.json; checked (check_rules)."""
    if path is None:
        text = resources.files(__package__).joinpath(RULES_FILE).read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    rules = json.loads(text)
    check_rules(rules)
    return rules


@functools.lru_cache(maxsize=None)
def _fields(template: str) -> tuple[str, ...]:
    out = []
    for _, name, _, _ in string.Formatter().parse(template):
        if name is not None:
            if not _NAME.fullmatch(name):
                raise ViewError(f"template {template!r}: field {name!r} is not a plain name")
            out.append(name)
    return tuple(out)


def fields(template: str) -> list[str]:
    """The names an address template formats ({name} or {name:spec})."""
    return list(_fields(template))


def _check_var(where: str, spec) -> None:
    chain = [spec] if isinstance(spec, str) else spec
    if not isinstance(chain, list) or not chain or not all(isinstance(s, str) and s for s in chain):
        raise ViewError(f"{where}: a column name or a list [column, Table.column, ...]")
    for step in chain[1:]:
        table, sep, col = step.partition(".")
        if not sep or not table or not col:
            raise ViewError(f"{where}: {step!r} is not Table.column")


def _check_when(where: str, when, names: set) -> None:
    for c in when if isinstance(when, list) else [when]:
        if not isinstance(c, dict) or set(c) - {"field", "op", "value"} or "field" not in c or "op" not in c:
            raise ViewError(f"{where}: a condition is {{field, op, value}}")
        if c["op"] not in OPS:
            raise ViewError(f"{where}: unknown operator {c['op']!r} (one of {', '.join(OPS)})")
        if c["field"] not in names and not c["field"].startswith("_"):
            raise ViewError(f"{where}: {c['field']!r} is neither a var nor a column")


def _check_expect(where: str, expect) -> None:
    if isinstance(expect, str):
        return
    if not isinstance(expect, list) or not expect or not all(isinstance(e, str) and e != ANY for e in expect):
        raise ViewError(f"{where}: expect is a class name, a list of class names or \"any\"")


def check_rules(rules: dict) -> None:
    """Raise ViewError naming the first problem of `rules` (format, views, roles, templates, resolvers)."""
    if rules.get("format") != FORMAT:
        raise ViewError(f"view rules: format {rules.get('format')!r}, this version reads {FORMAT}")
    resolvers = rules.get("resolvers", {})
    for rname, res in resolvers.items():
        for code, r in res.items():
            where = f"resolvers.{rname}.{code}"
            if not code.lstrip("-").isdigit():
                raise ViewError(f"{where}: resolver codes are integers")
            if set(r) - _RESOLVER_KEYS or "address" not in r or "name" not in r:
                raise ViewError(f"{where}: keys are {', '.join(sorted(_RESOLVER_KEYS))} (name and address needed)")
            rvars = r.get("vars", {})
            for v, spec in rvars.items():
                _check_var(f"{where}.vars.{v}", spec)
            if rvars and "table" not in r:
                raise ViewError(f"{where}: vars without a table")
            for f in fields(r["address"]):
                if f not in rvars:
                    raise ViewError(f"{where}: address field {f!r} is not a var")
            _check_expect(where, r.get("expect", ANY))
    for name, v in rules.get("views", {}).items():
        where = f"views.{name}"
        if not _NAME.fullmatch(name):
            raise ViewError(f"{where}: view names are lower case")
        if set(v) - _VIEW_KEYS:
            raise ViewError(f"{where}: unknown keys {sorted(set(v) - _VIEW_KEYS)}")
        if not isinstance(v.get("version"), int) or v["version"] < 1:
            raise ViewError(f"{where}: version is a positive integer")
        if ("table" in v) == ("sources" in v):
            raise ViewError(f"{where}: either a table or sources")
        names = set(v.get("vars", {}))
        if "sources" in v:
            for s in v["sources"]:
                if set(s) != {"table", "type", "id"}:
                    raise ViewError(f"{where}.sources: each source is {{table, type, id}}")
            names = {"type", "id"}
            if v.get("vars"):
                raise ViewError(f"{where}: a view of sources has the vars type and id only")
        for var, spec in v.get("vars", {}).items():
            if not _NAME.fullmatch(var):
                raise ViewError(f"{where}.vars.{var}: var names are lower case")
            _check_var(f"{where}.vars.{var}", spec)
        for label, spec in v.get("names", {}).items():
            _check_var(f"{where}.names.{label}", spec)
        seen = set()
        for role in v.get("roles", []):
            rw = f"{where}.roles.{role.get('role')}"
            if set(role) - _ROLE_KEYS or not role.get("role"):
                raise ViewError(f"{rw}: keys are {', '.join(sorted(_ROLE_KEYS))} (role needed)")
            if role["role"] in seen:
                raise ViewError(f"{rw}: repeated role")
            seen.add(role["role"])
            rnames = set(names)
            each = role.get("each")
            if each is not None:
                kinds = {"values", "column", "catalogPrefix"} & set(each)
                if len(kinds) != 1 or set(each) - {"var", "values", "column", "catalogPrefix"} or "var" not in each:
                    raise ViewError(f"{rw}.each: {{var}} and one of values, column, catalogPrefix")
                if each["var"] in names:
                    raise ViewError(f"{rw}.each: {each['var']!r} is already a var")
                if "catalogPrefix" in each:
                    for f in fields(each["catalogPrefix"]):
                        if f not in names:
                            raise ViewError(f"{rw}.each: prefix field {f!r} is not a var")
                rnames.add(each["var"])
            if "resolver" in role:
                if "address" in role or "each" in role or "expect" in role or role["resolver"] not in resolvers:
                    raise ViewError(f"{rw}: a resolver role names a known resolver and has no address, each or "
                                    f"expect")
                for k in ("type", "id"):
                    if role.get(k, k) not in rnames:
                        raise ViewError(f"{rw}: {k} var {role.get(k, k)!r} is not a var")
            else:
                if "address" not in role:
                    raise ViewError(f"{rw}: no address")
                for f in fields(role["address"]):
                    if f not in rnames:
                        raise ViewError(f"{rw}: address field {f!r} is not a var")
                _check_expect(rw, role.get("expect", ANY))
            if "when" in role:
                _check_when(rw, role["when"], rnames)
        for p in v.get("prefixes", []) + v.get("exclude", []):
            if not isinstance(p, str) or not p or p.startswith("*"):
                raise ViewError(f"{where}: a prefix is a non-empty string that does not start with *")


def view_names(rules: dict) -> list[str]:
    return sorted(rules.get("views", {}))


def _chain(spec) -> list[str]:
    return [spec] if isinstance(spec, str) else list(spec)


def used_resolvers(rules: dict, name: str) -> dict:
    """The resolvers the view `name` uses: {resolver: {code: spec}}."""
    v = rules["views"][name]
    used = sorted({r["resolver"] for r in v.get("roles", []) if "resolver" in r})
    return {r: rules["resolvers"][r] for r in used}


def view_tables(rules: dict, name: str) -> list[str]:
    """Every master table the view `name` reads, sorted."""
    v = rules["views"][name]
    out = set()
    if "table" in v:
        out.add(v["table"])
    out.update(s["table"] for s in v.get("sources", []))
    specs = list(v.get("vars", {}).values()) + list(v.get("names", {}).values())
    for res in used_resolvers(rules, name).values():
        for r in res.values():
            if "table" in r:
                out.add(r["table"])
            specs += list(r.get("vars", {}).values()) + list(r.get("names", {}).values())
            if r.get("names"):
                out.add(TEXT_TABLE)
    for spec in specs:
        out.update(step.partition(".")[0] for step in _chain(spec)[1:])
    if v.get("names"):
        out.add(TEXT_TABLE)
    return sorted(out)


def rules_digest(rules: dict, name: str) -> str:
    """The key hash of what the view `name` is made by: the rules format, its rule and the resolvers it uses."""
    return contract.digest({"format": rules["format"], "view": rules["views"][name],
                            "resolvers": used_resolvers(rules, name)})


# ---------------------------------------------------------------- one address
def parse_address(address: str) -> tuple[str, str | None]:
    """(key, sub-object name or None) of an address `key[sub]`."""
    m = _SUB.fullmatch(address)
    return (m["key"], m["sub"]) if m else (address, None)


def _classes(expect) -> list[str] | None:
    if expect == ANY:
        return None
    return [expect] if isinstance(expect, str) else list(expect)


def choose(objs: list[Obj], sub: str | None, expect=ANY) -> list[Obj]:
    """The objects (in stored order) that a load of `key[sub]` with the expected class(es) can return: those named
    `sub` (all without [sub]) of the first expected class that has any (all classes with `any`). The Addressables
    loader returns the first."""
    pool = [o for o in objs if o.name == sub] if sub is not None else list(objs)
    classes = _classes(expect)
    if classes is None:
        return pool
    for c in classes:
        tier = [o for o in pool if o.cls == c]
        if tier:
            return tier
    return []


def resolve(index: Addresses, address: str, expect=ANY) -> dict:
    """The resolution of one address (module docstring): {address, status, object, class, stable} when an object
    fits (`among`: how many fit, when several), the objects the key has when none fits, a detail when the key is
    missing."""
    key, sub = parse_address(address)
    objs = index.objects(key)
    out = {"address": address}
    if not objs:
        out.update(status="missing-key", detail="not a catalog key" if objs is None else "no census objects")
        return out
    pool = choose(objs, sub, expect)
    if not pool:
        out.update(status="missing-sub", available=[o.to_json() for o in objs])
        return out
    out.update(status="ok", object=pool[0].id, **{"class": pool[0].cls})
    if pool[0].stable is not None:
        out["stable"] = pool[0].stable
    if len(pool) > 1:
        out["among"] = len(pool)
    return out


# ---------------------------------------------------------------- one view
class _Row:
    """Values of one master row: its columns, its vars (chains through other tables) and conditions."""

    def __init__(self, row: dict, tables: "_Lookup"):
        self.row, self.tables = row, tables

    def value(self, spec) -> tuple[object, str | None]:
        """(value, why it is missing or None) of a var spec."""
        chain = _chain(spec)
        v = self.row.get(chain[0])
        for step in chain[1:]:
            if _empty(v):
                return None, f"{chain[0]} is empty"
            table, _, col = step.partition(".")
            target = self.tables.row(table, v)
            if target is None:
                return None, f"{table} has no row {v}"
            v = target.get(col)
        return v, None


class _Lookup:
    """Master tables by id (the `_id` column), read once."""

    def __init__(self, tables: Tables):
        self.tables = tables
        self._by_id: dict[str, dict] = {}

    def rows(self, name: str) -> list[dict]:
        try:
            return self.tables[name]
        except KeyError:
            raise ViewError(f"master table {name} is not in the master data") from None

    def row(self, name: str, rid):
        if name not in self._by_id:
            self._by_id[name] = {r.get("_id"): r for r in self.rows(name)}
        return self._by_id[name].get(rid)

    def text(self, tid):
        """The five languages of a MasterText id (None when the table has no such row)."""
        row = self.row(TEXT_TABLE, tid)
        return None if row is None else languages.texts(row)


def _names(row: _Row, specs: dict, look: _Lookup) -> dict:
    out = {}
    for label, spec in specs.items():
        tid, _ = row.value(spec)
        if not _empty(tid):
            out[label] = look.text(tid)
    return out


def _holds(when, values: dict, row: dict) -> bool:
    for c in when if isinstance(when, list) else [when]:
        f = c["field"]
        v = values[f] if f in values else row.get(f)
        if not OPS[c["op"]](v, c.get("value")):
            return False
    return True


def _format(template: str, values: dict, missing: dict) -> tuple[str | None, str | None]:
    for f in fields(template):
        if _empty(values.get(f)):
            return None, missing.get(f) or f"{f} is empty"
    try:
        return template.format_map(values), None
    except (ValueError, TypeError) as e:
        return None, f"{template}: {e}"


def _entry(index: Addresses, template: str, values: dict, missing: dict, expect) -> dict:
    address, why = _format(template, values, missing)
    if address is None:
        return {"status": "no-value", "detail": why}
    return resolve(index, address, expect)


def _enumerate(each: dict, values: dict, row: dict, index: Addresses) -> list:
    if "values" in each:
        return list(each["values"])
    if "column" in each:
        v = row.get(each["column"])
        return list(v) if isinstance(v, list) else ([] if _empty(v) else [v])
    prefix, why = _format(each["catalogPrefix"], values, {})
    if prefix is None:
        return []
    out = set()
    for k in index.keys(prefix):
        rest = k[len(prefix):]
        part = re.split(r"[/\[]", rest, maxsplit=1)[0]
        if part:
            out.add(part)
    return sorted(out)


def _resolved(rules: dict, role: dict, values: dict, index: Addresses, look: _Lookup) -> tuple[dict, dict]:
    """(entry, names of the target row) of a resolver role."""
    code, rid = values.get(role.get("type", "type")), values.get(role.get("id", "id"))
    spec = rules["resolvers"][role["resolver"]].get(str(code))
    if spec is None:
        return {"status": "not-applicable", "detail": f"{role['resolver']} {code} has no resolver"}, {}
    head = {"resource": spec["name"]}
    rvalues, missing, names = {}, {}, {}
    if "table" in spec:
        target = look.row(spec["table"], rid)
        if target is None:
            return {**head, "status": "no-value", "detail": f"{spec['table']} has no row {rid}"}, {}
        trow = _Row(target, look)
        for var, vs in spec.get("vars", {}).items():
            rvalues[var], why = trow.value(vs)
            if why:
                missing[var] = why
        names = _names(trow, spec.get("names", {}), look)
    return {**head, **_entry(index, spec["address"], rvalues, missing, spec.get("expect", ANY))}, names


def _row_doc(rules: dict, view: dict, rid, values: dict, missing: dict, raw: dict, names: dict,
             index: Addresses, look: _Lookup) -> dict:
    roles = {}
    for role in view.get("roles", []):
        applies = "when" not in role or role.get("each") is not None or _holds(role["when"], values, raw)
        if "resolver" in role:
            if applies:
                entry, tnames = _resolved(rules, role, values, index, look)
                names = names or tnames
            else:
                entry = {"status": "not-applicable"}
            roles[role["role"]] = entry
            continue
        expect = role.get("expect", ANY)
        each = role.get("each")
        if each is None:
            roles[role["role"]] = (_entry(index, role["address"], values, missing, expect) if applies
                                   else {"status": "not-applicable"})
            continue
        entries = []
        for item in _enumerate(each, values, raw, index):
            ivalues = {**values, each["var"]: item}
            if "when" in role and not _holds(role["when"], ivalues, raw):
                e = {"status": "not-applicable"}
            else:
                e = _entry(index, role["address"], ivalues, missing, expect)
            entries.append({"each": {each["var"]: item}, **e})
        roles[role["role"]] = entries
    doc = {"id": rid, "vars": values, "roles": roles}
    if names:
        doc["names"] = names
    return doc


def _sort_key(rid):
    return (0, rid, "") if isinstance(rid, (int, float)) else (1, 0, str(rid))


def _rows(rules: dict, view: dict, index: Addresses, look: _Lookup) -> list[dict]:
    out = []
    if "sources" in view:
        found: dict[tuple, set] = {}
        for s in view["sources"]:
            for r in look.rows(s["table"]):
                t, i = r.get(s["type"]), r.get(s["id"])
                if t is None or i is None:
                    continue
                found.setdefault((t, i), set()).add(s["table"])
        for (t, i), tables in sorted(found.items(), key=lambda kv: (_sort_key(kv[0][0]), _sort_key(kv[0][1]))):
            doc = _row_doc(rules, view, f"{t}:{i}", {"type": t, "id": i}, {}, {}, {}, index, look)
            doc["from"] = sorted(tables)
            out.append(doc)
        return out
    idcol = view.get("id", "_id")
    rows = look.rows(view["table"])
    ids = [r.get(idcol) for r in rows]
    if len(set(map(repr, ids))) != len(ids):
        raise ViewError(f"{view['table']}: {idcol} is not unique")
    for r in sorted(rows, key=lambda r: _sort_key(r.get(idcol))):
        row = _Row(r, look)
        values, missing = {}, {}
        for var, spec in view.get("vars", {}).items():
            values[var], why = row.value(spec)
            if why:
                missing[var] = why
        out.append(_row_doc(rules, view, r.get(idcol), values, missing, r, _names(row, view.get("names", {}), look),
                            index, look))
    return out


def _prefix_pattern(p: str) -> tuple[str, re.Pattern]:
    literal = p.split("*", 1)[0]
    return literal, re.compile(re.escape(p).replace(r"\*", "[^/]+"))


def prefix_keys(index: Addresses, prefixes: list[str], exclude: list[str] = ()) -> list[str]:
    """The catalog keys matching a prefix (`*` stands for one path segment) and no exclusion, sorted."""
    out = set()
    for p in prefixes:
        literal, pat = _prefix_pattern(p)
        out.update(k for k in index.keys(literal) if pat.match(k))
    for p in exclude:
        _, pat = _prefix_pattern(p)
        out = {k for k in out if not pat.match(k)}
    return sorted(out)


def _entries(row: dict):
    for role, e in row["roles"].items():
        for x in (e if isinstance(e, list) else [e]):
            yield role, x


def referenced(doc: dict) -> set[str]:
    """The catalog keys the rows of a view document reference (their addresses without [sub])."""
    return {parse_address(x["address"])[0] for row in doc["rows"] for _, x in _entries(row) if "address" in x}


def coverage(doc: dict, view: dict, index: Addresses) -> dict:
    """Forward coverage per role and reverse coverage of the view's prefixes (module docstring)."""
    roles = {}
    for role in view.get("roles", []):
        roles[role["role"]] = {"required": bool(role.get("required", False)), "counts": {}, "gaps": []}
    for row in doc["rows"]:
        for role, x in _entries(row):
            c = roles[role]
            c["counts"][x["status"]] = c["counts"].get(x["status"], 0) + 1
            if c["required"] and x["status"] in GAP_STATUSES and (not c["gaps"] or c["gaps"][-1] != row["id"]):
                c["gaps"].append(row["id"])
    for c in roles.values():
        c["counts"] = dict(sorted(c["counts"].items()))
    keys = prefix_keys(index, view.get("prefixes", []), view.get("exclude", []))
    refs = referenced(doc)
    return {"rows": len(doc["rows"]), "roles": roles,
            "reverse": {"prefixes": list(view.get("prefixes", [])), "exclude": list(view.get("exclude", [])),
                        "keys": len(keys), "unreferenced": [k for k in keys if k not in refs]}}


def build(rules: dict, name: str, tables: Tables, index: Addresses) -> dict:
    """The view document (nnnotes.view/1) of the view `name`: its rows (id, vars, names, roles) sorted by id, and
    its coverage. Wrap `index` in a Recorder to learn the part of the index it read."""
    if name not in rules.get("views", {}):
        raise ViewError(f"no view {name!r} (views: {', '.join(view_names(rules))})")
    view = rules["views"][name]
    look = _Lookup(tables)
    doc = {"schema": contract.VIEW, "view": name, "version": view["version"], "rules": rules_digest(rules, name),
           "tables": view_tables(rules, name), "rows": _rows(rules, view, index, look)}
    doc["coverage"] = coverage(doc, view, index)
    return doc


def gaps(doc: dict) -> int:
    """The number of (row, required role) gaps of a view document."""
    return sum(len(c["gaps"]) for c in doc["coverage"]["roles"].values())


def unreferenced(docs: Iterable[dict], rules: dict, index: Addresses) -> list[str]:
    """The catalog keys under the prefixes of the given views that none of them references (views sharing a
    prefix, such as two tables naming banners of one folder, cover each other here), sorted."""
    docs = list(docs)
    keys, refs = set(), set()
    for d in docs:
        v = rules["views"][d["view"]]
        keys.update(prefix_keys(index, v.get("prefixes", []), v.get("exclude", [])))
        refs |= referenced(d)
    return sorted(keys - refs)


def build_all(rules: dict, tables: Tables, index: Addresses, names=None) -> dict[str, dict]:
    """{name: view document} of `names` (default: every view), in name order."""
    return {n: build(rules, n, tables, index) for n in (sorted(names) if names is not None else view_names(rules))}


def _index(doc: dict) -> dict:
    out = {}
    for row in doc["rows"]:
        for role, x in _entries(row):
            each = x.get("each")
            out[(repr(row["id"]), role, repr(sorted(each.items())) if each else "")] = (row["id"], role, each, x)
    return out


def _state(x: dict) -> dict:
    return {k: x[k] for k in ("status", "address", "object", "stable") if k in x}


def diff(old: dict, new: dict) -> dict:
    """What changed between two documents of one view (two master or catalog versions): rows added and removed, and
    per (row, role, enumerated value) entry a change of status, of address, of where the object is (its stable
    address) or only of its object id (the same place, new bundle content). Sorted, deterministic."""
    if old.get("view") != new.get("view"):
        raise ViewError(f"documents of different views: {old.get('view')} and {new.get('view')}")
    oids = {repr(r["id"]): r["id"] for r in old["rows"]}
    nids = {repr(r["id"]): r["id"] for r in new["rows"]}
    a, b = _index(old), _index(new)
    changes = []
    for k in sorted(set(a) | set(b)):
        if k[0] not in oids or k[0] not in nids:
            continue                                   # an added or removed row: listed once below
        x, y = a.get(k), b.get(k)
        sx, sy = _state(x[3]) if x else None, _state(y[3]) if y else None
        if sx == sy:
            continue
        if sx is None or sy is None:
            kind = "added" if sx is None else "removed"
        elif sx.get("status") != sy.get("status"):
            kind = "status"
        elif sx.get("address") != sy.get("address"):
            kind = "address"
        elif sx.get("stable") != sy.get("stable"):
            kind = "moved"
        else:
            kind = "object"
        row, role, each, _ = x or y
        c = {"row": row, "role": role, "kind": kind, "old": sx, "new": sy}
        if each:
            c["each"] = each
        changes.append(c)
    key = lambda i: _sort_key(i)                       # noqa: E731
    return {"view": new["view"],
            "rows": {"added": sorted((nids[i] for i in set(nids) - set(oids)), key=key),
                     "removed": sorted((oids[i] for i in set(oids) - set(nids)), key=key)},
            "changes": changes}


# ---------------------------------------------------------------- stages
ADDRESSES_STAGE = "link.addresses"
STAGE_PREFIX = "view."
MASTER_ROLE = "master:"
VIEW_ROLE = "view"
MAIN_SUBJECT = "main"
_TABLE_CACHE = 128
_rows_cache: OrderedDict = OrderedDict()        # content id of a table file -> its rows (the latest _TABLE_CACHE)
_index_cache: OrderedDict = OrderedDict()       # content id of an address table -> its AddressIndex (the latest 2)


def _cached_rows(sha: str, read) -> list[dict]:
    """The rows of the table file with content id `sha` (`read()` gives its bytes; checked against `sha`)."""
    rows = _rows_cache.get(sha)
    if rows is not None:
        _rows_cache.move_to_end(sha)
        return rows
    data = read()
    if contract.sha256(data) != sha:
        raise ViewError(f"a master table file changed while it was read (expected {sha})")
    rows = _rows_cache[sha] = table_rows(data)
    while len(_rows_cache) > _TABLE_CACHE:
        _rows_cache.popitem(last=False)
    return rows


def _address_index(env, tid: str) -> AddressIndex:
    """The address index of the result of the link.addresses task `tid` (Pending until it has run)."""
    sha = env.artifact(tid, "addresses")["content"]["sha256"]
    ix = _index_cache.get(sha)
    if ix is None:
        ix = _index_cache[sha] = AddressIndex.from_addresses(contract.loads(env.store.read(sha)))
        while len(_index_cache) > 2:
            _index_cache.popitem(last=False)
    return ix


def _stored(store, role: str, data: bytes) -> Input:
    return Input(role, store.put(data), len(data), None, ({"kind": "store"},))


def _rank(aid: str) -> tuple:
    role = contract.parse_artifact_id(aid)[1]
    order = contract.PRIMARY_ROLES
    return (order.index(role) if role in order else len(order), role)


def with_paths(doc: dict, paths, layout: str = "original") -> dict:
    """A copy of a view document with the files of its objects in a layout (`paths.objects(object id)`: the
    object's artifact ids; `paths.artifact(artifact id)`: its path in the layout or None): every entry naming an
    object gets `files` ({artifact role: path} of the artifacts the layout places) and `path` (the file of its
    primary artifact, contract.PRIMARY_ROLES; null when the layout places none)."""
    out = copy.deepcopy(doc)
    seen: dict[str, dict] = {}
    for row in out["rows"]:
        for _, x in _entries(row):
            oid = x.get("object")
            if oid is None:
                continue
            if oid not in seen:
                files = {}
                for aid in sorted(paths.objects(oid) or (), key=_rank):
                    p = paths.artifact(aid)
                    if p is not None:
                        files.setdefault(contract.parse_artifact_id(aid)[1], p)
                seen[oid] = files
            x["path"] = next(iter(seen[oid].values()), None)
            x["files"] = dict(sorted(seen[oid].items()))
    out["layout"] = layout
    return out


def view_path(view: str, subject: str) -> str:
    """Where the `original` layout puts the view document of `view` for a catalog subject: views/<view>.json for
    the main catalog, views/<subject>/<view>.json for another."""
    return f"views/{view}.json" if subject == MAIN_SUBJECT else f"views/{subject}/{view}.json"


class ViewStage(Stage):
    """view.<name>: one view of the catalog subject's address table (the result of link.addresses:<subject>) and the
    decoded master data (the fact "master": its directory; no master data, no tasks). Selected by the fact "views"
    (view names; absent: every view). Inputs: "master:<Table>" for every table the view reads (the table files),
    "rules" (the rules document of this view alone: its rule, the resolvers it uses, the format) and "addresses"
    (the part of the address table the view reads, Recorder.subset); both stored at description. Version: the
    view's version in the rules. Artifact "<task id>#view" (nnnotes.view/1); no items (a view covers no object)."""
    after = (ADDRESSES_STAGE,)

    def __init__(self, view: str, rules: dict):
        if view not in rules.get("views", {}):
            raise ViewError(f"no view {view!r} (views: {', '.join(view_names(rules))})")
        self.view = view
        self.name = STAGE_PREFIX + view
        self.version = rules["views"][view]["version"]
        self.rules = {"format": rules["format"], "resolvers": used_resolvers(rules, view),
                      "views": {view: rules["views"][view]}}
        check_rules(self.rules)
        self.rules_bytes = contract.encode(self.rules)
        self.tables = view_tables(self.rules, view)

    def subjects(self, env) -> list[str]:
        if env.facts.get("master") is None:
            return []
        selected = env.facts.get("views")
        if selected is not None and self.view not in selected:
            return []
        return sorted(env.fact("catalogs"))

    def depends(self, subject: str, env) -> list[str]:
        return [contract.task_id(ADDRESSES_STAGE, subject)]

    def master_inputs(self, env) -> list[Input]:
        """The inputs "master:<Table>" of the tables the view reads (ViewError for a table the master data lacks)."""
        mdir = Path(env.fact("master"))
        out = []
        for t in self.tables:
            p = mdir / f"{t}.json"
            if not p.is_file():
                raise ViewError(f"view {self.view}: master table {t} is not in the master data")
            sha, size = env.store.identify(p)
            out.append(Input(MASTER_ROLE + t, sha, size, p.name, ({"kind": "file", "path": str(p)},)))
        return out

    def inputs(self, subject: str, env) -> list[Input]:
        index = _address_index(env, contract.task_id(ADDRESSES_STAGE, subject))
        masters = self.master_inputs(env)
        tables = {i.role[len(MASTER_ROLE):]: _cached_rows(i.sha256, Path(i.locators[0]["path"]).read_bytes)
                  for i in masters}
        rec = Recorder(index)
        build(self.rules, self.view, tables, rec)
        return masters + [_stored(env.store, "addresses", contract.encode(rec.subset())),
                          _stored(env.store, "rules", self.rules_bytes)]

    def estimate(self, subject: str, env, inputs: list[Input]) -> Cost:
        size = sum(i.size for i in inputs)
        return Cost(0.05 + size / 20e6, (48 << 20) + 16 * size)

    def run(self, task, store) -> Output:
        rules = contract.loads(store.input_bytes(task.input("rules")))
        check_rules(rules)
        if list(rules["views"]) != [self.view] or rules["views"][self.view]["version"] != task.version:
            raise ViewError(f"task {task.id}: its rules are not those of view {self.view} version {task.version}")
        tables = {i.role[len(MASTER_ROLE):]: _cached_rows(i.sha256, functools.partial(store.input_bytes, i))
                  for i in task.inputs if i.role.startswith(MASTER_ROLE)}
        index = AddressIndex.from_subset(contract.loads(store.input_bytes(task.input("addresses"))))
        doc = build(rules, self.view, tables, index)
        content = store.add(contract.encode(doc), "json")
        entries: dict[str, int] = {}
        for c in doc["coverage"]["roles"].values():
            for s, n in c["counts"].items():
                entries[s] = entries.get(s, 0) + n
        facts = {"rows": doc["coverage"]["rows"], "entries": dict(sorted(entries.items())), "gaps": gaps(doc),
                 "unreferenced": len(doc["coverage"]["reverse"]["unreferenced"])}
        rec = contract.artifact(contract.artifact_id(task.id, VIEW_ROLE), content, contract.provenance(task),
                                {"kind": "view", "format": "json", "facts": facts})
        return Output([rec], [])

    def derived(self, task_id: str, result: dict, store, paths) -> list[tuple[str, bytes]]:
        """The view document with the layout paths of its objects (with_paths), at view_path."""
        aid = contract.artifact_id(task_id, VIEW_ROLE)
        rec = next((a for a in result["artifacts"] if a["id"] == aid), None)
        if rec is None:
            raise ValueError(f"result of {task_id} has no artifact {aid}")
        doc = contract.loads(store.read(rec["content"]["sha256"]))
        subject = contract.parse_task_id(task_id, self.name)[1]
        return [(view_path(self.view, subject), contract.encode(with_paths(doc, paths)))]


def stages(rules: dict | None = None) -> list[ViewStage]:
    """One stage per view of `rules` (default: the package rules), in name order."""
    rules = load_rules() if rules is None else rules
    return [ViewStage(n, rules) for n in view_names(rules)]
