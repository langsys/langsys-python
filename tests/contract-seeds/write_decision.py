"""The worlds of tests/test_contract_write_decision.py: one project translating into it-it and
es-es, and the key whose write decision a case reads.

`off-list` allow-lists only the case's second address, so a session learns it may not write and
the case then moves there, into a world where the API would accept.
"""

INDEX = 1
RUNS = 8

_PROJECT = {"p1": {"target_locales": ["it-it", "es-es"]}}

WORLDS = {
    "writer": {"count": 3, "projects": _PROJECT, "keys": {"write": {"project": "p1", "type": "write"}}},
    "listed": {"count": 2, "projects": _PROJECT,
               "keys": {"ip": {"project": "p1", "type": "ip_write", "ip_allowlist": ["@self"]}}},
    "off-list": {"count": 2, "projects": _PROJECT,
                 "keys": {"ip": {"project": "p1", "type": "ip_write", "ip_allowlist": ["@moved"]}}},
}
