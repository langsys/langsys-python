# Conformance — `langsys-python`

| | |
|---|---|
| **SDK** | `langsys-python` (server core) |
| **Profiles** | `all`, `server` |
| **specVersion** | 7 |
| **Spec revision read** | langsys2 `docs/sdk-spec.mdx` blob `06ae105a0a1f` — read at `origin/main` `fabe22b2a54a` (2026-08-29T18:28:11Z) and re-read at `origin/main` `7bee50d63e78` (2026-08-30T04:22:01Z). **The blob is unchanged across both**, so every row below is filed against the same spec text |
| **SDK revision** | `feature/838_write_key_gating`, cut from `origin/main` `bc5ca62` |
| **Published** | **Never.** PyPI and TestPyPI both 404 (positive control: `httpx` → 200) |
| **Suite** | 306 tests in 17 files — 291 unit + 15 live (`pytest`, `pytest -m integration`) |
| **Binding rules** | **41 of 67** (`all` + `server`, after the GRANT ruling) |

**Per-rule revision column omitted, deliberately — fleet norm.** The rendered-section
hashes are served by `langsys://internal/docs/sdk-spec/revisions`, which no SDK lane can
reach. The document-level pin above is this file's provable revision claim. Omitted
rather than left pending, because a pending column invites someone to fill it with
strings they have not read — CONF-1's failure one level up.

**What surfaced while writing this.** Eight things that were on nobody's list. The two
that mattered most were found by review, not by me, and both are recorded first:

1. **A transient authorize failure destroyed the whole registration queue.** Found in
   review, not by me. Asking *whether we may write* could fail, that failure collapsed to
   "may not write", and the discard that is correct for a server **no** then ran for an
   outage — permanently, since nothing resends. The control that proves the hole is
   authorize-shaped: the identical outage on the registration POST retained the queue and
   backed off correctly. It also punctured this file's own GATE-2 reasoning twice over,
   and my REG-3 "never raises" test had been quietly enshrining the discard by asserting
   only that nothing was thrown.

2. **The miss that REG-6 saved was then stranded.** A debounce firing while a send held
   the lock had its timer cancelled by the declining flush, and nothing re-armed it. My
   first fix put the re-arm inside the send-lock's `try` — which the decline path returns
   before ever reaching, so it missed the one case it was written for. It is now in the
   outermost `finally`.


3. **The canonical fixture exists in two versions, and the authoritative one is
   unmerged.** php-sdk `origin/main` carries a 12-row `custom-id-reference.json` (blob
   `ed7b512b6c0a`); the 13-row file every SDK validates against (blob `60dc9b33ecfd`)
   lives only on `feature/838_write_key_gating_reland`. The extra row is exactly the
   `U+2028`/`U+2029` case, and main's copy also lacks the `codepoints` and
   `serialized_hex` columns the vendoring norm requires. The 12 shared rows agree
   byte-for-byte, so main is not wrong — it is missing the edge the CID rules exist to
   pin.
4. **`translate()` threw on an unreachable API.** Measured against a closed port, not
   inferred. On a server core that is a visitor-facing 500 on any page with a `t()`
   call, which is why it outranked the `custom_id` breakage in the fix order.
5. **GATE-3/GATE-4 was a landmine that would have armed itself with no change here.**
   `authorize()` cached the entire response, and `Project.raw` held it for the life of
   the client. Nothing leaked only because the server field did not yet exist in this
   repo's view.
6. **A live authorize answer was being dropped before it was recorded.** `authorize()`
   stripped `write_enabled` for storage (GATE-4, correct) but did so *before* anything
   observed it, so a stale catalog-envelope answer outranked a fresher authorize —
   the same latch shape from the other end. Caught by writing the second shadow-direction
   test, not by reading the code, and only because both directions were asserted.
7. **`Project.from_response` collapsed every non-`write` key type to `read`**, so an
   `ip_write` key reported as `read`. Found by a live test, not by reading: the gating
   logic reads the raw payload and was unaffected, so the defect was invisible to the
   mocked suite and to the code. `KeyType` now carries `ip_write` as its own arm.
8. **One of my own guards was non-discriminating.** The first ICU-5 vector gave
   `few`/`many`/`other` identical branch text and could not have failed whatever the
   renderer did. Recorded rather than quietly fixed, because it is the trap the spec
   names and it took writing the mutation to notice.

