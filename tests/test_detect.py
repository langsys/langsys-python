from langsys import detect_preferred_locale
from langsys.locale import find_best_locale_match


def test_exact_match_wins():
    assert detect_preferred_locale("es-ES,en;q=0.5", ["en-US", "es-ES"]) == "es-ES"


def test_language_only_via_likely_subtags():
    # 'en' should match 'en-US' through en-Latn.
    assert detect_preferred_locale("en", ["en-US", "es-ES"]) == "en-US"
    # 'zh-TW' should match a zh-Hant supported locale.
    assert detect_preferred_locale("zh-TW", ["zh-Hant-TW", "en-US"]) == "zh-Hant-TW"


def test_no_match_returns_none():
    assert detect_preferred_locale("xx-YY", ["en-US", "es-ES"]) is None


def test_no_supported_returns_first_preference_canonical():
    assert detect_preferred_locale("fr-fr,en;q=0.5") == "fr-FR"


def test_empty_header_returns_none():
    assert detect_preferred_locale("", ["en-US"]) is None
    assert detect_preferred_locale(None) is None


def test_quality_ordering_picks_best_supported():
    # es has higher q than en, and both supported -> es wins.
    assert detect_preferred_locale("en;q=0.5,es;q=0.9", ["en-US", "es-ES"]) == "es-ES"


def test_find_best_locale_match_returns_canonical_supported():
    assert find_best_locale_match(["es-es"], ["en-US", "es-ES"]) == "es-ES"
    assert find_best_locale_match(["de"], ["en-US", "es-ES"]) is None
