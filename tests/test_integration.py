"""Live integration tests against a real nova backend.

Skipped unless ``LANGSYS_API_KEY`` / ``LANGSYS_PROJECT_ID`` / ``LANGSYS_API_URL`` are
set. Run explicitly with: ``pytest -m integration``.
"""

import os

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

pytestmark = pytest.mark.integration

_HAVE_ENV = all(os.environ.get(k) for k in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID", "LANGSYS_API_URL"))


@pytest.fixture()
def client():
    if not _HAVE_ENV:
        pytest.skip("LANGSYS_* env not configured")
    c = LangsysClient(cache=MemoryCache())
    yield c
    c.close()


def test_authorize(client):
    project = client.authorize()
    assert project.base_locale
    assert client.key_type in ("read", "write")


def test_real_translation(client):
    # Seeded Kangen catalog: 'Technical Support' (CAT_3) is translated in es-es.
    assert client.translate("Technical Support", category="CAT_3", locale="es-ES") == "Soporte Técnico"


def test_untranslated_and_missing_fall_back(client):
    # 'enagic-news-article' exists as null in es -> base phrase.
    assert client.translate("enagic-news-article", category="CAT_5", locale="es-ES") == "enagic-news-article"
    # a phrase that doesn't exist -> base phrase.
    assert client.translate("A phrase that does not exist here", category="Demo", locale="es-ES") == (
        "A phrase that does not exist here"
    )


def test_base_locale_shows_source(client):
    assert client.translate("Technical Support", category="CAT_3", locale="en-US") == "Technical Support"


def test_utilities_localized(client):
    assert client.country_name("DE", "es-ES") == "Alemania"
    assert client.currency_name("USD", "es-ES") == "dólar estadounidense"
    assert len(client.locales_flat("en-US")) > 100
    assert client.locale_name("es-ES", in_locale="en-US") == "Spanish (Spain)"


def test_detect_preferred_locale_live(client):
    supported = ["en-US", "es-ES", "es-CR"]
    assert client.detect_preferred_locale("fr,es;q=0.8", supported) == "es-ES"
    assert client.detect_preferred_locale("en", supported) == "en-US"
    assert client.detect_preferred_locale("xx-YY", supported) is None


def test_write_key_registration():
    """Opt-in: set LANGSYS_WRITE_KEY to exercise registration (writes to the catalog)."""
    import time

    write_key = os.environ.get("LANGSYS_WRITE_KEY")
    if not (_HAVE_ENV and write_key):
        pytest.skip("LANGSYS_WRITE_KEY not set")
    client = LangsysClient(api_key=write_key, cache=MemoryCache())
    assert client.can_write
    phrase = f"langsys-py integration {time.strftime('%Y%m%d-%H%M%S')}"
    resp = client.register_phrases([{"phrase": phrase, "category": "SDKIntegration"}])
    assert resp[0].get("status") is True
    catalog = client.get_translations("es-ES", use_cache=False)
    assert phrase in catalog.get("SDKIntegration", {})
    client.close()
