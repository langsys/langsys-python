#!/usr/bin/env python3
"""Re-run the conformance mutations: break each behaviour, confirm its named test goes red.

CONF-3 - where a rule can only be proven by running something, the evidence is that breaking
the behaviour turns the test red, and the mutation performed is recorded, not just the test
name. This file IS that record for every runtime row in CONFORMANCE.md: each entry names the
rule, the file, the exact text replaced and its replacement, and the tests that must go red.
Committed so the record can be re-applied by anyone rather than remembered by one session.

**Mutations are applied to an isolated copy, never to this tree.** The Django and FastAPI
wrappers import this core through editable installs, so a mutation written here is what THEIR
suites test while it is applied - a red run measured in a wrapper during one of these batteries
was exactly that, and restoring the file afterwards protected this tree but not theirs. The
runner copies the working tree (tracked and untracked, ignoring what git ignores) into a
temporary directory, points the suite at the copy's `src`, refuses to start unless `langsys`
imports from the copy, and checks this tree's bytes are unchanged at the end.

Each mutation replaces exactly one anchor (it refuses if the anchor is missing or repeated),
runs the unit suite, collects EVERY failing or erroring test from the full output, and restores
the file byte-for-byte whatever happens. A mutation counts as caught only when the suite goes
red AND every test it names is among the failures. Reddening "something" proves nothing about
the rule: an unrelated test erroring would satisfy that, and has, in this repository's history.

    python3 _dev_/run_mutations.py              # every mutation
    python3 _dev_/run_mutations.py TOK-2 GATE-5 # some rules

Rows with nothing to run are not here: n/a rows, and the meta-rules CONF-2 and CONF-3 that are
discharged by CONFORMANCE.md and `_dev_/conformance_counts.py`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)

CLIENT = "src/langsys/client.py"
TRANSLATE = "src/langsys/translate.py"
CATALOG = "src/langsys/catalog.py"
HTTP = "src/langsys/http.py"
CONFIG = "src/langsys/config.py"
REGISTRATION = "src/langsys/registration.py"
INTERPOLATE = "src/langsys/interpolate.py"
PARSER = "src/langsys/html/parser.py"
PAGE = "src/langsys/html/page.py"
ATTRIBUTES = "src/langsys/html/attributes.py"


@dataclass
class Mutation:
    rule: str
    name: str
    path: str
    old: str
    new: str
    expect: tuple[str, ...]


def m(rule: str, name: str, path: str, old: str, new: str, *expect: str) -> Mutation:
    return Mutation(rule, name, path, old, new, expect)


MUTATIONS = [
    # -- GATE ---------------------------------------------------------------------------------
    m("GATE-1", "branch on key_type instead of the server's write_enabled", CLIENT,
      '        if isinstance(flag, bool):\n            self._observe_decision(flag)',
      '        if False:\n            self._observe_decision(flag)',
      "test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register",
      "test_GATE1_an_ip_write_key_that_is_write_enabled_does_register",
      "test_GATE1_an_allow_listed_ip_write_session_registers"),
    # The drift shape: an SDK that sends although told no is refused and keeps the phrase; once
    # the world would accept, that retained phrase lands. Modelled as "a server no is ignored".
    m("GATE-1", "send although the server said no (lands once the allow-list widens)", CLIENT,
      '        if decision is False:\n            phrase_count, block_count',
      '        if False:\n            phrase_count, block_count',
      "test_GATE1_a_session_told_no_sends_nothing_even_once_the_world_would_accept"),
    m("GATE-7", "the page path's phrases feed no lane", PAGE,
      '        translated = client.translate(tokens[0], category=category, locale=locale)\n        apply_element(el, {tokens[0]: translated}, attrs)',
      '        translated = tokens[0]\n        apply_element(el, {tokens[0]: translated}, attrs)',
      "test_GATE7_every_entry_point_lands_its_misses"),
    m("GATE-2", "collapse an unknown decision to False at the send site", CLIENT,
      '        decision = self._resolve_write_enabled()\n        if decision is None:',
      '        decision = self._resolve_write_enabled() is True\n        if decision is None:',
      "test_GATE2_a_transient_authorize_failure_holds_the_queue",
      "test_GATE2_an_unknown_answer_holds_the_queue_until_the_server_can_say_yes"),
    m("GATE-3", "make reset_write_decision() keep the observed answer", CLIENT,
      '        self._observed_decision = None\n        self._warned_unusable = False',
      '        self._warned_unusable = False',
      "test_GATE3_reset_write_decision_clears_an_observed_answer"),
    m("GATE-3", "hold the unstripped authorize payload in Project.raw", CLIENT,
      '        self._project = Project.from_response(data)\n        return self._project\n\n    def _observe_decision',
      '        self._project = Project.from_response(live)\n        return self._project\n\n    def _observe_decision',
      "test_GATE3_the_decision_is_not_latched_in_memory_either"),
    m("GATE-4", "cache the authorize payload without stripping write_enabled", CLIENT,
      '    return {k: v for k, v in data.items() if k != "write_enabled"}',
      '    return dict(data)',
      "test_GATE4_write_enabled_is_stripped_before_anything_is_cached"),
    m("GATE-5", "clear the queue when a registration send fails", CLIENT,
      '            self._schedule_flush()\n            return {\n                "phrases": 0,\n                "content_blocks": 0,\n                "success": False,\n                "error": str(exc),',
      '            self.clear_pending()\n            self._schedule_flush()\n            return {\n                "phrases": 0,\n                "content_blocks": 0,\n                "success": False,\n                "error": str(exc),',
      "test_GATE5_a_failed_registration_keeps_the_queue",
      "test_GATE5_a_refused_send_is_not_recorded_as_done_and_lands_on_the_second_read"),
    m("GATE-7", "the content-block path feeds no lane", CLIENT,
      '            if fetch.ok:\n                self._queue_content_block(html, cat_name, custom_id, phrases)',
      '            if fetch.ok:\n                pass',
      "test_translate_content_block_queues_when_missing",
      "test_CONTROL_content_block_queues_when_the_catalog_fetch_succeeds"),
    m("GATE-7", "the phrase path feeds no lane", CLIENT,
      '            self._queue_missing(phrase, category, fetch.catalog.get(category or UNCATEGORIZED))',
      '            pass',
      "test_CONTROL_translate_queues_a_miss_when_the_catalog_fetch_succeeds"),
    m("GATE-7", "the page path's blocks feed no lane", PAGE,
      '        client._queue_content_block(inner, item_cat, custom_id, phrases)',
      '        pass',
      "test_SRV5_a_depth_3_nested_block_is_registered_exactly_once"),
    m("GATE-8", "infer a write decision for ip_write from an absent flag", CLIENT,
      '        if key_type == "write":\n            logger.debug(',
      '        if key_type in ("write", "ip_write"):\n            logger.debug(',
      "test_GATE8_absent_flag_is_never_inferred_for_a_non_plain_write_key",
      "test_GATE8_absence_is_never_permission_for_ip_write"),
    m("GATE-8", "apply the key_type fallback to ip_write on a warm cache", CLIENT,
      '            return self._decide(data, allow_fallback=False)',
      '            return self._decide(data, allow_fallback=True) or data.get("key_type") == "ip_write"',
      "test_GATE8_ip_write_on_a_warm_cache_is_false_when_the_server_omits_the_field"),
    # -- CAT ----------------------------------------------------------------------------------
    m("CAT-1", "decide a miss by truthiness instead of key presence", TRANSLATE,
      '    if phrase in cat:\n        raw = cat[phrase]',
      '    if cat.get(phrase):\n        raw = cat[phrase]',
      "test_null_value_falls_back_to_base_and_not_missing"),
    m("CAT-2", "display an empty translation instead of the source", TRANSLATE,
      '        if isinstance(raw, str) and raw != "":',
      '        if isinstance(raw, str):',
      "test_empty_value_falls_back_to_base"),
    m("CAT-3", "treat a registered block with null phrases as unknown", TRANSLATE,
      '    block = cat.get(custom_id)\n    if isinstance(block, dict):\n        return block',
      '    block = cat.get(custom_id)\n    if isinstance(block, dict) and any(block.values()):\n        return block',
      "test_CAT3_a_registered_untranslated_block_is_known_not_missing"),
    # -- REG ----------------------------------------------------------------------------------
    m("REG-1", "send although the server said write_enabled is false", CLIENT,
      '        if decision is False:\n            phrase_count, block_count',
      '        if False:\n            phrase_count, block_count',
      "test_GATE1_a_write_key_that_is_not_write_enabled_does_not_register"),
    m("REG-2", "never schedule the debounce", CLIENT,
      '        if self._debounce is None:\n            return\n        with self._lock:',
      '        if True:\n            return\n        with self._lock:',
      "test_REG2_a_burst_of_misses_becomes_one_request",
      "test_REG2_the_debounce_is_a_real_send_path_not_just_a_helper"),
    m("REG-2", "do not re-arm the debounce after a flush that leaves work", CLIENT,
      '            phrases, blocks = self._sendable()\n            if phrases or blocks:\n                self._schedule_flush()\n\n    def _flush_outer',
      '            pass\n\n    def _flush_outer',
      "test_REG2_a_declining_flush_leaves_a_timer_armed"),
    m("REG-3", "do not register the end-of-context flush by default", CLIENT,
      '        if auto_flush:\n            atexit.register(self._auto_flush)',
      '        if False:\n            atexit.register(self._auto_flush)',
      "test_REG3_the_end_of_context_flush_is_registered_by_default"),
    m("REG-3", "let the shutdown flush respect the backoff", CLIENT,
      '                self.flush_pending(force=True)\n        except Exception as exc:  # never raise',
      '                self.flush_pending(force=False)\n        except Exception as exc:  # never raise',
      "test_REG3_the_shutdown_flush_forces_past_an_active_backoff"),
    m("REG-6", "clear the live queue after the send instead of the snapshot", CLIENT,
      '            for key in phrase_keys:\n                self._pending.pop(key, None)\n                self._phrase_scopes.pop(key, None)\n            for block_id in block_ids:\n                self._pending_blocks.pop(block_id, None)\n                self._block_scopes.pop(block_id, None)',
      '            self._pending.clear()\n            self._pending_blocks.clear()',
      "test_REG6_a_miss_recorded_during_a_send_is_not_dropped"),
    m("REG-7", "allow two sends in flight", CLIENT,
      '        self._sending = threading.Lock()',
      '        self._sending = threading.Semaphore(2)',
      "test_REG7_only_one_send_is_in_flight_at_a_time",
      "test_REG7_declining_keeps_the_queue_for_the_next_flush"),
    m("REG-8", "retry while backing off", CLIENT,
      '        if remaining > 0 and not force:\n            return {',
      '        if False:\n            return {',
      "test_REG8_while_backing_off_nothing_is_sent",
      "test_REG8_nothing_is_sent_while_backing_off_though_the_server_would_accept"),
    m("REG-8", "decay the backoff on success instead of resetting it", CLIENT,
      '            self._backoff_seconds = 0.0\n            self._backoff_until = 0.0',
      '            self._backoff_seconds = self._backoff_seconds / 2\n            self._backoff_until = 0.0',
      "test_REG8_backoff_resets_on_the_first_success"),
    m("REG-8", "remove the backoff ceiling", CLIENT,
      '            self._backoff_seconds = min(nxt, BACKOFF_MAX_SECONDS)',
      '            self._backoff_seconds = nxt',
      "test_REG8_the_backoff_doubles_and_stops_at_the_ceiling"),
    m("REG-9", "hardcode the batch size instead of the server's limit", REGISTRATION,
      '        self.batch_limit = batch_limit if batch_limit > 0 else 200',
      '        self.batch_limit = 200',
      "test_REG9_the_batch_limit_comes_from_the_server",
      "test_REG9_a_double_that_refuses_oversized_batches_ends_up_holding_every_item",
      "test_REG9_the_double_enforces_the_limit_and_holds_every_item"),
    m("REG-9", "one POST per content block", CLIENT,
      '                self._reg.register_content_blocks(blocks)',
      '                for block in blocks:\n                    self._reg.register_content_blocks([block])',
      "test_REG9_content_blocks_are_batched_into_one_post"),
    m("REG-10", "report a skipped write as success", CLIENT,
      '                "success": False,\n                "skipped": True,\n                "reason": "not-write-enabled",',
      '                "success": True,\n                "skipped": True,\n                "reason": "not-write-enabled",',
      "test_REG10_a_skipped_write_is_not_reported_as_success",
      "test_REG10_a_read_only_flush_is_reported_as_failure"),
    m("REG-10", "let a transport failure escape the flush", CLIENT,
      '        except (NetworkError, ApiError) as exc:\n            # REG-8/GATE-5',
      '        except ApiError as exc:\n            # REG-8/GATE-5',
      "test_REG10_flush_does_not_throw_when_registration_fails"),
    m("REG-11", "never suppress an ellipsis phrase", CLIENT,
      '        return longer is not None\n\n    @property',
      '        return False\n\n    @property',
      "test_REG11_suppresses_only_when_a_longer_entry_shares_the_prefix"),
    m("REG-11", "suppress every ellipsis phrase", CLIENT,
      '        return longer is not None\n\n    @property',
      '        return True\n\n    @property',
      "test_REG11_warns_but_still_registers_without_a_second_signal"),
    m("REG-12", "treat a nested map under the phrase as a missing phrase", TRANSLATE,
      '    if phrase in cat:\n        raw = cat[phrase]',
      '    if phrase in cat and not isinstance(cat[phrase], dict):\n        raw = cat[phrase]',
      "test_REG12_a_nested_map_is_a_content_block_never_a_missing_phrase"),
    m("REG-12", "decide block-ness by 32-hex string shape", TRANSLATE,
      '    if content_block_id is not None:\n        block = cat.get(content_block_id)',
      '    if len(phrase) == 32 and all(ch in "0123456789abcdef" for ch in phrase):\n        return Resolution(phrase, missing=False)\n    if content_block_id is not None:\n        block = cat.get(content_block_id)',
      "test_REG12_a_phrase_shaped_like_a_hash_is_still_a_phrase"),
    m("REG-12", "sync() counts a block's children but not the block's own key", CLIENT,
      '            keys.add(f"{category}::{phrase}")\n            if isinstance(value, dict):',
      '            if not isinstance(value, dict):\n                keys.add(f"{category}::{phrase}")\n            if isinstance(value, dict):',
      "test_REG12_presence_and_structure_agree_on_the_sync_path"),
    # -- HINT ---------------------------------------------------------------------------------
    m("HINT-2", "report a hint when the session cannot write", CLIENT,
      '            # Discarding a queue we have just been told we may not write is correct;',
      '            self._http.post("discovery/hint", json={"url": "https://site.test/page"})\n            # Discarding a queue we have just been told we may not write is correct;',
      "test_HINT2_a_server_sdk_never_reports_across_a_whole_render[False]"),
    # -- ICU ----------------------------------------------------------------------------------
    m("ICU-1", "no other-branch recovery for a missing argument", INTERPOLATE,
      '    branch = options.get("other")\n    if branch is None:',
      '    branch = None\n    if branch is None:',
      "test_ICU1_missing_select_argument_renders_the_other_branch",
      "test_ICU1_missing_plural_argument_renders_the_other_branch"),
    # The select-shaped ICU-2 tests cannot see this: a select given an unmatched value falls to
    # `other` anyway, so null-as-missing and null-as-supplied render the same. Only a plural
    # (which cannot count a null) and a plain slot (which would print "None") tell them apart.
    m("ICU-2", "treat a present-but-null argument as supplied", INTERPOLATE,
      '    if arg.name not in params or params[arg.name] is None:',
      '    if arg.name not in params:',
      "test_ICU5_the_ICU3_marker_survives_a_present_but_null_count",
      "test_ICU5_a_plain_argument_that_is_null_stays_visible"),
    m("ICU-3", "leave # unreplaced in a recovered plural", INTERPOLATE,
      '        hash_literal="{" + arg.name + "}" if arg.kind != "select" else None,',
      '        hash_literal=None,',
      "test_ICU3_hash_in_a_recovered_plural_prints_the_argument_name"),
    m("ICU-4", "recover silently", INTERPOLATE,
      '        if recovered:\n            _notice_recovery(template, locale, recovered)\n        return out',
      '        return out',
      "test_ICU4_emits_a_notice_naming_the_argument_and_the_locale"),
    m("ICU-4", "notice on every render instead of once per template and locale", INTERPOLATE,
      '    if key in _NOTICED:\n        return\n    _NOTICED.add(key)',
      '    _NOTICED.add(key)',
      "test_ICU4_deduplicates_per_template_and_locale"),
    m("ICU-5", "route a recovered template through the simplified renderer", INTERPOLATE,
      '        if recovered:\n            _notice_recovery(template, locale, recovered)\n        return out',
      '        if recovered:\n            _notice_recovery(template, locale, recovered)\n            return _simple(template, params, locale)\n        return out',
      "test_ICU5_missing_select_does_not_degrade_the_supplied_plural"),
    # -- CID ----------------------------------------------------------------------------------
    m("CID-1", "serialise with Python's default separators", REGISTRATION,
      '        [_hash_category(category), list(phrases)], ensure_ascii=False, separators=(",", ":")',
      '        [_hash_category(category), list(phrases)], ensure_ascii=False',
      "test_CID1_serialized_bytes_match_the_fixture", "test_CID1_custom_id_matches_the_fixture"),
    m("CID-1", "escape non-ASCII in the canonical JSON", REGISTRATION,
      '        [_hash_category(category), list(phrases)], ensure_ascii=False, separators=(",", ":")',
      '        [_hash_category(category), list(phrases)], ensure_ascii=True, separators=(",", ":")',
      "test_CID1_the_line_terminator_row_is_present_and_raw"),
    m("CID-2", "hash the uncategorised sentinel as a category", REGISTRATION,
      '    if category is None or category == _UNCATEGORIZED:\n        return ""',
      '    if category is None:\n        return ""',
      "test_CID2_none_and_the_sentinel_and_empty_all_hash_as_empty"),
    m("CID-3", "offer only one uncategorised spelling", REGISTRATION,
      '        slots = ["", _UNCATEGORIZED]',
      '        slots = [""]',
      "test_CID3_uncategorised_offers_both_historical_spellings",
      "test_both_uncategorised_spellings_are_offered_on_lookup"),
    m("CID-3", "offer a byte hash where the JS code-unit hash belongs", REGISTRATION,
      '        ids.append(_md5_utf16_code_units(js_form))',
      '        ids.append(hashlib.md5(js_form.encode("utf-8")).hexdigest())  # noqa: S324',
      "test_CID3_the_js_code_unit_form_is_offered_for_ascii_and_non_ascii"),
    m("CID-3", "emit a historical id when registering", REGISTRATION,
      '            "custom_id": custom_id or generate_custom_id(category, phrases),',
      '            "custom_id": custom_id or legacy_custom_ids(category, phrases)[-1],',
      "test_CID3_the_current_form_is_the_only_one_ever_emitted"),
    m("CID-4", "attach to a legacy id without verifying its content", TRANSLATE,
      '        if not _block_matches(candidate, phrases):',
      '        if False:',
      "test_CID4_a_legacy_id_whose_content_differs_is_declined"),
    # -- MSG ----------------------------------------------------------------------------------
    m("MSG-1", "an object without a template counts as an entry", "src/langsys/messages.py",
      '    if not (isinstance(code, str) and isinstance(message, str) and isinstance(template, str)):',
      '    if not (isinstance(code, str) and isinstance(message, str)):',
      "test_MSG1_entry_resolution_matches_the_vectors[missing-template-is-not-an-entry]"),
    m("MSG-1", "an entry's own params are searched for more entries", "src/langsys/messages.py",
      '        if entry is not None and name == "params":\n            continue',
      '        if False:\n            continue',
      "test_MSG1_entry_resolution_matches_the_vectors[params-are-not-searched]"),
    m("MSG-1", "the configured key does not narrow the search", "src/langsys/messages.py",
      '    if key:\n        body = _dig(body, key)',
      '    if False:\n        body = _dig(body, key)',
      "test_MSG1_entry_resolution_matches_the_vectors[configured-key-narrows]"),
    m("MSG-2", "a string field's size rule is too_small", "src/langsys/messages.py",
      '        return "too_short" if too == "small" else "too_long"',
      '        return "too_small" if too == "small" else "too_large"',
      "test_MSG2_a_size_rule_picks_its_code_by_the_field_type[abc-too_short-too_long]"),
    m("MSG-2", "the lt wording drifts from the spec's", "src/langsys/messages.py",
      '    "less_than": ("too_large", "The :attribute must be less than {value}."),',
      '    "less_than": ("too_large", "The :attribute must be smaller than {value}."),',
      "test_MSG2_the_wording_table_is_the_specs[less_than-too_large-]"),
    m("MSG-3", "a capitalised name reads as a marker", "src/langsys/messages.py",
      '_MARKER = re.compile(r"\{([a-z][a-z0-9_]*)\}")',
      '_MARKER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")',
      "test_MSG3_marker_extraction_matches_the_vectors[capitalised-is-not-a-marker]"),
    m("MSG-4", "a null param prints instead of keeping its marker", "src/langsys/messages.py",
      '    if value is None or isinstance(value, (Mapping, list, tuple, set)):\n        return None',
      '    if isinstance(value, (Mapping, list, tuple, set)):\n        return None',
      "test_MSG4_fill_matches_the_vectors[null-param-stays-marker]",
      "test_MSG4_a_present_but_null_param_stays_its_marker"),
    m("MSG-4", "a whole float prints with its decimal point", "src/langsys/messages.py",
      '        if math.isfinite(value) and value.is_integer():\n            return str(int(value))',
      '        if False:\n            return str(int(value))',
      "test_MSG4_a_param_prints_as_the_reference_prints_it[3.0-3]"),
    m("MSG-4", "params are sent for a template with no marker", "src/langsys/messages.py",
      '    if markers:\n        entry["params"]',
      '    if True:\n        entry["params"]',
      "test_MSG3_a_template_with_no_marker_is_its_own_message_and_carries_no_params"),
    m("MSG-5", "a received entry is rendered from message as the key", CLIENT,
      '        template = entry.get("template")\n        value = (',
      '        template = entry.get("message")\n        value = (',
      "test_MSG5_rendering_matches_the_vectors[message-is-never-the-key]"),
    m("MSG-6", "templates always go under Errors", CLIENT,
      '        category = self.message_category\n        fetch = self._catalog.get(self._effective_locale(None))',
      '        category = "Errors"\n        fetch = self._catalog.get(self._effective_locale(None))',
      "test_MSG6_the_category_defaults_to_errors_and_is_configurable"),
    m("MSG-7", "the listing re-registers what the catalog holds", CLIENT,
      '        new = [t for t in templates if not (isinstance(known, dict) and t in known)]',
      '        new = list(templates)',
      "test_MSG7_the_listing_registers_every_template_and_a_second_run_nothing_new"),
    m("MSG-7", "problems do not fail the run", "src/langsys/messages.py",
      '    return 1 if listing.problems else 0',
      '    return 0',
      "test_MSG7_an_unlistable_message_fails_the_run_with_an_actionable_line"),
    m("MSG-8", "an unlisted template is never queued", CLIENT,
      '        if not (isinstance(known, dict) and template in known):\n            self._queue_missing(template, category, known)',
      '        if False:\n            self._queue_missing(template, category, known)',
      "test_MSG8_an_unlisted_template_is_registered_after_the_response_not_before",
      "test_MSG3_one_template_per_field_is_two_phrases"),
    m("MSG-8", "a listed template is queued again", CLIENT,
      '        if not (isinstance(known, dict) and template in known):\n            self._queue_missing(template, category, known)',
      '        if True:\n            self._queue_missing(template, category, known)',
      "test_MSG8_a_listed_template_is_not_registered_again"),
    m("MSG-11", "a label-carrying marker is accepted", "src/langsys/messages.py",
      '    labelled = [m for m in template_markers(template) if m in LABEL_MARKERS]',
      '    labelled: list[str] = []',
      "test_MSG11_a_label_marker_or_a_leftover_placeholder_is_refused_when_added[label-field]"),
    m("MSG-11", "leftover framework placeholders are accepted", "src/langsys/messages.py",
      '    for kind, pattern in _FRAMEWORK_PLACEHOLDERS:\n        leftover = pattern.search(without_markers)',
      '    for kind, pattern in ():\n        leftover = pattern.search(without_markers)',
      "test_MSG11_a_label_marker_or_a_leftover_placeholder_is_refused_when_added[laravel-colon]"),
    m("MSG-11", "the catalogued-value warning is not deduplicated", CLIENT,
      '            warned = key in self._warned_marker_values',
      '            warned = False',
      "test_MSG11_a_marker_filled_with_a_catalogued_phrase_warns_once"),
    m("MSG-11", "no warning for a catalogued marker value", CLIENT,
      '            if isinstance(value, str) and value in phrases and not warned:',
      '            if False:',
      "test_MSG11_a_marker_filled_with_a_catalogued_phrase_warns_once"),
    # -- SRV-6 / GATE-10 ----------------------------------------------------------------------
    m("SRV-6", "the header is consulted before the cookie", "src/langsys/request_locale.py",
      '    cookie_locale = _supported(cookie, locales) if uses_cookie else None',
      '    cookie_locale = None if accept_language else (_supported(cookie, locales) if uses_cookie else None)',
      "test_SRV6_a_cookie_wins_over_the_header_and_varies_on_cookie"),
    m("SRV-6", "a URL candidate is served unvalidated", "src/langsys/request_locale.py",
      '    url_locale = _supported(url, locales)',
      '    url_locale = url.strip() if url and url.strip() else None',
      "test_SRV6_an_unsupported_url_locale_is_never_served[zz-zz]",
      "test_SRV6_the_client_validates_against_the_projects_own_locales"),
    m("SRV-6", "a cookie candidate is served unvalidated", "src/langsys/request_locale.py",
      '    cookie_locale = _supported(cookie, locales) if uses_cookie else None',
      '    cookie_locale = cookie if uses_cookie else None',
      "test_SRV6_an_unsupported_cookie_falls_through_to_the_header"),
    m("SRV-6", "a header-chosen response carries no Vary", "src/langsys/request_locale.py",
      '        return LocaleChoice(header_locale, "accept-language", tuple(consulted))',
      '        return LocaleChoice(header_locale, "accept-language", ())',
      "test_SRV6_with_only_a_header_the_negotiated_locale_varies_on_accept_language"),
    m("SRV-6", "a cookie-chosen response carries no Vary", "src/langsys/request_locale.py",
      '        return LocaleChoice(cookie_locale, "cookie", ("Cookie",))',
      '        return LocaleChoice(cookie_locale, "cookie", ())',
      "test_SRV6_a_cookie_wins_over_the_header_and_varies_on_cookie"),
    m("SRV-6", "an unreadable project serves whatever was asked", CLIENT,
      '            base = self._config.base_locale or ""\n            supported = [base] if base else []',
      '            base = self._config.base_locale or ""\n            supported = [base, url or "", cookie or ""]',
      "test_SRV6_when_the_project_cannot_be_read_only_the_configured_base_is_served"),
    m("GATE-10", "a translated render is not marked", PAGE,
      '        doc.set("data-ls-resolved", rendered)',
      '        pass',
      "test_GATE10_a_render_off_the_base_locale_marks_its_root_resolved"),
    m("GATE-10", "every render is marked, the base one included", PAGE,
      '    if base and rendered and rendered != normalize_locale(base):',
      '    if rendered:',
      "test_GATE10_the_same_render_in_the_base_locale_is_not_marked",
      "test_GATE10_an_unknowable_base_locale_leaves_the_page_unmarked"),
    # -- CACHE / OBS --------------------------------------------------------------------------
    m("CACHE-1", "drop the project id from the catalog cache key", CATALOG,
      '        return f"translations_{self._project_id}_{normalize_locale(locale)}"',
      '        return f"translations_{normalize_locale(locale)}"',
      "test_CACHE1_every_key_is_namespaced_by_project"),
    m("CACHE-1", "drop the locale from the catalog cache key", CATALOG,
      '        return f"translations_{self._project_id}_{normalize_locale(locale)}"',
      '        return f"translations_{self._project_id}"',
      "test_CACHE1_the_catalog_key_carries_the_locale"),
    m("OBS-1", "demote the unusable-capability diagnostic below warning", CLIENT,
      '        self._warned_unusable = True\n        logger.warning(',
      '        self._warned_unusable = True\n        logger.debug(',
      "test_OBS1_an_unusable_capability_is_surfaced_once_not_per_miss",
      "test_OBS1_an_unusable_capability_is_surfaced_once"),
    m("OBS-1", "emit the diagnostic on every resolution", CLIENT,
      '        if self._warned_unusable:\n            return\n        self._warned_unusable = True',
      '        self._warned_unusable = True',
      "test_OBS1_an_unusable_capability_is_surfaced_once_not_per_miss"),
    # -- WIRE ---------------------------------------------------------------------------------
    m("WIRE-1", "authenticate with Authorization instead of X-Authorization", HTTP,
      '                "X-Authorization": api_key,',
      '                "Authorization": api_key,',
      "test_WIRE1_every_request_authenticates_with_the_x_authorization_header"),
    m("WIRE-2", "parse every body unconditionally", HTTP,
      '        except ValueError:\n            body = {}',
      '        except ValueError:\n            raise',
      "test_WIRE2_an_empty_204_is_a_success_not_a_parse_error",
      "test_WIRE2_an_empty_204_is_success"),
    # Two sites: the wire value and the cache key normalise independently, so breaking the wire
    # alone leaves the cache test green - correctly, since one entry is still one entry.
    m("WIRE-3", "send the locale as given", CATALOG,
      '        wire_locale = normalize_locale(locale)\n        if use_cache:',
      '        wire_locale = locale\n        if use_cache:',
      "test_WIRE3_the_locale_goes_on_the_wire_lowercase"),
    m("WIRE-3", "key the memory and persistent caches by the locale as given", CATALOG,
      '    def _key(self, locale: str) -> str:\n        return f"translations_{self._project_id}_{normalize_locale(locale)}"\n\n    def get(self, locale: str, *, use_cache: bool = True) -> CatalogFetch:\n        wire_locale = normalize_locale(locale)',
      '    def _key(self, locale: str) -> str:\n        return f"translations_{self._project_id}_{locale}"\n\n    def get(self, locale: str, *, use_cache: bool = True) -> CatalogFetch:\n        wire_locale = locale',
      "test_WIRE3_casing_variants_are_one_cache_entry_not_two"),
    m("WIRE-4", "queue misses off a failed catalog fetch", CLIENT,
      '        if result.missing and content_block_id is None and fetch.ok:',
      '        if result.missing and content_block_id is None:',
      "test_WIRE4_a_failed_fetch_queues_nothing",
      "test_WIRE4_a_failed_catalog_degrades_and_queues_nothing"),
    m("WIRE-4", "let a transport failure escape the catalog fetch", CATALOG,
      '        except (NetworkError, ApiError) as exc:\n            # WIRE-4',
      '        except ApiError as exc:\n            # WIRE-4',
      "test_WIRE4_translate_degrades_to_the_source_phrase"),
    m("WIRE-4", "cache a failed fetch as an empty catalog", CATALOG,
      '        fetched = self._fetch(wire_locale)\n        if not fetched.ok:\n            return fetched',
      '        fetched = self._fetch(wire_locale)',
      "test_WIRE4_a_failed_fetch_is_not_cached_as_an_empty_catalog"),
    m("WIRE-5", "ignore LANGSYS_API_URL", CONFIG,
      '        resolved_url = (api_url or _env("LANGSYS_API_URL", DEFAULT_API_URL)) or DEFAULT_API_URL',
      '        resolved_url = api_url or DEFAULT_API_URL',
      "test_WIRE5_the_environment_redirects_the_base_and_a_request_arrives_there"),
    # -- TOK ----------------------------------------------------------------------------------
    m("TOK-1", "stop excluding script, style and noscript", PARSER,
      'SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "math"})',
      'SKIP_TAGS = frozenset({"template", "math"})',
      "test_TOK1_one_document_carrying_all_three_yields_exactly_one_phrase",
      "test_tokens_match_the_fixture[noscript-subtree]"),
    m("TOK-1", "stop excluding template (load-bearing on lxml)", PARSER,
      'SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "math"})',
      'SKIP_TAGS = frozenset({"script", "style", "noscript", "math"})',
      "test_TOK1_template_content_produces_no_token"),
    m("TOK-1", "stop excluding math", PARSER,
      'SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "math"})',
      'SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})',
      "test_TOK1_the_spec_document_yields_exactly_the_ordinary_phrase",
      "test_TOK1_math_is_excluded_and_the_surrounding_text_survives", "math-subtree"),
    m("TOK-1", "treat svg as a block element (the retracted mechanism)", PAGE,
      '        "details", "summary", "dialog",',
      '        "details", "summary", "dialog", "svg",',
      "test_TOK1_inline_svg_on_the_page_path_yields_the_same_tokens"),
    m("TOK-2", "drop U+FEFF from the collapse set", PARSER,
      "0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,",
      "0x2028, 0x2029, 0x202F, 0x205F, 0x3000,",
      "test_TOK2_feff_is_a_member_and_collapses",
      "test_TOK2_feff_is_trimmed_leading_and_trailing", "feff-in-text"),
    m("TOK-2", "add U+0085 to the collapse set", PARSER,
      "0x0009, 0x000A, 0x000B, 0x000C, 0x000D, 0x0020, 0x00A0, 0x1680,",
      "0x0009, 0x000A, 0x000B, 0x000C, 0x000D, 0x0020, 0x0085, 0x00A0, 0x1680,",
      "test_TOK2_non_members_survive_the_collapse[U+0085]", "nel-in-text"),
    # Only a character Python's str.isspace() and the spec's set disagree on can tell the two
    # trims apart. U+FEFF cannot: the collapse has already turned an edge run of members into
    # one space, which either trim removes. U+0085 can - Python trims it, the spec keeps it.
    m("TOK-2", "trim with Python's own whitespace instead of the enumerated set", PARSER,
      'return _WS.sub(" ", strip_c0(text)).strip(_COLLAPSE_CHARS) if text else ""',
      'return _WS.sub(" ", strip_c0(text)).strip() if text else ""',
      "test_TOK2_non_members_survive_trimming[U+0085]"),
    m("TOK-2", "map the C0 controls to a space instead of removing them", "src/langsys/text.py",
      '_DELETE = dict.fromkeys(C0_STRIPPED)',
      '_DELETE = dict.fromkeys(C0_STRIPPED, " ")',
      "test_TOK2_the_28_c0_controls_are_removed_not_mapped_to_a_space[U+001C]",
      "test_tokens_match_the_fixture[fs-in-text]"),
    m("TOK-2", "leave VT and FF to the collapse", "src/langsys/text.py",
      'C0_STRIPPED = frozenset([*range(0x01, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20)])',
      'C0_STRIPPED = frozenset([*range(0x01, 0x09), *range(0x0E, 0x20)])',
      "test_tokens_match_the_fixture[vt-in-text]"),
    m("TOK-2", "strip nothing", PARSER,
      'return _WS.sub(" ", strip_c0(text)).strip(_COLLAPSE_CHARS) if text else ""',
      'return _WS.sub(" ", text).strip(_COLLAPSE_CHARS) if text else ""',
      "test_tokens_match_the_fixture[fs-in-attr]",
      "test_TOK6_every_fixture_row_tokenizes_identically_on_the_page_path[fs-in-attr]"),
    m("TOK-2", "t() keys are not stripped", CLIENT,
      '        phrase = strip_c0(phrase)\n',
      '',
      "test_TOK2_a_code_registered_key_is_stripped_on_lookup_and_on_register"),
    m("TOK-2", "rewrite every text node, translated or not", PARSER,
      '    if value is not getattr(el, slot):\n        setattr(el, slot, strip_c0(value) if value else value)',
      '    setattr(el, slot, value)',
      "test_TOK2_markup_carrying_a_c0_control_renders_instead_of_raising[text]"),
    m("TOK-6", "every unit registers as a phrase", PARSER,
      '    return len(tokens) == 1 and text_nodes == 1',
      '    return len(tokens) >= 1',
      "test_TOK6_the_page_path_registers_each_unit_in_its_shape[control]",
      "test_TOK6_the_block_path_registers_the_fragment_in_its_shape[control]"),
    m("TOK-6", "a single attribute token is a phrase", PARSER,
      '    return len(tokens) == 1 and text_nodes == 1',
      '    return len(tokens) == 1',
      "test_TOK6_the_page_path_registers_each_unit_in_its_shape[top-level-void]"),
    m("TOK-6", "every unit registers as a block", PARSER,
      '    return len(tokens) == 1 and text_nodes == 1',
      '    return False',
      "test_TOK6_the_page_path_registers_each_unit_in_its_shape[one-text-node]",
      "test_TOK6_the_block_path_registers_the_fragment_in_its_shape[svg]"),
    m("TOK-6", "the page walker descends into inline elements instead of making them units", PAGE,
      '        if _contains_nested_blocks(child):\n            _walk(',
      '        if _contains_nested_blocks(child) or child.tag.lower() not in BLOCK_ELEMENTS:\n            _walk(',
      "test_TOK6_the_page_path_registers_each_unit_in_its_shape[top-level-void]"),
    m("TOK-6", "a unit's own attributes are dropped", PARSER,
      '    _walk_extract(el, attrs, tokens, text_nodes)\n    return tokens, text_nodes[0]',
      '    if el.text and normalize_phrase(el.text):\n        tokens.append(normalize_phrase(el.text))\n        text_nodes[0] += 1\n    for child in el:\n        _walk_extract(child, attrs, tokens, text_nodes)\n    return tokens, text_nodes[0]',
      "test_TOK6_the_page_path_registers_each_unit_in_its_shape[own-attribute]"),
    m("MARK-3", "the bare attribute opts out", ATTRIBUTES,
      'BLOCK_DECLARATION_VALUES = frozenset({"", "true", "1", "yes"})',
      'BLOCK_DECLARATION_VALUES = frozenset({"true", "1", "yes"})',
      "test_MARK3_a_declaration_registers_one_block_with_the_same_id_on_the_page_path[bare-data-ls-contentblock]",
      "test_MARK3_the_classifier_table"),
    m("MARK-3", "true reads as an identity", ATTRIBUTES,
      'BLOCK_DECLARATION_VALUES = frozenset({"", "true", "1", "yes"})',
      'BLOCK_DECLARATION_VALUES = frozenset({"", "1", "yes"})',
      "test_MARK3_a_declaration_registers_one_block_with_the_same_id_on_the_page_path[true-data-langsys-contentblock]"),
    m("MARK-3", "no and off opt out", ATTRIBUTES,
      'MARKER_OPT_OUT_VALUES = frozenset({"false", "0"})',
      'MARKER_OPT_OUT_VALUES = frozenset({"false", "0", "no", "off"})',
      "test_MARK3_any_other_value_is_an_identity_that_renders_and_registers_nothing[no-data-ls-contentblock]"),
    m("MARK-3", "false no longer opts out", ATTRIBUTES,
      'MARKER_OPT_OUT_VALUES = frozenset({"false", "0"})',
      'MARKER_OPT_OUT_VALUES = frozenset({"0"})',
      "test_MARK3_an_opt_out_registers_the_content_as_its_units_would[false-data-ls-contentblock]"),
    m("MARK-3", "an identity host is not rendered from the catalog", PAGE,
      '        if isinstance(entry, dict):\n            apply_element(el, entry, attrs)\n        return',
      '        return',
      "test_MARK3_any_other_value_is_an_identity_that_renders_and_registers_nothing[abc123-data-ls-contentblock]",
      "test_MARK3_an_identity_on_the_block_path_renders_under_its_id"),
    m("MARK-3", "an identity host is registered as a declared block", PAGE,
      '    if block_marker_kind(el) == "identity":',
      '    if False:',
      "test_MARK3_an_identity_with_no_catalog_entry_keeps_its_source"),
    m("MARK-3", "a declaration yields to the TOK-6 phrase shape", PAGE,
      '    if not declared and is_phrase_unit(tokens, text_nodes):',
      '    if is_phrase_unit(tokens, text_nodes):',
      "test_MARK3_a_declaration_registers_one_block_with_the_same_id_on_the_page_path[bare-data-ls-contentblock]",
      "test_MARK3_a_declaration_registers_one_block_on_the_block_path[bare-data-ls-contentblock]"),
    m("MARK-4", "a nested declared host is folded into the enclosing unit", PARSER,
      '    return is_phrase_host(el) or block_marker_kind(el) in ("declaration", "identity")',
      '    return is_phrase_host(el) or block_marker_kind(el) == "identity"',
      "test_MARK4_nested_hosts_are_excised_and_each_registers_once_on_its_own[block]",
      "test_MARK4_excision_moves_the_outer_id"),
    m("MARK-4", "nested hosts are never handled on their own", PAGE,
      '        if is_marked_host(child):\n            _process_host(client, child, attrs, locale, default_category, effective, selmap)\n        else:\n            _process_nested_hosts(',
      '        if False:\n            _process_host(client, child, attrs, locale, default_category, effective, selmap)\n        else:\n            _process_nested_hosts(',
      "test_MARK4_nested_hosts_are_excised_and_each_registers_once_on_its_own[block]",
      "test_MARK2_a_js_rendered_phrase_host_is_not_re_split_on_the_page_path"),
    m("MARK-4", "an opted-out block marker is still excised", PARSER,
      '    return is_phrase_host(el) or block_marker_kind(el) in ("declaration", "identity")',
      '    return is_phrase_host(el) or block_marker_kind(el) != "absent"',
      "test_MARK4_control_an_opted_out_nested_block_folds_into_the_outer_tokens"),
    m("MARK-2", "a phrase host is excised instead of registered whole", PAGE,
      '            translated = client.translate(text, category=category, locale=locale)\n            if translated != text:',
      '            translated = text\n            if translated != text:',
      "test_MARK2_a_js_rendered_phrase_host_is_not_re_split_on_the_page_path",
      "test_MARK2_a_phrase_host_with_markup_registers_as_one_string[data-ls-phrase]"),
    m("MARK-2", "a phrase host's markup is not encoded as tokens", "src/langsys/html/markup.py",
      '                out += f"{{m{index}o}}" + _encode_children(child, slots) + f"{{m{index}c}}"',
      '                out += _encode_children(child, slots)',
      "test_MARK2_a_phrase_host_with_markup_registers_as_one_string[data-langsys-phrase]"),
    m("MARK-2", "a translated phrase host loses its markup", "src/langsys/html/markup.py",
      '    rebuilt = _rebuild(translated, slots)',
      '    rebuilt = None',
      "test_MARK2_a_phrase_host_translation_keeps_its_markup_where_the_tokens_now_sit"),
    m("MARK-2", "a phrase marker set to false still marks", PARSER,
      '    return any(marker_is_on(el.get(attr)) for attr in PHRASE_HOST_ATTRS)',
      '    return any(el.get(attr) is not None for attr in PHRASE_HOST_ATTRS)',
      "test_MARK2_a_phrase_host_marked_false_is_ordinary_markup"),
    m("SRV-5", "register every unit twice", PAGE,
      '        _process_unit(client, child, attrs, locale, _item_category(effective, default_category))\n\n\ndef _excluded',
      '        _process_unit(client, child, attrs, locale, _item_category(effective, default_category))\n        _process_unit(client, child, attrs, locale, _item_category(effective, default_category))\n\n\ndef _excluded',
      "test_SRV5_a_depth_3_nested_phrase_is_registered_exactly_once",
      "test_SRV5_a_depth_3_nested_block_is_registered_exactly_once"),
    m("TOK-3", "swap the first two attributes", ATTRIBUTES,
      '    "placeholder",\n    "alt",',
      '    "alt",\n    "placeholder",',
      "test_TOK3_the_attribute_list_is_the_twenty_seven_in_order"),
    m("TOK-3", "drop data-bs-title from the list", ATTRIBUTES,
      '    "data-bs-title",\n    "data-bs-content",',
      '    "data-bs-content",',
      "test_tokens_match_the_fixture[attr-new-data-bs-title]"),
    m("TOK-4", "trim attribute values without collapsing their interior", PARSER,
      '            normalized = normalize_phrase(value)\n            if normalized:',
      '            normalized = value.strip()\n            if normalized:',
      "test_TOK4_attribute_values_collapse_internal_whitespace_like_text_nodes",
      "test_tokens_match_the_fixture[attr-multiline]"),
    m("TOK-5", "stop rewriting %name% at capture", INTERPOLATE,
      '    return _PERCENT_SLOT.sub(lambda match: "{" + match.group(1) + "}", text)',
      "    return text",
      "test_TOK5_percent_form_in_markup_is_captured_as_the_brace_form", "percent-name-in-markup"),
    m("TOK-5", "stop accepting %name% at interpolation", INTERPOLATE,
      '    if "%" not in template:\n        return template\n\n    def repl',
      '    return template\n\n    def repl',
      "test_TOK5_both_placeholder_forms_interpolate_the_same_argument"),
    # -- MARK ---------------------------------------------------------------------------------
    m("MARK-1", "return an unstamped block on a miss", CLIENT,
      '            return stamp_content_block(html, custom_id)\n        translated = apply_block_translations',
      '            return html\n        translated = apply_block_translations',
      "test_MARK1_an_untranslated_block_is_still_stamped"),
    m("MARK-1", "stop stamping page-path blocks", PAGE,
      '    el.set("data-ls-contentblock", custom_id)',
      '    pass',
      "test_MARK1_page_rendered_blocks_are_stamped_too"),
    m("MARK-2", "read only the data-ls- phrase spelling on the block path", PARSER,
      'PHRASE_HOST_ATTRS = ("data-ls-phrase", "data-langsys-phrase")',
      'PHRASE_HOST_ATTRS = ("data-ls-phrase",)',
      "test_MARK2_the_mirror_case_both_spellings_on_the_block_path"),
    m("MARK-2", "the tokenizer reads only the data-langsys- phrase spelling", PARSER,
      'PHRASE_HOST_ATTRS = ("data-ls-phrase", "data-langsys-phrase")',
      'PHRASE_HOST_ATTRS = ("data-langsys-phrase",)',
      "test_MARK2_a_js_rendered_phrase_host_is_not_re_split_on_the_page_path"),
    m("MARK-2", "the tokenizer reads only the data-ls- block identity spelling", PARSER,
      'BLOCK_HOST_ATTRS = ("data-ls-contentblock", "data-langsys-contentblock")',
      'BLOCK_HOST_ATTRS = ("data-ls-contentblock",)',
      "test_MARK2_a_nested_content_block_host_is_left_alone_on_the_block_path[data-langsys-contentblock]"),
    m("MARK-2", "the tokenizer reads only the data-langsys- block identity spelling", PARSER,
      'BLOCK_HOST_ATTRS = ("data-ls-contentblock", "data-langsys-contentblock")',
      'BLOCK_HOST_ATTRS = ("data-langsys-contentblock",)',
      "test_MARK2_a_nested_content_block_host_is_left_alone_on_the_block_path[data-ls-contentblock]"),
    # -- SRV ----------------------------------------------------------------------------------
    m("SRV-1", "serve the base language whatever the catalog holds", CLIENT,
      '        result = resolve(fetch.catalog, phrase, category, content_block_id)',
      '        result = resolve({}, phrase, category, content_block_id)',
      "test_SRV1_the_served_output_carries_the_request_locale_translation"),
    m("SRV-2", "hold the fetched catalog in shared state across the lookup", CLIENT,
      '        loc = self._effective_locale(locale)\n        fetch = self._catalog.get(loc)\n        self._observe_decision(fetch.write_enabled)\n        result = resolve(',
      '        loc = self._effective_locale(locale)\n        self._shared_fetch = self._catalog.get(loc)\n        time.sleep(0.05)\n        fetch = self._shared_fetch\n        self._observe_decision(fetch.write_enabled)\n        result = resolve(',
      "test_SRV2_concurrent_locales_do_not_observe_each_others_catalog"),
    m("SRV-3", "ignore the request scope when recording a miss", CLIENT,
      '        scope = current_scope()\n        if scope is None:\n            scopes.pop(key, None)',
      '        scope = None\n        if scope is None:\n            scopes.pop(key, None)',
      "test_SRV3_a_miss_is_not_sent_before_a_render_longer_than_the_debounce_has_responded",
      "test_SRV3_one_requests_flush_does_not_send_another_in_flight_requests_misses",
      "test_SRV3_scopes_are_per_asyncio_task"),
    m("SRV-3", "a flush takes every queued item, held or not", CLIENT,
      '            else:\n                phrase_keys, block_ids = self._sendable()',
      '            else:\n                phrase_keys, block_ids = list(self._pending), list(self._pending_blocks)',
      "test_SRV3_one_requests_flush_does_not_send_another_in_flight_requests_misses"),
    m("SRV-3", "a miss waits for EVERY request that recorded it", CLIENT,
      '        return held is None or any(scope.ended for scope in held)',
      '        return held is None or all(scope.ended for scope in held)',
      "test_SRV3_either_request_that_recorded_a_miss_releases_it"),
    m("SRV-3", "a request re-recording a free miss holds it", CLIENT,
      '        if existed and key not in scopes:\n            return\n',
      '',
      "test_SRV3_a_miss_already_free_stays_free_when_a_request_records_it_again"),
    m("SRV-3", "ending a scope does not wake the debounce", "src/langsys/scope.py",
      '    for client in clients:\n        client._scope_released()',
      '    for client in clients:\n        pass',
      "test_SRV3_ending_the_scope_arms_the_debounce"),
    m("SRV-3", "the shutdown flush leaves held misses behind", CLIENT,
      '            if force:\n                phrase_keys = list(self._pending.keys())',
      '            if False:\n                phrase_keys = list(self._pending.keys())',
      "test_SRV3_an_unended_scope_holds_its_misses_until_the_shutdown_flush"),
    m("SRV-3", "collect on the render call", CLIENT,
      '            self._tag(self._phrase_scopes, key, existed)\n        self._schedule_flush()',
      '            self._tag(self._phrase_scopes, key, existed)\n        self.flush_pending(force=True)',
      "test_SRV3_collection_does_not_happen_on_the_render_call",
      "test_SRV3_the_send_happens_off_the_render_call"),
    m("SRV-3", "push from a read-only key", CLIENT,
      '        if decision is False:\n            phrase_count, block_count',
      '        if False:\n            phrase_count, block_count',
      "test_SRV3_a_read_only_key_pushes_nothing"),
    # -- CONF-1: every register/lookup pair ---------------------------------------------------
    m("CONF-1", "look attributes up raw", PARSER,
      "            key = normalize_phrase(value)",
      "            key = value",
      "test_CONF1_an_attribute_is_looked_up_the_way_it_was_registered",
      "test_CONF1_an_nbsp_attribute_is_looked_up_the_way_it_was_registered"),
    m("CONF-1", "look attributes up with trim() (the TS lane's revert shape)", PARSER,
      "            key = normalize_phrase(value)",
      "            key = value.strip()",
      "test_CONF1_attribute_lookup_on_the_block_path",
      "test_CONF1_attribute_lookup_on_the_page_path"),
    m("CONF-1", "look button values up raw", PARSER,
      "        key = normalize_phrase(button_attr)",
      "        key = button_attr",
      "test_CONF1_a_button_value_is_looked_up_the_way_it_was_registered"),
    m("CONF-1", "look text nodes up with trim()", PARSER,
      "    normalized = normalize_phrase(text)",
      "    normalized = text.strip()",
      "test_CONF1_option_text_lookup_on_the_block_path",
      "test_CONF1_option_text_lookup_on_the_page_path"),
    m("CONF-1", "look text nodes up without the %name% rewrite", PARSER,
      "    normalized = normalize_phrase(text)",
      "    normalized = normalize_whitespace(text)",
      "test_TOK5_capture_and_lookup_agree_so_the_translation_is_found"),
    m("CONF-1", "register the title raw", PAGE,
      "        key = normalize_phrase(title.text)",
      "        key = title.text.strip()",
      "test_CONF1_the_title_route_registers_the_collapsed_phrase"),
    m("CONF-1", "register meta content raw", PAGE,
      "    key = normalize_phrase(content)",
      "    key = content",
      "test_CONF1_the_meta_route_registers_the_collapsed_phrase"),
    m("CONF-1", "overwrite an untranslated title with its normalised key", PAGE,
      "            if translated != key:\n                title.text = translated",
      "            title.text = translated",
      "test_CONF1_a_head_miss_keeps_the_markup_as_authored"),
]


def working_tree_files() -> list[str]:
    """Tracked and untracked files, minus what git ignores and what the tree has deleted."""
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [rel for rel in listing.split("\0") if rel and (ROOT / rel).is_file()]


#: What the unit suite reads. Only these can make a mutation result wrong, so only these are
#: checked for change during a run; editing CONFORMANCE.md meanwhile voids nothing.
SUITE_INPUTS = ("src/", "tests/", "pyproject.toml", "README.md")


def tree_digest(files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in (f for f in files if f.startswith(SUITE_INPUTS)):
        digest.update(rel.encode("utf-8") + b"\0" + (ROOT / rel).read_bytes())
    return digest.hexdigest()


def isolated_copy(dest: Path, files: list[str]) -> dict[str, str]:
    """Copy the working tree into `dest` and return the environment that runs against it."""
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    env = {**os.environ, "PYTHONPATH": str(dest / "src")}
    imported = subprocess.run(
        [sys.executable, "-c", "import langsys; print(langsys.__file__)"],
        cwd=dest, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not Path(imported).resolve().is_relative_to(dest.resolve()):
        raise SystemExit(f"refusing to run: langsys imports from {imported}, not the copy at {dest}")
    return env


def run_suite(workdir: Path, env: dict[str, str]) -> tuple[int, list[str]]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "not integration", "-q", "-p", "no:cacheprovider"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, FAILED_LINE.findall(proc.stdout)


def apply(mutation: Mutation, workdir: Path, env: dict[str, str]) -> list[str]:
    target = workdir / mutation.path
    original = target.read_bytes()
    text = original.decode("utf-8")
    found = text.count(mutation.old)
    if found != 1:
        raise SystemExit(
            f"{mutation.rule} / {mutation.name}: anchor matched {found}x in {mutation.path}"
        )
    try:
        target.write_text(text.replace(mutation.old, mutation.new), encoding="utf-8")
        _, failures = run_suite(workdir, env)
    finally:
        target.write_bytes(original)
    if target.read_bytes() != original:
        raise SystemExit(f"{mutation.path} was not restored byte-for-byte")
    return failures


def main(argv: list[str]) -> int:
    wanted = set(argv[1:])
    selected = [m for m in MUTATIONS if not wanted or m.rule in wanted]
    for mutation in selected:  # every anchor resolves before anything runs
        count = (ROOT / mutation.path).read_text(encoding="utf-8").count(mutation.old)
        if count != 1:
            raise SystemExit(f"{mutation.rule} / {mutation.name}: anchor matched {count}x")
    files = working_tree_files()
    before = tree_digest(files)
    with tempfile.TemporaryDirectory(prefix="langsys-mutations-") as scratch:
        workdir = Path(scratch)
        env = isolated_copy(workdir, files)
        print(f"mutating an isolated copy at {workdir}; this tree is not touched")
        uncaught = run_battery(selected, workdir, env)
    if tree_digest(files) != before:
        raise SystemExit("src/, tests/ or pyproject changed in this tree during the run - void")
    return 1 if uncaught else 0


def run_battery(selected: list[Mutation], workdir: Path, env: dict[str, str]) -> int:
    code, failures = run_suite(workdir, env)
    if code != 0 or failures:
        raise SystemExit(f"unmutated suite is not green ({len(failures)} failing); fix that first")

    uncaught = 0
    for mutation in selected:
        failures = apply(mutation, workdir, env)
        missing = [e for e in mutation.expect if not any(e in f for f in failures)]
        caught = bool(failures) and not missing
        uncaught += 0 if caught else 1
        verdict = "caught" if caught else "NOT CAUGHT"
        print(f"\n[{mutation.rule}] {mutation.name}  ({mutation.path})")
        print(f"  {verdict} - {len(failures)} failing test(s), counted from the full output")
        for failure in failures:
            print(f"    {failure}")
        if missing:
            print(f"  named but not failing: {missing}")
    rules = sorted({m.rule for m in selected})
    print(f"\n{len(selected) - uncaught}/{len(selected)} mutations caught by their named tests, "
          f"across {len(rules)} rules: {', '.join(rules)}")
    return uncaught


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
