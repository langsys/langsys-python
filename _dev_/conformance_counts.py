#!/usr/bin/env python3
"""Count CONFORMANCE.md's status rows against the spec's rule ids.

The summary table in `CONFORMANCE.md` claims to be counted rather than asserted. This
is the thing that counts it. Committed because a claim of "counted by script" with no
script in the repo is the same shape as a test nobody can re-run.

    python3 _dev_/conformance_counts.py [path/to/sdk-spec.mdx]

With no argument it extracts the spec from git at the blob the header pins, so the
count is against the revision the file says it is filed against and not whatever is
checked out.

Exits non-zero when a rule id is unaccounted for or appears twice, both of which have
happened: a duplicate `GATE-1 (precedence)` row made an independent tally read 80 ids
over 58 rows against a 79-rule spec.
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = REPO_ROOT / "CONFORMANCE.md"

#: Pinned in CONFORMANCE.md's header. `rev-parse <commit>:<path>` rather than
#: `ls-tree <branch>` — a branch moves, and this must resolve the revision the rows
#: were actually filed against.
SPEC_COMMIT = "483f98fb9c22155fdd51e0946239556470f57936"
SPEC_PATH = "docs/sdk-spec.mdx"
LANGSYS2 = Path.home() / "Documents" / "dev" / "langsys2"

RULE_HEADING = re.compile(r"^### ([A-Z]+-\d+) ", re.M)
ROW = re.compile(r"^\|\s*([A-Z][A-Za-z0-9\-–,\s()]*?)\s*\|\s*([^|]+?)\s*\|")
RANGE = re.compile(r"^([A-Z]+)-(\d+)\s*[–-]\s*(\d+)$")
SINGLE = re.compile(r"^([A-Z]+)-(\d+)$")
BARE_RANGE = re.compile(r"^(\d+)\s*[–-]\s*(\d+)$")


def spec_text(argv: list[str]) -> str:
    if len(argv) > 1:
        return Path(argv[1]).read_text(encoding="utf-8")
    return subprocess.run(
        ["git", "-C", str(LANGSYS2), "show", f"{SPEC_COMMIT}:{SPEC_PATH}"],
        capture_output=True, text=True, check=True,
    ).stdout


def parse_rows(markdown: str) -> tuple[dict[str, str], list[str]]:
    """Rule id -> status, plus any id claimed by more than one row."""
    status: dict[str, str] = {}
    duplicates: list[str] = []

    def record(rule: str, value: str) -> None:
        if rule in status:
            duplicates.append(rule)
            return
        status[rule] = value

    for line in markdown.splitlines():
        match = ROW.match(line)
        if not match or match.group(1).strip() == "Rule":
            continue
        label = match.group(1).replace("(precedence)", "")
        value = match.group(2).replace("**", "").strip()
        family = None
        for part in (p.strip() for p in label.split(",")):
            if ranged := RANGE.match(part):
                family = ranged.group(1)
                for n in range(int(ranged.group(2)), int(ranged.group(3)) + 1):
                    record(f"{family}-{n}", value)
            elif single := SINGLE.match(part):
                family = single.group(1)
                record(part, value)
            elif (bare := BARE_RANGE.match(part)) and family:
                for n in range(int(bare.group(1)), int(bare.group(2)) + 1):
                    record(f"{family}-{n}", value)
    return status, duplicates


def bucket(value: str) -> str:
    if value.startswith("n/a (profile"):
        return "n/a (profile)"
    if value.startswith("n/a"):
        return "n/a (architecture)"
    return value


def main(argv: list[str]) -> int:
    rules = RULE_HEADING.findall(spec_text(argv))
    markdown = CONFORMANCE.read_text(encoding="utf-8")
    # Only the Status section. Other tables in the file use the same pipe shape — the
    # deferral table is keyed by rule id too — and counting those would report a rule
    # twice for saying two different things about it.
    start = markdown.index("## Status")
    end = markdown.index("\n## ", start + 1)
    status, duplicates = parse_rows(markdown[start:end])

    missing = [r for r in rules if r not in status]
    extra = [r for r in status if r not in rules]
    counts = collections.Counter(bucket(status[r]) for r in rules if r in status)

    print(f"spec rules: {len(rules)}  (commit {SPEC_COMMIT[:12]})")
    for name, count in counts.most_common():
        print(f"  {name:22} {count}")
    binding = sum(v for k, v in counts.items() if k != "n/a (profile)")
    print(f"  {'TOTAL':22} {sum(counts.values())}   binding: {binding}")

    failed = False
    for label, items in (
        ("unaccounted for in CONFORMANCE", missing),
        ("claimed but not in the spec", extra),
        ("claimed by more than one row", sorted(set(duplicates))),
    ):
        if items:
            failed = True
            print(f"\nERROR — {label}: {items}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