**Evidence grades** follow CONF-2: `live` (real API instance), `contract` (stateful
double), `mock` (canned responses — does not meet the bar), `n/a (pure)` for rules
provable without transport, `none`.

**On the `live` rows.** They run against a local nova (`langsys2.test`) with the seeded
per-SDK fixture project, which survives `migrate:fresh --seed` because every id and raw
key is a fixed constant. That makes them re-runnable on demand rather than a memory —
the CONF-2 bar — but they do require that stack, and they assert **HTTP acceptance
only** for registration: the local stack deliberately runs with its queue workers down,
so a POST is enqueued and never processed. Asserting catalog contents after a write
would fail for a reason that has nothing to do with this SDK.

**No row claims `contract`.** The shared stateful contract fixture does not exist —
CONF-2's own open item, which it says gates every claim in all 13 repos.

---

## Summary

Counted from the table below, not asserted beside it. 67 rules, every one accounted for.

| Status | Count | |
|---|---|---|
| `implemented` | 37 | |
| `partial` | 4 | GATE-6, GATE-7 (report direction is profile-vacuous), CONF-1, CONF-3 |
| `not implemented` | 0 | |
| `n/a` (architecture) | 0 | **GATE-2 vacated this bucket** — see below |
| `n/a` (profile) | 26 | browser/binding rules that do not apply to a server core |
| **total** | **67** | of which **41 bind** (`all` + `server`) |

The architecture-`n/a` bucket is empty and the fact is worth keeping, not deleting. It
held three rules across the program's waves and every one of them left it by becoming
**live**, not by staying true:

* REG-6 and REG-7 exited when REG-2's debounce added a timer thread — the "async twin"
  their expiry condition named, arriving through a side door.
* GATE-2 exited when review showed that a *failed* capability resolution is exactly the
  unknown the rule is about, so the row had never been vacuous at all.

Three for three. An architecture `n/a` is a claim about this SDK that expires when the
SDK changes, and on the evidence here it expires more often than it holds — which is the
argument for keeping it separate from the profile bucket, where nothing would have been
pointing at any of them.

## Status

