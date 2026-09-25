"""ICU-1..5 — interpolation recovery.

The file opens with the ICU-5 regression guard, and that ordering is deliberate:
ICU-5 passed *before* recovery existed, because this SDK has a single Babel-CLDR
renderer with no simplified path to degrade into. The tempting way to implement
ICU-1 is a second, simpler renderer for templates that have a missing argument —
which is exactly what ICU-5 prohibits, and it would degrade only the locales with
a plural set richer than English's. So the guard is written to fail if anyone ever
adds that path, and it must stay green through every change below.

The Polish vectors give ``one``/``few``/``many``/``other`` **distinct branch text**.
An earlier draft of this guard reused the same string in every branch, which could
not have failed whatever the renderer did — the discriminating-vector trap the spec
names. Keep the branch texts distinct.
"""

from __future__ import annotations

import logging

import pytest

from langsys.interpolate import interpolate

# Distinct text per CLDR category, so a wrong category is visible in the output.
PL_PLURAL = "{n, plural, one{ONE} few{FEW} many{MANY} other{OTHER}}"
# One select (recovered when `gender` is absent) beside one plural (always supplied).
MIXED = "{gender, select, male{M} female{F} other{O}} / " + PL_PLURAL

# CLDR Polish: 1 -> one, 3 -> few, 5 -> many.
PL_CATEGORIES = [(1, "ONE"), (3, "FEW"), (5, "MANY")]


# -- ICU-5: supplied arguments keep CLDR selection ----------------------------


@pytest.mark.parametrize(("n", "expected"), PL_CATEGORIES)
def test_supplied_plural_keeps_cldr_selection_when_nothing_is_missing(n, expected):
    """Positive control for the guard below: with every argument supplied, the
    renderer must already pick the right Polish category. If this fails, the guard
    that follows proves nothing."""
    assert interpolate(MIXED, {"n": n, "gender": "male"}, "pl-PL") == f"M / {expected}"


@pytest.mark.parametrize(("n", "expected"), PL_CATEGORIES)
def test_ICU5_missing_select_does_not_degrade_the_supplied_plural(n, expected):
    """ICU-5 — recovery rewrites only the missing node.

    `gender` is absent, so the select recovers. The plural was supplied and MUST
    keep full CLDR selection: Polish `few` at 3 and `many` at 5, never `other`.
    A second simplified renderer knows only =N/one/other and would return OTHER
    for both."""
    out = interpolate(MIXED, {"n": n}, "pl-PL")
    assert out.endswith(f"/ {expected}"), f"plural degraded to {out!r} for n={n}"


def test_ICU2_a_present_but_null_select_still_recovers_to_other():
    """ICU-2 + ICU-1 — null is absent, and absence on a branching node means the
    `other` branch. (Not the ICU-5 literal case: there is no literal here, because
    recovery replaced the whole node with its branch.)"""
    assert interpolate(MIXED, {"n": 3, "gender": None}, "pl-PL") == "O / FEW"


def test_ICU5_the_ICU3_marker_survives_a_present_but_null_count():
    """ICU-5 — the recovered `{argName}` literal must survive formatting even when
    the argument is present-and-null.

    This is the vector the rule names: a formatter handed `n=None` substitutes the
    marker with an empty string, erasing the visible gap ICU-3 just created and
    turning it back into the silent hole. `" items"` or `"0 items"` here is the
    failure; `"{n} items"` is the requirement."""
    out = interpolate(PLURAL_EN, {"n": None}, "en-US")
    assert out == "{n} items"
    assert "0" not in out


def test_ICU5_a_plain_argument_that_is_null_stays_visible():
    """The plain-argument path, for the same reason: `"{amt} due"` is obviously a
    bug and costs a report; `" due"` and `"0 due"` are plausible and false."""
    assert interpolate("{amt, number} due", {"amt": None}, "en-US") == "{amt} due"


# -- ICU-1 / ICU-2: a missing argument selects `other` ------------------------

