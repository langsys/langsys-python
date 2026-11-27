"""The worlds of the clone rows in tests/test_snapshot.py: a project whose catalog spans three
categories, one whose live catalog has moved on from a snapshot, and one with nothing in it."""

INDEX = 3
RUNS = 6

_WRITE = {"write": {"project": "p1", "type": "write"}}

WORLDS = {
    "catalog": {"count": 5, "keys": _WRITE, "projects": {"p1": {
        "target_locales": ["it-it", "es-es"],
        "phrases": [
            {"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa", "es-es": "Caja"}},
            {"category": "UI", "phrase": "Untranslated"},
            {"category": "Errors", "phrase": "The name is required.",
             "translations": {"it-it": "Il nome è obbligatorio."}},
            {"category": "Marketing", "phrase": "Buy now", "translations": {"it-it": "Compra ora"}},
        ],
    }}},
    "outranked": {"keys": _WRITE, "projects": {"p1": {
        "target_locales": ["it-it"],
        "phrases": [{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Alla cassa"}}],
    }}},
    "empty": {"keys": _WRITE, "projects": {"p1": {"target_locales": ["it-it"]}}},
}
