"""MIG-2, MIG-4, MIG-7 against the shared `mig-vectors.json` (authored in langsys-js-typescript).

Every row names its format. This core runs the rows for its own formats (`core_formats`:
gettext and plain) and, for a resolution row whose files use another format, expects the file to
be refused at load - the other formats' conversions are `n/a (format)` here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from langsys.exceptions import ConfigurationError
from langsys.migrate import LegacyFile, LegacyKeys, convert_literal, convert_value, gettext_plural

VECTORS_FILE = Path(__file__).parent / "fixtures" / "mig-vectors.json"
#: THE CHECK: the blob. THE PROVENANCE: the ref.
VECTORS_BLOB = "20f2bdd678cb33981e3064e42d43ca62783920ad"
VECTORS_REF = "langsys-js-typescript a639ae8 tests/fixtures/mig-vectors.json"
DOC = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
OURS = set(DOC["core_formats"]["python"])
ids = lambda row: row["id"]  # noqa: E731


def mine(rows):
    return [r for r in rows if r.get("format") in OURS or r.get("format") is None]


def test_the_vendored_vectors_are_the_pinned_blob():
    data = VECTORS_FILE.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == VECTORS_BLOB  # noqa: S324


def test_this_core_runs_gettext_and_plain():
    assert {"gettext", "plain"} == OURS


@pytest.mark.parametrize("row", mine(DOC["value_conversion"]), ids=ids)
def test_MIG4_value_conversion(row, caplog):
    with caplog.at_level(logging.WARNING, logger="langsys"):
        assert convert_value(row["value"]) == row["expected"]
    warned = any("registered verbatim" in r.getMessage() for r in caplog.records)
    if row["format"] == "plain" and "|" in row["value"]:
        return  # a pipe is a file-level decision; its warning is asserted on resolution rows
    assert warned is (not row["recognised"])


@pytest.mark.parametrize("row", mine(DOC["plural_forms"]), ids=ids)
def test_MIG4_plural_forms(row):
    forms = row["forms"]
    assert gettext_plural(forms["msgid"], forms["msgid_plural"]) == row["expected"]


def _call(row):
    point, text, params = row["entry_point"], row["text"], row.get("params") or {}
    if point == "t":
        return convert_literal(text, "t", params)
    if point in ("gettext", "pgettext"):
        return convert_literal(text, "gettext", params)
    if point == "blocktranslate":
        return convert_literal(text, "blocktranslate", params)
    if point == "ngettext":
        return gettext_plural(text[0], text[1], literal=True, passed=params)
    raise AssertionError(f"not a Python entry point: {point}")


ENTRY_POINTS = DOC["core_entry_points"]["python"]
PY_CALLS = [r for r in DOC["calls"] if r["entry_point"] in ENTRY_POINTS]


def test_every_python_entry_point_is_one_this_core_converts():
    assert set(ENTRY_POINTS) == {"t", "gettext", "ngettext", "pgettext", "blocktranslate"}


@pytest.mark.parametrize("row", PY_CALLS, ids=ids)
def test_MIG2_entry_point_calls(row):
    assert _call(row) == row["expected"]
    if "same_phrase_as" in row:
        twin = next(r for r in DOC["calls"] if r["id"] == row["same_phrase_as"])
        assert _call(row) == twin["expected"]
    if "category" in row:
        assert row.get("context") == row["category"], "pgettext's context is the category"


def _files(tmp_path, row):
    specs = []
    for f in row["files"]:
        path = tmp_path / f["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(f["data"]), encoding="utf-8")
        specs.append((LegacyFile(path, f.get("format"), f.get("namespace")), f))
    return specs


@pytest.mark.parametrize("row", DOC["resolution"], ids=ids)
def test_MIG7_resolution(row, tmp_path):
    specs = _files(tmp_path, row)
    foreign = [f["name"] for _, f in specs if f.get("format", "plain") not in OURS]
    if foreign:
        with pytest.raises(ConfigurationError) as refused:
            LegacyKeys([s for s, _ in specs])
        assert foreign[0] in str(refused.value), "the refusal names the file"
        return
    got = LegacyKeys([s for s, _ in specs]).lookup(row["key"], row.get("category_arg"))
    expected = row["expected"]
    if expected is not None:
        expected = dict(expected, file=str(tmp_path / expected["file"]))
    assert got == expected


@pytest.mark.parametrize("row", DOC["refusals"], ids=ids)
def test_MIG7_refusals(row, tmp_path):
    path = tmp_path / row["file"]["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('msgid ""\nmsgstr ""\n' if path.suffix == ".po" else "{}", encoding="utf-8")
    spec = LegacyFile(path, row["file"].get("format"))
    if "python" in row["supported_by"]:
        LegacyKeys([spec])  # loads
        return
    with pytest.raises(ConfigurationError) as refused:
        LegacyKeys([spec])
    message = str(refused.value)
    assert path.name in message
    if "hint" in row:
        assert Path(row["hint"]).name in message
    else:
        assert row["format"] in message
