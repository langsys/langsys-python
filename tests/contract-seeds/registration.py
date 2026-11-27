"""The worlds of tests/test_contract_registration.py: one project translating into it-it and
es-es, with a write key and a read key, and one whose catalog already translates `Checkout`."""

INDEX = 6
RUNS = 8

WORLDS = {
    "writer": {"count": 6, "projects": {"p1": {"target_locales": ["it-it", "es-es"]}}, "keys": {
        "write": {"project": "p1", "type": "write"},
        "read": {"project": "p1", "type": "read"},
    }},
    "translated": {"projects": {"p1": {
        "target_locales": ["it-it", "es-es"],
        "phrases": [{"category": "UI", "phrase": "Checkout", "translations": {"it-it": "Cassa"}}],
    }}, "keys": {"write": {"project": "p1", "type": "write"}}},
}
