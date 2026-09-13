# Conformance — `langsys-python`

| | |
|---|---|
| **SDK** | `langsys-python` (server core) |
| **Profiles** | all, server |
| **specVersion** | 8.0.1 (committed, unpublished) |
| **Spec revision read** | langsys2 5cff03a17751e7dae9dcf1af52a9454d027c9006, docs/sdk-spec.mdx blob 5c5c0723f88fb8e6b13f58876c7adca8b6b35691 |
| **Re-derive** | `git -C ~/Documents/dev/langsys2 rev-parse 5cff03a17751e7dae9dcf1af52a9454d027c9006:docs/sdk-spec.mdx` — by commit, never by branch. Re-derived at this write, not carried from the previous header (v8 blob `b657b490…`) |
| **SDK revision** | `feature/838_write_key_gating`, on `c79fd57`; cut from `origin/main` `bc5ca62` |
| **Runtime measured** | CPython 3.9.6 · lxml 6.1.2 · libxml2 2.14.6. The pyproject floor is `lxml>=4.9`, so an older wheel can bring a libxml2 below 2.14 — see *Parser model* |
| **Published** | **Never.** PyPI and TestPyPI both 404 (positive control: `httpx` → 200) |
| **Suite** | 578 tests in 21 files — 563 unit (560 pass, 3 strict xfail pinning measured gaps) + 15 live |
| **Binding rules** | **53 of 79** (`all` + `server`) |
| **Checked by** | `_dev_/conformance_counts.py` (accounting, vocabulary, header) · `_dev_/run_mutations.py` (CONF-3) |

**Per-rule revision column omitted, deliberately — fleet norm.** The rendered-section
hashes are served by `langsys://internal/docs/sdk-spec/revisions`, which no SDK lane can
reach. The document-level pin above is this file's provable revision claim. Omitted
rather than left pending, because a pending column invites someone to fill it with
strings they have not read — CONF-1's failure one level up.

**What surfaced while writing this (the 8.0.1 re-row).** None of it was on anyone's list:

1. **Three register/lookup pairs disagreed, each invisible to a test that took one
   route.** Attribute and button values were registered normalised and looked up raw; the
   page `<title>` and metas were registered raw where the body registered normalised text.
   A value with a whitespace run, a no-break space or `%name%` missed forever and
   re-registered on every render. Fixed, and every lookup site is now pinned by a test
   that goes red when the site reverts — including the `trim()` revert the TS lane found in
   its own attribute and `<option>` lookups.
2. **Trimming was a second TOK-2 site.** `str.strip()` removed U+0085 and kept U+FEFF after
   the collapse was already correct. The mutation record then showed the FEFF trim test
   could not tell the two trims apart — the collapse has already turned an edge run of
   members into one space, which either trim removes — so the discriminating vector is a
   U+0085 edge.
3. **The page path is 19/26 on the shared canonicalization fixture.** Measured on request:
   the seven attribute rows diverge, for two causes — a top-level void or inline element is
   never tokenized, and a leaf host's own attributes are dropped — and top-level bare text,
   links, `<textarea>` and `<select>` register nothing. Ruby and PHP fail the same seven.
   Not changed: the registration shape is with the operator.
4. **SRV-3's order of events does not hold, and this file said it did.** The previous row
   read "with the debounce on the send lands later from another thread" — true, and not the
   rule. The Django lane measured the 0.4s debounce POSTing a long render's misses before
   the response existed; the FastAPI lane measured one request's end-of-request flush
   draining another in-flight request's misses with no timer at all. Both are reproduced
   here as strict xfails. The fix is a core seam, and whether to build it is the operator's
   call.
5. **REG-12's two checks disagree.** `translate()` treats text equal to a block id as known
   (presence); `sync()` flattens the block into its children and treats the same text as
   new, so it re-registers on every call. Found while writing REG-12's first named test.
   Measured and pinned, not fixed.
6. **Seven rows claimed `implemented` with no named test** although the behaviour was
   present: CAT-3, REG-12, HINT-2, WIRE-1, WIRE-2, WIRE-5, and OBS-1's diagnostic (only the
   flag behind it was asserted). A rule with no test is not implemented; each now has tests
   and a mutation.
7. **Mutating every runtime row caught five tests of mine that could not fail as named.**
   The new CAT-3 vector was ASCII, where the legacy code-unit hash equals the current id, so
   the legacy lookup rescued the mutated path. The ICU-2 select tests pass whether null
   counts as missing or as an unmatched value — both land on `other`. The WIRE-3 cache test
   relied on "a second fetch would raise", which does not hold on this pytest-httpx. And
   MARK-2's identity host was tested in one spelling per path. Each vector was fixed, or the
   expectation re-pointed at the test that does discriminate, and re-run.
8. **The tier column graded the wrong thing.** Per the corrected fleet guidance, a tier
   describes the evidence for the property the rule governs. GATE-4, GATE-8, the REG timing
   rules, TOK, MARK, CACHE-1, OBS-1, SRV-1 and SRV-2 are in-process properties no stateful
   double could prove better, and are `n/a (pure)`; WIRE-1 is `live`. Five rows remain
   `provisional`, each because the shared contract fixture would actually change its
   evidence.

**Earlier waves (v7, v8).** Eight things that were on nobody's list. The two that mattered most were found by review, not by me, and both are recorded first:

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

**Evidence tiers** follow CONF-2 as corrected fleet-wide: a tier describes the evidence for
the property the rule governs, not whether a double appears in the test. `live`, `contract`
and `mock` apply only where that property depends on what the API answers — acceptance,
refusal, or state across calls. `n/a (pure)` covers in-process behaviour, cross-implementation
identity fixtures (never `contract`), the meta-rules CONF-2 and CONF-3, artifact inspection
with a positive control, and isolation or scoping properties a stateful fixture could neither
prove nor disprove. A `provisional` row names what the shared contract fixture would change.

**On the `live` rows.** They run against a local nova (`langsys2.test`) with the seeded
per-SDK fixture project, which survives `migrate:fresh --seed` because every id and raw key
is a fixed constant — re-runnable on demand, which is the CONF-2 bar. They assert **HTTP
acceptance only** for registration: the local stack runs with its queue workers down on
purpose, so a POST is enqueued and never processed, and asserting catalog contents after a
write would fail for a reason unrelated to this SDK.

**No row claims `contract`.** The shared stateful contract fixture does not exist.

---

## Summary

Counted by `_dev_/conformance_counts.py`, which exits non-zero on a rule id unaccounted for,
unknown or claimed twice, a range where one id belongs, a word outside the vocabulary, a
status/tier pairing the vocabulary forbids, or a header naming a different blob. Run it
rather than trusting this table.

