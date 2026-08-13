import hashlib
import json

from langsys import generate_custom_id
from langsys.http import HttpClient
from langsys.registration import Registrar

API = "https://api.test/api"


def test_custom_id_matches_backend_scheme():
    # md5 of "category|phrase1|phrase2" — the scheme the stored catalog uses.
    cid = generate_custom_id("News", ["Home", "About"])
    expected = hashlib.md5("News|Home|About".encode()).hexdigest()
    assert cid == expected
    assert generate_custom_id("News", ["Home", "About"]) == cid  # stable
    assert generate_custom_id(None, ["x"]) == generate_custom_id("", ["x"])  # None == ""


def test_custom_id_matches_a_real_catalog_block():
    # The Kangen CAT_3 content block id, verified live.
    assert (
        generate_custom_id("CAT_3", ["Technical Support", "Customer Support", "image description"])
        == "36efd6e4d5673f70474d02627be110c0"
    )


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
