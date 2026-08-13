import re

from langsys import LangsysClient, generate_custom_id
from langsys.cache import MemoryCache
from langsys.html import apply_block_translations, extract_phrases

API = "https://api.test/api"
TRANS = re.compile(r"https://api\.test/api/translations")


def make(**kw):
    return LangsysClient("k", "proj-1", api_url=API, cache=MemoryCache(), **kw)


def catalog(data):
    return {"status": True, "words": 0, "untranslatedWords": 0, "data": data}


# -- pure extraction / application (offline) ----------------------------------


def test_extract_phrases_text_attributes_and_buttons():
    html = (
        '<div><h2>Welcome</h2>'
        '<img alt="A photo" src="x.png">'
        '<input type="text" placeholder="Your name">'
        '<button value="Send">Click</button>'
        '<span translate="no">DoNotTranslate</span>'
        '<span data-notrans="1">SkipMe</span></div>'
    )
    phrases = extract_phrases(html)
    assert "Welcome" in phrases
    assert "A photo" in phrases  # alt
    assert "Your name" in phrases  # placeholder
    assert "Send" in phrases  # button value
    assert "Click" in phrases  # button text
    assert "DoNotTranslate" not in phrases
    assert "SkipMe" not in phrases


def test_extract_normalizes_whitespace_and_preserves_order():
    assert extract_phrases("<p>  Hello   world  </p>") == ["Hello world"]
    assert extract_phrases("<div><span>One</span><span>Two</span></div>") == ["One", "Two"]


def test_apply_block_translations_text_and_attributes():
    html = '<div><h2>Welcome</h2><input placeholder="Your name"></div>'
    out = apply_block_translations(
        html, {"Welcome": "Bienvenido", "Your name": "Tu nombre"}
    )
    assert "Bienvenido" in out and 'placeholder="Tu nombre"' in out


def test_apply_skips_translate_no():
    html = '<div><span translate="no">Keep</span><span>Change</span></div>'
    out = apply_block_translations(html, {"Keep": "NO", "Change": "Cambiado"})
    assert "Keep" in out and "NO" not in out and "Cambiado" in out


# -- content blocks via the client (mocked backend) ---------------------------


def test_translate_content_block_applies_when_present(httpx_mock):
    html = "<div><h2>Technical Support</h2><p>Customer Support</p></div>"
    phrases = extract_phrases(html)
    cid = generate_custom_id("CAT_3", phrases)
    httpx_mock.add_response(
        url=TRANS,
        json=catalog(
            {"CAT_3": {cid: {"Technical Support": "Soporte Técnico", "Customer Support": "Atención"}}}
        ),
    )
    client = make()
    client.set_locale("es-ES")
    out = client.translate_content_block(html, category="CAT_3")
    assert "Soporte Técnico" in out and "Atención" in out
    assert client.pending_content_blocks == []


def test_translate_content_block_queues_when_missing(httpx_mock):
    httpx_mock.add_response(url=TRANS, json=catalog({"CAT_3": {}}))
    client = make()
    client.set_locale("es-ES")
    html = "<div><p>Brand new block phrase</p></div>"
    assert client.translate_content_block(html, category="CAT_3") == html
    assert len(client.pending_content_blocks) == 1


# -- full page ----------------------------------------------------------------


def test_translate_page_head_and_body(httpx_mock):
    page = (
        "<!doctype html><html><head><title>Save</title>"
        '<meta name="description" content="Welcome"></head>'
        "<body><h1>Save</h1><p>Cancel</p>"
        '<nav data-langsys-category="Nav"><span>Home</span></nav></body></html>'
    )
    httpx_mock.add_response(
        url=TRANS,
        json=catalog(
            {
                "UI": {"Save": "Guardar", "Welcome": "Bienvenido", "Cancel": "Cancelar"},
                "Nav": {"Home": "Inicio"},
            }
        ),
    )
    client = make()
    client.set_locale("es-ES")
    out = client.translate_page(page, category="UI")
    assert re.search(r"<title>Guardar</title>", out)
    assert 'content="Bienvenido"' in out
    assert "Guardar" in out and "Cancelar" in out  # body simple phrases
    assert "Inicio" in out  # nav uses data-langsys-category="Nav"
    assert 'lang="es-ES"' in out


def test_translate_page_skips_translate_no(httpx_mock):
    page = '<html><body><p translate="no">Keep</p><p>Save</p></body></html>'
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {"Save": "Guardar", "Keep": "NO"}}))
    client = make()
    client.set_locale("es-ES")
    out = client.translate_page(page, category="UI")
    assert "Keep" in out and "NO" not in out and "Guardar" in out
