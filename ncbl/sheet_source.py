"""Fetch the league sheet straight from a shareable link (no manual download).

Given a **Google Sheets URL**, we build its workbook-export URL and download the
whole workbook as .xlsx — every tab is preserved (with its name), so both the
Data-Entry and Rankings tabs come through just like a File -> Download. A direct
http(s) link to a .xlsx/.csv is fetched as-is.

The sheet must be **link-viewable** ("Anyone with the link -> Viewer") or
published to the web. A private sheet that requires a Google login can't be read
without credentials — download it once and pass the file path instead. We detect
the sign-in/permission HTML page Google returns in that case and raise a clear,
actionable error rather than feeding garbage to the loader.

Only the Python standard library is used (urllib) — no extra dependency.
"""
from __future__ import annotations
import os
import re
import tempfile
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; ncbl-pipeline/1.0)"


def is_url(s):
    return isinstance(s, str) and re.match(r"https?://", s.strip()) is not None


def _gsheet_id(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def resolve(url):
    """Map a sheet link to (download_url, file_suffix).

    Google Sheets -> the whole-workbook xlsx export (keeps every tab + name).
    A direct .xlsx/.xlsm/.csv link -> itself. Anything else -> assume xlsx.
    """
    url = url.strip()
    doc = _gsheet_id(url)
    if doc:
        return f"https://docs.google.com/spreadsheets/d/{doc}/export?format=xlsx", ".xlsx"
    path = urllib.parse.urlparse(url).path.lower()
    for ext in (".xlsx", ".xlsm", ".csv"):
        if path.endswith(ext):
            return url, ext
    return url, ".xlsx"


def _get(url, timeout):
    """Fetch bytes; return (data, final_url_after_redirects)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl()


def fetch(url, timeout=30):
    """Download a sheet link to a temp file and return its path. Raises RuntimeError
    (with guidance) on network failure or a non-spreadsheet response.

    Handles link shorteners (tinyurl, bit.ly, …): if the first request redirects to a
    Google Sheets page, we re-target the workbook-export URL and fetch that instead.
    """
    dl, suffix = resolve(url)
    try:
        data, final = _get(dl, timeout)
        # a shortener may land on a Sheets *edit* page (HTML) — re-target its xlsx export
        if _gsheet_id(final) and "/export" not in final:
            dl2, suffix = resolve(final)
            if dl2 != dl:
                data, final = _get(dl2, timeout)
    except Exception as e:                      # network / HTTP / DNS — surface actionably
        raise RuntimeError(
            f"could not download the sheet from its link ({e}).\n"
            "Make sure it is shared 'Anyone with the link -> Viewer' (or published to the web).\n"
            "If it is private, download it once (File -> Download -> .xlsx) and pass the file path instead.")
    # xlsx/xlsm are zip archives (start with 'PK'). Google serves an HTML sign-in / permission
    # page instead when the sheet is not link-viewable — catch that so we fail loud, not weird.
    if suffix in (".xlsx", ".xlsm") and data[:2] != b"PK":
        raise RuntimeError(
            "the link did not return a spreadsheet (likely a Google sign-in / permission page).\n"
            "Share it as 'Anyone with the link -> Viewer' or publish to the web,\n"
            "or download it (File -> Download -> .xlsx) and pass the file path instead.")
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="ncbl_sheet_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return tmp
