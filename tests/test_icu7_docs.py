"""ICU-7: every surface this SDK ships shows phrases flat.

The README and the examples never write a `plural`, `select` or `selectordinal` construct into a
phrase. ICU appears in three roles only, each passage named below with its role, so a new ICU
passage fails until someone decides it belongs: what the catalog returns, for code that renders it
(ICU-1..6); what MIG-4 converts a legacy plural into; and a counter-example marked wrong. The
changelog records earlier releases and is outside the rule. The gate is proven the way CONF-3
proves a runtime rule: a planted ICU phrase fails it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACES = [ROOT / "README.md", *sorted((ROOT / "examples").rglob("*.py"))]
CONSTRUCT = re.compile(r"\{\s*[A-Za-z_][\w]*\s*,\s*(plural|select|selectordinal)\s*[,}]")

#: Every passage allowed to hold ICU, by file, the text it holds and its role.
EXEMPT = [
    ("README.md", "`{n, plural, …}`, `select`, `selectordinal`",
     "what the catalog returns: translate() and interpolate() render it (ICU-1..6)"),
    ("README.md", "`{count, plural, =1 {…} other {…}}`",
     "what MIG-4 converts a .po plural into, registered by the SDK, not the developer"),
]


def violations(name: str, text: str) -> list[str]:
    allowed = [passage for file, passage, _ in EXEMPT if file == name]
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if CONSTRUCT.search(line) and not any(passage in line for passage in allowed):
            found.append(f"{name}:{number}: {line.strip()}")
    return found


def test_ICU7_no_surface_writes_icu_into_a_phrase():
    found = [v for path in SURFACES
             for v in violations(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8"))]
    assert found == [], "ICU written into a phrase:\n" + "\n".join(found)


def test_ICU7_every_exempt_passage_is_still_there():
    """An exemption that matches nothing is a stale permission: remove it."""
    for file, passage, role in EXEMPT:
        assert passage in (ROOT / file).read_text(encoding="utf-8"), (file, passage, role)


def test_ICU7_control_an_icu_phrase_planted_in_an_example_fails_the_gate():
    planted = (ROOT / "examples" / "basic_usage.py").read_text(encoding="utf-8").replace(
        '"Hello, {name}! You have {count} new messages."',
        '"Hello, {name}! You have {count, plural, one {# new message} other {# new messages}}."')
    assert planted != (ROOT / "examples" / "basic_usage.py").read_text(encoding="utf-8")
    assert len(violations("examples/basic_usage.py", planted)) == 1
    assert violations("README.md", "{gender, select, male {his} other {their}}")
    assert violations("README.md", "{n, selectordinal, one {#st} other {#th}}")
