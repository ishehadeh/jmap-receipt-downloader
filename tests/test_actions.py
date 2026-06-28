"""Tests for action_text, action_save_attachment, and action_fetch_link.

action_html and action_screenshot_link require Chromium and are marked
@pytest.mark.playwright; they are skipped unless -m playwright is passed.
"""

import os
import shutil
from pathlib import Path
import pytest
import requests as req_lib
import receipts as R
from tests.conftest import FAKE_PDF


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email(subject="Receipt", received="2024-03-15T10:00:00Z", **kwargs):
    return {
        "subject": subject,
        "receivedAt": received,
        "htmlBody": [],
        "textBody": [],
        "bodyValues": {},
        "attachments": [],
        **kwargs,
    }


def _with_text(content, **kwargs):
    return _email(
        textBody=[{"partId": "t1"}],
        bodyValues={"t1": {"value": content}},
        **kwargs,
    )


def _with_html(content, **kwargs):
    return _email(
        htmlBody=[{"partId": "h1"}],
        bodyValues={"h1": {"value": content}},
        **kwargs,
    )


def _with_link(url, **kwargs):
    return _with_html(f'<a href="{url}">receipt</a>', **kwargs)


# ---------------------------------------------------------------------------
# action_text
# ---------------------------------------------------------------------------

class TestActionText:
    def test_saves_text_body(self, tmp_path):
        email = _with_text("Hello receipt text")
        R.action_text(None, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text() == "Hello receipt text"

    def test_uses_html_body_when_body_part_html(self, tmp_path):
        email = _with_html("<b>HTML content</b>")
        R.action_text(None, email, {"options": {"body_part": "html"}}, tmp_path)
        files = list(tmp_path.rglob("*.txt"))
        assert len(files) == 1
        assert "<b>HTML content</b>" in files[0].read_text()

    def test_no_body_prints_warning_and_writes_nothing(self, tmp_path, capsys):
        email = _email()
        R.action_text(None, email, {"options": {}}, tmp_path)
        assert "[!]" in capsys.readouterr().out
        assert not list(tmp_path.rglob("*.txt"))

    def test_output_path_in_date_subfolder(self, tmp_path):
        email = _with_text("content")
        R.action_text(None, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.txt"))
        assert files[0].parent.name == "2024-03"

    def test_encoding_is_utf8(self, tmp_path):
        email = _with_text("Héllo wörld")
        R.action_text(None, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.txt"))
        assert files[0].read_text(encoding="utf-8") == "Héllo wörld"


# ---------------------------------------------------------------------------
# action_save_attachment
# ---------------------------------------------------------------------------

class TestActionSaveAttachment:
    def _attachment(self, blob_id, name, mime):
        return {"blobId": blob_id, "name": name, "type": mime, "size": 24}

    def test_downloads_pdf_attachment(self, jmap_client, tmp_path):
        email = _email(attachments=[
            self._attachment("blob-pdf-001", "invoice.pdf", "application/pdf")
        ])
        R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"%PDF")

    def test_mime_filter_skips_non_matching(self, jmap_client, tmp_path, capsys):
        email = _email(attachments=[
            self._attachment("blob-pdf-001", "invoice.pdf", "application/pdf")
        ])
        R.action_save_attachment(
            jmap_client, email, {"options": {"mime_types": ["image/png"]}}, tmp_path
        )
        assert not list(tmp_path.rglob("*.pdf"))
        assert "No attachments matched" in capsys.readouterr().out

    def test_no_attachments_prints_warning(self, jmap_client, tmp_path, capsys):
        email = _email()
        R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        assert "[!] No attachments" in capsys.readouterr().out

    def test_single_attachment_no_label(self, jmap_client, tmp_path):
        email = _email(attachments=[
            self._attachment("blob-pdf-001", "invoice.pdf", "application/pdf")
        ])
        R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.pdf"))
        # Single attachment: label not appended → filename ends with .pdf directly
        assert len(files) == 1

    def test_multiple_attachments_each_labeled(self, jmap_client, tmp_path):
        email = _email(attachments=[
            self._attachment("blob-pdf-001", "invoice.pdf", "application/pdf"),
            self._attachment("blob-png-001", "logo.png", "image/png"),
        ])
        R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.*"))
        names = [f.name for f in files if f.is_file()]
        assert any("invoice" in n for n in names)
        assert any("logo" in n for n in names)

    def test_mime_type_filter_accepts_matching(self, jmap_client, tmp_path):
        email = _email(attachments=[
            self._attachment("blob-pdf-001", "invoice.pdf", "application/pdf")
        ])
        R.action_save_attachment(
            jmap_client, email, {"options": {"mime_types": ["application/pdf"]}}, tmp_path
        )
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1

    def test_missing_blob_id_skips_attachment(self, jmap_client, tmp_path, capsys):
        # Attachment with no blobId — must warn and write nothing, not raise
        email = _email(attachments=[{"name": "invoice.pdf", "type": "application/pdf", "size": 10}])
        R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        assert "[!]" in capsys.readouterr().out
        assert not list(tmp_path.rglob("*.*"))


# ---------------------------------------------------------------------------
# action_fetch_link
# ---------------------------------------------------------------------------

class TestActionFetchLink:
    def test_pdf_response_saved_as_pdf(self, jmap_client, receipt_server, tmp_path):
        url = receipt_server.url_for("/receipt.pdf")
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=req_lib.Session())
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"%PDF")

    def test_redirect_followed_to_pdf(self, jmap_client, receipt_server, tmp_path):
        url = receipt_server.url_for("/receipt-redirect")
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=req_lib.Session())
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"%PDF")

    def test_html_response_calls_url_to_pdf(self, jmap_client, receipt_server, tmp_path, monkeypatch):
        captured = []

        def fake_url_to_pdf(url, path):
            captured.append(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF fake")

        monkeypatch.setattr(R, "_url_to_pdf", fake_url_to_pdf)
        url = receipt_server.url_for("/receipt.html")
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=req_lib.Session())
        assert len(captured) == 1

    def test_406_falls_back_to_playwright(self, jmap_client, receipt_server, tmp_path, monkeypatch):
        captured = []

        def fake_url_to_pdf(url, path):
            captured.append(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF fake")

        monkeypatch.setattr(R, "_url_to_pdf", fake_url_to_pdf)
        url = receipt_server.url_for("/gone")
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=req_lib.Session())
        assert captured == [url]

    def test_bom_prefixed_html_calls_url_to_pdf(self, jmap_client, tmp_path, monkeypatch):
        """HTML with UTF-8 BOM must be rendered via Playwright, not saved as raw bytes."""
        from unittest.mock import MagicMock

        captured = []

        def fake_url_to_pdf(url, path):
            captured.append(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF fake")

        monkeypatch.setattr(R, "_url_to_pdf", fake_url_to_pdf)

        bom_html = b"\xef\xbb\xbf<!DOCTYPE html><html><body><p>Receipt</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.content = bom_html
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.url = "http://receipts.example.com/view/abc"
        mock_resp.raise_for_status.return_value = None

        session = MagicMock()
        session.get.return_value = mock_resp

        email = _with_link("http://receipts.example.com/view/abc")
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=session)
        assert len(captured) == 1, "Expected _url_to_pdf to be called for BOM-prefixed HTML"

    def test_connection_error_falls_back_to_playwright(self, jmap_client, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        import requests as req_lib

        captured = []

        def fake_url_to_pdf(url, path):
            captured.append(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF fake")

        monkeypatch.setattr(R, "_url_to_pdf", fake_url_to_pdf)

        session = MagicMock()
        session.get.side_effect = req_lib.ConnectionError("connection refused")

        url = "http://example.com/receipt"
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=session)
        assert len(captured) == 1

    def test_no_links_prints_warning(self, jmap_client, tmp_path, capsys):
        email = _email()
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path)
        assert "[!] No matching links" in capsys.readouterr().out

    def test_no_links_writes_nothing(self, jmap_client, tmp_path):
        email = _email()
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path)
        assert not list(tmp_path.rglob("*.*"))

    def test_link_pattern_filters_to_correct_url(self, jmap_client, receipt_server, tmp_path):
        pdf_url = receipt_server.url_for("/receipt.pdf")
        html_url = receipt_server.url_for("/receipt.html")
        email = _with_html(f'<a href="{html_url}">html</a><a href="{pdf_url}">pdf</a>')
        R.action_fetch_link(
            jmap_client, email,
            {"options": {"link_pattern": r"receipt\.pdf"}},
            tmp_path,
            _session=req_lib.Session(),
        )
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"%PDF")

    def test_stripe_redirect_chain_followed(self, jmap_client, receipt_server, tmp_path):
        url = receipt_server.url_for("/stripe-click-tracker")
        email = _with_link(url)
        R.action_fetch_link(jmap_client, email, {"options": {}}, tmp_path, _session=req_lib.Session())
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1


