"""Sheet-source: URL detection, Google-Sheets export resolution, redirect re-targeting,
and the permission-page guard — all network-free via a monkeypatched fetcher."""
import os

import pytest

from ncbl import sheet_source as SS


def test_is_url():
    assert SS.is_url("https://docs.google.com/spreadsheets/d/ABC/edit")
    assert SS.is_url("http://example.com/x.csv")
    assert not SS.is_url("/local/path/sheet.xlsx")
    assert not SS.is_url("sheet.xlsx")


def test_resolve_google_sheet_to_xlsx_export():
    url = "https://docs.google.com/spreadsheets/d/1AbC_dEF-123/edit#gid=42"
    dl, suffix = SS.resolve(url)
    assert dl == "https://docs.google.com/spreadsheets/d/1AbC_dEF-123/export?format=xlsx"
    assert suffix == ".xlsx"


def test_resolve_direct_csv_link_is_passthrough():
    dl, suffix = SS.resolve("https://example.com/data/rankings.csv")
    assert dl == "https://example.com/data/rankings.csv" and suffix == ".csv"


def test_resolve_unknown_link_assumes_xlsx():
    dl, suffix = SS.resolve("https://tinyurl.com/NCBL2026Rankings")
    assert suffix == ".xlsx"


def test_fetch_writes_xlsx_tempfile(monkeypatch):
    monkeypatch.setattr(SS, "_get", lambda url, timeout: (b"PK\x03\x04payload", url))
    path = SS.fetch("https://example.com/x.xlsx")
    try:
        assert os.path.exists(path) and path.endswith(".xlsx")
        assert open(path, "rb").read() == b"PK\x03\x04payload"
    finally:
        os.remove(path)


def test_fetch_follows_shortener_to_gsheet_export(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        if "tinyurl" in url:                       # shortener -> HTML edit page
            return b"<html>sheet</html>", "https://docs.google.com/spreadsheets/d/XYZ/edit#gid=0"
        return b"PK\x03\x04realbook", url          # the re-targeted export URL

    monkeypatch.setattr(SS, "_get", fake_get)
    path = SS.fetch("https://tinyurl.com/NCBL2026Rankings")
    try:
        assert calls[-1] == "https://docs.google.com/spreadsheets/d/XYZ/export?format=xlsx"
        assert open(path, "rb").read() == b"PK\x03\x04realbook"
    finally:
        os.remove(path)


def test_fetch_rejects_permission_html(monkeypatch):
    # a private sheet returns an HTML sign-in page, not a PK zip -> clear error, no temp file
    monkeypatch.setattr(SS, "_get", lambda url, timeout: (b"<!DOCTYPE html> sign in", url))
    with pytest.raises(RuntimeError, match="sign-in|permission|spreadsheet"):
        SS.fetch("https://docs.google.com/spreadsheets/d/PRIV/edit")


def test_fetch_network_error_is_actionable(monkeypatch):
    def boom(url, timeout):
        raise OSError("Name or service not known")
    monkeypatch.setattr(SS, "_get", boom)
    with pytest.raises(RuntimeError, match="could not download"):
        SS.fetch("https://docs.google.com/spreadsheets/d/ABC/edit")
