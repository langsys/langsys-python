"""Live integration tests against a real nova backend.

Skipped unless ``LANGSYS_API_KEY`` / ``LANGSYS_PROJECT_ID`` / ``LANGSYS_API_URL`` are
set. Run explicitly with: ``pytest -m integration``.

The seeded per-SDK project carries all three key types on one project, because the 838
surface is a **gating matrix** and one key cannot express it. Set ``LANGSYS_READ_KEY``
and ``LANGSYS_IPWRITE_KEY`` to exercise the GATE arms live.

**Registration asserts the HTTP layer only.** The local stack deliberately runs with
its queue workers down, so a registration POST is accepted and enqueued but never
processed — asserting on catalog contents afterwards would fail for a reason that has
nothing to do with this SDK.
"""

import os

import pytest

from langsys import LangsysClient
from langsys.cache import MemoryCache

pytestmark = pytest.mark.integration

_HAVE_ENV = all(
    os.environ.get(k) for k in ("LANGSYS_API_KEY", "LANGSYS_PROJECT_ID", "LANGSYS_API_URL")
)


@pytest.fixture()
def client():
    if not _HAVE_ENV:
        pytest.skip("LANGSYS_* env not configured")
    c = LangsysClient(cache=MemoryCache())
    yield c
    c.close()


def _client_for(key_env):
    key = os.environ.get(key_env)
    if not (_HAVE_ENV and key):
        pytest.skip(f"{key_env} not set")
    return LangsysClient(api_key=key, cache=MemoryCache())


def test_authorize(client):
    project = client.authorize()
    assert project.base_locale
    assert client.key_type in ("read", "write", "ip_write")


def test_real_translation(client):
    # Seeded catalog: 'Technical Support' (CAT_3) is translated in es-es.
    assert client.translate("Technical Support", category="CAT_3", locale="es-es") == (
        "Soporte Técnico"
    )


def test_locale_casing_resolves_to_the_same_entry(client):
    """WIRE-3 — `es-ES` from a host application's locale store must resolve to the
    same catalog entry as `es-es`, on the deprecated route that does not normalise."""
    assert client.translate("Technical Support", category="CAT_3", locale="es-ES") == (
        "Soporte Técnico"
    )


def test_untranslated_and_missing_fall_back(client):
    assert client.translate("A phrase that does not exist here", category="Demo", locale="es-es") == (
        "A phrase that does not exist here"
    )


def test_base_locale_shows_source(client):
    assert client.translate("Technical Support", category="CAT_3", locale="en-us") == (
        "Technical Support"
    )


def test_utilities_localized(client):
    assert client.country_name("DE", "es-es") == "Alemania"
    assert len(client.locales_flat("en-us")) > 100


# -- GATE, live ---------------------------------------------------------------


def test_GATE1_the_write_key_is_write_enabled():
    assert _client_for("LANGSYS_API_KEY").can_write is True


def test_GATE1_the_read_key_is_not_write_enabled():
    """The server computes this; the SDK must not infer it from the key string."""
    c = _client_for("LANGSYS_READ_KEY")
    assert c.key_type == "read"
    assert c.can_write is False


def test_GATE1_an_ip_write_key_is_write_enabled_from_an_allow_listed_address():
    """The vector no client-side value can express: the same key answers differently
    from different addresses. The seeded key is loopback-only, and this suite runs on
    loopback, so the answer here is true."""
    c = _client_for("LANGSYS_IPWRITE_KEY")
    assert c.key_type == "ip_write"
    assert c.can_write is True


def test_GATE1_write_enabled_is_on_both_endpoint_shapes():
    """Location differs by endpoint and getting it wrong reads as false: inside
    ``data`` on authorize, at envelope level on translations."""
    c = _client_for("LANGSYS_API_KEY")
    data = c._authorize_data()
    assert isinstance(data.get("write_enabled"), bool), "absent from the authorize payload"

    fetch = c._catalog.get("es-es", use_cache=False)
    assert fetch.ok
    assert isinstance(fetch.write_enabled, bool), "absent from the translations envelope"


def test_GATE4_the_decision_never_reaches_the_cache():
    c = _client_for("LANGSYS_API_KEY")
    c.authorize()
    cached = c._cache.get(f"auth_{c._config.project_id}")
    assert cached is not None
    assert "write_enabled" not in cached


def test_no_write_grant_header_is_ever_sent():
    """Server posture is affirmative non-participation: this SDK holds a write key and
    takes the ``type-allows-write`` arm only. If this ever changes, the key_type
    short-circuit documented alongside GATE-1 stops being sound."""
    c = _client_for("LANGSYS_API_KEY")
    assert "X-Write-Grant" not in c._http._client.headers
    assert "x-write-grant" not in {k.lower() for k in c._http._client.headers}


# -- registration, HTTP layer only --------------------------------------------


def test_write_key_registration_is_accepted():
    """Asserts ACCEPTANCE, not catalog contents: the local stack's queue workers are
    deliberately down, so the item is enqueued and never processed."""
    import time

    c = _client_for("LANGSYS_API_KEY")
    phrase = f"langsys-py integration {time.strftime('%Y%m%d-%H%M%S')}"
    responses = c.register_phrases([{"phrase": phrase, "category": "SDKIntegration"}])
    assert responses and responses[0].get("status") is not False
    c.close()


def test_REG10_a_read_key_flush_reports_failure_rather_than_success():
    c = _client_for("LANGSYS_READ_KEY")
    c.translate("A brand new phrase for the read key", category="SDKIntegration", locale="es-es")
    assert c.has_pending
    result = c.flush_pending()
    assert result["success"] is False
    assert result["skipped"] is True
    c.close()


def test_WIRE4_an_unreachable_api_degrades_rather_than_throwing():
    """The rule that takes a customer's site down when it is broken."""
    c = LangsysClient(
        api_key="k", project_id="p", api_url="http://127.0.0.1:9/api", cache=MemoryCache()
    )
    assert c.translate("Technical Support", category="CAT_3") == "Technical Support"
    assert c.pending_phrases == []
    c.close()
