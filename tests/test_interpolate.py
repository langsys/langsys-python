import datetime

from langsys import interpolate, is_icu


def test_is_icu_detection():
    assert is_icu("{n, plural, one {#} other {#}}")
    assert is_icu("{n, number}")
    assert is_icu("{d, date, medium}")
    assert not is_icu("Hello {name}")
    assert not is_icu("no placeholders")


def test_simple_substitution():
    assert interpolate("Hi {name}!", {"name": "Sarah"}, "en-US") == "Hi Sarah!"


def test_unknown_or_none_key_left_visible():
    assert interpolate("Hi {name}", {}, "en-US") == "Hi {name}"
    assert interpolate("Hi {name}", {"name": None}, "en-US") == "Hi {name}"


def test_number_formatting_is_locale_aware():
    assert interpolate("T: {n}", {"n": 1234.5}, "en-US") == "T: 1,234.5"
    assert interpolate("T: {n}", {"n": 1234.5}, "es-ES") == "T: 1.234,5"


def test_string_number_opts_out_of_grouping():
    assert interpolate("id {n}", {"n": "1234"}, "en-US") == "id 1234"


def test_bool_renders_lowercase_word():
    assert interpolate("{ok}", {"ok": True}, "en") == "true"
    assert interpolate("{ok}", {"ok": False}, "en") == "false"


def test_date_formatting():
    out = interpolate("On {d}", {"d": datetime.date(2026, 7, 7)}, "es-ES")
    assert "2026" in out and out != "On {d}"


def test_icu_plural_english():
    msg = "You have {count, plural, one {# new message} other {# new messages}}."
    assert interpolate(msg, {"count": 1}, "en-US") == "You have 1 new message."
    assert interpolate(msg, {"count": 5}, "en-US") == "You have 5 new messages."


def test_icu_plural_russian_categories():
    msg = "{n, plural, one {# книга} few {# книги} many {# книг} other {# книги}}"
    assert interpolate(msg, {"n": 1}, "ru") == "1 книга"
    assert interpolate(msg, {"n": 2}, "ru") == "2 книги"
    assert interpolate(msg, {"n": 5}, "ru") == "5 книг"


def test_icu_plural_exact_match_and_offset():
    msg = "{n, plural, =0 {none} one {# item} other {# items}}"
    assert interpolate(msg, {"n": 0}, "en") == "none"
    offset = "{n, plural, offset:1 =0 {nobody} one {you and one other} other {you and # others}}"
    assert interpolate(offset, {"n": 3}, "en") == "you and 2 others"


def test_icu_select():
    msg = "{g, select, male {He} female {She} other {They}} won"
    assert interpolate(msg, {"g": "female"}, "en") == "She won"
    assert interpolate(msg, {"g": "x"}, "en") == "They won"


def test_malformed_icu_degrades_without_raising():
    # Looks like ICU (matches detector) but is unbalanced -> falls back to simple.
    out = interpolate("{n, plural, one {oops", {"n": 1}, "en")
    assert isinstance(out, str)


# -- a missing ICU argument (same recovery as langsys-js-typescript 0.6.4 and langsys-php 1.3.1) --
#
# Reachable with no caller mistake: Langsys promotes a plain "{username}" phrase to
# "{username_gender, select, …}" in gendered target locales, so the app never passes
# username_gender. It must read as a sentence, not as "{username_gender}".

GENDERED = (
    "{username_gender, select, female {{username} ha sido invitada} "
    "male {{username} ha sido invitado} other {{username} ha sido invitade}}"
)


def test_missing_select_argument_takes_the_other_branch():
    assert interpolate(GENDERED, {"username": "Ana"}, "es-ES") == "Ana ha sido invitade"


def test_none_select_argument_counts_as_missing():
    assert interpolate(GENDERED, {"username": "Ana", "username_gender": None}, "es-ES") == "Ana ha sido invitade"


def test_supplied_select_argument_is_untouched():
    assert interpolate(GENDERED, {"username": "Ana", "username_gender": "female"}, "es-ES") == "Ana ha sido invitada"


def test_missing_plural_argument_takes_other_with_a_visible_gap():
    msg = "{count, plural, one {Tienes # mensaje nuevo.} other {Tienes # mensajes nuevos.}}"
    assert interpolate(msg, {}, "es-ES") == "Tienes {count} mensajes nuevos."
    # None is missing too; a real 0 still renders as a number.
    assert interpolate(msg, {"count": None}, "es-ES") == "Tienes {count} mensajes nuevos."
    assert interpolate(msg, {"count": 0}, "es-ES") == "Tienes 0 mensajes nuevos."


def test_missing_simple_and_number_arguments_stay_visible():
    assert interpolate("{n, number} por {who}, {g, select, other {ok}}", {}, "es-ES") == "{n} por {who}, ok"


def test_missing_argument_in_an_unchosen_branch_costs_nothing():
    msg = "{g, select, f {Ella} other {{n, plural, one {# amiga} other {# amigas}}}}"
    assert interpolate(msg, {"g": "f"}, "es-ES") == "Ella"
    assert interpolate(msg, {}, "es-ES") == "{n} amigas"


def test_select_without_other_is_left_as_a_visible_slot():
    # Malformed: nothing safe to choose, so it stays visible rather than guessing.
    assert interpolate("{g, select, male {He} female {She}} won", {}, "en") == "{g} won"