| Status | Tier | Count | Rules |
|---|---|---|---|
| implemented | live | 6 | GATE-1, REG-1, REG-10, WIRE-1, WIRE-3, WIRE-4 |
| implemented | n/a (pure) | 34 | GATE-3, GATE-4, GATE-8, CAT-1–3, REG-2, REG-3, REG-6, REG-7, REG-11, HINT-2, ICU-1–5, CID-1–4, SRV-1, SRV-2, SRV-5, CACHE-1, OBS-1, WIRE-2, WIRE-5, TOK-1, TOK-5, MARK-1, MARK-2, CONF-2, CONF-3 |
| provisional | mock | 5 | GATE-2, GATE-5, REG-8, REG-9, CONF-1 |
| partial | — | 5 | GATE-7, REG-12, SRV-3, TOK-3, TOK-4 |
| held (strip ruling) | — | 1 | TOK-2 |
| n/a (architecture) | — | 2 | GATE-6, SRV-4 |
| n/a (profile) | — | 26 | REG-4, REG-5, HINT-1, HINT-3–12, SSR-1–3, BIND-1–6, GRANT-1–4 |
| **total** | | **79** | 53 bind (`all` + `server`) |

**Not green, and each blocker has an owner.** `provisional` ×5 waits on the CONF-2 shared
contract fixture (fleet). `held` ×1 waits on the strip ruling (operator). Of the five
`partial` rows, GATE-7, TOK-3 and TOK-4 wait on the registration-shape ruling (operator),
SRV-3 on whether the request-scope seam is built (operator), and REG-12 on a go-ahead for a
one-line fix. `delegated` is a binding status and appears on no row of a core.

## Status

