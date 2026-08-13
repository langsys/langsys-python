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