# ---------------------------------------------------------------------------
# action_html / action_screenshot_link (Playwright — skipped by default)
# ---------------------------------------------------------------------------

def _chromium_available() -> bool:
    if shutil.which("chromium") or shutil.which("chromium-browser"):
        return True
    # Playwright manages its own Chromium under PLAYWRIGHT_BROWSERS_PATH
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright"))
    return any(browsers.glob("chromium-*/chrome-linux/chrome"))

_has_chromium = _chromium_available()


@pytest.mark.playwright
@pytest.mark.skipif(not _has_chromium, reason="Chromium not installed; run inside Docker")
class TestPlaywrightActions:
    def test_action_html_produces_pdf(self, jmap_client, tmp_path):
        email = _with_html("<html><body><h1>$42.00</h1></body></html>")
        R.action_html(jmap_client, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.pdf"))
        assert len(files) == 1
        assert files[0].read_bytes().startswith(b"%PDF")

    def test_action_screenshot_link_produces_png(self, jmap_client, receipt_server, tmp_path):
        url = receipt_server.url_for("/receipt.html")
        email = _with_link(url)
        R.action_screenshot_link(jmap_client, email, {"options": {}}, tmp_path)
        files = list(tmp_path.rglob("*.png"))
        assert len(files) == 1
        assert files[0].read_bytes()[:4] == b"\x89PNG"
