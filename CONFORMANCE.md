# Conformance — `langsys-python`

| | |
|---|---|
| **SDK** | `langsys-python` (server core) |
| **Profiles** | all, server |
| **specVersion** | 8.2.16 (committed, unpublished) |
| **Spec revision read** | langsys2 2dce7f411c38f642a5c41129a5d8a362adc023c1, docs/sdk-spec.mdx blob 99c86b55de39f7d45cf9c25d931d210953cff8be |
| **Re-derive** | `git -C ~/Documents/dev/langsys2 rev-parse 2dce7f411c38f642a5c41129a5d8a362adc023c1:docs/sdk-spec.mdx` — by commit, never by branch; re-derived at this write |
| **SDK revision** | `feature/838_write_key_gating`; cut from `origin/main` `bc5ca62` |
| **Runtime measured** | CPython 3.9.6 · lxml 6.1.2 · libxml2 2.14.6 · Node 22.13.1 (contract double) |
| **Published** | **Never.** PyPI and TestPyPI both 404 (positive control: `httpx` → 200) |
| **Suite** | 1004 tests in 30 files — 989 unit (contract rows included) + 15 live |
| **Binding rules** | **84 of 113** — every rule not `n/a (profile)` |
| **Checked by** | `_dev_/conformance_counts.py` (accounting, vocabulary, header) · `_dev_/run_mutations.py` (CONF-3, in an isolated copy) |

**Per-rule revision column omitted** — optional since 8.2.1; the blob above is this file's
revision claim.

**What surfaced while writing this (the 8.2 push).** None of it was on anyone's list:

1. **`translate_page` and `translate_content_block` raised on any markup carrying a C0
   control.** lxml parses those characters but refuses to assign them, and the apply step
   wrote every text node back whether it changed or not. The shared fixture's `fs-in-attr`
   row exposed it the moment the page path ran it. The apply step now writes only
   translated values, stripped.
2. **The formatter put raw ICU syntax, or an empty string, on the page.** An unsupported
   argument type or unbalanced braces printed the construct verbatim; a plural or select
   with no branch for its value rendered `""`. ICU-6's fallback now renders them. The shared
   ICU-6 vector itself renders natively here.
3. **The fallback masked broken recovery.** With ICU-1/2's recovery mutated away, the
   lenient fallback still produced the right text, so no output test could see it. What
   separates them is the warning: recovery is normal and silent, a formatter failure warns.
   Pinned both ways.
4. **Snapshot formats diverged across the fleet.** PHP and Ruby wrote different formats with
   different checksums, so neither loaded the other's. Raised from this lane; SNAP-1 now pins
   one format and one canonical checksum, and this SDK writes and reads it.
5. **A mutation runner that reads test names up to the first space cannot see a
   parametrized case whose id has one.** Every parametrized case a mutation names now carries
   an explicit id.
6. **Four of my new vectors could not fail as first written**: a `t()` conversion vector
   already in Langsys syntax, two page-walker spelling mutations aimed at code the TOK-6
   rewrite made dead, and a MARK-4 page case the walker reaches by recursion rather than
   through the helper under test. Each was replaced or re-pointed and re-run.

**Evidence tiers** follow CONF-2: a tier describes the evidence for the property the rule
governs. `contract` rows run against the shared contract double — `contract-fixture/` from
langsys-js-typescript, vendored byte-exact (tree `542f57f5`), started once per test file —
and assert on a status the SDK reports or on accepted state read back, never on what was
sent. Where a row turns on holding back, the capability drifts after the SDK has learned it
must not act, and a control acts in the drifted world. `live` rows run against a local nova
(`langsys2.test`) with the seeded per-SDK project; they assert HTTP acceptance only for
registration, because the local stack runs its queue workers down. `n/a (pure)` covers
in-process behaviour, cross-implementation vector files, meta-rules, and isolation
properties.

---

## Summary

Counted by `_dev_/conformance_counts.py`, which exits non-zero on a rule id unaccounted for,
unknown or claimed twice, a range where one id belongs, a word outside the vocabulary, a
status/tier pairing the vocabulary forbids, or a header naming a different blob.

| Status | Tier | Count |
|---|---|---|
| implemented | n/a (pure) | 57 |
| implemented | contract | 15 |
| implemented | live | 6 |
| not implemented | — | 1 (MIG-9: waits on the 907 endpoint) |
| n/a (architecture) | — | 5 (GATE-6, SRV-4, MSG-9, MSG-10, MSG-12) |
| n/a (profile) | — | 29 |
| **total** | | **113** |

**Not green by one row**, MIG-9, whose endpoint arrives with the 907 merge. No row is
`provisional`, `partial` or held.

## Status

