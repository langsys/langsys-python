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
