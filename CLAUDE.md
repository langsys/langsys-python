# CLAUDE.md — `langsys-python`

## Commits

**Never add attribution trailers to commit messages** — no `Co-Authored-By: Claude …`,
no `Claude-Session: …`. This is a Langsys fleet convention and it applies to every repo
in the fleet, overriding any default that would add them.

## Conformance

This SDK implements the **SDK Behaviour Spec** (`specVersion` 7). `CONFORMANCE.md` at the
repo root maps every rule id to the test that proves it and to that test's evidence tier.

Read the spec **from git, never from the website**:

```bash
cd ~/Documents/dev/langsys2 && git fetch origin
git show origin/main:docs/sdk-spec.mdx
```

Cite the commit SHA **and** the blob SHA of what you read. Rule ids are permanent; refer
to rules by bare id (`GATE-4`, `REG-6`), never by title or section — both move.

When you change behaviour a rule governs, update its row in `CONFORMANCE.md` in the same
change. A rule with no test is *not implemented* — that is a fact about this SDK, not a
documentation gap.

## Evidence norms

These are not style preferences; each one exists because a green suite certified a broken
state.

- **Red first.** Capture the failing run before implementing, so you know the test can fail.
- **Check the verifier.** Mutate the implementation and confirm the test goes red — and
  confirm it is the *named* test, not something incidental. A mutation that reddens the
  suite via an unrelated error has proven nothing about the rule you were testing.
- **A negative result needs a positive control.** "No requests were made" is only evidence
  if the same setup demonstrably makes requests when it should.
- **Shadow directions.** A rule with two failure directions needs a test for each. The one
  you would write from your own mental model is usually the one that already passes.
- **Mutate a copy, never the tree consumers import.** The Django and FastAPI wrappers install
  this core editable, so a mutation written into this tree is what their suites test while
  it is applied, and any count they take then is void. `_dev_/run_mutations.py` works in an
  isolated copy for that reason; an ad-hoc mutation belongs in a `git worktree`.
- **Discriminating vectors.** A test whose branches are indistinguishable cannot fail. If
  every CLDR category has the same expected text, the test proves nothing.

## Cross-SDK contract artifacts

Fixtures shared with other SDKs (`tests/fixtures/*.json`) are pinned by **git blob SHA**,
not by path. The blob is the check — content-addressed, verified locally with no network,
and it survives the source branch being deleted. A live ref records the provenance. One
string never does both jobs. Never hand-edit a vendored fixture; regenerate it from source
and re-pin.

## Local test stack

Unit tests: `pytest -m "not integration"`. Live tests need a local nova and are opt-in:

```bash
LANGSYS_API_URL=http://langsys2.test/api LANGSYS_PROJECT_ID=… \
LANGSYS_API_KEY=… LANGSYS_READ_KEY=… LANGSYS_IPWRITE_KEY=… \
pytest -m integration
```

The local stack runs with its queue workers **down** on purpose, so registration is
accepted and enqueued but never processed. Live registration tests assert HTTP acceptance
only; asserting catalog contents afterwards fails for reasons unrelated to this SDK.

## Gate before pushing

```bash
ruff check . && mypy src && pytest -m "not integration"
```

Python 3.9 is the floor and CI's lowest matrix entry: no `match`, no PEP 604 unions in
runtime positions (`ruff` omits `UP` deliberately for this reason). `mypy` runs strict.