| Rule | Status | Tier | Evidence |
|---|---|---|---|
| GATE-1 | implemented | live | `test_integration::test_GATE1_the_write_key_is_write_enabled`, `…_the_read_key_is_not_write_enabled`, `…_an_ip_write_key_is_write_enabled_from_an_allow_listed_address`, `…_write_enabled_is_on_both_endpoint_shapes`, against the local nova. Discriminating unit vector, `key_type` and `write_enabled` disagreeing: `test_gating::test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register` and `…_an_ip_write_key_that_is_write_enabled_does_register`; recency precedence in both directions (`test_gating::test_PRECEDENCE_*`). Mutation: `client.py::_decide`, the `isinstance(flag, bool)` branch disabled so `key_type` decides → both discriminating tests red |
| GATE-2 | provisional | mock | `test_registration_lane` GATE-2 block: an authorize failure HOLDS the queue and arms backoff; a server *no* still discards; the held queue is accepted by the recovering flush (`…_the_queue_survives_to_the_recovering_flush`); `ip_write` protected. Asserts on queue state and acceptance, never on the call. **Waits on: CONF-2 shared contract fixture** — a double that fails authorize, recovers, and holds what it accepted would show the held phrase landing on a second read. Mutation: `client.py::_flush_locked`, `decision = self._resolve_write_enabled() is True` (unknown collapsed to False) → `test_GATE2_a_transient_authorize_failure_holds_the_queue` red |
| GATE-3 | implemented | n/a (pure) | Scoping, which a stateful fixture could neither prove nor disprove: `test_gating::test_GATE3_the_decision_is_not_latched_in_memory_either`, `…_an_address_dependent_decision_is_never_inherited_from_a_warm_store`, `…_reset_write_decision_clears_an_observed_answer`, `…_reset_does_not_disturb_cached_project_metadata`. Mutations: `client.py::reset_write_decision` keeps `_observed_decision` → the reset test red; `client.py::authorize` builds `Project` from the unstripped payload → the not-latched test red. The request-boundary reset is a wrapper obligation, **not yet discharged** — see *Declared obligations* |
| GATE-4 | implemented | n/a (pure) | Artifact inspection with a positive control: `test_gating::test_GATE4_write_enabled_is_stripped_before_anything_is_cached` asserts `key_type` survives in the cache and `write_enabled` does not. Live corroboration: `test_integration::test_GATE4_the_decision_never_reaches_the_cache`. Mutation: `client.py::_without_write_decision` returns the payload unchanged → named test red |
| GATE-5 | provisional | mock | `test_gating::test_GATE5_a_failed_registration_keeps_the_queue` (a 500 leaves the queue), `…_the_queue_clears_only_after_the_server_accepts`, `…_no_persistent_registered_marker_is_written` — state after refusal and after acceptance; this SDK has no "already registered" store. **Waits on: CONF-2 shared contract fixture** — the second read the rule names, observing whether the first write landed. Mutation: `client.py::_flush_locked`, the failure branch calls `clear_pending()` → `…_keeps_the_queue` red |
| GATE-6 | n/a (architecture: no report lane exists, per HINT-2; live if one is ever added) | - | The register half is REG-1's gate. The report half cannot fail because nothing here reports — HINT-2's test proves that against a double willing to accept a hint |
| GATE-7 | partial | n/a (pure) | **Registration half, every entry point:** `translate()` (`test_wire4_degradation::test_CONTROL_translate_queues_a_miss_when_the_catalog_fetch_succeeds`), `translate_content_block` (`test_html::test_translate_content_block_queues_when_missing`, `test_wire4_degradation::test_CONTROL_content_block_queues_when_the_catalog_fetch_succeeds`), page-path blocks and phrases (`test_spec_801::test_SRV5_*`). No path can feed both lanes: there is no report lane (HINT-2). Mutations: each of the three queue calls (`client.py::translate`, `client.py::translate_content_block`, `page.py::_apply_or_queue_block`) replaced by `pass` → its named test red. **Partial: the page path leaves measured content feeding NEITHER lane** — a top-level void or inline element and its attributes, a leaf block host's own translatable attributes, and top-level bare text, links, `<textarea>` and `<select>` (see *Page path, measured*). Held with TOK-3 and TOK-4 on the registration-shape ruling |
| GATE-8 | implemented | n/a (pure) | Interpreting an absent field is in-process. `test_gating` GATE-8 block: fallback only on true absence, never for `ip_write` or `read`, re-evaluated per response, a live `false` honoured on the first call, both warm-cache directions. Mutations: `client.py::_decide` infers for `ip_write` → `test_GATE8_absent_flag_is_never_inferred_for_a_non_plain_write_key` red; the warm-cache `ip_write` branch allowed the fallback → `test_GATE8_ip_write_on_a_warm_cache_is_false_when_the_server_omits_the_field` red |
| CAT-1 | implemented | n/a (pure) | `test_translate::test_null_value_falls_back_to_base_and_not_missing` (present-with-null is not a miss), `…_absent_phrase_is_missing`; `test_client::test_translate_hit_and_fallbacks` (null not queued, absent queued). Mutation: `translate.py::resolve`, `if phrase in cat` → `if cat.get(phrase)` → the null test red |
| CAT-2 | implemented | n/a (pure) | `test_translate::test_empty_value_falls_back_to_base` — present, not a miss, and displays the source rather than `""`. Mutation: `translate.py::resolve` drops `raw != ""` → named test red |
| CAT-3 | implemented | n/a (pure) | `test_translate::test_CAT3_a_registered_untranslated_block_is_known_not_missing` — a block whose inner phrases are null is not queued again and displays the source; control: an absent block queues. Non-ASCII vector, because for ASCII the legacy code-unit hash equals the current id and masks the defect. Mutation: `translate.py::lookup_block` requires a non-null inner value → named test red |
| REG-1 | implemented | live | `test_integration::test_REG10_a_read_key_flush_reports_failure_rather_than_success` — the real server answers `write_enabled: false` for the read key and the flush returns `skipped` without sending. Unit: `test_gating::test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register` (no POST reaches the double). Mutation: `client.py::_flush_locked`, `if decision is False` → `if False` → named unit test red |
| REG-2 | implemented | n/a (pure) | In-process timing. `test_registration_lane::test_REG2_a_burst_of_misses_becomes_one_request`, `…_the_debounce_is_a_real_send_path_not_just_a_helper`, `…_a_declining_flush_leaves_a_timer_armed`; control: with the debounce off nothing sends by itself. Mutations: `client.py::_schedule_flush` returns early → both burst tests red; the re-arm in `flush_pending`'s outer `finally` removed → the re-arm test red. The debounce is also what sends mid-render on a long server render — recorded on SRV-3 |
| REG-3 | implemented | n/a (pure) | The automatic flush is on by default and forced past backoff; the public `flush_pending()` stays because the automatic path is best-effort. `test_registration_lane::test_REG3_*`. Mutations: `client.py::__init__` stops registering `_auto_flush` → `…_the_end_of_context_flush_is_registered_by_default` red; `_auto_flush` passes `force=False` → `…_forces_past_an_active_backoff` red. The per-request flush is a wrapper obligation |
| REG-4 | n/a (profile: browser) | - | No page teardown exists |
| REG-5 | n/a (profile: browser) | - | No page teardown exists |
| REG-6 | implemented | n/a (pure) | `test_registration_lane::test_REG6_a_miss_recorded_during_a_send_is_not_dropped`. Mutation: `client.py::_flush_locked` clears the live queue instead of the sent keys → named test red |
| REG-7 | implemented | n/a (pure) | `test_registration_lane::test_REG7_only_one_send_is_in_flight_at_a_time`, `…_declining_keeps_the_queue_for_the_next_flush`. Mutation: `client.py::__init__`, `_sending = threading.Semaphore(2)` → both red |
| REG-8 | provisional | mock | A refused send keeps the queue and arms 3s → doubling → 300s; nothing sends while backing off; the first accepted send resets it. `test_registration_lane::test_REG8_*`. The timing is in-process; the property that matters — a refused batch eventually lands — depends on what the server answers. **Waits on: CONF-2 shared contract fixture** (refuse, accept, then a second read showing the retried batch). Mutations: backoff guard disabled → `…_while_backing_off_nothing_is_sent` red; reset decays by half → `…_backoff_resets_on_the_first_success` red; ceiling removed → `…_the_backoff_doubles_and_stops_at_the_ceiling` red |
| REG-9 | provisional | mock | **Acceptance-shaped:** `test_gating::test_REG9_a_double_that_refuses_oversized_batches_ends_up_holding_every_item` — a local double refuses any batch over the server's limit and keeps what it accepted; the assertion is on what it ends up holding. Also `…_the_batch_limit_comes_from_the_server` and `…_content_blocks_are_batched_into_one_post`. Every path: the phrase flush, `register_phrases`, and the block flush that page-path blocks share. **Waits on: CONF-2 shared contract fixture**, which enforces the real limit. Mutations: `registration.py::Registrar.__init__` hardcodes 200 → both limit tests red; `client.py::_flush_locked` posts blocks one at a time → the batching test red |
| REG-10 | implemented | live | `test_integration::test_REG10_a_read_key_flush_reports_failure_rather_than_success`; unit `test_gating::test_REG10_*` (a skip is not success, an empty queue is an honest success, a transport failure does not throw). Mutations: the not-write-enabled result reports `success: True` → `…_a_skipped_write_is_not_reported_as_success` red; the send catches `ApiError` only → `…_flush_does_not_throw_when_registration_fails` red |
| REG-11 | implemented | n/a (pure) | `test_registration_lane::test_REG11_*` — warns naming the phrase, still registers without a second signal, suppresses only when a longer catalog entry shares the prefix, deduplicated. Mutations: `client.py::_ellipsis_suppresses` returns `False` → the suppression test red; returns `True` → the still-registers test red |
| REG-12 | partial | n/a (pure) | **Translate path, structural:** `test_translate::test_REG12_a_nested_map_is_a_content_block_never_a_missing_phrase`, `…_a_phrase_shaped_like_a_hash_is_still_a_phrase`. Mutations: `translate.py::resolve` treats a nested map as missing → the first red; a 32-hex shape test added → the second red. **Partial — the sync path disagrees, measured:** text equal to a block id is *known* to `translate()` and *new* to `sync()`, whose `_existing_keys` flattens a block into its children and never records the block's own key, so `sync()` re-registers it on every call. Pinned by strict xfail `test_translate::test_REG12_presence_and_structure_agree_on_the_sync_path`. The fix is one line in `client.py::_existing_keys`; not built, awaiting a go-ahead |
| HINT-1 | n/a (profile: browser) | - | Reports begin at `window.location`; a server SDK never reports (HINT-2) |
| HINT-2 | implemented | n/a (pure) | `test_server_render::test_HINT2_a_server_sdk_never_reports_across_a_whole_render[False]` and `[True]` — `translate`, `translate_content_block`, `translate_page` and a flush, against a double that **will** accept a hint: no request reaches a hint or discovery path, with positive controls that the render reached both the catalog and authorize. Mutation: `client.py::_flush_locked` posts `discovery/hint` when the session cannot write → `[False]` red |
| HINT-3 | n/a (profile: browser) | - | |
| HINT-4 | n/a (profile: browser) | - | |
| HINT-5 | n/a (profile: browser) | - | |
| HINT-6 | n/a (profile: browser) | - | |
| HINT-7 | n/a (profile: browser) | - | |
| HINT-8 | n/a (profile: browser) | - | |
| HINT-9 | n/a (profile: browser) | - | |
| HINT-10 | n/a (profile: browser) | - | |
| HINT-11 | n/a (profile: browser) | - | |
| HINT-12 | n/a (profile: browser) | - | Its server mirror is the langsys backend (`ContentDiscoveryHintService`), not a server SDK |
| ICU-1 | implemented | n/a (pure) | `test_icu_recovery::test_ICU1_*` — a missing `select` or `plural` renders `other`; a node with no `other` is left to normal handling; a supplied value still wins. Mutation: `interpolate.py::_recover` finds no `other` branch → `…_missing_select_argument_renders_the_other_branch` and `…_missing_plural_argument_renders_the_other_branch` red |
| ICU-2 | implemented | n/a (pure) | `test_icu_recovery::test_ICU5_the_ICU3_marker_survives_a_present_but_null_count` and `…_a_plain_argument_that_is_null_stays_visible` are the vectors that discriminate; `test_ICU2_*` are select-shaped and pass whether null counts as missing or as an unmatched value, since both land on `other`. Mutation: `interpolate.py::_render_arg` treats null as supplied → both discriminating tests red |
| ICU-3 | implemented | n/a (pure) | `test_icu_recovery::test_ICU3_*` — `#` in a recovered plural prints `{n}`, never `0`; recursion into nested and supplied branches. Mutation: `interpolate.py::_recover` passes `hash_literal=None` → `test_ICU3_hash_in_a_recovered_plural_prints_the_argument_name` red |
| ICU-4 | implemented | n/a (pure) | `test_icu_recovery::test_ICU4_*` — one debug notice naming every defaulted argument and the locale, deduplicated per `(template, locale)`, again per locale, silent when debug logging is off. Mutations: `interpolate.py::interpolate` skips `_notice_recovery` → `…_emits_a_notice_naming_the_argument_and_the_locale` red; `_notice_recovery` drops its dedup → `…_deduplicates_per_template_and_locale` red |
| ICU-5 | implemented | n/a (pure) | `test_icu_recovery::test_ICU5_*` — Polish `one`/`few`/`many` survive a co-present missing argument on distinct branch text; the ICU-3 marker survives a present-but-null count. Mutation: `interpolate.py::interpolate` routes a recovered template through `_simple` → `test_ICU5_missing_select_does_not_degrade_the_supplied_plural` red |
| CID-1 | implemented | n/a (pure) | Cross-implementation identity fixture `custom-id-reference.json` (blob `60dc9b33…`): 13/13 rows on codepoints, bytes (`serialized_hex`), canonical JSON and hash, through the function the implementation hashes. Mutations: `registration.py::canonical_content_block_json` with default separators → `test_custom_id::test_CID1_serialized_bytes_match_the_fixture` and `…_custom_id_matches_the_fixture` red; with `ensure_ascii=True` → `…_the_line_terminator_row_is_present_and_raw` red |
| CID-2 | implemented | n/a (pure) | `test_custom_id::test_CID2_*`, enforced in the id function and at its callers. Mutation: `registration.py::_hash_category` hashes the sentinel → `test_CID2_none_and_the_sentinel_and_empty_all_hash_as_empty` red |
| CID-3 | implemented | n/a (pure) | `test_custom_id::test_CID3_*`; `test_legacy_custom_id` — 20 vectors executed against the real JS implementation (`legacy-custom-id-reference.json`, blob `dc555646…`); `test_registration` resolves a live-observed pre-correction id. Mutations: `registration.py::legacy_custom_ids` offers one uncategorised slot → `…_uncategorised_offers_both_historical_spellings` and `test_legacy_custom_id::test_both_uncategorised_spellings_are_offered_on_lookup` red; a byte hash where the code-unit hash belongs → `…_the_js_code_unit_form_is_offered_for_ascii_and_non_ascii` red; `Registrar._content_block_item` emits a legacy id → `…_the_current_form_is_the_only_one_ever_emitted` red |
| CID-4 | implemented | n/a (pure) | `test_custom_id::test_CID4_*` — a legacy hit with matching content attaches and does not re-register; with differing content it is declined and registers cleanly. Mutation: `translate.py::lookup_block` skips the content check → `…_a_legacy_id_whose_content_differs_is_declined` red |
| SSR-1 | n/a (profile: browser) | - | The families table assigns SSR to the browser SDK's module instance under server rendering |
| SSR-2 | n/a (profile: browser) | - | As SSR-1 |
| SSR-3 | n/a (profile: browser) | - | As SSR-1 |
| SRV-1 | implemented | n/a (pure) | Serving a held catalog is in-process: `test_server_render::test_SRV1_the_served_output_carries_the_request_locale_translation`, and a phrase absent from the same render emits base and queues. Live corroboration: `test_integration::test_real_translation` serves the seeded `es-es` catalog. Mutation: `client.py::translate` resolves against `{}` → named test red |
| SRV-2 | implemented | n/a (pure) | Isolation: `test_server_render::test_SRV2_concurrent_locales_do_not_observe_each_others_catalog` (two concurrent renders, widened interleave), `…_no_process_global_holds_per_request_translation_state`. Mutation: `client.py::translate` holds the fetched catalog on the instance across a 50ms window → the concurrent test red |
| SRV-3 | partial | live | **Read-only half:** `test_integration::test_REG10_a_read_key_flush_reports_failure_rather_than_success` (the real server's `false`, nothing sent); unit `test_server_render::test_SRV3_a_read_only_key_pushes_nothing` with a write key on the same render as the positive control. **Render-call half:** `…_collection_does_not_happen_on_the_render_call` (the double can accept a registration, so an inline flush would show), `…_the_send_happens_off_the_render_call`. Mutations: `client.py::_queue_missing` flushes inline → both render-call tests red; `if decision is False` disabled → the read-only test red. **The order of events fails, measured twice:** a render that outlasts the 0.4s debounce has its misses POSTed before the response exists (Django lane; reproduced as strict xfail `…_a_miss_is_not_sent_before_a_render_longer_than_the_debounce_has_responded`, events `posted, rendered, response-returned`); and with no timer, one request's flush drains another in-flight request's misses (FastAPI lane; strict xfail `…_one_requests_flush_does_not_send_another_in_flight_requests_misses`, events `quick-response, posted [Held miss, Quick miss], held-response`). The core seam is the operator's call — *Held and awaiting a decision*. No wrapper row exists: `langsys-python-django` and `langsys-python-fastapi` carry no CONFORMANCE.md yet |
| SRV-4 | n/a (architecture: terminal-HTML server SDK, no hydration hand-off; live if this SDK ever emits a client seed) | - | `translate_page()` returns a finished page and nothing hydrates against it; 8.0.1 scopes the rule to SDKs in a hydration hand-off |
| SRV-5 | implemented | n/a (pure) | **Once-per-subtree half, measured:** `test_spec_801::test_SRV5_a_depth_3_nested_phrase_is_registered_exactly_once` and `…_block_…` count registration CALLS, because the queue is a dict and would hide duplicates. **Fail-loudly half:** n/a on mechanism — no component model, so no `lazy`/`Suspense` child exists to capture. Mutation: `page.py::_walk` calls `_translate_leaf` twice → both tests red |
| BIND-1 | n/a (profile: binding) | - | This is a core; the Django and FastAPI wrappers carry the BIND rows |
| BIND-2 | n/a (profile: binding) | - | As BIND-1 |
| BIND-3 | n/a (profile: binding) | - | As BIND-1 |
| BIND-4 | n/a (profile: binding) | - | As BIND-1 |
| BIND-5 | n/a (profile: binding) | - | As BIND-1 |
| BIND-6 | n/a (profile: binding) | - | As BIND-1 |
| GRANT-1 | n/a (profile: browser) | - | A server SDK holds a write key. Affirmative non-participation is tested: `test_gating::test_GRANT_no_write_grant_header_is_ever_sent`, `test_integration::test_no_write_grant_header_is_ever_sent` |
| GRANT-2 | n/a (profile: browser) | - | As GRANT-1 |
| GRANT-3 | n/a (profile: browser) | - | As GRANT-1 |
| GRANT-4 | n/a (profile: browser) | - | As GRANT-1; `X-Write-Grant` is never sent |
| CACHE-1 | implemented | n/a (pure) | `test_gating::test_CACHE1_every_key_is_namespaced_by_project` (with a control that something was cached), `…_the_catalog_key_carries_the_locale`. No registered-items key exists. Mutations: `catalog.py::CatalogStore._key` drops the project id → the first red; drops the locale → the second red |
| OBS-1 | implemented | n/a (pure) | `test_gating::test_OBS1_an_unusable_capability_is_surfaced_once_not_per_miss` asserts on the emitted WARNING record across three misses and flushes; control `…_a_write_enabled_session_is_not_reported_unusable`; `test_GATE3_reset_rearms_the_obs1_notice`. Mutations: `client.py::_notice_unusable_capability` logs at debug → named test red; its once-guard removed → named test red |
| WIRE-1 | implemented | live | Every `test_integration` test authenticates against the real API with `X-Authorization` alone, `test_authorize` first. Unit: `test_wire::test_WIRE1_every_request_authenticates_with_the_x_authorization_header` — raw key, no scheme, no cookie, not in the URL, on authorize, catalog and registration. Mutation: `http.py::HttpClient.__init__` sends `Authorization` → named test red |
| WIRE-2 | implemented | n/a (pure) | `test_wire::test_WIRE2_an_empty_204_is_a_success_not_a_parse_error` (a zero-length 204 on registration clears the queue); control `…_an_empty_error_body_is_still_a_failure`. Mutation: `http.py::HttpClient._send` re-raises the parse error → named test red |
| WIRE-3 | implemented | live | `test_integration::test_locale_casing_resolves_to_the_same_entry` — the deprecated route resolves `es-ES` as `es-es` against the real catalog. Unit: lowercase on the wire, casing variants one fetch and one stored key, the sentinel never sent. Mutations: `catalog.py::CatalogStore.get` sends the locale as given → `test_gating::test_WIRE3_the_locale_goes_on_the_wire_lowercase` red; `_key` and `get` both key by the raw casing → `…_casing_variants_are_one_cache_entry_not_two` red |
| WIRE-4 | implemented | live | `test_integration::test_WIRE4_an_unreachable_api_degrades_rather_than_throwing` (a closed port); `test_wire4_degradation` — 12 tests, each failure shape paired with a success control that does queue. Mutations: `client.py::translate` queues off a failed fetch → `…_a_failed_fetch_queues_nothing` red; `catalog.py::_fetch` catches `ApiError` only → `…_translate_degrades_to_the_source_phrase` red; `CatalogStore.get` caches a failed fetch → `…_a_failed_fetch_is_not_cached_as_an_empty_catalog` red |
| WIRE-5 | implemented | n/a (pure) | `test_wire::test_WIRE5_*` — `LANGSYS_API_URL` redirects and a request **arrives** at the double; an explicit `api_url` wins; the base is read at construction, so a later environment change has no effect and there is no setter to call too late; the README documents both. Mutation: `config.py::Config.resolve` ignores the variable → `…_the_environment_redirects_the_base_and_a_request_arrives_there` red |
| TOK-1 | implemented | n/a (pure) | **Every path:** block extraction (fixture rows `style-subtree`, `script-subtree`, `noscript-subtree`, `math-subtree`, `svg-inline-icon`); the page path, the same rows through `translate_page`, all matching; a standalone page-level svg (`test_spec_801::test_TOK1_a_standalone_svg_is_tokenized_on_the_page_path`); the simple-phrase route (`…_svg_only_text_on_the_simple_phrase_route_renders_in_place`); a declared block (`…_svg_inside_a_declared_block_is_tokenized`); apply, translating svg text in place with `<path>` intact (`…_inline_svg_translates_in_place_with_its_path_intact_on_a_real_render`, `…_a_standalone_svg_translates_in_place_with_its_path_intact`); the spec's own document (`…_the_spec_document_yields_exactly_the_ordinary_phrase`). **`<template>` is load-bearing on lxml:** on `<div><template><p>x</p></template><p>y</p></div>` the exclusion yields `['y']`, and removing it yields `['x', 'y']` (`…_template_exclusion_is_load_bearing_on_lxml`). Mutations: `parser.py::SKIP_TAGS` without script, style and noscript; without template; without math → each named test red; the `page.py::_walk` svg branch disabled → both standalone tests red; svg added to `BLOCK_ELEMENTS`, the retracted mechanism → `…_inline_svg_on_the_page_path_yields_the_same_tokens` red |
| TOK-2 | held (strip ruling) | n/a (pure) | **Implemented for the enumerated set, on every path.** Membership asserted on `normalize_whitespace` directly: U+FEFF collapses and trims; U+0085, U+180E, U+200B and U+2060 survive the collapse and the trim; the real members still collapse (`test_spec_801::test_TOK2_*`). The trim is a second site and uses the same set. Fixture rows `nbsp-in-text`, `feff-in-text`, `nel-in-text`, `mvs-in-text` and `line-separators` match on the block AND page path, and register/lookup pairs normalise identically (CONF-1). **Held:** CPython's class also collapses U+001C–U+001F and JavaScript's does not; they are pinned at today's behaviour (`…_HELD_strip_ruling_characters_are_pinned_at_todays_behaviour`) and VT/FF are unchanged, pending the ruling on stripping C0 controls. The parser split is its own row, labelled libxml2 2.14.6 (*Parser model*). Mutations: U+FEFF dropped → the FEFF tests and `feff-in-text` red; U+0085 added → the non-member tests and `nel-in-text` red; `str.strip()` for the enumerated trim → `…_non_members_survive_trimming[U+0085]` red; JavaScript's set adopted wholesale → the HELD pins red |
| TOK-3 | partial | n/a (pure) | **Block path:** the twenty-seven in order (`test_canonicalization::test_TOK3_*`), and all seven fixture attribute rows match. **Page path: the same seven rows diverge**, measured — a top-level void or inline element is never tokenized, and a leaf block host's own attributes are dropped (`<p title="Tooltip">Hello</p>` registers `Hello` only). Not changed: whether a host's attributes belong inside its token sequence or as separate attribute phrases is with the operator as a spec gap. Mutations (block path): the first two attributes swapped → the order test red; `data-bs-title` dropped → `test_tokens_match_the_fixture[attr-new-data-bs-title]` red |
| TOK-4 | partial | n/a (pure) | **Block path:** attribute interiors collapse exactly as text nodes (`test_canonicalization::test_TOK4_*`; fixture `attr-multiline`, `attr-nbsp`). **Lookups agree on block and page apply:** `test_spec_801::test_CONF1_attribute_lookup_on_the_block_path` and `…_on_the_page_path`, each over `alt` and `placeholder` × line break, doubled space, NBSP. **The page path does not register** the `attr-multiline` and `attr-nbsp` rows — same cause, same held ruling as TOK-3. Mutation: `parser.py::_walk_extract` trims attribute values without collapsing → `test_TOK4_attribute_values_collapse_internal_whitespace_like_text_nodes` and `attr-multiline` red |
| TOK-5 | implemented | n/a (pure) | Interpolation accepts both forms (`test_canonicalization::test_TOK5_*`, including the parameter-injection guard). **Capture normalises `%name%` to `{name}` before the id, on every path**, through one function, `normalize_phrase`: text, attributes (`test_spec_801::test_TOK5_percent_form_in_an_attribute_is_captured_as_the_brace_form`), the page leaf, title and meta; lookup uses the same key (`…_capture_and_lookup_agree_so_the_translation_is_found`); prose percents are untouched. Fixture rows `percent-name-in-markup` and `brace-name-in-markup` share one id on the block AND page path. Mutations: `interpolate.py::percent_placeholders_to_braces` returns its input → the capture tests and `percent-name-in-markup` red; `_rewrite_percent_slots` returns the template → `test_TOK5_both_placeholder_forms_interpolate_the_same_argument` red |
| MARK-1 | implemented | n/a (pure) | Block path (miss and hit), page path, declared block; the expectation re-derived by running the tokenizer over the same subtree; stamping leaves the markup verbatim and honours quotes and comments (`test_canonicalization::test_MARK1_*`). Mutations: `client.py::translate_content_block` returns an unstamped miss → `…_an_untranslated_block_is_still_stamped` red; `page.py::_apply_or_queue_block` stops stamping → `…_page_rendered_blocks_are_stamped_too` red |
| MARK-2 | implemented | n/a (pure) | Both spellings on read, for phrase hosts and block hosts, on every reader: the tokenizer (block path, extract and apply with the excision mirrored) and the page walker (block-level hosts and identified blocks), classified by one shared function. `test_canonicalization::test_MARK2_*`, each spelling on each reader. Mutations, one per reader × spelling (eight): e.g. the tokenizer reads only `data-ls-contentblock` → `…_a_nested_content_block_host_is_left_alone_on_the_block_path[data-langsys-contentblock]` red; the page walker reads only `data-ls-phrase` → `…_a_block_level_phrase_host_is_excised_by_the_page_walker[host-is-the-block]` red; opt-out values read as identities → `…_classified_three_ways_on_the_page_path` red. **A contested cell is recorded, and it is not a MARK-2 question:** what the bare attribute means (*Held and awaiting a decision*) |
| CONF-1 | provisional | mock | **Assertion shape:** every row whose property depends on what the API answers has an acceptance- or state-shaped test — the live rows (GATE-1, REG-1, REG-10, WIRE-1, WIRE-3, WIRE-4) on the real server; GATE-2, GATE-5 and REG-8 on queue state after refusal and acceptance; REG-9 on what a refusing, stateful local double ends up holding. What is missing is only a shared double that can refuse. **Every path:** the TOK and MARK rows name each path proven on, and every register/lookup pair is pinned — attribute (raw and `trim()` reverts), button value, text node and `<option>`, the page leaf key, title, meta, and a head miss leaving authored text. Mutations: nine, one per pair site (`parser.py::_walk_apply`, `_translate_text`, `text_content`; `page.py::_process_head`, `_translate_meta`), each reddening its named test. **Waits on: CONF-2 shared contract fixture** |
| CONF-2 | implemented | n/a (pure) | Every row carries a tier from the vocabulary, graded by the property the rule governs rather than by whether a double appears in the test. `_dev_/conformance_counts.py` checks the vocabulary, the status/tier pairing, one rule id per row and this file's header blob, and exits non-zero on any of them. No row claims `contract`: the shared fixture does not exist |
| CONF-3 | implemented | n/a (pure) | Every row whose proof requires running something names its tests and a specific mutation — file, symbol, the exact text replaced and its replacement — in `_dev_/run_mutations.py`, the passing halves of `partial` rows included. **Last run: 97/97 caught across 49 rules, each by its named tests**, counted from the full output. Nothing to run for the `n/a` rows, CONF-2 or this row |

---

## Page path, measured

Every row of the shared canonicalization fixture run through `translate_page`, reading what
is actually queued. Re-run with `python3 _dev_/measure_page_path.py`. **19 of 26 rows match;
the seven attribute rows diverge.** The block path matches all 26 (`test_canonicalization`).

| Fixture row | Expected tokens | Page path registers |
|---|---|---|
| `attr-multiline` | `['A long description']` | nothing |
| `attr-nbsp` | `['A long description']` | nothing |
| `attr-original-15` | `['Your name']` | nothing |
| `attr-new-data-confirm` | `['Are you sure?', 'Go']` | nothing — the button's text is lost too |
| `attr-new-data-bs-title` | `['Tip', 'x']` | nothing — the span's text is lost too |
| `attr-order-two-on-one-element` | `['Alt', 'Tip']` | nothing |
| `attr-all-new-twelve` | 13 tokens, twelve attributes then `x` | `['x']` — the host's attributes are dropped |

The 19 that match: `nbsp-in-text`, `nbsp-vs-plain-space`, `text-multiline`, `style-subtree`,
`script-subtree`, `noscript-subtree`, `math-subtree`, `svg-inline-icon`, `line-separators`,
`non-bmp`, `slashes`, `category-empty`, `category-sentinel`, `multi-token`, `feff-in-text`,
`nel-in-text`, `mvs-in-text`, `percent-name-in-markup`, `brace-name-in-markup`.

**Two causes.** A top-level void or inline element is never tokenized, because the walker
recurses into anything that is not a block element and reads neither its attributes nor its
text; and a leaf block host's own translatable attributes are dropped, because the leaf hands
only its inner HTML to the tokenizer. The same probe, for the shapes the fixture does not
carry:

| Markup directly under `<body>` | Page path registers | The same inside a leaf block |
|---|---|---|
| `<textarea>Write here</textarea>` | nothing | `Write here` |
| `<select><option>First choice</option>…</select>` | nothing | `First choice` |
| `<a href="/x">Read more</a>` | nothing | `Read more` |
| `Loose body text` | nothing | — |
| `<p title="Tooltip">Hello</p>` | `Hello` — the title is dropped | — |

**Not changed.** Whether a host's attributes belong inside its element's token sequence or
register as separate attribute phrases decides the ids, so it is with the operator as a spec
gap. Ruby and PHP fail the same seven rows.

## Block apply path, measured

Asked because a fleet SDK substituted translations by POSITION in a second walk that skipped
neither marked nor excluded subtrees. Every token translated to `[token]`, through
`translate_content_block`, `apply_block_translations` and `translate_page`. Re-run with
`python3 _dev_/measure_block_apply.py`. **All three paths render identically, and every
translation lands on its own node**; the stamp is omitted below.

| Vector | Rendered |
|---|---|
| `<p><img alt="Hi there"> Body text</p>` | `<p><img alt="[Hi there]"> [Body text]</p>` |
| `<p>Before <button value="Go">Click</button> after</p>` | `<p>[Before] <button value="[Go]">[Click]</button> [after]</p>` |
| `<p><input type="submit" value="Send"> Tail</p>` | `<p><input type="submit" value="[Send]"> [Tail]</p>` |
| `<p><img alt="A" title="T"> Body</p>` | `<p><img alt="[A]" title="[T]"> [Body]</p>` |
| `<p>Lead <img alt="Pic"> Trail</p>` | `<p>[Lead] <img alt="[Pic]"> [Trail]</p>` |
| `<p>Intro <span data-ls-phrase>Marked phrase</span> outro</p>` | `<p>[Intro] <span data-ls-phrase>Marked phrase</span> [outro]</p>` |
| `<p>Intro <span translate="no">Kept</span> outro</p>` | `<p>[Intro] <span translate="no">Kept</span> [outro]</p>` |
| control `<p>One <b>Two</b> Three</p>` | `<p>[One] <b>[Two]</b> [Three]</p>` |

Apply looks every node up by its own normalised text and walks with the same skip and
excision rules as extraction, so no positional shift is possible. The trade-off is that two
nodes with identical text always receive the same translation — which is what one phrase id
means anyway.

## Parser model — libxml2 2.14.6

Rows here pin **parser** behaviour, not this SDK's code, so no mutation applies; each is
labelled with the libxml2 version and skips below 2.14 (`needs_214` in `test_spec_801`),
because the behaviour moves when a runtime crosses 2.14.

- **Parse model — 7/7 agree with the JS family** on tokens and block ids (category `UI`),
  vectors from the PHP fixture blob `741c8cfc7f49dc0eb3242afa30a307880771d9aa` (`langsys-php-sdk`):
  `test_spec_801::test_PARSE_MODEL_agrees_with_the_js_family_on_libxml2_2_14`.
- **C0 controls in DOM text.** libxml2 2.14.6 keeps U+0001–U+0008, U+000B, U+000C and
  U+000E–U+001F, normalising CR to LF, and leaves them to the collapse set. Before 2.14 the
  parser drops them, so this SDK's id for such content moves when its runtime libxml2 crosses
  2.14 — on a dependency bump nobody edited. Collapse membership is therefore asserted on the
  collapse function, never through the DOM. Held with the strip ruling.
- **Residuals, measured and pinned:** raw NUL and `&#0;` become U+FFFD; a lone CR and CRLF
  become LF; raw C0 and `&#x1C;` are kept in attribute values
  (`test_RESIDUAL_parser_text_normalisation_on_libxml2_2_14`,
  `test_RESIDUAL_c0_is_kept_in_attributes_on_libxml2_2_14`).
- **Bytes input.** Markup handed over as `bytes` with no charset declared decodes as Latin-1:
  the UTF-8 bytes of U+0085 arrive as U+00C2 U+0085
  (`test_RESIDUAL_bytes_without_a_charset_decode_as_latin1`). Pass `str`.
- **`<template>` is load-bearing.** lxml puts template children in the ordinary tree; see
  TOK-1 for the vector.

## Held and awaiting a decision

Nothing below is built. Each is either a ruling that is not this lane's to make, or new scope.

| Decision | Rows | Whose call | In the code today |
|---|---|---|---|
| Stripping C0 controls: U+001C–U+001F, VT, FF, and the libxml2 split | TOK-2 | operator | U+001C–U+001F still collapse, one tuple (`parser.py::_HELD_C0_SEPARATORS`) |
| Registration shape for a host's own attributes and for top-level void or inline elements | TOK-3, TOK-4, GATE-7 | operator (spec gap) | page path unchanged; 19/26 |
| A request-scope send seam for SRV-3 | SRV-3 | operator — whether it is built | debounce and queue are process-wide; two strict xfails |
| What a bare `data-ls-contentblock` means | — (noted on MARK-2) | operator | `attributes.py::BARE_BLOCK_ATTRIBUTE = "opt-out"`; PHP reads it as a declaration; one line flips it |
| `sync()` presence vs structure | REG-12 | operator go-ahead | a one-line fix in `client.py::_existing_keys`; strict xfail |
| The shared stateful contract fixture | GATE-2, GATE-5, REG-8, REG-9, CONF-1 | fleet | none exists |

**On the bare attribute.** The value classifier was built to walk an opt-out *and* a bare
attribute as ordinary content, as prescribed in an earlier review. The fleet's boolean-marker
convention treats presence as intent — PHP reads a bare content-block attribute as a
declaration — so that one cell is contested and routed to the operator. It is isolated so the
ruling flips one line. The identity class is not contested: an id value is excised on every
path.

---

## Design choice — a nested declaration inside a fragment is folded, not split

`translate_content_block` is handed a fragment and asked to translate it as **one**
block, so a `data-ls-contentblock="1"` on a descendant is **folded into the enclosing
block** rather than becoming a block of its own. The page walker, which has a document
and somewhere for sub-blocks to live, does make it its own block. Measured:

| path | `<div><div data-ls-contentblock="1"><p>Hello</p><p>Second</p></div><p>Bye</p></div>` |
|---|---|
| `translate_content_block` | one block, `['Hello','Second','Bye']` |
| `translate_page` | block `['Hello','Second']` + phrase `Bye` |

Honouring the declaration on the fragment path would make a single call produce several
registrations under ids the caller never sees and cannot address. **Page parity is the
coherent alternative** and would be new scope rather than a fix.

*Corrected here:* an earlier revision of this row, and the commit that introduced the
shared classifier, said the block path honours a nested declaration "the same way the
page walker does". It does not — it classifies the value the same way and then folds.
Both are strictly better than the behaviour before the classifier, which excised the
subtree and lost its content; the overstatement was in the description, not the code.

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

## Vendored fixtures

All are pinned by **git blob SHA, not by path** — content-addressed, verified locally with no
network, surviving deletion of the source branch. A live ref records provenance; one string
never does both jobs.

| Fixture | Blob (the check) | Provenance (a live ref) |
|---|---|---|
| `tests/fixtures/custom-id-reference.json` | `60dc9b33ecfd5fa3256fca7d36063ceb8ef1a00a` | `langsys-php origin/feature/838_write_key_gating_reland @ 8862841+` |
| `tests/fixtures/canonicalization-reference.json` | `1ae7bc2900c073085ae3ebbf1f81cd37c81d553b` | `langsys-js-typescript` `4eac870` — derive with `git -C ~/Documents/dev/langsys-js-typescript rev-parse 4eac870:tests/fixtures/canonicalization-reference.json` |
| `tests/fixtures/legacy-custom-id-reference.json` | `dc5556466dc54fe82e81ac9fdbf4549b2b76e7ce` | generated by executing `langsys-js-typescript` `md5Core` @ `6cdb388` |

**The canonicalization fixture is authored in langsys-js-typescript** by the JS core as its
owner, 26 rows. It declares the spec it was authored against as blob `8e2527b9…` at langsys2
`63df13c7` — an ancestor of the revision this file is filed against (`5cff03a1`, blob
`5c5c0723`), with TOK-1, TOK-2 and TOK-5 text changed in between. The basis is recorded rather
than assumed equal: every row still agrees with the target text on this SDK's block path, and
19 of 26 on its page path.

### CID fixture

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

Ranked by what the gap costs, not by rule order.

1. **SRV-3 — a server render can collect before its response, and one request drains
   another's misses.** Registration POSTs land on the visitor's request path whenever a render
   outlasts 0.4s or a concurrent request flushes, in every wrapper built on this core. Needs a
   core seam; whether to build it is the operator's decision.
2. **The wrappers bypass GATE-2 and GATE-3 in the shape customers deploy.** Django and FastAPI
   discard the queue whenever `can_write` is not true — an unknown answer included — and never
   call `reset_write_decision()`. A transient authorize failure loses the request's
   discoveries, and an allow-listed request's answer can outlive it. The fix lives in those
   repositories.
3. **Page-path coverage — GATE-7, TOK-3, TOK-4.** On the page entry point, attribute text on
   top-level elements and on leaf hosts, and top-level link, textarea, select and bare text,
   register nothing, and seven shared fixture rows derive ids no other SDK agrees with. Waits
   on the registration-shape ruling.
4. **CONF-2's shared contract fixture.** Five rows are `provisional` for want of a double that
   can refuse and holds state. Fleet-blocked.
5. **REG-12 on `sync()`.** Text equal to a block id re-registers on every sync. Rare; one line.
6. **TOK-2's held C0 characters.** Content carrying U+001C–U+001F derives a different id than
   the JS family, and on a pre-2.14 libxml2 a different one again. Rare; waits on the strip
   ruling.

## Declared obligations — framework wrappers

GATE-3 requires the write decision not to survive a single request, and says explicitly that
runtimes whose object graph outlives the request MUST reset it rather than rely on process
death. A `LangsysClient` held as a module-level singleton, a Django app config, or a FastAPI
dependency behind `lru_cache` is exactly that shape.

The core provides the seam — **`reset_write_decision()`** — and cannot call it itself, because
a library has no request lifecycle of its own. Each wrapper MUST call it at the request
boundary (Django `request_finished`, FastAPI middleware or dependency teardown) and record that
it does in its own conformance file.

**REG-3 adds a second obligation.** The end-of-context flush is on by default, but on the
server profile the automatic path is best-effort: an `atexit` hook does not run on an OOM kill
or a hard timeout. A wrapper MUST also flush at the end of each request — `flush_pending()` is
the public seam — rather than rely on process shutdown.

A short-lived script, worker or CLI that builds a client per run needs no reset; there the
process is the boundary.

Tested here: `test_gating::test_GATE3_reset_write_decision_clears_an_observed_answer`,
`…_reset_does_not_disturb_cached_project_metadata` and `…_reset_rearms_the_obs1_notice`.

**Measured in the wrappers and routed back here.** Neither obligation is discharged yet, and
one wrapper pattern defeats a rule this core meets:

- `langsys-python-django` `src/langsys_django/middleware.py:67–70` (@`34a6a87`) and
  `langsys-python-fastapi` `src/langsys_fastapi/middleware.py:92–95` (@`29bb650`) call
  `flush_pending()` only when `client.can_write` is true, and `clear_pending()` otherwise.
  `can_write` collapses an **unknown** answer to `False` on purpose — never infer permission
  from a failure to ask — so a transient authorize failure discards the request's discoveries
  in the wrapper, and the GATE-2 hold this core implements never runs. A wrapper should call
  `flush_pending()` unconditionally after the response and let the core choose the lane: it
  holds on unknown and discards on a server *no*.
- Neither wrapper calls `reset_write_decision()` at the request boundary.
- **SRV-3 needs a core seam before any wrapper can meet it.** Stated as behaviour: a miss
  recorded inside a request scope is sent by no flush — timer or explicit, this request's or
  another's — until that scope's own response has been flushed; a miss recorded outside any
  request scope (Celery, management commands, scripts) keeps REG-2's debounce; the binding
  only marks the scope and flushes after its response, since BIND-3 forbids it timers. The
  contract is an order-of-events test with two concurrent requests, ordered by events rather
  than sleeps — the shape of the second SRV-3 xfail here.

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