| Rule | Status | Evidence | Test |
|---|---|---|---|
| GATE-1 | implemented | live | `test_gating` write-key-not-enabled + ip_write-enabled pair (the discriminating vector: `key_type` and `write_enabled` disagree) · `test_integration` all three live key types · mutation: branching on `key_type` reddens 7 tests |
| GATE-2 | implemented | mock | **No longer `n/a` — the row was wrong twice and this is the correction.** Wave 1 filed it `n/a (synchronous)`; wave 2 re-argued it as "the decision resolves synchronously inside the flush, so no window exists in which it is unknown". Review punctured that: a resolution that **fails** is unknown, and this SDK collapsed it to `False` and discarded the whole queue on a transient authorize blip — with the reason string misdiagnosing it as `not-write-enabled`, and no backoff, so recovery never resent it. `_resolve_write_enabled` now returns True/False/**None** and the flush **holds** on None: queue retained, backoff armed, honest reason. `test_registration_lane` GATE-2 block — held on unknown, still discarded on a server *no* (the half that makes this more than "never discard"), reason not misdiagnosed, survives to the recovering flush, `ip_write` protected specifically, `can_write` still refuses on unknown, explicit registration names ignorance rather than denial |
| GATE-3 | implemented | live | `test_gating` decision-not-latched-in-memory (`Project.raw`) + address-dependent-not-inherited-from-warm-store · `test_integration` GATE-4 cache read-back |
| GATE-1 (precedence) | implemented | mock | `test_gating` PRECEDENCE block — **both shadow directions**: a fresher envelope beats an older authorize, a fresher authorize beats an older envelope, and an *absent* flag never displaces a real answer. Precedence is by recency, never by source. Mutations: dropping the observe-before-strip in `authorize()` reddens a named test. **Correction:** an earlier revision of this file claimed the absent-flag-records-as-`False` mutation also did. It did not — under it all three original precedence tests passed, and the suite caught the mutant only incidentally, through a GRANT test erroring. The fourth test (`never_strips_a_real_yes`) was added to close that and *does* redden by name |
| GATE-4 | implemented | live | `test_gating` stripped-before-anything-is-cached (asserts `key_type` survives, `write_enabled` does not) · mutation: caching the decision reddens 3 tests |
| GATE-5 | implemented | mock | `test_gating` failed-registration-keeps-the-queue · clears-only-after-acceptance · no-persistent-marker-is-written. Structurally unreachable here: this SDK has no "already registered" store at all |
| GATE-6 | partial | mock | Register half gated and tested. Report half is **vacuous** — no report lane exists (HINT-2), so it cannot fail. Recorded partial rather than green |
| GATE-7 | partial | mock | Coverage holds: both detection paths feed the register lane. Report direction vacuous per HINT-2 |
| GATE-8 | implemented | mock | `test_gating` GATE-8 block: fallback only on genuine absence, never for `ip_write`/`read`, re-evaluated per response, **live-false-honoured-on-the-first-call** and **fallback-engages-only-on-true-absence** (the ordering inversion the Ruby lane hit), plus both warm-cache directions |
| CAT-1 | implemented | n/a (pure) | `test_translate` — key presence, not truthiness |
| CAT-2 | implemented | n/a (pure) | `test_translate` — presence decides registration, value decides display |
| CAT-3 | implemented | n/a (pure) | `test_translate` — a registered block is an object, not a null |
| REG-1 | implemented | live | Gated on the resolved decision, not the key type — see GATE-1 |
| REG-2 | implemented | mock | `test_registration_lane` — a five-miss burst coalesces into **one** POST; the debounce is a real send path with no explicit flush anywhere in the test; an explicit flush still sends immediately and cancels the timer. Control: with the debounce off nothing sends by itself. Mutation: dropping the scheduling reddens both |
| REG-3 | implemented | mock | The end-of-context flush is now **on by default** — opt-in was the shipping bug the rule names. The public manual flush is retained and required, because the automatic path is best-effort: a shutdown hook does not run on an OOM kill or hard timeout. `test_registration_lane` — registered by default, forces past an active backoff (last attempt, not a retry loop), never raises. Mutation: not forcing reddens the named test |
| REG-6 | implemented | mock | **No longer `n/a`.** REG-2's debounce added a timer thread, so the send now releases the lock across a slow POST. The batch is snapshotted **by key** and only the sent keys are removed, so a miss recorded mid-send survives. Mutation: restoring `clear_pending()` reddens the named test |
| REG-7 | implemented | mock | **No longer `n/a`**, same cause as REG-6. One send in flight, guarded before any costly work so a declining flush does not pay an authorize round-trip. A second flush arriving mid-send declines and keeps its queue. Mutation: disabling the guard reddens both named tests |
| REG-8 | implemented | mock | 3s → doubling → 300s ceiling, reset on first success (not gradually). `test_registration_lane` — while backing off **nothing is sent** while the queue keeps growing; the debounce reschedules past the backoff rather than waking into it. Mutations: no backoff, and a decaying reset, each redden named tests |
| REG-9 | implemented | mock | `test_gating` five-blocks-one-POST + batch-limit-comes-from-the-server (`[2,2,1]` at a server limit of 2) |
| REG-10 | implemented | live | `test_gating` skipped-write-is-not-success + failure-does-not-throw · `test_integration` read-key flush reports `success: False` |
| REG-11 | implemented | mock | Warns on `…` and `...`, naming the phrase, deduped per phrase. Suppresses **only** on the second signal — a longer catalog entry sharing the prefix — so `Loading…` still registers and a truncated paragraph does not. Mutations: blanket-skip and never-suppress each redden the opposite named tests |
| REG-12 | implemented | n/a (pure) | Structural, not string-shaped: object-ness decides. No 32-hex guard exists |
| REG-4, REG-5 | n/a (profile: browser) | n/a | No page teardown exists |
| HINT-2 | implemented | n/a (pure) | **No report lane exists.** Falsifiable and genuinely green — this SDK has a write lane it could have hung reporting off |
| HINT-1, 3–12 | n/a (profile: browser) | n/a | HINT-12's mirror confirmed backend-only (`ContentDiscoveryHintService`) |
| ICU-1 | implemented | n/a (pure) | `test_icu_recovery` — missing `select`/`plural` renders `other`; malformed (no `other`) left to normal handling; positive control that a supplied value still wins |
| ICU-2 | implemented | n/a (pure) | `test_icu_recovery` — null shares the absence branch |
| ICU-3 | implemented | n/a (pure) | `test_icu_recovery` — `#` prints `{n}` and never `0`; recursion into nested and into supplied branches |
| ICU-4 | implemented | n/a (pure) | `test_icu_recovery` — notice names every defaulted argument and the locale, deduped per `(template, locale)`, per-locale re-notice, and **silent when debug logging is off** |
| ICU-5 | implemented | n/a (pure) | `test_icu_recovery` — Polish `one`/`few`/`many` survive a co-present missing argument, on **distinct branch text**; the ICU-3 marker survives a present-but-null count. Mutation: routing a recovered template through the simplified renderer reddens 4 |
| CID-1 | implemented | n/a (pure) | `test_custom_id` — 13/13 fixture rows on **bytes** (`serialized_hex`), canonical JSON and hash, hashed through the same function the implementation uses |
| CID-2 | implemented | n/a (pure) | `test_custom_id` — enforced in the id function **and** at the callers (`client.translate_content_block`, `html/page.py`) |
| CID-3 | implemented | n/a (pure) | `test_custom_id` — both pipe-join spellings, deduped, never emitted; mutation: collapsing the slots to `['']` reddens the both-spellings test. `test_legacy_custom_id` — 20 vectors against the real JS implementation, plus the not-secretly-a-byte-hash property and both collision pairs. `test_registration` resolves a **live-observed** pre-correction id (`36efd6e4…`) |
| CID-4 | implemented | n/a (pure) | `test_custom_id` — a legacy hit with matching content attaches and does not re-register; a legacy hit with **differing** content is declined and registers cleanly |
| SSR-1–3 | n/a (profile: browser) | n/a | JS-specific; no Python analogue |
| BIND-1–6 | n/a (profile: binding) | n/a | This is a core. Django/FastAPI wrappers will carry these |
| GRANT-1–4 | n/a (profile: browser) | live | Per the Reviewer ruling the families table governs over the four `Profiles: all` lines. Posture is **affirmative non-participation**: `test_gating` and `test_integration` assert `X-Write-Grant` is never sent, with a failure message naming the removal condition |
| CACHE-1 | implemented | mock | `test_gating` — every key carries the project id; the catalog key carries the locale. PHP's unnamespaced `registered_items_<category>` has no analogue: that key does not exist here |
| OBS-1 | implemented | mock | One warning per client session when a write-capable key type resolves to not-write-enabled, keyed on the resolved decision rather than the key type. Re-armed by `reset_write_decision()`, so a new request boundary can surface a still-broken integration rather than staying silent for the process lifetime |
| WIRE-1 | implemented | n/a (pure) | `X-Authorization`, raw key, no `Bearer`; plus `X-Langsys-Capabilities: icu` |
| WIRE-2 | implemented | n/a (pure) | Unparseable/empty body becomes `{}` rather than raising |
| WIRE-3 | implemented | live | `test_gating` lowercase-on-the-wire + casing-variants-are-one-cache-entry + sentinel-never-sent · `test_integration` `es-ES` and `es-es` resolve identically on the deprecated route |
| WIRE-4 | implemented | live | `test_wire4_degradation` — 14 tests: connect/500/401/authorize-failure all degrade; **a failed fetch queues nothing**, each paired with a positive control proving the same call does queue on success; a failure is not cached as an empty catalog. Mutation: queueing on a failed fetch reddens 4 |
| WIRE-5 | implemented | n/a (pure) | Constructor `api_url` plus `LANGSYS_API_URL`; findable and redirectable to a double |
| CONF-1 | partial | — | The live rows assert on server acceptance and on values read back from a real instance. The mocked rows do not meet the bar and are graded accordingly rather than relabelled |
| CONF-2 | implemented | — | Grading adopted; every row carries a tier. No row claims `contract` — the shared fixture does not exist |
| CONF-3 | partial | — | Mutations recorded per rule across three waves — GATE-1, GATE-4, WIRE-4, ICU-5, CID-3 ×2, precedence ×2, byte-hash port, REG-2, REG-3, REG-6, REG-7, REG-8 ×2, REG-11 ×2, GATE-2, and the debounce re-arm — each verified to redden a **named** test, not merely to redden the suite. Still not systematic across every rule, and the `n/a` rows have nothing to mutate |

---

## Historical id space

**This SDK has no historical id space of its own and introduces no new shape to the
fleet.** One commit before this branch, no tags, no releases, and PyPI/TestPyPI both
404 against a 200 positive control — so its pre-correction `md5("|".join(…))` form
never produced a stored id. That form is in any case byte-identical to the PHP
pipe-join variants already on CID-3's authoritative list, across all three reachable
callers (sentinel / empty / category).

**CID-3's atomicity requirement was therefore free here.** The rule exists because one
SDK shipped the corrected hash five tagged releases before the tolerating half. There is
no earlier release to be out of step with, so the CID-1 form and the legacy read path
ship together in the first release by construction. The tolerating half is still
required — this SDK must resolve blocks registered by the *published* JS and PHP SDKs.

---

## Vendored fixture

| | |
|---|---|
| Source | `langsys-php-sdk` `tests/fixtures/custom-id-reference.json` |
| **THE CHECK — blob SHA** | `60dc9b33ecfd5fa3256fca7d36063ceb8ef1a00a` — content-addressed, verified locally with no network, survives deletion of the source branch |
| **THE PROVENANCE — live ref** | `langsys-php origin/feature/838_write_key_gating_reland` @ `8862841`+ |
| Cross-check | Byte-identical to the copy TypeScript vendored @ `6cdb388` (sha256 `28c03f42ffa6…`) |

The two are recorded as separate rows on purpose: when the PHP reland merges to `main`
the provenance updates and the check does not move, so no re-vendor is needed.

Assertion order in `tests/test_custom_id.py` is the contract: **codepoints first**
(a vendoring pipeline can normalise `U+2028` away, and a hash-first check would agree
with the damaged input), then **bytes**, then the **blob pin**, then the hash — computed
through `canonical_content_block_json`, the same function the id is hashed from, never a
second expression written inside the test.

### Tolerance breadth (CID-3)

**A tolerance function must not normalise its inputs.** Normalising is right for the id
you *emit* and exactly wrong for ids you *accept*, because the old paths did not
normalise — that asymmetry is the whole point of the function. `legacy_custom_ids`
therefore iterates the slots rather than folding them: an uncategorised block yields
**both** `''` and `'__uncategorized__'` spellings, deduped, and a real category yields
one. Mutation: collapsing the slots to `['']` — Ruby's bug — reddens the test that
asserts both spellings.

The scoping rule this follows: **legacy exposure scopes to whose ids you will read, not
whose you minted.** This SDK's unpublished history means it never *wrote* a legacy id;
it says nothing about what it must *resolve*.

**Ruling recorded — the JS code-unit shape does not bind the server profile** (fleet
precedent; pipe variants only). **This SDK retains it anyway**, deliberately, and the
deviation is recorded rather than silent:

- The ruling is permissive ("does not bind"), so retaining is conforming; and the same
  message's generalised rule — scope to *what published JS/PHP wrote into catalogs you
  will serve* — points at keeping it, since CID-1 states that **every id in production
  came from a published JS SDK**.
- The risk is asymmetric. Keeping it costs one extra dict lookup on a block miss and
  cannot mis-attach, because CID-4 verifies content before attaching. Removing it, if
  the premise is wrong, silently orphans every JS-registered block a Python server
  serves — and a false negative there is indistinguishable from "this block had no
  legacy id", which is the same silence that hid the original defect.

If the fleet wants strict scoping, deleting the two `_md5_utf16_code_units` lines from
`legacy_custom_ids` is the whole change; the tests that pin it are named in this file.

### The legacy JS hash — and its vector file

`legacy_custom_ids` includes the JS core's code-unit MD5, ported from
`langsys-js-typescript` `src/utils.ts` rather than from a description of it (which the
spec warns gets Latin-1 right and CJK, Cyrillic, Greek, Hebrew and Arabic wrong).

The spec says of the historical shapes: *"Call the exported functions; do not
reimplement them."* No non-JS lane can. The **blessed substitute is execution
validation** — generate vectors by running the reference implementation, pin them, and
pin the property that distinguishes a correct port from a plausible one.

| | |
|---|---|
| File | `tests/fixtures/legacy-custom-id-reference.json` (20 rows) |
| **THE CHECK — blob SHA** | `dc5556466dc54fe82e81ac9fdbf4549b2b76e7ce` |
| **THE PROVENANCE** | Generated by executing `md5Core` (`src/utils.ts`) and `generateLegacyCustomId` (`src/content-block.ts`) from `langsys-js-typescript` @ `6cdb388`, node v22 |
| Status | **Shared contract artifact.** Its home is `langsys-php/tests/fixtures/` beside `custom-id-reference.json`, landing at a later batch. **Lifters pin the blob SHA, not the path.** |

Coverage: Cyrillic, CJK, Greek, Hebrew, Arabic, non-BMP (surrogate pairs, which count as
two code units and shift the packing), `U+2028`/`U+2029`, both uncategorised spellings,
the untyped-caller `[null,…]` shape, and **both documented collision pairs, each under
its own scheme** — `["UI",["xxxA"]]` vs `["UI",["xxxŁ"]]` under the code-unit hash, and
`UI|Buy now` + `[]` vs `UI` + `Buy now` under the pipe join. The two collide under
different schemes and not under each other's, which is the concrete argument for CID-4.

*A note on building it:* the first draft used category `home` for the `xxxA`/`xxxŁ` pair
and documented **no** collisions, because the collision is a property of the final
hashed string — changing the category moves every character into different lanes. The
fixture asserted a property it did not contain until that was fixed.

`test_legacy_custom_id.py` pins the distinguishing property directly: the code-unit hash
must **agree** with a UTF-8 byte hash on ASCII and **diverge** above it. A port that
silently became a byte hash passes every ASCII row and fails there.

---

## Gaps, ranked by cost

Ranked by what the gap costs, not by rule order. Wave 2 closed every implementable
Python-side gap; what remains is E2E-dependent or lives in another repo.

1. **CONF-1 / CONF-2 / CONF-3 — evidence, not behaviour.** Most rows are `mock`-tier,
   which CONF-2 says does not count, and the shared stateful contract fixture that would
   let them count does not exist. Fleet-blocked, not Python-blocked. Mutation coverage
   (CONF-3) is now broad but still not systematic across every rule.
2. **GATE-6 / GATE-7 — half-vacuous by profile.** Their report direction cannot fail on a
   server core, so those rows carry less signal than their status suggests. Filed
   `partial` rather than green for exactly that reason.
3. **The Django/FastAPI wrapper obligations** — `reset_write_decision()` at request
   boundaries (GATE-3) and a request-end flush (REG-3). Declared below; neither can be
   discharged from this repo.

## Declared obligation — framework wrappers must reset at request boundaries

GATE-3 requires the write decision not to survive a single request, and says explicitly
that runtimes whose object graph outlives the request MUST reset it rather than rely on
process death. A `LangsysClient` held as a module-level singleton, a Django app config,
or a FastAPI dependency behind `lru_cache` is exactly that shape.

The core provides the seam — **`reset_write_decision()`** — and cannot call it itself,
because a library has no request lifecycle of its own. **The obligation is therefore
declared here and addressed to the Django/FastAPI wrapper wave:** each wrapper MUST call
it at the request boundary (Django `request_finished`, FastAPI middleware / dependency
teardown) and record that it does in its own conformance file.

**A second obligation joins it this wave, from REG-3.** The end-of-context flush is now
on by default, but on the server profile the automatic path is explicitly best-effort:
an `atexit` hook does not run on an OOM kill or a hard timeout, and unlike a browser
there is no later page in the same session to recover on. A wrapper MUST therefore also
**flush at the end of each request** rather than relying on process shutdown — the same
hooks, and `flush_pending()` is the public seam for it. Left to `atexit` alone, a
long-running server holds discovered phrases for its whole lifetime and loses them all
if it is killed rather than stopped.

A short-lived script, worker or CLI that builds a client per run needs no reset — there
the process *is* the boundary. The hazard is specifically the long-lived server, and it
fails in both directions: one allow-listed request would write-enable every anonymous
visitor on the host, and one anonymous request would make the allow-listed origin
silently register nothing.

Tested here: `test_gating` reset-clears-an-observed-answer, reset-does-not-disturb-
cached-metadata (a reset that dropped `key_type` would make every boundary a
round-trip), and reset-rearms-the-OBS-1-notice. Mutation: making the reset a no-op
reddens two.

## Known trade-off, recorded rather than discovered

On a **warm cache** the authorize payload lacks `write_enabled` by construction, because
GATE-4 strips it. Per the fleet ruling, plain `read`/`write` keys then fall back to
`key_type` — sound because the server guarantees `write_enabled ≡ key_type` for them —
while `ip_write` never falls back and pays a live authorize.

The consequence worth stating: a plain `write` key whose session is *not* write-enabled
would be answered `true` from a warm cache. That combination is excluded by the server
invariant, and it is honoured correctly on every live payload (`test_gating`
first-call-honours-a-live-false). An earlier draft of this branch asserted the stricter
behaviour — always re-ask — and that test was **replaced** when the ruling landed, not
deleted quietly. If the invariant ever weakens, this is the row to revisit.
