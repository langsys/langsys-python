"""The worlds of tests/test_contract_wire.py: one project translating into it-it and es-es, and
one whose catalog already translates `Checkout`."""

INDEX = 7
RUNS = 7

_WRITE = {"write": {"project": "p1", "type": "write"}}

WORLDS = {
    "writer": {"count": 4, "projects": {"p1": {"target_locales": ["it-it", "es-es"]}}, "keys": _WRITE},
    "translated": {"count": 2, "keys": _WRITE, "projects": {"p1": {
        "target_locales": ["it-it", "es-es"],
        "phrases": [{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa"}}],
    }}},
}
