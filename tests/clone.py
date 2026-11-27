"""The contract fixture (spec CONF-2): a real Langsys API on an SDK test clone, seeded before the run.

Every contract test reaches the clone through `tests/contract-fixture/forwarder.mjs`, vendored byte
for byte from langsys-js-typescript and pinned by its git tree id. The forwarder passes every
`/api` route to the clone under the test case's own caller address, and answers only the case's
setup, its faults and its crafted reference lists itself. There is no record of what it received:
a test asserts on what the API holds afterwards, read back through the API's own routes.

**The world is seeded before the run.** Each contract test file names a world module in
`tests/contract-seeds/` (`SEED = "<slug>"`), and the seed file beside it (`<slug>.json`, read by the
clone's seeder) is that module rendered. `test_clone_seed_files` fails when the two differ, and
`LANGSYS_WRITE_SEEDS=1 pytest tests/test_clone_seed_files.py` writes the JSON again. A world is a
pool of identical instances and a case takes the next unused one, so no case reads another's
writes, whatever order the cases run in. A module's `RUNS` sets how many runs one reseed serves,
and `LANGSYS_CONTRACT_RUN` (from 0; one number, or `slug=N,...` per file) picks the run, so a
mutant runs on instances no earlier run wrote to. Ids, keys, user tokens and caller addresses are fixed by an instance's place in its file,
inside this SDK's ranges on the clone (langsys `docs/sdk-test-clone.md`): ids `8200xxxx-...`, raw
keys `py-...`, user token ids 170000 to 179999, addresses in 10.82.0.0/16.

`LANGSYS_CONTRACT_TARGET` names the clone's origin and `LANGSYS_CONTRACT_CLONE_ROOT` its langsys
checkout. Without them every contract test errors: a contract test never passes by skipping.
`NODE_EXTRA_CA_CERTS` names the certificate a clone served over HTTPS uses, and
`LANGSYS_CONTRACT_LOG` the file the forwarder logs to.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx

FIXTURE_DIR = Path(__file__).parent / "contract-fixture"
SEEDS_DIR = Path(__file__).parent / "contract-seeds"

#: THE CHECK: the git tree id of the vendored directory. THE PROVENANCE: the ref below.
FORWARDER_TREE = "f72fab59caae1822687ca0a61f6711dcfefc4e6d"
FORWARDER_REF = "langsys-js-typescript v0.7.5 contract-fixture/ (tree f72fab59)"

#: This SDK's ranges on the clone, reserved by the SDK API lane.
ID_PREFIX = "8200"
KEY_PREFIX = "py-"
TOKEN_FIRST = 170000
TOKEN_LAST = 179999
ADDRESS_NET = "10.82"

UNCATEGORIZED = "__uncategorized__"


def tree_id(directory: Path) -> str:
    """Git's tree id for a flat directory of regular files, computed with no git."""
    entries = b""
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).digest()  # noqa: S324
        entries += b"100644 " + path.name.encode() + b"\0" + blob
    return hashlib.sha1(b"tree %d\0" % len(entries) + entries).hexdigest()  # noqa: S324


def contract_run(slug: str) -> int:
    """The run this process takes its worlds from, from 0: `LANGSYS_CONTRACT_RUN` is one number
    for every seed file, or `slug=N,...` per file, so a mutant that touches one file spends a run
    of that file alone (a file it does not name runs 0)."""
    value = os.environ.get("LANGSYS_CONTRACT_RUN", "").strip()
    if not value:
        return 0
    if "=" not in value:
        return int(value)
    runs = dict(part.split("=", 1) for part in value.split(",") if part.strip())
    return int(runs.get(slug, 0))


def wire_locale(locale: str) -> str:
    """A locale as the clone stores it."""
    return locale.replace("_", "-").lower()


def served_category(category: Optional[str]) -> str:
    """The category the catalog serves a seeded item under."""
    return UNCATEGORIZED if category in (None, "") else str(category)


