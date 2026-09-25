#!/usr/bin/env python3
"""Count and check CONFORMANCE.md's status table against the spec's rule ids.

The summary in `CONFORMANCE.md` claims to be counted rather than asserted. This is the thing
that counts it, committed because a claim of "counted by script" with no script in the repo is
a test nobody can re-run.

    python3 _dev_/conformance_counts.py [path/to/sdk-spec.mdx]

With no argument it reads the spec from git at the pinned commit, and refuses to count if that
commit's blob is not the one pinned below - so the tally is always against the revision the file
says it is filed against, never whatever happens to be checked out. It also refuses if the
file's own `Spec revision read` header names a different blob: a header carried forward from an
earlier write is a claim about a check that did not happen.

The table is the fleet's canonical shape - `| Rule | Status | Tier | Evidence |`, ONE rule id per
row - and the vocabulary is checked, not just counted:

    status  implemented | provisional | delegated | partial | not implemented |
            held (strip ruling) | waived | n/a (profile: ...) | n/a (architecture: ...)
    tier    live | contract | mock | n/a (pure) | -

`implemented` needs a `live`, `contract` or `n/a (pure)` tier; `provisional` needs `mock`; an
`n/a` or `not implemented` row carries `-`.

The exit code is about accounting integrity: a rule id unaccounted for, unknown, claimed twice,
a range where one id belongs, a word outside the vocabulary, or a mismatched header. Whether the
file is GREEN (no row provisional, partial, not implemented or held) is reported, not enforced.
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = REPO_ROOT / "CONFORMANCE.md"

#: Pinned in CONFORMANCE.md's header, by commit rather than by branch: a branch moves, and this
#: must resolve the revision the rows were actually filed against.
SPEC_COMMIT = "cd5468c765c67764a0e08c434d41413ead3678dc"
SPEC_BLOB = "abe122cf5346f92a0474b627d49451e6de9cd761"
SPEC_PATH = "docs/sdk-spec.mdx"
LANGSYS2 = Path.home() / "Documents" / "dev" / "langsys2"

RULE_HEADING = re.compile(r"^### ([A-Z]+-\d+) ", re.M)
HEADER_BLOB = re.compile(r"^\|\s*\*\*Spec revision read\*\*\s*\|.*?\bblob\s+`?([0-9a-f]{40})", re.M)
TABLE_HEADER = re.compile(r"^\|\s*Rule\s*\|\s*Status\s*\|\s*Tier\s*\|\s*Evidence\s*\|", re.M)
RULE_ID = re.compile(r"^[A-Z]+-\d+$")

STATUSES = ("implemented", "provisional", "delegated", "partial", "not implemented",
            "held (strip ruling)", "waived")
TIERS = ("live", "contract", "mock", "n/a (pure)", "-")
NOT_GREEN = ("provisional", "partial", "not implemented", "held")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(LANGSYS2), *args], capture_output=True, text=True, check=True
    ).stdout


def spec_text(argv: list[str]) -> str:
    if len(argv) > 1:
        return Path(argv[1]).read_text(encoding="utf-8")
    blob = _git("rev-parse", f"{SPEC_COMMIT}:{SPEC_PATH}").strip()
    if blob != SPEC_BLOB:
        raise SystemExit(
            f"ERROR - {SPEC_COMMIT[:12]}:{SPEC_PATH} resolves to {blob}, pinned {SPEC_BLOB}"
        )
    return _git("show", f"{SPEC_COMMIT}:{SPEC_PATH}")


def _clean(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def parse_table(markdown: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Rule id -> (status, tier), plus every integrity problem found on the way."""
    header = TABLE_HEADER.search(markdown)
    if header is None:
        return {}, ["no table whose header starts `| Rule | Status | Tier | Evidence |`"]
    rows: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for line in markdown[header.end():].splitlines()[1:]:
        if not line.startswith("|"):
            if line.strip():
                break
            continue
        cells = line.strip().strip("|").split("|")
        if set(line.strip()) <= set("|-: "):
            continue
        if len(cells) < 4:
            problems.append(f"row with fewer than four cells: {line[:60]!r}")
            continue
        rule, status, tier = _clean(cells[0]), _clean(cells[1]), _clean(cells[2])
        if not RULE_ID.match(rule):
            problems.append(f"not ONE rule id per row: {rule!r}")
            continue
        if rule in rows:
            problems.append(f"{rule} claimed by more than one row")
            continue
        rows[rule] = (status, tier)

        profile_na = status.startswith("n/a (profile:") and status.endswith(")")
        arch_na = status.startswith("n/a (architecture:") and status.endswith(")")
        if not (status in STATUSES or profile_na or arch_na):
            problems.append(f"{rule}: status {status!r} is outside the vocabulary")
        if tier not in TIERS:
            problems.append(f"{rule}: tier {tier!r} is outside the vocabulary")
        if status == "implemented" and tier not in ("live", "contract", "n/a (pure)"):
            problems.append(f"{rule}: implemented needs live, contract or n/a (pure), not {tier!r}")
        if status == "provisional" and tier != "mock":
            problems.append(f"{rule}: provisional needs a mock tier, not {tier!r}")
        if (profile_na or arch_na or status == "not implemented") and tier != "-":
            problems.append(f"{rule}: {status.split(' (')[0]} carries tier '-', not {tier!r}")
    return rows, problems


def group(status: str, tier: str) -> str:
    if status.startswith("n/a (profile"):
        return "n/a (profile)"
    if status.startswith("n/a (architecture"):
        return "n/a (architecture)"
    if status == "implemented":
        return f"implemented ({tier})"
    return status


def main(argv: list[str]) -> int:
    rules = RULE_HEADING.findall(spec_text(argv))
    markdown = CONFORMANCE.read_text(encoding="utf-8")

    problems: list[str] = []
    header = HEADER_BLOB.search(markdown)
    if header is None:
        problems.append("no `| **Spec revision read** |` header row naming a blob")
    elif header.group(1) != SPEC_BLOB:
        problems.append(f"header names blob {header.group(1)}, pinned {SPEC_BLOB}")

    rows, table_problems = parse_table(markdown)
    problems += table_problems
    problems += [f"{r} unaccounted for in CONFORMANCE" for r in rules if r not in rows]
    problems += [f"{r} claimed but not in the spec" for r in rows if r not in rules]

    counts = collections.Counter(group(*rows[r]) for r in rules if r in rows)
    source = argv[1] if len(argv) > 1 else f"commit {SPEC_COMMIT[:12]}, blob {SPEC_BLOB[:12]}"
    print(f"spec rules: {len(rules)}  ({source})")
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {name:30} {count}")
    print(f"  {'TOTAL':30} {sum(counts.values())}")

    blocking = {k: v for k, v in counts.items() if k.startswith(NOT_GREEN)}
    print(f"\nGREEN: {'yes' if not problems and not blocking else 'no'}")
    for name, count in sorted(blocking.items()):
        ids = [r for r in rules if r in rows and group(*rows[r]) == name]
        print(f"  blocked by: {name} x{count}  {', '.join(ids)}")

    for problem in problems:
        print(f"ERROR - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
