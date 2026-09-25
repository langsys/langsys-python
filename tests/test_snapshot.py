"""SNAP-1 - export is a client-side filter of the catalog into the fleet's one format; SNAP-3 - a
snapshot is a cache.

The export runs against the contract double: the snapshot must carry exactly what the API serves
for the chosen categories.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from contract import PROJECT, WRITE_KEY, world

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.snapshot import FORMAT, Snapshot, SnapshotError, _canonical, _checksum

PHRASES = [
    {"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa", "es-es": "Caja"}},
    {"category": "UI", "phrase": "Untranslated"},
    {"category": "Errors", "phrase": "The name is required.", "translations": {"it-it": "Il nome è obbligatorio."}},
    {"category": "Marketing", "phrase": "Buy now", "translations": {"it-it": "Compra ora"}},
]


def client(double) -> LangsysClient:
    return LangsysClient(WRITE_KEY, PROJECT, api_url=double.base_url, cache=MemoryCache(),
                         debounce=0, auto_flush=False)


def served(double, locale):
    response = httpx.get(f"{double.base_url}/translations",
                         params={"project_id": PROJECT, "locale": locale, "format": "flat"},
                         headers={"X-Authorization": WRITE_KEY})
    return response.json()["data"]


def test_SNAP1_the_snapshot_carries_exactly_what_the_api_serves_for_its_categories(double):
    double.seed(world(phrases=PHRASES))
    snapshot = Snapshot.export(client(double), ["it-IT", "es-es"], ["UI", "Errors"])
    for locale in ("it-it", "es-es"):
        api = served(double, locale)
        assert snapshot.catalog(locale) == {c: api[c] for c in ("UI", "Errors") if c in api}
    assert "Marketing" not in snapshot.catalog("it-it")
    assert snapshot.locales == ["es-es", "it-it"], "locales are sorted ascending"
    assert snapshot.project_id == PROJECT and snapshot.base_locale == "en-us"


def test_SNAP1_a_catalog_that_cannot_be_read_fails_the_export(double):
    double.seed(world(faults=[{"method": "GET", "path": "/translations", "status": 500}]))
    with pytest.raises(SnapshotError, match="it-it"):
        Snapshot.export(client(double), ["it-it"], ["UI"])


def test_SNAP1_an_export_names_a_locale_and_a_category(double):
    with pytest.raises(SnapshotError):
        Snapshot.export(client(double), [], ["UI"])


def test_SNAP3_a_snapshot_round_trips_through_its_file(double, tmp_path):
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    loaded = Snapshot.load(path)
    assert loaded.catalog("it-it")["UI"]["Checkout"] == "Cassa"
    assert json.loads(path.read_text())["format"] == FORMAT


def test_SNAP3_an_edited_snapshot_is_refused(double, tmp_path):
    """Hand-editing a cached artifact is a defect, and a loader that served the edit would make the
    snapshot the source of truth. The refresh is a new export."""
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
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
def test_SNAP1_a_loader_refuses_by_name(double, member, value, reason):
    double.seed(world(phrases=PHRASES))
    document = json.loads(Snapshot.export(client(double), ["it-it"], ["UI"]).to_json())
    if value is None:
        del document[member]
    else:
        document[member] = value
    with pytest.raises(SnapshotError, match=reason):
        Snapshot.load(json.dumps(document))


def test_SNAP1_the_file_is_any_json_encoding_of_the_document(double):
    """A loader recomputes the checksum from the parsed document, so re-encoding the file (key
    order, whitespace, escapes) does not break it."""
    double.seed(world(phrases=PHRASES))
    document = json.loads(Snapshot.export(client(double), ["it-it"], ["UI"]).to_json())

    def reversed_maps(node):
        if isinstance(node, dict):
            return {k: reversed_maps(v) for k, v in reversed(list(node.items()))}
        return node

    reencoded = json.dumps(reversed_maps(document), ensure_ascii=True, indent=1)
    assert Snapshot.load(reencoded).catalog("it-it")["UI"]["Checkout"] == "Cassa"


def test_SNAP_the_command_line_writes_a_loadable_snapshot(double, tmp_path, monkeypatch):
    from langsys.snapshot import main

    double.seed(world(phrases=PHRASES))
    monkeypatch.setenv("LANGSYS_API_URL", double.base_url)
    monkeypatch.setenv("LANGSYS_API_KEY", WRITE_KEY)
    monkeypatch.setenv("LANGSYS_PROJECT_ID", PROJECT)
    out = tmp_path / "cli.json"
    assert main(["--locale", "it-it", "--category", "UI", "--out", str(out)]) == 0
    assert Snapshot.load(out).catalog("it-it")["UI"]["Checkout"] == "Cassa"


# -- SNAP-2: the loader the bindings seed from at startup -------------------------------------------


def _seeded(double, tmp_path, **kw):
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    c = LangsysClient(WRITE_KEY, PROJECT, api_url=kw.get("api_url", double.base_url),
                      cache=MemoryCache(), base_locale="en-us", debounce=0, auto_flush=False)
    c.load_snapshot(path)
    return c


def test_SNAP2_a_seeded_phrase_renders_with_no_fetch(double, tmp_path):
    c = _seeded(double, tmp_path, api_url="http://127.0.0.1:9/api")  # nothing is listening
    assert c.translate("Checkout", category="UI", locale="it-it") == "Cassa"
    assert c.translate("Untranslated", category="UI", locale="it-it") == "Untranslated"
    assert not c.has_pending, "a phrase the snapshot holds is known"


def test_SNAP2_a_phrase_the_snapshot_lacks_falls_back_to_the_fetch(double, tmp_path):
    c = _seeded(double, tmp_path)
    assert c.translate("The name is required.", category="Errors", locale="it-it") == (
        "Il nome è obbligatorio."
    ), "not in the snapshot (category Errors was not exported), so fetched"


def test_SNAP2_and_to_source_text_with_the_network_unavailable(double, tmp_path):
    c = _seeded(double, tmp_path, api_url="http://127.0.0.1:9/api")
    assert c.translate("Buy now", category="Marketing", locale="it-it") == "Buy now"


def test_SNAP2_a_seeded_block_renders_with_no_fetch(double, tmp_path):
    from langsys.registration import generate_custom_id

    tokens = ["Hello", "World"]
    custom_id = generate_custom_id("UI", tokens)
    double.seed(world(blocks=[{"category": "UI", "custom_id": custom_id, "phrases": [
        {"phrase": "Hello", "translations": {"it-it": "Ciao"}},
        {"phrase": "World", "translations": {"it-it": "Mondo"}},
    ]}]))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "b.json")
    c = LangsysClient(WRITE_KEY, PROJECT, api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                      debounce=0, auto_flush=False)
    c.set_locale("it-it")
    c.load_snapshot(path)
    out = c.translate_content_block("<div><p>Hello</p><p>World</p></div>", "UI")
    assert "Ciao" in out and "Mondo" in out and f'data-ls-contentblock="{custom_id}"' in out
    page = c.translate_page("<html><body><div><p>Hello <b>World</b></p></div></body></html>", "UI")
    assert "<body>" in page


def test_SNAP2_a_snapshot_for_another_project_is_refused(double, tmp_path):
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    other = LangsysClient(WRITE_KEY, "another-project", api_url=double.base_url,
                          cache=MemoryCache(), debounce=0, auto_flush=False)
    with pytest.raises(SnapshotError, match="another-project"):
        other.load_snapshot(path)


# -- SRV-6 with a snapshot loaded and authorization unavailable ----------------------------------


def _offline_seeded(double, tmp_path):
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["es-es", "it-it"], ["UI"]).write_to(tmp_path / "s.json")
    c = LangsysClient(WRITE_KEY, PROJECT, api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                      debounce=0, auto_flush=False)  # no configured base: only the snapshot knows it
    c.load_snapshot(path)
    return c


def test_SRV6_offline_a_snapshot_locale_is_served_from_the_snapshot(double, tmp_path):
    c = _offline_seeded(double, tmp_path)
    choice = c.resolve_request_locale(url="es-ES")
    assert (choice.locale.lower(), choice.source) == ("es-es", "url")
    assert c.translate("Checkout", category="UI", locale=choice.locale) == "Caja"


def test_SRV6_offline_a_locale_the_snapshot_lacks_falls_to_its_base(double, tmp_path):
    c = _offline_seeded(double, tmp_path)
    choice = c.resolve_request_locale(url="fr-fr", accept_language="de")
    assert (choice.locale.lower(), choice.source) == ("en-us", "base")


def test_SRV6_control_without_a_snapshot_offline_serves_only_the_configured_base():
    c = LangsysClient("k", PROJECT, api_url="http://127.0.0.1:9/api", cache=MemoryCache(),
                      base_locale="en-us", debounce=0, auto_flush=False)
    assert c.resolve_request_locale(url="es-es").source == "base"


def test_SNAP2_the_live_catalog_outranks_the_snapshot_once_fetched(double, tmp_path):
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    double.seed(world(phrases=[{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Alla cassa"}}]))
    c = client(double)
    c.load_snapshot(path)
    assert c.translate("Checkout", category="UI", locale="it-it") == "Cassa", "control: seeded"
    c.translate("Not in the snapshot", category="UI", locale="it-it")  # this fetches the live catalog
    assert c.translate("Checkout", category="UI", locale="it-it") == "Alla cassa"


def test_SNAP2_registration_is_decided_against_the_live_catalog_never_the_snapshot(double, tmp_path):
    """`Untranslated` is in the snapshot; the live catalog no longer holds it. Served from the
    snapshot it decides nothing; once the live catalog is loaded, it is a miss."""
    double.seed(world(phrases=PHRASES))
    path = Snapshot.export(client(double), ["it-it"], ["UI"]).write_to(tmp_path / "s.json")
    double.seed(world())
    c = client(double)
    c.load_snapshot(path)
    c.translate("Untranslated", category="UI", locale="it-it")
    assert not c.has_pending, "a snapshot hit decided a registration"
    c.translate("Anything else", category="UI", locale="it-it")  # loads the live catalog
    c.translate("Untranslated", category="UI", locale="it-it")
    assert {"phrase": "Untranslated", "category": "UI"} in c.pending_phrases
