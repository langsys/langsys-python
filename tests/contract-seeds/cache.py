"""The worlds of the clone rows in tests/test_cache2.py: one project whose catalog translates
`Checkout` into it-it and es-es."""

INDEX = 4
RUNS = 6

WORLDS = {
    "translated": {"count": 4, "keys": {"write": {"project": "p1", "type": "write"}}, "projects": {"p1": {
        "target_locales": ["it-it", "es-es"],
        "phrases": [{"category": "UI", "phrase": "Checkout",
                     "translations": {"it-it": "Cassa", "es-es": "Caja"}}],
    }}},
}