SELECT_ES = "{gender, select, male{Bienvenido} female{Bienvenida} other{Bienvenido/a}}"
PLURAL_EN = "{n, plural, one{# item} other{# items}}"


def test_ICU1_missing_select_argument_renders_the_other_branch():
    assert interpolate(SELECT_ES, {}, "es-ES") == "Bienvenido/a"


def test_ICU1_supplied_select_argument_still_wins():
    """Positive control — recovery must not swallow a supplied value."""
    assert interpolate(SELECT_ES, {"gender": "female"}, "es-ES") == "Bienvenida"


def test_ICU2_null_counts_as_missing_and_selects_other():
    assert interpolate(SELECT_ES, {"gender": None}, "es-ES") == "Bienvenido/a"


def test_ICU1_missing_plural_argument_renders_the_other_branch():
    assert interpolate(PLURAL_EN, {}, "en-US") == "{n} items"


def test_ICU1_malformed_node_without_other_is_left_to_normal_handling():
    """A `select` with no `other` branch is malformed; ICU-1 leaves it to normal error handling,
    which is ICU-6's recovery: the unsupplied value stays the visible `{gender}`, never the raw
    construct."""
    assert interpolate("{gender, select, male{M} female{F}}", {}, "en-US") == "{gender}"


# -- ICU-3: recursion, and `#` becomes the visible argument name --------------


def test_ICU3_hash_in_a_recovered_plural_prints_the_argument_name():
    """`#` has no count to render inside a recovered plural. A plausible `0`
    states something false; `{n}` is visibly a gap."""
    assert interpolate(PLURAL_EN, {}, "en-US") == "{n} items"
    assert "0" not in interpolate(PLURAL_EN, {}, "en-US")


def test_ICU3_recovery_descends_into_nested_nodes():
    """An argument missing two levels down recovers the same way as one at the top."""
    template = "{a, select, other{{b, select, x{X} other{deep-other}}}}"
    assert interpolate(template, {}, "en-US") == "deep-other"


def test_ICU3_recovery_descends_into_a_supplied_branch():
    """The chosen branch of a *supplied* select still recovers its own missing args."""
    template = "{gender, select, male{{n, plural, other{#-ish}}} other{O}}"
    assert interpolate(template, {"gender": "male"}, "en-US") == "{n}-ish"


# -- ICU-4: recovery must be observable ---------------------------------------


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture()
def captured_debug():
    handler = _Capture()
    log = logging.getLogger("langsys")
    previous_level, previous_propagate = log.level, log.propagate
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)
        log.propagate = previous_propagate


def test_ICU4_emits_a_notice_naming_the_argument_and_the_locale(captured_debug):
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    interpolate(SELECT_ES, {}, "es-ES")
    joined = " ".join(captured_debug.messages)
    assert "gender" in joined, f"notice did not name the argument: {captured_debug.messages}"
    assert "es-ES" in joined, f"notice did not name the locale: {captured_debug.messages}"


def test_ICU4_fires_for_plural_recoveries_too(captured_debug):
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    interpolate(PLURAL_EN, {}, "en-US")
    assert any("n" in m for m in captured_debug.messages)


def test_ICU4_names_every_defaulted_argument(captured_debug):
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    interpolate("{a, select, other{A}} {b, select, other{B}}", {}, "en-US")
    joined = " ".join(captured_debug.messages)
    assert "a" in joined and "b" in joined


def test_ICU4_deduplicates_per_template_and_locale(captured_debug):
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    for _ in range(5):
        interpolate(SELECT_ES, {}, "es-ES")
    recovery_notices = [m for m in captured_debug.messages if "gender" in m]
    assert len(recovery_notices) == 1, f"expected one notice, got {recovery_notices}"


def test_ICU4_a_different_locale_notices_again(captured_debug):
    """Dedup is per (template, locale), not per template."""
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    interpolate(SELECT_ES, {}, "es-ES")
    interpolate(SELECT_ES, {}, "fr-FR")
    assert len([m for m in captured_debug.messages if "gender" in m]) == 2


