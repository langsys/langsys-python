"""SNAP-1 - export is a client-side filter of the catalog; SNAP-3 - a snapshot is a cache.

The export runs against the contract double: the snapshot must carry exactly what the API serves
for the chosen categories. The format and checksum are the PHP SDK's, pinned by a vector PHP 8.3
produced, so a snapshot exported by either SDK loads in both.
"""

from __future__ import annotations

import json

import httpx
import pytest
from contract import PROJECT, WRITE_KEY, world

from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.snapshot import FORMAT, Snapshot, SnapshotError, _checksum

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
    response = httpx.get(f"{double.base_url}/translations/data",
                         params={"project_id": PROJECT, "locale": locale},
                         headers={"X-Authorization": WRITE_KEY})
    return response.json()["data"]


def test_SNAP1_the_snapshot_carries_exactly_what_the_api_serves_for_its_categories(double):
    double.seed(world(phrases=PHRASES))
    snapshot = Snapshot.export(client(double), ["it-IT", "es-es"], ["UI", "Errors"])
    for locale in ("it-it", "es-es"):
        api = served(double, locale)
        assert snapshot.catalog(locale) == {c: api[c] for c in ("UI", "Errors") if c in api}
    assert "Marketing" not in snapshot.catalog("it-it")
    assert snapshot.locales == ["it-it", "es-es"] and snapshot.project_id == PROJECT


def test_SNAP1_a_catalog_that_cannot_be_read_fails_the_export(double):
    double.seed(world(faults=[{"method": "GET", "path": "/translations/data", "status": 500}]))
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


def test_SNAP_the_checksum_is_byte_identical_to_the_php_sdks():
    """PHP 8.3.19: json_encode(payload, UNESCAPED_SLASHES|UNICODE|LINE_TERMINATORS), sha256. The
    vector carries a slash, U+2028, non-ASCII and an empty locale map, which PHP writes as []."""
    payload = {
        "project_id": "p", "generated_at": "2026-09-24T00:00:00Z", "locales": ["it-it"],
        "categories": ["UI", "Empty"],
        "catalog": {"it-it": {"UI": {"Checkout": "Cassa", "Pay/now   é": None, "blk": {"A": None}}},
                    "es-es": {}},
    }
    assert _checksum(payload) == "sha256:f66f2ca2791d8aa626351f57a8d4cb75231fc3b5601fe4e21727a39bdfe70d32"


def test_SNAP_the_command_line_writes_a_loadable_snapshot(double, tmp_path, monkeypatch):
    from langsys.snapshot import main

    double.seed(world(phrases=PHRASES))
    monkeypatch.setenv("LANGSYS_API_URL", double.base_url)
    monkeypatch.setenv("LANGSYS_API_KEY", WRITE_KEY)
    monkeypatch.setenv("LANGSYS_PROJECT_ID", PROJECT)
    out = tmp_path / "cli.json"
    assert main(["--locale", "it-it", "--category", "UI", "--out", str(out)]) == 0
    assert Snapshot.load(out).catalog("it-it")["UI"]["Checkout"] == "Cassa"
