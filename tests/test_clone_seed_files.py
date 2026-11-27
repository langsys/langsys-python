"""The seed files the SDK test clone reads, and the forwarder every contract test reaches it through.

`tests/contract-seeds/*.json` are their world modules rendered, and stay inside this SDK's ranges on
the clone, since one file the clone's seeder refuses stops the reseed for every SDK sharing it.
`LANGSYS_WRITE_SEEDS=1` writes each file again from its module. Needs no clone: it reads only this
repository.
"""

from __future__ import annotations

import ipaddress
import os
import re

from clone import (
    FIXTURE_DIR,
    FORWARDER_REF,
    FORWARDER_TREE,
    ID_PREFIX,
    KEY_PREFIX,
    SEEDS_DIR,
    TOKEN_FIRST,
    TOKEN_LAST,
    SeedFile,
    tree_id,
)

ID = re.compile(rf"^{ID_PREFIX}[0-9a-f]{{4}}-[0-9a-f]{{4}}-4000-8000-[0-9a-f]{{12}}$")
OWN_NET = ipaddress.ip_network("10.82.0.0/16")


def files() -> list[SeedFile]:
    return [SeedFile.load(path.stem) for path in sorted(SEEDS_DIR.glob("*.py"))]


def test_the_vendored_forwarder_is_the_pinned_tree():
    """THE CHECK: git's tree id for the directory, computed here with no git and no network."""
    assert tree_id(FIXTURE_DIR) == FORWARDER_TREE, (
        f"tests/contract-fixture/ is no longer {FORWARDER_REF}: re-vendor it deliberately, never edit it"
    )


def test_each_seed_file_is_its_module_rendered():
    found = files()
    assert found, "control: no world modules found"
    for seed in found:
        path = SEEDS_DIR / f"{seed.slug}.json"
        if os.environ.get("LANGSYS_WRITE_SEEDS"):
            path.write_text(seed.json(), encoding="utf-8")
        assert path.exists(), f"{path.name} is missing: write it with LANGSYS_WRITE_SEEDS=1"
        assert path.read_text(encoding="utf-8") == seed.json(), \
            f"{path.name} differs from its module: write it with LANGSYS_WRITE_SEEDS=1"


def test_every_seed_file_has_a_module():
    for path in SEEDS_DIR.glob("*.json"):
        assert path.with_suffix(".py").exists(), f"{path.name} has no module, so nothing renders it"


def test_every_id_is_unique_and_inside_this_sdks_ranges():
    """Ids, raw keys and token ids are unique across every SDK's folders, and each SDK keeps to its
    own ranges (langsys docs/sdk-test-clone.md): Python's are 82."""
    seen: dict[str, dict[str, str]] = {k: {} for k in ("index", "id", "key", "token id",
                                                        "token secret", "device")}

    def claim(kind: str, value: str, where: str) -> None:
        assert value not in seen[kind], f"{where} repeats the {kind} {value}, also in {seen[kind][value]}"
        seen[kind][value] = where

    for seed in files():
        claim("index", str(seed.index), seed.slug)
        for project in seed.document["projects"]:
            where = f"{seed.slug} project {project['id']}"
            assert ID.match(project["id"]), where
            claim("id", project["id"], where)
            token_id, secret = project["user"]["token"].split("|", 1)
            assert TOKEN_FIRST <= int(token_id) <= TOKEN_LAST, where
            assert "|" not in secret, where
            claim("token id", token_id, where)
            claim("token secret", secret, where)
            claim("device", project["user"]["device_id"], where)
        for key in seed.document["keys"]:
            where = f"{seed.slug} key {key['id']}"
            assert ID.match(key["id"]), where
            assert key["key"].startswith(KEY_PREFIX), where
            claim("id", key["id"], where)
            claim("key", key["key"], where)
            for entry in key.get("ip_allowlist") or []:
                assert ipaddress.ip_network(entry, strict=False).subnet_of(OWN_NET), \
                    f"{where} allow-lists {entry}, outside 10.82.0.0/16"


def test_every_world_address_is_inside_this_sdks_range():
    for seed in files():
        for pool in seed._pools.values():
            for world in pool:
                for address in (world.address, world.moved):
                    assert ipaddress.ip_address(address) in OWN_NET, f"{seed.slug} {world.name}"
