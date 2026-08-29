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
    """A `select` with no `other` branch is malformed; ICU-1 says leave it rather
    than inventing a fallback. Degrading to simple interpolation is this SDK's
    normal handling for malformed ICU."""
    assert interpolate("{gender, select, male{M} female{F}}", {}, "en-US") == (
        "{gender, select, male{M} female{F}}"
    )


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
