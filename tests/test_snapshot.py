"""SNAP-1 - export is a client-side filter of the catalog into the fleet's one format; SNAP-3 - a
snapshot is a cache; SNAP-2 - the loader a binding seeds from at startup.

The export runs against the SDK test clone (spec CONF-2): the snapshot must carry exactly what the
API serves for the chosen categories. The loader's cases that turn on the live catalog run there
too, with a snapshot built for the world's own project; the rest are in-process.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional
from urllib.parse import urlencode

import pytest
from clone import Clone, World

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.snapshot import FORMAT, Snapshot, SnapshotError, _canonical, _checksum

SEED = "snapshot"
OFFLINE = "http://127.0.0.1:9/api"  # nothing is listening

PHRASES = [
    {"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa", "es-es": "Caja"}},
    {"category": "UI", "phrase": "Untranslated"},
    {"category": "Errors", "phrase": "The name is required.", "translations": {"it-it": "Il nome è obbligatorio."}},
    {"category": "Marketing", "phrase": "Buy now", "translations": {"it-it": "Compra ora"}},
]


def client(clone: Clone, world: World) -> LangsysClient:
    return LangsysClient(world.key("write"), world.project(), api_url=clone.base_url,
                         cache=MemoryCache(), debounce=0, auto_flush=False)


def served(clone: Clone, world: World, locale: str) -> dict[str, Any]:
    answer = clone.api("/translations?" + urlencode(
        {"project_id": world.project(), "locale": locale, "format": "flat"}), key=world.key("write"))
    assert answer.status == 200, answer.raw
    return dict(answer.body["data"] or {})


def crafted(project: str, catalog: dict[str, Any], *, base: str = "en-us",
            categories: Optional[list[str]] = None) -> Snapshot:
    """A snapshot as an export writes one, for a project of the test's choosing: what a binding
    loads at boot, built without a server, since the loader never reads one."""
    return Snapshot({
        "project_id": project, "generated_at": "2026-11-26T00:00:00Z", "base_locale": base,
        "locales": sorted(catalog),
        "categories": categories or sorted({c for by in catalog.values() for c in by}),
        "catalog": catalog,
    })


LOCAL_CATALOG = {"it-it": {"UI": {"Checkout": "Cassa", "Untranslated": None}},
                 "es-es": {"UI": {"Checkout": "Caja", "Untranslated": None}}}


@pytest.mark.contract
def test_SNAP1_the_snapshot_carries_exactly_what_the_api_serves_for_its_categories(clone):
    world = clone.world("catalog")
    snapshot = Snapshot.export(client(clone, world), ["it-IT", "es-es"], ["UI", "Errors"])
    for locale in ("it-it", "es-es"):
        api = served(clone, world, locale)
        assert snapshot.catalog(locale) == {c: api[c] for c in ("UI", "Errors") if c in api}
        assert "Marketing" in api, "control: the API serves the category the export leaves out"
    assert "Marketing" not in snapshot.catalog("it-it")
    assert snapshot.catalog("it-it")["UI"]["Checkout"] == "Cassa", "control: the seed landed"
    assert snapshot.locales == ["es-es", "it-it"], "locales are sorted ascending"
    assert snapshot.project_id == world.project() and snapshot.base_locale == "en-us"


@pytest.mark.contract
def test_SNAP1_a_catalog_that_cannot_be_read_fails_the_export(clone):
    world = clone.world("catalog", faults=[{"method": "GET", "path": "/translations", "status": 500}])
    with pytest.raises(SnapshotError, match="it-it"):
        Snapshot.export(client(clone, world), ["it-it"], ["UI"])


def test_SNAP1_an_export_names_a_locale_and_a_category():
    c = LangsysClient("k", "p", api_url=OFFLINE, cache=MemoryCache(), debounce=0, auto_flush=False)
    with pytest.raises(SnapshotError):
        Snapshot.export(c, [], ["UI"])


@pytest.mark.contract
def test_SNAP3_a_snapshot_round_trips_through_its_file(clone, tmp_path):
    world = clone.world("catalog")
    path = Snapshot.export(client(clone, world), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    loaded = Snapshot.load(path)
    assert loaded.catalog("it-it")["UI"]["Checkout"] == "Cassa"
    assert json.loads(path.read_text())["format"] == FORMAT


def test_SNAP3_an_edited_snapshot_is_refused(tmp_path):
    """Hand-editing a cached artifact is a defect, and a loader that served the edit would make the
    snapshot the source of truth. The refresh is a new export."""
    path = crafted("p", LOCAL_CATALOG).write_to(tmp_path / "s.json")
    assert Snapshot.load(path).catalog("it-it")["UI"]["Checkout"] == "Cassa", "control: it loads"
    document = json.loads(path.read_text())
    document["catalog"]["it-it"]["UI"]["Checkout"] = "Edited by hand"
    path.write_text(json.dumps(document))
    with pytest.raises(SnapshotError, match="export it again"):
        Snapshot.load(path)


@pytest.mark.parametrize("text", ["not json", '{"format": "other"}',
                                  '{"format": "langsys-catalog-snapshot", "version": 2}'],
                         ids=["garbage", "foreign", "future-version"])
def test_SNAP3_anything_that_is_not_a_current_snapshot_is_refused(text):
    with pytest.raises(SnapshotError):
        Snapshot.load(text)


def test_SNAP1_the_canonical_serialisation_is_the_specs():
    """Keys in code point order (U+E000 before U+1F600, `"10"` before `"404"` as strings), no
    whitespace, `{}` for an empty map, CID-1 escaping: a C0 control as lowercase `\\u00xx`,
    U+2028 and `/` raw. `snapshot-vectors.json` will pin the fleet's bytes; until it is authored,
    these are the spec's own cases."""
    payload = {
        "project_id": "p", "generated_at": "2026-09-24T00:00:00Z", "base_locale": "en-us",
        "locales": ["es-es", "it-it"], "categories": ["UI"],
        "catalog": {"it-it": {"UI": {"\U0001F600": "x", "\ue000": "y", "404": None, "10": "a/b\u2028\x1c",
                                     "blk": {"A": None}}}, "es-es": {"UI": {}}},
    }
    expected = (
        '{"base_locale":"en-us","catalog":{"es-es":{"UI":{}},"it-it":{"UI":{"10":"a/b\u2028\\u001c",'
        '"404":null,"blk":{"A":null},"\ue000":"y","\U0001F600":"x"}}},"categories":["UI"],'
        '"generated_at":"2026-09-24T00:00:00Z","locales":["es-es","it-it"],"project_id":"p"}'
    )
    assert _canonical(payload) == expected
    assert _checksum(payload) == "sha256:" + hashlib.sha256(expected.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("member", "value", "reason"), [
    ("format", "other", "format"), ("version", 2, "version"), ("base_locale", None, "base_locale"),
    ("checksum", "sha256:00", "checksum"),
], ids=["format", "version", "missing-member", "checksum"])
def test_SNAP1_a_loader_refuses_by_name(member, value, reason):
    document = json.loads(crafted("p", LOCAL_CATALOG).to_json())
    if value is None:
        del document[member]
    else:
        document[member] = value
    with pytest.raises(SnapshotError, match=reason):
        Snapshot.load(json.dumps(document))


