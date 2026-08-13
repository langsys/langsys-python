from langsys import canonicalize_locale, normalize_locale, parse_accept_language


def test_canonicalize_casing():
    assert canonicalize_locale("en-us") == "en-US"
    assert canonicalize_locale("ES-es") == "es-ES"
    assert canonicalize_locale("pt_br") == "pt-BR"
    assert canonicalize_locale("zh-hant-tw") == "zh-Hant-TW"


def test_canonicalize_empty():
    assert canonicalize_locale("") == ""
    assert canonicalize_locale(None) == ""


def test_normalize_lowercase():
    assert normalize_locale("en_US") == "en-us"
    assert normalize_locale("es-ES") == "es-es"


def test_parse_accept_language_orders_by_quality():
    assert parse_accept_language("fr-FR,fr;q=0.9,es;q=0.8,en;q=0.5") == [
        "fr-FR",
        "fr",
        "es",
        "en",
    ]


def test_parse_accept_language_edge_cases():
    assert parse_accept_language("") == []
    assert parse_accept_language(None) == []
    assert parse_accept_language("*") == []
    # invalid q dropped; wildcard skipped
    assert parse_accept_language("en;q=2,es;q=0.5,*") == ["es"]
