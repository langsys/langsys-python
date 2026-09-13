import pytest

from langsys.translate import resolve

CATALOG = {
    "CAT_3": {
        "__category__": "CAT_3",
        "Technical Support": "Soporte Técnico",
        "Untranslated": None,
        "Empty": "",
        "abc123": {"Child": "Hijo"},  # content block keyed by custom_id
    },
    "__uncategorized__": {"Plain": "Llano"},
}


def test_hit_returns_translation():
    r = resolve(CATALOG, "Technical Support", "CAT_3")
    assert r.text == "Soporte Técnico" and r.missing is False


def test_null_value_falls_back_to_base_and_not_missing():
    r = resolve(CATALOG, "Untranslated", "CAT_3")
    assert r.text == "Untranslated" and r.missing is False


def test_empty_value_falls_back_to_base():
    r = resolve(CATALOG, "Empty", "CAT_3")
    assert r.text == "Empty" and r.missing is False


def test_absent_phrase_is_missing():
    r = resolve(CATALOG, "Brand new", "CAT_3")
    assert r.text == "Brand new" and r.missing is True


def test_uncategorized_default():
    r = resolve(CATALOG, "Plain")
    assert r.text == "Llano" and r.missing is False


def test_absent_category_is_missing():
    r = resolve(CATALOG, "Whatever", "NoSuchCat")
    assert r.text == "Whatever" and r.missing is True


def test_content_block_child_lookup():
    r = resolve(CATALOG, "Child", "CAT_3", content_block_id="abc123")
    assert r.text == "Hijo" and r.missing is False


def test_content_block_missing_child_falls_back():
    r = resolve(CATALOG, "Ghost", "CAT_3", content_block_id="abc123")
    assert r.text == "Ghost" and r.missing is False


# -- REG-12: structure, not string shape ---------------------------------------

BLOCK_ID = "0123456789abcdef0123456789abcdef"


def test_REG12_a_nested_map_is_a_content_block_never_a_missing_phrase():
    """Text colliding with a block id is PRESENT, so it is not a miss; and it is displayed as
    the text, never as the block's map."""
    r = resolve({"UI": {BLOCK_ID: {"Hello": "Ciao"}}}, BLOCK_ID, "UI")
    assert r.missing is False
    assert r.text == BLOCK_ID


def test_REG12_a_phrase_shaped_like_a_hash_is_still_a_phrase():
    """The 32-hex shape test was this rule's original wording, and it rejects every
    legitimate phrase that happens to look like a hash - both as a miss and as a hit."""
    assert resolve({"UI": {}}, BLOCK_ID, "UI").missing is True
    assert resolve({"UI": {BLOCK_ID: "tradotto"}}, BLOCK_ID, "UI").text == "tradotto"


@pytest.mark.xfail(strict=True, reason="REG-12: measured divergence on the sync path, not yet fixed")
def test_REG12_presence_and_structure_agree_on_the_sync_path():
    """Where presence and structure are tested in separate places both must agree, or text
    colliding with a block id re-registers forever. translate() reads presence and calls it
    known; sync() flattens a block into its children and calls the same text new.

    Measured, not fixed. Strict, so the day it is fixed this reports XPASS and fails, and the
    REG-12 row in CONFORMANCE.md has to move with it."""
    from unittest.mock import patch

    from langsys import LangsysClient
    from langsys.cache import MemoryCache
    from langsys.catalog import CatalogFetch

    catalog = {"UI": {BLOCK_ID: {"Hello": "Ciao"}}}
    client = LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False,
    )
    with patch.object(client._catalog, "get", return_value=CatalogFetch(catalog, ok=True)):
        client.translate(BLOCK_ID, category="UI", locale="it-it")
        assert not client.has_pending, "control: translate() treats the text as present"
        with patch.object(client, "_resolve_write_enabled", return_value=False):
            result = client.sync([{"phrase": BLOCK_ID, "category": "UI"}], locale="it-it")
    assert result["new_phrases"] == [], "sync() calls new what translate() calls known"


# -- CAT-3 --------------------------------------------------------------------


def test_CAT3_a_registered_untranslated_block_is_known_not_missing():
    """A block registered but not yet translated comes back as an object whose inner phrases
    are null. Reading that as unknown is a WRITE storm: every write-enabled session re-POSTs
    the whole block for the whole machine-translation window."""
    pytest.importorskip("lxml")
    from unittest.mock import patch

    from langsys import LangsysClient
    from langsys.cache import MemoryCache
    from langsys.catalog import CatalogFetch
    from langsys.registration import generate_custom_id

    # Non-ASCII on purpose. For ASCII text the legacy JS code-unit hash EQUALS the current id,
    # so a lookup that wrongly skipped the block under its own id found it again one step later
    # under the "legacy" one. The first vector here was ASCII and could not fail.
    html = "<div><p>Привет</p><p>Мир</p></div>"
    block_id = generate_custom_id("UI", ["Привет", "Мир"])
    client = LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us",
        debounce=0, auto_flush=False,
    )
    registered = {"UI": {block_id: {"Привет": None, "Мир": None}}}
    with patch.object(client._catalog, "get", return_value=CatalogFetch(registered, ok=True)):
        out = client.translate_content_block(html, category="UI")
    assert client.pending_content_blocks == [], "a registered block was queued again"
    assert "Привет" in out and "Мир" in out, "null inner phrases must display the source"

    with patch.object(client._catalog, "get", return_value=CatalogFetch({"UI": {}}, ok=True)):
        client.translate_content_block(html, category="UI")
    assert len(client.pending_content_blocks) == 1, "control: an absent block must queue"