def test_ICU4_is_silent_when_debug_logging_is_off():
    """A notice that ignores the log level warns in production on every render."""
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    handler = _Capture()
    log = logging.getLogger("langsys")
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    try:
        interpolate(SELECT_ES, {}, "es-ES")
    finally:
        log.removeHandler(handler)
        log.setLevel(logging.NOTSET)
    assert handler.messages == []



# -- ICU-6: what the formatter cannot render goes through our own branch selection ---------------

FAILURES = [
    ("You have {count, plural, one {{count} car} other {{count} cars}} for {price, spellout}",
     {"count": 3, "price": 5}, "You have 3 cars for 5"),
    ("{count, plural, one {# car}}", {"count": 3}, "3"),
    ("You have {count, plural, one {# car} other {# cars}", {"count": 3}, "You have 3 cars"),
    ("You have {count, plural, one {# car} other {# cars}", {"count": 1}, "You have 1 car"),
    ("{g, select, f {Ella}}", {"g": "m"}, "m"),
    ("{g, select, f {Ella} other {{n, plural, one {# amigo}}}", {"g": "m"}, "{n}"),
]
FAILURE_IDS = ["unsupported-type", "no-branch-fits", "unbalanced-3", "unbalanced-1",
               "select-no-branch", "nested-missing"]


@pytest.mark.parametrize(("template", "params", "expected"), FAILURES, ids=FAILURE_IDS)
def test_ICU6_a_phrase_the_formatter_cannot_render_goes_through_branch_selection(
    template, params, expected
):
    rendered = interpolate(template, params, "en")
    assert rendered == expected
    assert rendered != "" and ", plural," not in rendered and ", select," not in rendered


def _failure_warnings(caplog):
    return [r for r in caplog.records
            if r.levelname == "WARNING" and "could not render" in r.getMessage()]


def test_ICU6_warns_with_debug_off_naming_the_phrase_the_locale_and_the_error(caplog):
    template = "Total {price, spellout}"
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert interpolate(template, {"price": 5}, "it-IT") == "Total 5"
    [record] = _failure_warnings(caplog)
    message = record.getMessage()
    assert template in message and "it-IT" in message and "spellout" in message


def test_ICU6_the_warning_is_deduplicated_per_template_and_locale(caplog):
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    template = "{count, plural, one {# car}}"
    with caplog.at_level(logging.WARNING, logger="langsys"):
        for _ in range(3):
            interpolate(template, {"count": 3}, "en")
        interpolate(template, {"count": 3}, "de")
    assert len(_failure_warnings(caplog)) == 2


def test_ICU6_control_the_shared_vector_renders_natively_and_warns_nothing(caplog):
    """`{count}` inside a branch of its own plural: another platform's formatter fails on it,
    ours does not, so this is the direct pass - and it must not raise a false alarm."""
    template = "You have {count, plural, one {{count} car} other {{count} cars}}"
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert interpolate(template, {"count": 3}, "en") == "You have 3 cars"
        assert interpolate(template, {"count": 1}, "en") == "You have 1 car"
    assert _failure_warnings(caplog) == []


@pytest.mark.parametrize(("template", "params", "expected"), [
    ("{g, select, male {He} female {She} other {They}} left", {}, "They left"),
    ("{count, plural, one {# item} other {# items}}", {}, "{count} items"),
    ("{count, plural, one {# item} other {# items}}", {"count": None}, "{count} items"),
], ids=["missing-select", "missing-plural", "null-count"])
def test_ICU1_ICU2_recovery_is_normal_and_never_raises_the_formatter_failure_warning(
    template, params, expected, caplog
):
    """A missing or null argument is the ordinary case ICU-1/ICU-2 recover from. ICU-6's warning
    is for a phrase the formatter cannot render at all; raising it here would make every
    gendered phrase a false alarm - and hide a recovery that had stopped working behind the
    fallback that happens to render the same text."""
    from langsys.interpolate import reset_recovery_notices

    reset_recovery_notices()
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert interpolate(template, params, "en") == expected
    assert _failure_warnings(caplog) == []