def test_SNAP1_the_file_is_any_json_encoding_of_the_document():
    """A loader recomputes the checksum from the parsed document, so re-encoding the file (key
    order, whitespace, escapes) does not break it."""
    document = json.loads(crafted("p", LOCAL_CATALOG).to_json())

    def reversed_maps(node):
        if isinstance(node, dict):
            return {k: reversed_maps(v) for k, v in reversed(list(node.items()))}
        return node

    reencoded = json.dumps(reversed_maps(document), ensure_ascii=True, indent=1)
    assert Snapshot.load(reencoded).catalog("it-it")["UI"]["Checkout"] == "Cassa"


@pytest.mark.contract
def test_SNAP_the_command_line_writes_a_loadable_snapshot(clone, tmp_path, monkeypatch):
    from langsys.snapshot import main

    world = clone.world("catalog")
    monkeypatch.setenv("LANGSYS_API_URL", clone.base_url)
    monkeypatch.setenv("LANGSYS_API_KEY", world.key("write"))
    monkeypatch.setenv("LANGSYS_PROJECT_ID", world.project())
    out = tmp_path / "cli.json"
    assert main(["--locale", "it-it", "--category", "UI", "--out", str(out)]) == 0
    assert Snapshot.load(out).catalog("it-it")["UI"]["Checkout"] == "Cassa"


# -- SNAP-2: the loader the bindings seed from at startup -------------------------------------------


def _seeded(tmp_path, project: str = "p", api_url: str = OFFLINE) -> LangsysClient:
    path = crafted(project, {"it-it": {"UI": {"Checkout": "Cassa", "Untranslated": None}}}).write_to(
        tmp_path / "s.json")
    c = LangsysClient("k", project, api_url=api_url, cache=MemoryCache(), base_locale="en-us",
                      debounce=0, auto_flush=False)
    c.load_snapshot(path)
    return c


def test_SNAP2_a_seeded_phrase_renders_with_no_fetch(tmp_path):
    c = _seeded(tmp_path)
    assert c.translate("Checkout", category="UI", locale="it-it") == "Cassa"
    assert c.translate("Untranslated", category="UI", locale="it-it") == "Untranslated"
    assert not c.has_pending, "a phrase the snapshot holds is known"


@pytest.mark.contract
def test_SNAP2_a_phrase_the_snapshot_lacks_falls_back_to_the_fetch(clone, tmp_path):
    world = clone.world("catalog")
    path = crafted(world.project(), {"it-it": {"UI": {"Checkout": "Cassa"}}}).write_to(
        tmp_path / "s.json")
    c = client(clone, world)
    c.load_snapshot(path)
    assert c.translate("The name is required.", category="Errors", locale="it-it") == (
        "Il nome è obbligatorio."
    ), "not in the snapshot (category Errors was not in it), so fetched"


