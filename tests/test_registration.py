import hashlib
import json

from langsys import generate_custom_id
from langsys.http import HttpClient
from langsys.registration import Registrar, legacy_custom_ids

API = "https://api.test/api"


def test_custom_id_is_the_canonical_cross_sdk_form():
    """CID-1. The byte-level and fixture-anchored assertions live in
    ``test_custom_id.py``; this pins the two properties this module cares about —
    that the id is stable, and that the pre-release pipe-join scheme is gone."""
    cid = generate_custom_id("News", ["Home", "About"])
    assert cid == generate_custom_id("News", ["Home", "About"])  # stable
    assert cid != hashlib.md5(b"News|Home|About").hexdigest()  # not the old scheme
    assert generate_custom_id(None, ["x"]) == generate_custom_id("", ["x"])  # CID-2


def test_a_real_catalog_block_registered_by_an_older_sdk_still_resolves():
    """CID-3, on a live-observed id rather than a synthetic one.

    ``36efd6e4…`` is the id this exact block was stored under, read from a real
    catalog before the scheme was corrected. The current form no longer produces it —
    that is the point of the correction — so it must be reachable through the
    historical-id list, or every translation attached to that block is orphaned."""
    phrases = ["Technical Support", "Customer Support", "image description"]
    stored_id = "36efd6e4d5673f70474d02627be110c0"

    assert generate_custom_id("CAT_3", phrases) != stored_id
    assert stored_id in legacy_custom_ids("CAT_3", phrases)


def test_phrase_item_normalization():
    assert Registrar._phrase_item("Save") == {
        "type": "phrase",
        "phrase": "Save",
        "category": None,
        "translatable": True,
    }
    item = Registrar._phrase_item({"phrase": "Save", "category": "UI", "translatable": False})
    assert item["category"] == "UI" and item["translatable"] is False


def test_register_phrases_chunks_by_batch_limit(httpx_mock):
    httpx_mock.add_response(url=f"{API}/translatable-items", json={"status": True})
    httpx_mock.add_response(url=f"{API}/translatable-items", json={"status": True})

    http = HttpClient(API, "k")
    reg = Registrar(http, "proj-1", batch_limit=200)
    responses = reg.register_phrases([f"phrase {i}" for i in range(250)])

    requests = httpx_mock.get_requests()
    assert len(requests) == 2  # 250 -> 200 + 50
    first = json.loads(requests[0].content)
    assert first["project_id"] == "proj-1"
    assert len(first["translatable_items"]) == 200
    assert len(json.loads(requests[1].content)["translatable_items"]) == 50
    assert len(responses) == 2
    http.close()


def test_register_content_block_payload(httpx_mock):
    httpx_mock.add_response(url=f"{API}/translatable-items", json={"status": True})
    http = HttpClient(API, "k")
    reg = Registrar(http, "proj-1")
    reg.register_content_block(
        "<ul><li>Home</li></ul>", ["Home"], category="Nav", label="Menu"
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    item = body["translatable_items"][0]
    assert item["type"] == "content_block"
    assert item["custom_id"] == generate_custom_id("Nav", ["Home"])
    assert item["phrases"] == [{"phrase": "Home"}]
    assert item["category"] == "Nav" and item["label"] == "Menu"
    http.close()