# -- the seeded world --------------------------------------------------------------------------


@dataclass
class World:
    """One instance of a seeded world: what a case needs to reach it and to name itself."""

    name: str
    address: str
    #: A second address of the case's own, to move to when its world drifts.
    moved: str
    projects: dict[str, str]
    keys: dict[str, str]
    key_ids: dict[str, str]
    users: dict[str, dict[str, str]]
    #: What the instance's seed holds, for the check that it landed before the case acts.
    declared: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def project(self, role: str = "p1") -> str:
        return self._lookup(self.projects, "project", role)

    def key(self, role: str) -> str:
        return self._lookup(self.keys, "key", role)

    def key_id(self, role: str) -> str:
        return self._lookup(self.key_ids, "key", role)

    def user(self, role: str = "p1") -> dict[str, str]:
        if role not in self.users:
            raise LookupError(f"{self.name} has no user {role}")
        return self.users[role]

    def names(self) -> dict[str, list[str]]:
        """What names this case to the forwarder: its keys, projects and user tokens."""
        return {
            "keys": list(self.keys.values()),
            "projects": list(self.projects.values()),
            "tokens": [u["token"] for u in self.users.values()],
        }

    def _lookup(self, table: dict[str, str], kind: str, role: str) -> str:
        if role not in table:
            raise LookupError(f"{self.name} has no {kind} {role}")
        return table[role]


