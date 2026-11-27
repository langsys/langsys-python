"""The worlds of the server-message rows in tests/test_messages.py: one project translating into
es-es, with the templates its catalog already lists, and the key a case registers with."""

INDEX = 2
RUNS = 6

_EMPTY = {"p1": {"target_locales": ["es-es"]}}

WORLDS = {
    "writer": {"count": 3, "projects": _EMPTY, "keys": {
        "write": {"project": "p1", "type": "write"},
        "read": {"project": "p1", "type": "read"},
    }},
    "listed": {"projects": {"p1": {
        "target_locales": ["es-es"],
        "phrases": [{"category": "Errors", "phrase": "The name field is required."}],
    }}, "keys": {"write": {"project": "p1", "type": "write"}}},
    "off-list": {"projects": _EMPTY, "keys": {
        "ip": {"project": "p1", "type": "ip_write", "ip_allowlist": ["@moved"]},
    }},
}