| Rule | Status | Tier | Evidence |
|---|---|---|---|
| GATE-1 | implemented | live | `test_integration::test_GATE1_*` against the local nova, all three key types. Contract: `test_contract::test_GATE1_an_allow_listed_ip_write_session_registers` (key type says no, server computes yes) and `…_a_session_told_no_sends_nothing_even_once_the_world_would_accept` (drift: the allow-list widens after the SDK learned no; control acts in the drifted world). Mutations: `client.py::_decide` ignores the flag → both unit and contract presence tests red; `_flush_locked` sends despite a server no → the drift test red |
| GATE-2 | implemented | contract | `test_contract::test_GATE2_an_unknown_answer_holds_the_queue_until_the_server_can_say_yes` — an authorize fault holds the queue; the recovering flush lands it in accepted state. Unit: `test_registration_lane::test_GATE2_*`. Mutation: `client.py::_flush_locked` collapses unknown to False → both red |
| GATE-3 | implemented | n/a (pure) | Scoping a stateful fixture cannot observe: `test_gating::test_GATE3_*` (not latched in memory, not inherited from a warm store, `reset_write_decision()` clears it and keeps metadata). Mutations: the reset keeps the answer; `Project` built from the unstripped payload → named tests red. The request-boundary reset is the wrappers' (*Declared obligations*) |
| GATE-4 | implemented | n/a (pure) | Artifact inspection with a control: `test_gating::test_GATE4_write_enabled_is_stripped_before_anything_is_cached`; live corroboration `test_integration::test_GATE4_the_decision_never_reaches_the_cache`. Mutation: `_without_write_decision` returns the payload → red |
| GATE-5 | implemented | contract | `test_contract::test_GATE5_a_refused_send_is_not_recorded_as_done_and_lands_on_the_second_read` — a refused send leaves no state and keeps the queue; the retry lands. No "already registered" store exists (`test_gating::test_GATE5_no_persistent_registered_marker_is_written`). Mutation: the failure branch clears the queue → red |
| GATE-6 | n/a (architecture: no report lane exists, per HINT-2; live if one is ever added) | - | The register half is REG-1's gate; the report half cannot fail because nothing here reports (HINT-2's test proves that against a double willing to accept a hint) |
| GATE-7 | implemented | contract | `test_contract::test_GATE7_every_entry_point_lands_its_misses` — `translate`, `translate_content_block` and `translate_page` each land in accepted state. The page path's units cover top-level void and inline elements and a leaf's own attributes (TOK-6); nothing it detects feeds neither lane, and nothing can feed two (no report lane). Mutations: each of the four queue sites replaced by `pass` → named tests red |
| GATE-8 | implemented | contract | `test_contract::test_GATE8_a_legacy_server_falls_back_to_key_type_for_the_plain_write_arm`, `…_absence_is_never_permission_for_ip_write` (the double would accept, so the empty state is evidence). Unit: `test_gating` GATE-8 block. Mutations: inference for `ip_write`, and the warm-cache fallback for it → red |
| GATE-9 | n/a (profile: browser) | - | A locale gate on a browser session's misses. A server render is GATE-10's producer instead |
| GATE-10 | implemented | n/a (pure) | **Producing half** (the server profile's): `test_request_locale::test_GATE10_a_render_off_the_base_locale_marks_its_root_resolved`; the base-locale render and an unknowable base stay unmarked. The reading half is browser/binding. Mutations: never mark; mark every render → named tests red |
| CAT-1 | implemented | n/a (pure) | `test_translate::test_null_value_falls_back_to_base_and_not_missing`, `…_absent_phrase_is_missing`. Mutation: truthiness instead of presence → red |
| CAT-2 | implemented | n/a (pure) | `test_translate::test_empty_value_falls_back_to_base`. Mutation: display `""` → red |
| CAT-3 | implemented | n/a (pure) | `test_translate::test_CAT3_a_registered_untranslated_block_is_known_not_missing` (non-ASCII, where the legacy hash cannot rescue a broken lookup). Mutation: a null-valued block reads as unknown → red |
| REG-1 | implemented | n/a (pure) | A never-attempt clause, proven at the transport seam: `test_gating::test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register`; live corroboration `test_integration::test_REG10_a_read_key_flush_reports_failure_rather_than_success`. Mutation: send despite `decision is False` → red |
| REG-2 | implemented | n/a (pure) | `test_registration_lane::test_REG2_*` — a burst is one request; the debounce is a real send path; a declining flush re-arms it. Mutations: never schedule; no re-arm → red |
| REG-3 | implemented | n/a (pure) | `test_registration_lane::test_REG3_*` — on by default, forced past backoff, releases request-scoped misses (`test_server_render::test_SRV3_an_unended_scope_holds_its_misses_until_the_shutdown_flush`). Mutations: not registered; not forced; held misses left behind → red |
| REG-4 | n/a (profile: browser) | - | No page teardown exists |
| REG-5 | n/a (profile: browser) | - | No page teardown exists |
| REG-6 | implemented | n/a (pure) | `test_registration_lane::test_REG6_a_miss_recorded_during_a_send_is_not_dropped`. Mutation: clear the live queue → red |
| REG-7 | implemented | n/a (pure) | `test_registration_lane::test_REG7_*`. Mutation: two sends in flight → red |
| REG-8 | implemented | contract | `test_contract::test_REG8_nothing_is_sent_while_backing_off_though_the_server_would_accept` — the fault is consumed by the first send, so an in-backoff send would land; state stays empty, and the forced control lands. The clock lives on the client, which a server process holds across requests, one per project. Unit: `test_registration_lane::test_REG8_*` (3s doubling to 300s, reset on success). Mutations: no backoff; decaying reset; no ceiling → red |
| REG-9 | implemented | contract | `test_contract::test_REG9_the_double_enforces_the_limit_and_holds_every_item` — the double refuses over-limit batches (422); every item lands. Mutations: hardcode 200; one POST per block → red |
| REG-10 | implemented | live | `test_integration::test_REG10_a_read_key_flush_reports_failure_rather_than_success`; contract `test_contract::test_REG10_a_read_only_flush_is_reported_as_failure` with a write-key control. Mutations: a skip reported as success; a transport failure escapes → red |
| REG-11 | implemented | n/a (pure) | `test_registration_lane::test_REG11_*`. Mutations: never / always suppress → opposite tests red |
| REG-12 | implemented | n/a (pure) | `test_translate::test_REG12_*` — a nested map is a block on the translate path, a hash-shaped phrase is a phrase, and `sync()` agrees with `translate()` about text equal to a block id. Mutations: structure lost; 32-hex shape test; sync forgets the block's own key → red |
| REG-13 | implemented | n/a (pure) | `test_cache2::test_REG13_a_miss_is_not_decided_before_the_first_catalog_read_settles` — the first read delayed past three debounce windows; the candidate set stays empty. Met by construction: the catalog is read before any miss is decided. Mutation: record the miss before the read → red |
| HINT-1 | n/a (profile: browser) | - | |
| HINT-2 | implemented | n/a (pure) | `test_server_render::test_HINT2_a_server_sdk_never_reports_across_a_whole_render[False]`/`[True]` — against a double that would accept a hint, no hint or discovery request is made, with controls that the render reached catalog and authorize. Mutation: post a hint when the session cannot write → red |
| HINT-3 | n/a (profile: browser) | - | |
| HINT-4 | n/a (profile: browser) | - | |
| HINT-5 | n/a (profile: browser) | - | |
| HINT-6 | n/a (profile: browser) | - | |
| HINT-7 | n/a (profile: browser) | - | |
| HINT-8 | n/a (profile: browser) | - | |
| HINT-9 | n/a (profile: browser) | - | |
| HINT-10 | n/a (profile: browser) | - | |
| HINT-11 | n/a (profile: browser) | - | |
| HINT-12 | n/a (profile: browser) | - | Its server mirror is the langsys backend, not a server SDK |
| HINT-13 | n/a (profile: browser, binding) | - | |
| ICU-1 | implemented | n/a (pure) | `test_icu_recovery::test_ICU1_*`, `test_ICU1_ICU2_recovery_is_normal_and_never_raises_the_formatter_failure_warning` (a node with no `other` recovers through ICU-6 to the visible `{name}`); shared vectors `test_interpolation_vectors` (blob `017bffdd`). Mutation: `_recover` finds no `other` → the silence test red |
| ICU-2 | implemented | n/a (pure) | `test_icu_recovery::test_ICU1_ICU2_…[null-count]`, `test_ICU5_a_plain_argument_that_is_null_stays_visible`; the vectors' null rows. Mutation: null counts as supplied → red |
| ICU-3 | implemented | n/a (pure) | `test_icu_recovery::test_ICU3_*`. Mutation: `#` left unreplaced → red |
| ICU-4 | implemented | n/a (pure) | `test_icu_recovery::test_ICU4_*` — debug-only, deduplicated per `(template, locale)`. Mutations: silent; not deduplicated → red |
| ICU-5 | implemented | n/a (pure) | `test_icu_recovery::test_ICU5_*`. Mutation: a recovered template through the simple renderer → red |
| ICU-6 | implemented | n/a (pure) | The shared vector renders natively (`test_interpolation_vectors`, both rows; `test_icu_recovery::test_ICU6_control_the_shared_vector_renders_natively_and_warns_nothing`). **Forced failures** (`test_ICU6_a_phrase_the_formatter_cannot_render_goes_through_branch_selection[*]`): an unsupported type, unbalanced braces, no branch for the value, a nested missing value — branch selection, never `""` or raw syntax; one WARNING per `(template, locale)` with debug off, naming phrase, locale and error. Mutations: degrade to simple; no warning; debug level; no dedup; `""` for no branch; unsupported kinds left on the simple path → named tests red |
| CID-1 | implemented | n/a (pure) | `custom-id-reference.json` (blob `60dc9b33`): 13/13 on codepoints, bytes, canonical JSON and hash. Mutations: default separators; `ensure_ascii=True` → red |
| CID-2 | implemented | n/a (pure) | `test_custom_id::test_CID2_*`. Mutation: the sentinel hashed as a category → red |
| CID-3 | implemented | n/a (pure) | `test_custom_id::test_CID3_*`; `legacy-custom-id-reference.json` (blob `dc555646`, 20 vectors executed against the JS core); a live-observed pre-correction id. Mutations: one uncategorised spelling; a byte hash for the code-unit one; emitting a legacy id → red |
| CID-4 | implemented | n/a (pure) | `test_custom_id::test_CID4_*`. Mutation: attach without verifying content → red |
| TOK-1 | implemented | n/a (pure) | Exclusions and svg on every path: fixture rows `style-`/`script-`/`noscript-`/`math-subtree`, `svg-inline-icon` on block AND page path; standalone and inline svg translated in place with `<path>` intact; `<template>` load-bearing on lxml (`test_spec_801::test_TOK1_template_exclusion_is_load_bearing_on_lxml`). Mutations: each exclusion removed; svg as a block element → red |
| TOK-2 | implemented | n/a (pure) | The enumerated collapse set, trimmed with the same set, and **the 28 C0 controls removed before collapse** on markup and `translate()` keys: `test_spec_801::test_TOK2_*` on the function (all 28 removed not mapped; TAB/LF/CR collapse; NUL, DEL, NEL kept; strip→collapse→trim order; `t()` keys stripped on lookup and register); fixture rows `fs-in-text`, `vt-in-text`, `tab-in-text`, `del-in-text`, `fs-in-attr` (the discriminating DOM row) on both paths; `test_canonicalization::test_TOK2_markup_carrying_a_c0_control_renders_instead_of_raising`. Residuals bound to libxml2 in *Parser model*. Mutations: FEFF dropped; NEL added; Python's trim; C0 mapped to space; VT/FF left to collapse; no strip; `t()` keys unstripped; every node rewritten → named tests red |
| TOK-3 | implemented | n/a (pure) | The twenty-seven in order (`test_canonicalization::test_TOK3_*`); all seven attribute fixture rows on block AND page path. Mutations: first two swapped; `data-bs-title` dropped → red |
| TOK-4 | implemented | n/a (pure) | `test_canonicalization::test_TOK4_*`; `attr-multiline`/`attr-nbsp` on both paths; lookups pinned per shape on both paths (`test_spec_801::test_CONF1_attribute_lookup_on_the_*_path`). Mutation: trim without collapse → red |
| TOK-5 | implemented | n/a (pure) | Both forms interpolate (`test_canonicalization::test_TOK5_*`); `%name%` normalises to `{name}` at capture on every path and in lookups (`test_spec_801::test_TOK5_*`); `percent-name-in-markup` = `brace-name-in-markup` on both paths. Mutations: capture unconverted; interpolation ignores `%name%` → red |
| TOK-6 | implemented | n/a (pure) | `test_canonicalization::test_TOK6_the_page_path_registers_each_unit_in_its_shape[*]` and `…_the_block_path_registers_the_fragment_in_its_shape[*]` — `<p>Hello</p>` a phrase, `<p title>` a block `[Tooltip, Hello]`, top-level `<img alt>` and `<button data-confirm>` blocks, svg-only a phrase, `<p>Hello <b>bold</b></p>` a block (control); **all 32 fixture rows through `translate_page`** (`…_every_fixture_row_tokenizes_identically_on_the_page_path`); in-place rendering of a phrase unit and a void block. Mutations: all phrase; single attribute token a phrase; all block; inline elements descended; own attributes dropped → named tests red |
| MARK-1 | implemented | n/a (pure) | `test_canonicalization::test_MARK1_*` — block and page paths, miss and hit, declared hosts, re-derived by the tokenizer; the stamp leaves the markup verbatim. Mutations: unstamped miss; page blocks unstamped → red |
| MARK-2 | implemented | n/a (pure) | Both spellings on read, one reader for every path (`parser.py`). A phrase host registers **whole** (`test_MARK2_a_js_rendered_phrase_host_is_not_re_split_on_the_page_path`), inline markup as `{mNo}…{mNc}` in the `<Phrase>` wire format (`…_a_phrase_host_with_markup_registers_as_one_string[*]`), rebuilt around its elements (`…_translation_keeps_its_markup_where_the_tokens_now_sit`); `false`/`0` ignores the marker. Mutations: each spelling dropped (four); host excised instead of registered; markup not encoded; markup lost on render; `false` still marks → red |
| MARK-3 | implemented | n/a (pure) | `test_canonicalization::test_MARK3_*`, both spellings, both paths — bare/`""`/`true`/`1`/`YES` one block with one id (a declaration outranks the phrase shape); `0`/`false` register the content as its units; `abc123`/`no`/`off` render the catalog entry under the id and register nothing; source kept with no entry. Mutations: bare opts out; `true` an identity; `no`/`off` opt out; `false` no longer opts out; identity not rendered; identity registered; declaration yields to the phrase shape → red |
| MARK-4 | implemented | n/a (pure) | `test_canonicalization::test_MARK4_nested_hosts_are_excised_and_each_registers_once_on_its_own[block]`/`[page]`; control `…_an_opted_out_nested_block_folds_into_the_outer_tokens`; `…_excision_moves_the_outer_id`. Mutations: declared host folded; nested hosts never processed; opted-out marker excised → red |
| SSR-1 | n/a (profile: browser) | - | The families table assigns SSR to the browser SDK's module instance |
| SSR-2 | n/a (profile: browser) | - | As SSR-1 |
| SSR-3 | n/a (profile: browser) | - | As SSR-1 |
| SRV-1 | implemented | n/a (pure) | `test_server_render::test_SRV1_*`; live corroboration `test_integration::test_real_translation`. Mutation: resolve against `{}` → red |
| SRV-2 | implemented | n/a (pure) | `test_server_render::test_SRV2_*` — concurrent `it`/`de` renders with a widened interleave. Mutation: the catalog held on the instance across the lookup → red |
| SRV-3 | implemented | live | **Order of events**, with `langsys.begin_request_scope()`/`end_request_scope()`/`request_scope()`: `test_server_render::test_SRV3_a_miss_is_not_sent_before_a_render_longer_than_the_debounce_has_responded`, `…_one_requests_flush_does_not_send_another_in_flight_requests_misses` (event-ordered, no timer), `…_scopes_are_per_asyncio_task`, `…_a_client_built_during_the_request_joins_its_scope`, `…_either_request_that_recorded_a_miss_releases_it`, `…_a_miss_outside_any_scope_keeps_the_debounce`. **Read-only half**: `test_integration::test_REG10_…` against the real server; `test_SRV3_a_read_only_key_pushes_nothing` with a write-key control. Mutations: scope ignored at record; a flush takes held items; every scope must end; a free miss held; end does not wake the debounce; shutdown leaves held misses; collect on the render call; push from a read-only key → named tests red |
| SRV-4 | n/a (architecture: terminal-HTML server SDK, no hydration hand-off; live if this SDK ever emits a client seed) | - | `translate_page()` returns a finished page; nothing hydrates against it |
| SRV-5 | implemented | n/a (pure) | Once-per-subtree, counted in calls: `test_spec_801::test_SRV5_a_depth_3_nested_*_is_registered_exactly_once`. The fail-loudly half is n/a on mechanism: no component model, so no uncapturable child. Mutation: every unit processed twice → red |
| SRV-6 | implemented | n/a (pure) | `test_request_locale::test_SRV6_*` — the spec's four requests (URL wins with no `Vary`; cookie wins with `Vary: Cookie`; header with `Vary: Accept-Language`; an unsupported cookie falls through and is not re-set), validation of every candidate, the project's own locales read from the contract double. When authorization is unavailable, a loaded snapshot's base and locales are the served set (`test_snapshot::test_SRV6_offline_*`), and with no snapshot only the configured base. Mutations: header before cookie; URL or cookie unvalidated; no `Vary`; an unreadable project serves what was asked; a snapshot's locales not served offline → red |
| MSG-1 | implemented | n/a (pure) | `server-message-vectors.json` (blob `c8125549`): every `resolve` row (`test_messages::test_MSG1_entry_resolution_matches_the_vectors[*]`); a foreign envelope through a resolver equals the default envelope. Mutations: an entry without a template; params searched; the key ignored → red |
| MSG-2 | implemented | n/a (pure) | The 21-code vocabulary; `size_code` by field type; the wording table for failures the reference's rules do not produce (`WORDINGS`, `with_label`) (`test_messages::test_MSG2_*`). Mutations: string size codes; the `lt` wording drifts → red |
| MSG-3 | implemented | n/a (pure) | Every `markers` vector; one template per field is two phrases; a template with no marker is its message and carries no params. Mutations: a capitalised name as a marker; params on a marker-less template → red |
| MSG-4 | implemented | n/a (pure) | Every `fill` vector; every canonical entry fills to its message; numbers stay JSON numbers; a param prints as the reference prints it (`3.0` → `3`, `True` → `true`). Mutations: null printed; whole float printed with its point → red |
| MSG-5 | n/a (profile: browser, binding) | - | A server core that renders a received entry does so identically: every `render` vector passes (`test_MSG5_rendering_matches_the_vectors`) |
| MSG-6 | implemented | contract | Templates register under `Errors` by default and read back there from the double (`test_messages::test_MSG7_…`); `message_category` is configurable (`test_MSG6_the_category_defaults_to_errors_and_is_configurable`); another category misses (`render` vector `other-category-misses`). Mutation: always `Errors` → red |
| MSG-7 | implemented | contract | `test_messages::test_MSG7_the_listing_registers_every_template_and_a_second_run_nothing_new` against the double; `…_an_unlistable_message_fails_the_run_with_an_actionable_line`; the `python -m langsys.messages` entry point. Declarations come from a provider the binding supplies. Mutations: re-register what the catalog holds; problems do not fail → red |
| MSG-8 | implemented | contract | `test_messages::test_MSG8_an_unlisted_template_is_registered_after_the_response_not_before` (request scope, double state); a listed template is not queued again; a non-writer registers nothing even once it could (drift, with control). Mutations: never queued; always queued → red |
| MSG-9 | n/a (architecture: a core has no validator; entries from failed rules are the Django and FastAPI bindings') | - | The core supplies `server_message`, `size_code`, `WORDINGS` and `TemplateProblem` |
| MSG-10 | n/a (architecture: a core has no label facility; `verbose_name` and `Field(title=…)` are the bindings') | - | The listing command reports what a provider names as unlabelled |
| MSG-11 | implemented | n/a (pure) | Refused at add: label markers and leftover `:attribute`, `{{ }}`, `%(x)s`, `%s`, `{0}` (`test_messages::test_MSG11_a_label_marker_or_a_leftover_placeholder_is_refused_when_added[*]`), ordinary templates accepted (percent prose included); a catalogued marker value warns once, an unseen one stays silent. Mutations: label markers accepted; placeholders accepted; warning not deduplicated; no warning → red |
| MSG-12 | n/a (architecture: a core has no redirect or session; the Django and FastAPI bindings carry entries across one) | - | |
| MIG-1 | implemented | n/a (pure) | `test_migrate::test_MIG1_unset_reads_no_file_and_looks_up_no_key` (file reads and lookups forbidden outright), control `…_set_it_reads_the_file`. Mutation: lookup with nothing configured → red |
| MIG-2 | implemented | n/a (pure) | `mig-vectors.json` `calls` rows for this core's entry points, `same_phrase_as` included (`test_mig_vectors::test_MIG2_entry_point_calls[*]`). A hit is the source value, a miss literal; `convert_literal` converts under the entry point's syntax (`t` nothing, `gettext` passed `%(name)s` only, `blocktranslate` passed `{{ name }}` only); `gettext_plural` for `ngettext`. Mutations: hit ignored; unpassed converted; `t()` converts → red |
| MIG-3 | implemented | n/a (pure) | `test_migrate::test_MIG3_a_key_registers_the_same_phrase_and_id_as_its_source_text_and_never_itself`. Mutation: the key registered → red |
| MIG-4 | implemented | n/a (pure) | `mig-vectors.json` `value_conversion` and `plural_forms` rows for `gettext` and `plain` (`test_mig_vectors::test_MIG4_*`); the other formats' rows are `n/a (format)`. `test_migrate::test_MIG4_*` — every placeholder form to `{name}`, `%%` to `%`; `%(name).2f`, `%<name>.2f`, positional `%s`, `:Name` verbatim with a warning; a `|` in a plain file verbatim with a warning; a gettext plural as `=1`/`other` with `#`. Mutations: `%(name)s` unconverted; `%%` kept; unexpressible converted; pipe silent; CLDR `one` for `=1` → red |
| MIG-5 | implemented | n/a (pure) | A dotted key's namespace (a whitespace-free leading segment, so `Welcome back.` has none), a file's namespace, or `msgctxt` is the category; an explicit category wins (`mig-vectors` `resolution` rows). Mutations: no namespace; namespace over explicit; no context; a sentence takes a namespace → red |
| MIG-6 | implemented | n/a (pure) | An absent key warns at debug and registers its argument; a changed value is a new phrase. Mutation: the absent key silent → red |
| MIG-7 | implemented | n/a (pure) | `mig-vectors.json` `resolution` rows (files in another format refused naming the file) and `refusals` rows (`test_mig_vectors::test_MIG7_*`). Python's set, `gettext` (`.po`) and `plain` JSON, with a per-file namespace: nested keys by path, multi-line `.po` strings, first file wins and the duplicate is reported (`python -m langsys.migrate`); a `.mo` refused naming its `.po`; `.php`, `.yml`, declared `vue-i18n`/`laravel` refused at load naming format and file. Mutations: last file wins; `.mo` read; unsupported format loaded; per-file namespace ignored; an undeclared `.php` not named `laravel` → red |
| MIG-8 | implemented | n/a (pure) | Per ecosystem, one core's two entry points over one resolver: `t()` and `translate_legacy(entry_point="gettext"/"ngettext"/"blocktranslate")`, which Django's gettext family delegates to, yield one phrase, one id and one category for the same key, with the file a `.po` whose entry has a context and a plural (`test_migrate::test_MIG8_t_and_the_framework_entry_point_register_one_phrase_id_and_category[*]`); the plural renders (`…_the_entry_point_renders_the_resolved_plural`); a literal miss converts only what the call passes. Across ecosystems: the `same_phrase_as` rows of `mig-vectors.json`. Mutations: the entry point skips the shared resolver; a literal miss unconverted → red |
| MIG-9 | not implemented | - | Waits on the 907 merge, which brings the `translations` map on `POST /translatable-items` |
| SNAP-1 | implemented | contract | `test_snapshot::test_SNAP1_the_snapshot_carries_exactly_what_the_api_serves_for_its_categories` against the double (the flat `GET /translations` catalog, filtered by category; locales and categories sorted); an unreadable catalog fails the export. The fleet format: `base_locale` present, the canonical serialisation byte for byte (`…_the_canonical_serialisation_is_the_specs` — code point key order with U+E000 before U+1F600 and `"10"` before `"404"`, `{}` for an empty map, a C0 control as `\u001c`, U+2028 raw), any JSON encoding loads (`…_the_file_is_any_json_encoding_of_the_document`), and the loader refuses a wrong format, version, missing member or checksum by name (`…_a_loader_refuses_by_name[*]`). **Every `snapshot-vectors.json` row**: canonical bytes and checksum (`test_snapshot_vectors::test_SNAP1_canonical_bytes_and_checksum[*]`), refusals by name, a re-encoded load. Mutations: every category kept; a failure exported as empty; insertion order; ASCII escaping; `base_locale` dropped; locales unsorted; a missing member not named → red |
| SNAP-2 | implemented | contract | **The seam is the core's**: `client.load_snapshot()`, which the Django and FastAPI bindings call at boot; when to seed is theirs. Seeded, lookups read the snapshot with no fetch (phrases and blocks, on every path, offline included); a phrase it lacks falls back to the double's catalog, and to source text with no network; the live catalog outranks it once fetched, and registration is decided against the live catalog alone (`test_snapshot::test_SNAP2_*`); the SRV-6 resolver serves its locales while authorization is unavailable; another project's snapshot is refused. Mutations: never consulted for phrases, then for blocks; the snapshot outranks the live catalog; another project's snapshot loads → red |
| SNAP-3 | implemented | n/a (pure) | `Snapshot.load()` refuses an edited, foreign or future-version file and names re-export as the refresh (`test_snapshot::test_SNAP3_*`); the SDK reads a snapshot only through that checked loader. Mutation: an edited file loads → red |
| BIND-1 | n/a (profile: binding) | - | This is a core; the Django and FastAPI wrappers carry the BIND rows |
| BIND-2 | n/a (profile: binding) | - | As BIND-1 |
| BIND-3 | n/a (profile: binding) | - | As BIND-1 |
| BIND-4 | n/a (profile: binding) | - | As BIND-1 |
| BIND-5 | n/a (profile: binding) | - | As BIND-1 |
| BIND-6 | n/a (profile: binding) | - | As BIND-1 |
| GRANT-1 | n/a (profile: browser) | - | A server SDK holds a write key; affirmative non-participation tested: `test_gating::test_GRANT_no_write_grant_header_is_ever_sent`, `test_integration::test_no_write_grant_header_is_ever_sent` |
| GRANT-2 | n/a (profile: browser) | - | As GRANT-1 |
| GRANT-3 | n/a (profile: browser) | - | As GRANT-1 |
| GRANT-4 | n/a (profile: browser) | - | As GRANT-1; `X-Write-Grant` is never sent |
| CACHE-1 | implemented | n/a (pure) | `test_gating::test_CACHE1_*`. Mutations: project id, then locale, dropped from the key → red |
| CACHE-2 | implemented | contract | **Degradation and window** against the double: `test_cache2::test_CACHE2_a_lookup_inside_the_window_renders_source_without_fetching_again` (the fault is consumed, so a re-fetch would render the translation), `…_after_the_window_the_translation_renders`, control `…_a_successful_first_fetch_renders_at_once`, per locale. **At the seam**: 3s doubling to 300s and reset on success; `status: false` a failure; five concurrent lookups share one request; never written to the cache backend. Measured: one GET per window in every failing scenario (*Failed catalog fetches, measured*). Mutations: no window; no growth; no reset; no sharing; `status: false` a catalog; failure written to the cache → red |
| OBS-1 | implemented | contract | `test_contract::test_OBS1_an_unusable_capability_is_surfaced_once` — the double computes `write_enabled: false` for a non-allow-listed `ip_write` key; one WARNING across three misses; control an allow-listed session. Unit: `test_gating::test_OBS1_*`. Mutations: debug level; once-guard removed → red |
| WIRE-1 | implemented | live | Every `test_integration` test authenticates with `X-Authorization` alone; `test_wire::test_WIRE1_*` checks every endpoint. Mutation: `Authorization` → red |
| WIRE-2 | implemented | contract | `test_contract::test_WIRE2_an_empty_204_is_success`; `test_wire::test_WIRE2_*` with a failure control. Mutation: parse every body → red |
| WIRE-3 | implemented | live | `test_integration::test_locale_casing_resolves_to_the_same_entry`; unit lowercase on the wire, one fetch and one key per casing, no sentinel sent, an uncategorised block registered and read back under `__uncategorized__` (`test_contract::test_WIRE3_an_uncategorised_block_reads_back_under_the_sentinel_and_is_found`). Mutations: the locale sent as given; the cache keyed by casing → red |
| WIRE-4 | implemented | live | `test_integration::test_WIRE4_an_unreachable_api_degrades_rather_than_throwing`; contract `test_contract::test_WIRE4_a_failed_catalog_degrades_and_queues_nothing[*]` (dropped and 500, with a translating control); `test_wire4_degradation`. Mutations: queue off a failed fetch; a transport error escapes; a failure cached as a catalog → red |
| WIRE-5 | implemented | n/a (pure) | `test_wire::test_WIRE5_*` — `LANGSYS_API_URL` redirects and a request arrives at the double; `api_url` wins; read at construction; documented. Every contract test uses the seam. Mutation: the variable ignored → red |
| CONF-1 | implemented | contract | Every row whose property depends on what the API answers asserts on status or accepted state — against the contract double or the live nova — and every register/lookup pair is pinned on every path (`test_spec_801::test_CONF1_*`). Mutations: raw and `trim()` attribute lookups; raw button lookup; `trim()` text lookup; unconverted `%name%` lookup; raw title and meta; head miss overwritten → red |
| CONF-2 | implemented | n/a (pure) | Every row carries a tier from the vocabulary, graded by the property the rule governs; `_dev_/conformance_counts.py` checks vocabulary, status/tier pairing, one id per row and this file's header blob |
| CONF-3 | implemented | n/a (pure) | Every row whose proof requires running something names its test and a specific mutation — file, symbol, exact text replaced and its replacement — in `_dev_/run_mutations.py`, applied in an isolated copy of the tree (the Django and FastAPI wrappers import this one editable). **Last run: 197/197 caught across 76 rules, each by its named tests** |

---

## Page path, measured

Every row of the shared canonicalization fixture through `translate_page`, reading what it
registers: **32 of 32 rows match** the fixture's tokens, as on the block path. Both are asserted
row by row in `test_canonicalization`; `python3 _dev_/measure_page_path.py` prints them side by
side, with the top-level shapes the fixture does not carry:

| Markup directly under `<body>` | Registers |
|---|---|
| `<textarea>Write here</textarea>` | phrase `Write here` |
| `<select><option>First choice</option><option>Second</option></select>` | block `[First choice, Second]` |
| `<a href="/x">Read more</a>` | phrase `Read more` |
| `<img alt="Alt text">` | block `[Alt text]` |
| `<p title="Tooltip">Hello</p>` | block `[Tooltip, Hello]` |
| `Loose body text` (a bare text node) | nothing — not an element, so not a unit |

## Block apply path, measured

Every token translated to `[token]`, through `translate_content_block`,
`apply_block_translations` and `translate_page` (`python3 _dev_/measure_block_apply.py`). All
three render identically and each translation lands on its own node: attribute and `value`
tokens apply, and `translate="no"` and marked-host subtrees are untouched. Apply looks each node
up by its own normalised text under the same skip and excision rules as extraction, so no
positional shift is possible. A phrase host inside a block registers whole, as its own phrase.

## Failed catalog fetches, measured

`python3 _dev_/measure_catalog_refetch.py`; catalog GETs counted at the transport, each lookup a
distinct phrase.

| Catalog answer | `translate()` × 5 | `translate_page`, 11 tokens |
|---|---|---|
| 200 (control) | 1 GET | 1 GET |
| 404, 422, 500, connection refused | 1 GET | 1 GET |
| hung upstream, timeout 0.5s | 1 GET, 0.50s | 1 GET, 0.50s |
| live nova, unsupported locale `zz-zz` (422) | 1 GET | 1 GET |

One request per CACHE-2 window, whatever fails, and nothing is queued inside it.

## Parser model — libxml2 2.14.6

These rows pin **parser** behaviour, not this SDK's code, so no mutation applies; each is
labelled with the libxml2 version and skips below 2.14 (`needs_214` in `test_spec_801`).

- **Parse model** — 7/7 agree with the JS family on tokens and block ids, vectors from the PHP
  fixture blob `741c8cfc7f49dc0eb3242afa30a307880771d9aa`.
- **C0 controls** — libxml2 2.14.6 keeps them in DOM text (CR normalised to LF). TOK-2's strip
  removes the 28 before collapse on every path, so the id no longer depends on the parser for
  them; collapse membership and the strip are asserted on the function.
- **Residuals the strip does not close**, pinned: raw NUL and `&#0;` become U+FFFD; a C0
  character reference in an attribute value is kept here (libxml2 before 2.14 truncates the
  value at it).
- **Bytes input** — markup handed over as `bytes` with no charset declared decodes as Latin-1.
  Pass `str`.
- **`<template>`** — lxml puts template children in the ordinary tree, so the exclusion is
  load-bearing; see TOK-1.

## Waiting on others

| What | Rows | Whose | In the code today |
|---|---|---|---|
| The `translations` map on `POST /translatable-items` | MIG-9 | the 907 merge | no import |

---

## Vendored fixtures

All are pinned by **git blob or tree SHA, not by path** — content-addressed, verified locally with
no network, surviving deletion of the source branch. A live ref records provenance; one string
never does both jobs.

| Fixture | The check | Provenance |
|---|---|---|
| `tests/contract-fixture/` (the shared contract double) | tree `542f57f5ffcb9038db1b7411152b7e31b96cb269` | `langsys-js-typescript` `contract-fixture/` |
| `tests/fixtures/canonicalization-reference.json` | blob `34034931872b93e761faea49fb040f3fd8a6b9f5` | `langsys-js-typescript` `a639ae8`, 32 rows, authored against 8.2.15 (the TOK and MARK text is byte-identical at 8.2.16) |
| `tests/fixtures/mig-vectors.json` | blob `20f2bdd678cb33981e3064e42d43ca62783920ad` | `langsys-js-typescript` `a639ae8`, 70 rows; this core runs the 44 in its formats and entry points |
| `tests/fixtures/snapshot-vectors.json` | blob `594bd77a0289abfdf608508ac93cc9f4c4f88459` | `langsys-js-typescript` `a639ae8`, 8 byte rows, 4 refusals, 1 re-encoded load |
| `tests/fixtures/server-message-vectors.json` | blob `c8125549cfee0f5286f79a8cbc194cd30ccd446e` | `langsys-js-typescript`, 47 rows |
| `tests/fixtures/interpolation-reference.json` | blob `017bffdd1d83a1b0a00a91f0d157a7fff726ee90` | `langsys-php-sdk` `c11a711`, 25 rows |
| `tests/fixtures/custom-id-reference.json` | blob `60dc9b33ecfd5fa3256fca7d36063ceb8ef1a00a` | `langsys-php origin/feature/838_write_key_gating_reland @ 8862841+` |
| `tests/fixtures/legacy-custom-id-reference.json` | blob `dc5556466dc54fe82e81ac9fdbf4549b2b76e7ce` | generated by executing `langsys-js-typescript` `md5Core` @ `6cdb388` |

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


`test_legacy_custom_id.py` pins the distinguishing property directly: the code-unit hash
must **agree** with a UTF-8 byte hash on ASCII and **diverge** above it. A port that
silently became a byte hash passes every ASCII row and fails there.

---

## Gaps, ranked by cost

1. **MIG-9 — no import of existing translations.** An app migrating off keys loses the human
   translations it already paid for, and they are machine-translated again. Waits on the 907
   endpoint.
2. **Bare text directly under a container is not a unit.** `<body>Loose text</body>` registers
   nothing, as on the PHP page path; TOK-6 names element units only.

## Declared obligations — framework wrappers

A `LangsysClient` held for the life of a server process outlives every request, so three
things are the wrapper's to do at each request, with the core providing the seam for each:

- **Mark the request scope (SRV-3).** Call `langsys.begin_request_scope()` at request start and
  `end_request_scope(scope)` once the response has been sent, then `flush_pending()`. Misses
  recorded inside are sent by no flush until then; misses outside any scope keep the debounce.
  A wrapper adds no timers of its own (BIND-3).
- **Flush unconditionally after the response (REG-3, GATE-2).** Let the core choose the lane: it
  holds the queue when write capability is unknown and discards it on a server *no*. Gating the
  flush on `can_write` discards the queue on an unknown answer, which GATE-2 forbids.
- **Reset the write decision (GATE-3).** Call `reset_write_decision()` at the request boundary;
  capability is address-dependent and must not carry from one request to the next.

The core also supplies what the framework-shaped rules build on: `resolve_request_locale()`
(SRV-6), `server_message()` / `WORDINGS` / the listing command (MSG-9, MSG-10, MSG-12), and
`translate_legacy()` (MIG-8), which a framework's own translation functions delegate to.

A short-lived script, worker or CLI that builds a client per run needs none of this; there the
process is the boundary.

## Known trade-off

On a **warm cache** the authorize payload lacks `write_enabled` by construction, because GATE-4
strips it. Plain `read`/`write` keys then fall back to `key_type`, which is sound because the
server guarantees `write_enabled ≡ key_type` for them, while `ip_write` never falls back and pays
a live authorize. A plain `write` key whose session is not write-enabled would be answered `true`
from a warm cache; the server invariant excludes that combination, and every live payload is
honoured as sent (`test_gating::test_GATE8_a_live_false_is_honored_on_the_very_first_call`). If
the invariant ever weakens, this is the row to revisit.