class SeedFile:
    """The worlds one contract test file declares, and the seed file the clone reads for them."""

    def __init__(self, slug: str, index: int, runs: int, worlds: dict[str, dict[str, Any]]) -> None:
        if not isinstance(index, int) or not 1 <= index <= 20:
            raise ValueError(f"seed file index {index} is outside 1 to 20")
        self.slug = slug
        self.index = index
        self.runs = runs
        self._pools: dict[str, list[World]] = {}
        self._counts: dict[str, int] = {}
        self._taken: dict[str, int] = {}

        projects: list[dict[str, Any]] = []
        keys: list[dict[str, Any]] = []
        instance = project_serial = key_serial = 0

        for name, world in worlds.items():
            count = int(world.get("count", 1))
            self._counts[name] = count
            self._pools[name] = []
            for i in range(count * runs):
                instance += 1
                if instance > 500:
                    raise ValueError(f"{slug}: more than 500 world instances in one file")
                label = f"{name}#{i + 1}"
                # Two /24s per file, 250 addresses in each, and the moved addresses 100 above.
                block = 2 * index + (instance - 1) // 250
                host = (instance - 1) % 250 + 1
                address = f"{ADDRESS_NET}.{block}.{host}"
                moved = f"{ADDRESS_NET}.{100 + block}.{host}"
                net = f"{ADDRESS_NET}.{block}.0/24"

                ids: dict[str, str] = {}
                users: dict[str, dict[str, str]] = {}
                declared: dict[str, list[dict[str, Any]]] = {"projects": [], "keys": []}

                for role, project in world["projects"].items():
                    project_serial += 1
                    # 500 token ids per file.
                    if project_serial > 499:
                        raise ValueError(f"{slug}: more than 499 projects in one file")
                    pid = f"{ID_PREFIX}{index:02x}00-{project_serial:04x}-4000-8000-000000000000"
                    secret = hashlib.sha256(f"py:{slug}:{project_serial}".encode()).hexdigest()[:40]
                    user = {
                        "token": f"{TOKEN_FIRST + (index - 1) * 500 + project_serial}|{secret}",
                        "device_id": f"py-{slug}-{project_serial}",
                    }
                    ids[role] = pid
                    users[role] = user
                    declared["projects"].append({"role": role, "id": pid, "seed": project})
                    projects.append({"id": pid, "title": f"py {slug} {label} {role}", **project,
                                     "user": user})

                raw: dict[str, str] = {}
                key_ids: dict[str, str] = {}
                for role, key in world.get("keys", {}).items():
                    key_serial += 1
                    kid = f"{ID_PREFIX}{index:02x}01-{key_serial:04x}-4000-8000-000000000000"
                    value = f"{KEY_PREFIX}{slug}-{instance}-{role}"
                    owner = key["project"]
                    if owner is not None and owner not in ids:
                        raise ValueError(f"{slug} {name}: key {role} names no project {owner}")
                    allowlist: Optional[list[str]] = None
                    if "ip_allowlist" in key:
                        named = {"@self": address, "@moved": moved, "@net": net}
                        allowlist = []
                        for entry in key["ip_allowlist"]:
                            if entry.startswith("@") and entry not in named:
                                raise ValueError(f"{slug} {name}: key {role} names no address {entry}")
                            allowlist.append(named.get(entry, entry))
                    raw[role] = value
                    key_ids[role] = kid
                    # The clone stores an empty allow-list as none.
                    declared["keys"].append({
                        "role": role, "id": kid,
                        "project": None if owner is None else ids[owner],
                        "type": key["type"], "ip_allowlist": allowlist or None,
                        "write_grant_secret": "write_grant_secret" in key,
                    })
                    rest = {k: v for k, v in key.items() if k not in ("project", "ip_allowlist")}
                    entry_doc: dict[str, Any] = {"id": kid, "key": value, **rest,
                                                 "project": None if owner is None else ids[owner]}
                    if allowlist is not None:
                        entry_doc["ip_allowlist"] = allowlist
                    keys.append(entry_doc)

                unknown = world.get("unknown", {})
                for role in unknown.get("keys", []):
                    raw[role] = f"{KEY_PREFIX}{slug}-{instance}-{role}"
                for n, role in enumerate(unknown.get("projects", [])):
                    ids[role] = f"{ID_PREFIX}{index:02x}02-{instance:04x}-4000-8000-{n + 1:012x}"

                self._pools[name].append(
                    World(label, address, moved, ids, raw, key_ids, users, declared)
                )

        self.document: dict[str, Any] = {"projects": projects, "keys": keys}

    @classmethod
    def load(cls, slug: str) -> SeedFile:
        """The seed file a module in `tests/contract-seeds/` declares."""
        path = SEEDS_DIR / f"{slug}.py"
        spec = importlib.util.spec_from_file_location(f"contract_seed_{slug}", path)
        if spec is None or spec.loader is None:
            raise FileNotFoundError(path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cls(slug, module.INDEX, getattr(module, "RUNS", 1), module.WORLDS)

    def take(self, name: str) -> World:
        """The next unused instance of a world in this run."""
        if name not in self._pools:
            raise LookupError(f"{self.slug} declares no world {name}")
        run = contract_run(self.slug)
        if not 0 <= run < self.runs:
            raise LookupError(
                f"tests/contract-seeds/{self.slug}.json serves {self.runs} run(s) a reseed and "
                f"this is run {run + 1}: raise its RUNS, write the JSON again and reseed the clone"
            )
        taken = self._taken.get(name, 0)
        if taken >= self._counts[name]:
            raise LookupError(
                f"tests/contract-seeds/{self.slug}.json holds {self._counts[name]} {name!r} worlds "
                f"a run and this is case {taken + 1} to take one: raise its count in "
                f"tests/contract-seeds/{self.slug}.py, write the JSON again and reseed the clone"
            )
        self._taken[name] = taken + 1
        return self._pools[name][run * self._counts[name] + taken]

    def json(self) -> str:
        """The seed file as the clone's seeder reads it."""
        return json.dumps(self.document, indent=4, ensure_ascii=False) + "\n"


# -- the forwarder and a case's view of the clone ----------------------------------------------


@dataclass
class Answer:
    status: int
    body: Any
    raw: str


class Clone:
    """One running forwarder for one test file, and the reads a case makes through it."""

    def __init__(self, seed: str) -> None:
        self.seeds = SeedFile.load(seed)
        target = os.environ.get("LANGSYS_CONTRACT_TARGET")
        clone_root = os.environ.get("LANGSYS_CONTRACT_CLONE_ROOT")
        if not target or not clone_root:
            raise RuntimeError(
                "The contract tests run against an SDK test clone, a real Langsys API (spec "
                "CONF-2), and none is named. Set LANGSYS_CONTRACT_TARGET to its origin, "
                "LANGSYS_CONTRACT_CLONE_ROOT to its langsys checkout, and NODE_EXTRA_CA_CERTS to "
                "the certificate it serves with (README, 'Contract tests')."
            )
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("the contract fixture needs Node 18 or later on PATH")
        command = [node, str(FIXTURE_DIR / "forwarder.mjs"), "--target", target,
                   "--clone-root", os.path.expanduser(clone_root)]
        log = os.environ.get("LANGSYS_CONTRACT_LOG")
        if log:
            command += ["--log", log]
        self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
        ready: dict[str, Any] = {}

        def read_ready() -> None:
            assert self._proc.stdout is not None
            line = self._proc.stdout.readline()
            if line:
                ready.update(json.loads(line))

        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        reader.join(20)
        if not ready.get("ready"):
            self.close()
            raise RuntimeError(f"the contract fixture did not report ready within 20s: {ready!r}")
        self.base_url: str = ready["base_url"]
        self.fixture_url: str = ready["fixture_url"]
        self._http = httpx.Client(timeout=60)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if hasattr(self, "_http"):
            self._http.close()

    # -- cases ------------------------------------------------------------------------------

    def world(self, name: str, *, caller: Optional[str] = None,
              faults: Optional[list[dict[str, Any]]] = None,
              reference: Optional[dict[str, Any]] = None) -> World:
        """Take the next unused instance of a world, name it to the forwarder from its own
        address, and confirm its seed landed before anything spends its faults or lists."""
        world = self.seeds.take(name)
        address = caller or world.address
        self._fixture("/setup", {**world.names(), "caller": address, "faults": [],
                                 "reference": None})
        self._confirm_seed(world)
        if faults or reference:
            self._fixture("/setup", {**world.names(), "caller": address, "faults": faults or [],
                                     "reference": self._with_format(world, reference)})
        return world

    def update(self, world: World, **setup: Any) -> None:
        """Change a case's setup, such as a move to another address; what it leaves out stays."""
        if "reference" in setup:
            setup["reference"] = self._with_format(world, setup["reference"])
        self._fixture("/setup", {**world.names(), **setup})

    # -- reads, as a client or the project's owner makes them -------------------------------

    def catalog(self, key: str, project: str, locale: str) -> dict[str, Any]:
        """What the catalog serves a project at a locale, as the API sends it."""
        data = self._read("/translations?" + urlencode({"project_id": project, "locale": locale}),
                          {"X-Authorization": key, "X-Langsys-Capabilities": "icu"})
        return data if isinstance(data, dict) else {}

    def held(self, key: str, project: str, locale: str) -> dict[str, Any]:
        """The catalog by category: phrases by text, blocks by custom id with their phrases,
        sorted throughout, since the API promises no order."""
        phrases: dict[str, list[str]] = {}
        blocks: dict[str, dict[str, list[str]]] = {}
        for category, entries in self.catalog(key, project, locale).items():
            for name, value in (entries or {}).items():
                if isinstance(value, dict):
                    blocks.setdefault(category, {})[str(name)] = sorted(map(str, value))
                else:
                    phrases.setdefault(category, []).append(str(name))
        return {
            "phrases": {c: sorted(p) for c, p in sorted(phrases.items())},
            "blocks": {c: dict(sorted(b.items())) for c, b in sorted(blocks.items())},
        }

    @staticmethod
    def any_phrase(held: dict[str, Any]) -> list[str]:
        """Every phrase text a catalog holds, under any category: what an absence is checked
        against."""
        return [p for phrases in held["phrases"].values() for p in phrases]

    @staticmethod
    def any_block(held: dict[str, Any]) -> list[str]:
        return [b for blocks in held["blocks"].values() for b in blocks]

    def stored_blocks(self, world: World, key: str, locale: str,
                      role: str = "p1") -> list[dict[str, Any]]:
        """The blocks the world's project holds, each with the content it stored, as its owner
        reads them, sorted by custom id."""
        rows = self._read("/translations/data?" + urlencode(
            {"project_id": world.project(role), "locale": locale}), {"X-Authorization": key})
        blocks = []
        for items in (rows or {}).values() if isinstance(rows, dict) else []:
            for row in items if isinstance(items, list) else []:
                if not isinstance(row, dict) or row.get("type") != "content_block":
                    continue
                item = self.as_user(world, "/translatable-items/" + quote(str(row["id"]), safe=""),
                                    role=role)
                assert item.status == 200, f"the owner's read of block {row.get('custom_id')} " \
                                           f"answered {item.status}: {item.raw}"
                blocks.append({
                    "custom_id": str(row.get("custom_id") or ""),
                    "category": row.get("category"),
                    "content": ((item.body or {}).get("data") or {}).get("content"),
                })
        return sorted(blocks, key=lambda b: b["custom_id"])

    def api(self, path: str, *, key: Optional[str] = None, method: Optional[str] = None,
            body: Any = None, headers: Optional[dict[str, str]] = None) -> Answer:
        """A call to the API under an API key, made by the test itself."""
        sent = {**({"X-Authorization": key} if key else {}), **(headers or {})}
        return self._call(path, method, sent, body)

    def as_user(self, world: World, path: str, *, method: Optional[str] = None, body: Any = None,
                role: str = "p1") -> Answer:
        """A call to the API's user routes as the seeded user who owns the world's project."""
        user = world.user(role)
        return self._call(path, method, {
            "Authorization": f"Bearer {user['token']}", "x-device-id": user["device_id"],
            "X-Langsys-Capabilities": "icu",
        }, body)

    def edit_key(self, world: World, role: str, fields: dict[str, Any]) -> None:
        """Change one of the world's keys as its project's owner would (`PATCH /api-keys/{id}`)."""
        project_role = "p1"
        for key in world.declared["keys"]:
            if key["role"] == role:
                for project in world.declared["projects"]:
                    if project["id"] == key["project"]:
                        project_role = project["role"]
        answer = self.as_user(world, "/api-keys/" + quote(world.key_id(role), safe=""),
                              method="PATCH", body=fields, role=project_role)
        assert answer.status == 200, f"editing key {role} answered {answer.status}: {answer.raw}"

    def hints(self, world: World) -> list[dict[str, Any]]:
        """The hints the clone stored for the world's projects, in the order it accepted them."""
        query = "&".join("project=" + quote(p, safe="") for p in world.names()["projects"])
        answer = self._http.get(f"{self.fixture_url}/state?{query}")
        assert answer.status_code == 200, f"fixture /state answered {answer.status_code}: {answer.text}"
        return list(answer.json().get("hints", []))

    # -- the seed check ---------------------------------------------------------------------

    def _confirm_seed(self, world: World) -> None:
        """Confirm through the API that a world's seed landed, and that no run has written to it
        since, before its case acts: a seed that did not land fails as one instead of passing an
        absence vacuously. Every read is the project's owner's, from the case's own address."""
        gaps: list[str] = []
        for project in world.declared["projects"]:
            gaps += self._project_gaps(world, project)
        stored = self.hints(world)
        if stored:
            gaps.append(f"the clone holds {len(stored)} hint(s) for its projects, which its seed "
                        "does not: a run has used this world, so reseed the clone")
        if gaps:
            raise AssertionError(f"seed not landed: {world.name} " + "; ".join(gaps))

    def _project_gaps(self, world: World, project: dict[str, Any]) -> list[str]:
        role, seed = project["role"], project["seed"]
        base = wire_locale(seed.get("base_locale", "en-us"))
        targets = [wire_locale(t) for t in seed.get("target_locales", [])]
        gaps: list[str] = []

        def owner(path: str) -> Answer:
            return self.as_user(world, path, role=role)

        def query(locale: str) -> str:
            return urlencode({"project_id": project["id"], "locale": locale})

        listed = owner("/api-keys?" + urlencode({"project_id": project["id"]}))
        if listed.status != 200:
            gaps.append(f"{role}'s key listing answered {listed.status}")
        else:
            rows = {r["id"]: r for r in ((listed.body or {}).get("data") or [])}
            for key in world.declared["keys"]:
                if key["project"] != project["id"]:
                    continue
                row = rows.get(key["id"])
                if row is None:
                    gaps.append(f"key {key['role']} ({key['id']}) is not listed")
                    continue
                held = {"type": row.get("type"), "ip_allowlist": row.get("ip_allowlist"),
                        "active": row.get("active"),
                        "write_grant_secret": row.get("write_grant_secret_set_at") is not None}
                seeded = {"type": key["type"], "ip_allowlist": key["ip_allowlist"],
                          "active": True, "write_grant_secret": key["write_grant_secret"]}
                if held != seeded:
                    gaps.append(f"key {key['role']} is {held}, seeded {seeded}")

        suspended = bool(seed.get("subscription_suspended"))
        has_items = bool(seed.get("phrases") or seed.get("blocks"))
        locales = [base] + (targets if not suspended and has_items else [])
        for locale in locales:
            answer = owner("/translations?" + query(locale))
            if suspended:
                code = ((answer.body or {}).get("error") or {}).get("code") \
                    if isinstance(answer.body, dict) else None
                if not (answer.status == 402 and code == "subscription_suspended"):
                    gaps.append(f"{role} is not suspended: its catalog answered {answer.status}")
                continue
            if answer.status != 200:
                gaps.append(f"{role}'s catalog at {locale} answered {answer.status}")
                continue
            data = (answer.body or {}).get("data") or {}
            gaps += [f"{role}: {g}" for g in _catalog_gaps(seed, locale, base, data)]
            if locale == base:
                extras = _catalog_extras(seed, data)
                if extras:
                    gaps.append(f"{role} holds {', '.join(extras)}, which its seed does not: a run "
                                "has used this world, so reseed the clone")
        if suspended:
            return gaps

        read = owner("/projects/" + quote(project["id"], safe=""))
        if read.status != 200:
            gaps.append(f"{role}'s project read answered {read.status}")
        else:
            data = (read.body or {}).get("data") or {}
            settings = (data.get("settings") or {}).get("locales") or {}
            held_settings = {
                "website_url": data.get("website_url"),
                "base_locale": wire_locale(settings.get("base_locale") or ""),
                "target_locales": sorted(wire_locale(t) for t in settings.get("target_locales") or []),
                "discovery_base_locale_only": settings.get("discovery_base_locale_only", False),
                "machine_translate_new_phrases": ((data.get("settings") or {}).get("ai_translator")
                                                  or {}).get("machine_translate_new_phrases"),
            }
            seeded_settings = {
                "website_url": seed.get("website_url"),
                "base_locale": base,
                "target_locales": sorted(targets),
                "discovery_base_locale_only": seed.get("discovery_base_locale_only", False),
                "machine_translate_new_phrases": seed.get("machine_translate_new_phrases", False),
            }
            if held_settings != seeded_settings:
                gaps.append(f"{role}'s settings are {held_settings}, seeded {seeded_settings}")

        base_translated = [p for p in seed.get("phrases", [])
                           if p.get("translatable") is not False and _seeded_at(p, base) is not None]
        if seed.get("blocks") or base_translated:
            rows = owner("/translations/data?" + query(base))
            if rows.status != 200:
                return gaps + [f"{role}'s catalog rows answered {rows.status}"]
            block_ids: dict[str, str] = {}
            phrase_ids: dict[tuple[str, str], str] = {}
            for category, items in ((rows.body or {}).get("data") or {}).items():
                for row in items if isinstance(items, list) else []:
                    if row.get("type") == "content_block" and row.get("custom_id"):
                        block_ids[row["custom_id"]] = row["id"]
                    if row.get("type") == "phrase":
                        phrase_ids[(str(category), row["phrase"])] = row["id"]

            def item_gap(item_id: Optional[str], what: str, field_name: str, want: Any) -> list[str]:
                if item_id is None:
                    return [f"{role}: {what} has no row"]
                item = owner("/translatable-items/" + quote(str(item_id), safe=""))
                if item.status != 200:
                    return [f"{role}: {what}'s read answered {item.status}"]
                got = ((item.body or {}).get("data") or {}).get(field_name)
                return [] if got == want else [f"{role}: {what} holds {got!r}, seeded {want!r}"]

            for block in seed.get("blocks", []):
                gaps += item_gap(block_ids.get(block["custom_id"]), f"block {block['custom_id']}",
                                 "content", block.get("content"))
            for phrase in base_translated:
                category = served_category(phrase.get("category"))
                gaps += item_gap(phrase_ids.get((category, phrase["phrase"])),
                                 f"{phrase['phrase']!r} in {category}", "translation",
                                 _seeded_at(phrase, base))
        return gaps

    def _with_format(self, world: World, reference: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """A case's crafted lists take the locale-code format its world's seed gives its
        organisation, so a setup never names one."""
        if not reference:
            return reference
        if "locale_code_format" in reference:
            raise ValueError(f"{world.name}: a setup does not name a locale_code_format")
        formats = sorted({p["seed"].get("locale_code_format", "lowercase-hyphen")
                          for p in world.declared["projects"]})
        if len(formats) > 1:
            raise ValueError(f"{world.name}: its projects write locale codes as {formats}")
        return {**reference, "locale_code_format": formats[0] if formats else "lowercase-hyphen"}

    # -- HTTP -------------------------------------------------------------------------------

    def _read(self, path: str, headers: dict[str, str]) -> Any:
        answer = self._send("GET", self.base_url + path, {"Accept": "application/json", **headers})
        assert 200 <= answer.status < 300 and isinstance(answer.body, dict), \
            f"{path} answered {answer.status}: {answer.raw}"
        return answer.body.get("data")

    def _call(self, path: str, method: Optional[str], headers: dict[str, str], body: Any) -> Answer:
        verb = method or ("POST" if body is not None else "GET")
        return self._send(verb, self.base_url + path, {
            "Accept": "application/json", "Content-Type": "application/json", **headers,
        }, None if body is None else json.dumps(body))

    def _fixture(self, path: str, body: dict[str, Any]) -> None:
        answer = self._send("POST", self.fixture_url + path,
                            {"Content-Type": "application/json"}, json.dumps(body))
        assert 200 <= answer.status < 300, f"fixture {path} answered {answer.status}: {answer.raw}"

    def _send(self, method: str, url: str, headers: dict[str, str],
              content: Optional[str] = None) -> Answer:
        response = self._http.request(method, url, headers=headers, content=content)
        try:
            body = response.json()
        except ValueError:
            body = None
        return Answer(response.status_code, body, response.text)


def _seeded_at(phrase: dict[str, Any], locale: str) -> Optional[str]:
    for given, text in (phrase.get("translations") or {}).items():
        if wire_locale(given) == locale:
            return str(text)
    return None


def _catalog_gaps(seed: dict[str, Any], locale: str, base: str, data: dict[str, Any]) -> list[str]:
    """Where a catalog read at `locale` differs from the phrases and blocks a seed declares."""
    gaps: list[str] = []

    def check(served: Any, phrase: dict[str, Any], where: str) -> None:
        if not isinstance(served, dict) or phrase["phrase"] not in served:
            gaps.append(f"{phrase['phrase']!r} {where} is not served at {locale}")
            return
        # An untranslatable phrase is served as null; at a target locale a seeded translation as
        # given; otherwise the API decides.
        if phrase.get("translatable") is False:
            seeded = None
        elif locale == base or _seeded_at(phrase, locale) is None:
            return
        else:
            seeded = _seeded_at(phrase, locale)
        if served[phrase["phrase"]] != seeded:
            gaps.append(f"{phrase['phrase']!r} {where} is served at {locale} as "
                        f"{served[phrase['phrase']]!r}, seeded {seeded!r}")

    for phrase in seed.get("phrases", []):
        category = served_category(phrase.get("category"))
        check(data.get(category), phrase, f"in {category}")
    for block in seed.get("blocks", []):
        category = served_category(block.get("category"))
        served = (data.get(category) or {}).get(block["custom_id"])
        if not isinstance(served, dict):
            gaps.append(f"block {block['custom_id']} is not served at {locale}")
            continue
        # A block is served whole: outside the base locale, unless every translatable phrase in
        # it is translated there, each reads null.
        phrases = block.get("phrases", [])
        whole = locale == base or not [
            p for p in phrases if p.get("translatable") is not False and _seeded_at(p, locale) is None
        ]
        for phrase in phrases:
            check(served, phrase if whole else {"phrase": phrase["phrase"], "translatable": False},
                  f"in block {block['custom_id']}")
    return gaps


def _catalog_extras(seed: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """What a catalog read serves that a seed does not declare: a world a run has already
    written to holds what that run registered, so its absences prove nothing until a reseed."""
    phrases = {(served_category(p.get("category")), p["phrase"]) for p in seed.get("phrases", [])}
    blocks = {(served_category(b.get("category")), b["custom_id"]): [p["phrase"] for p in b.get("phrases", [])]
              for b in seed.get("blocks", [])}
    extras = []
    for category, entries in data.items():
        for name, value in (entries or {}).items():
            key = (str(category), str(name))
            if not isinstance(value, dict):
                if key not in phrases:
                    extras.append(f"{name!r} in {category}")
                continue
            if key not in blocks:
                extras.append(f"block {name} in {category}")
                continue
            extras += [f"{p!r} in block {name}" for p in value if str(p) not in blocks[key]]
    return extras


# -- the SDK under test, pointed at the clone --------------------------------------------------


def sdk_client(clone: Clone, world: World, role: str = "write", **kw: Any) -> Any:
    """A real client on the world's project, through the forwarder. Every call goes over HTTP."""
    from langsys import LangsysClient
    from langsys.cache import MemoryCache

    options: dict[str, Any] = {"cache": MemoryCache(), "base_locale": "en-us", "debounce": 0,
                               "auto_flush": False, **kw}
    return LangsysClient(world.key(role), world.project(), api_url=clone.base_url, **options)


def held_phrases(clone: Clone, world: World, role: str = "write",
                 project_role: str = "p1") -> list[tuple[Optional[str], str]]:
    """What the API holds, as (category, phrase): the catalog at the base locale serves every
    registered phrase. An uncategorised phrase reads as category None."""
    held = clone.held(world.key(role), world.project(project_role), "en-us")
    return sorted(
        (None if category == UNCATEGORIZED else category, phrase)
        for category, texts in held["phrases"].items() for phrase in texts
    )


def held_blocks(clone: Clone, world: World, role: str = "write") -> list[list[str]]:
    """The phrases of every block the API holds, each block's sorted."""
    held = clone.held(world.key(role), world.project(), "en-us")
    return [texts for by_id in held["blocks"].values() for texts in by_id.values()]