def test_SNAP2_and_to_source_text_with_the_network_unavailable(tmp_path):
    c = _seeded(tmp_path)
    assert c.translate("Buy now", category="Marketing", locale="it-it") == "Buy now"


def test_SNAP2_a_seeded_block_renders_with_no_fetch(tmp_path):
    from langsys.registration import generate_custom_id

    custom_id = generate_custom_id("UI", ["Hello", "World"])
    path = crafted("p", {"it-it": {"UI": {custom_id: {"Hello": "Ciao", "World": "Mondo"}}}}).write_to(
        tmp_path / "b.json")
    c = LangsysClient("k", "p", api_url=OFFLINE, cache=MemoryCache(), debounce=0, auto_flush=False)
    c.set_locale("it-it")
    c.load_snapshot(path)
    out = c.translate_content_block("<div><p>Hello</p><p>World</p></div>", "UI")
    assert "Ciao" in out and "Mondo" in out and f'data-ls-contentblock="{custom_id}"' in out
    page = c.translate_page("<html><body><div><p>Hello <b>World</b></p></div></body></html>", "UI")
    assert "<body>" in page


def test_SNAP2_a_snapshot_for_another_project_is_refused(tmp_path):
    path = crafted("p", LOCAL_CATALOG).write_to(tmp_path / "s.json")
    other = LangsysClient("k", "another-project", api_url=OFFLINE, cache=MemoryCache(),
                          debounce=0, auto_flush=False)
    with pytest.raises(SnapshotError, match="another-project"):
        other.load_snapshot(path)
    same = LangsysClient("k", "p", api_url=OFFLINE, cache=MemoryCache(), debounce=0, auto_flush=False)
    same.load_snapshot(path)  # control: its own project's snapshot loads


# -- SRV-6 with a snapshot loaded and authorization unavailable ----------------------------------


def _offline_seeded(tmp_path):
    path = crafted("p", LOCAL_CATALOG).write_to(tmp_path / "s.json")
    c = LangsysClient("k", "p", api_url=OFFLINE, cache=MemoryCache(),
                      debounce=0, auto_flush=False)  # no configured base: only the snapshot knows it
    c.load_snapshot(path)
    return c


def test_SRV6_offline_a_snapshot_locale_is_served_from_the_snapshot(tmp_path):
    c = _offline_seeded(tmp_path)
    choice = c.resolve_request_locale(url="es-ES")
    assert (choice.locale.lower(), choice.source) == ("es-es", "url")
    assert c.translate("Checkout", category="UI", locale=choice.locale) == "Caja"


def test_SRV6_offline_a_locale_the_snapshot_lacks_falls_to_its_base(tmp_path):
    c = _offline_seeded(tmp_path)
    choice = c.resolve_request_locale(url="fr-fr", accept_language="de")
    assert (choice.locale.lower(), choice.source) == ("en-us", "base")


def test_SRV6_control_without_a_snapshot_offline_serves_only_the_configured_base():
    c = LangsysClient("k", "p", api_url=OFFLINE, cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False)
    assert c.resolve_request_locale(url="es-es").source == "base"


@pytest.mark.contract
def test_SNAP2_the_live_catalog_outranks_the_snapshot_once_fetched(clone, tmp_path):
    """The live catalog holds `Alla cassa`; the snapshot, from an earlier export, `Cassa`."""
    world = clone.world("outranked")
    path = crafted(world.project(), {"it-it": {"UI": {"Checkout": "Cassa"}}}).write_to(
        tmp_path / "s.json")
    c = client(clone, world)
    c.load_snapshot(path)
    assert c.translate("Checkout", category="UI", locale="it-it") == "Cassa", "control: seeded"
    c.translate("Not in the snapshot", category="UI", locale="it-it")  # this fetches the live catalog
    assert c.translate("Checkout", category="UI", locale="it-it") == "Alla cassa"


@pytest.mark.contract
def test_SNAP2_registration_is_decided_against_the_live_catalog_never_the_snapshot(clone, tmp_path):
    """`Untranslated` is in the snapshot; the live catalog does not hold it. Served from the
    snapshot it decides nothing; once the live catalog is loaded, it is a miss."""
    world = clone.world("empty")
    path = crafted(world.project(), {"it-it": {"UI": {"Untranslated": None}}}).write_to(
        tmp_path / "s.json")
    c = client(clone, world)
    c.load_snapshot(path)
    c.translate("Untranslated", category="UI", locale="it-it")
    assert not c.has_pending, "a snapshot hit decided a registration"
    c.translate("Anything else", category="UI", locale="it-it")  # loads the live catalog
    c.translate("Untranslated", category="UI", locale="it-it")
    assert {"phrase": "Untranslated", "category": "UI"} in c.pending_phrases
