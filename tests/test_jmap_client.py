"""Tests for JMAPClient, find_mailbox, and fetch_emails against a real HTTP server."""

import pytest
from datetime import datetime, timezone
import receipts as R
from tests.conftest import ACCOUNT_ID, EMAIL_FIXTURES


class TestJMAPClientConnect:
    def test_account_id_set(self, jmap_client):
        assert jmap_client.account_id == ACCOUNT_ID

    def test_api_url_set(self, jmap_server, jmap_client):
        assert jmap_client.api_url == jmap_server.url_for("/jmap/api")

    def test_download_template_set(self, jmap_client):
        assert "{blobId}" in jmap_client._download_url_template

    def test_wrong_token_still_connects(self, jmap_server):
        # Our mock doesn't validate tokens; test that connect() runs without error
        client = R.JMAPClient(token="bad-token", session_url=jmap_server.url_for("/jmap/session"))
        client.connect()
        assert client.account_id == ACCOUNT_ID


class TestJMAPClientCall:
    def test_returns_method_responses(self, jmap_client):
        result = jmap_client.call([
            ["Mailbox/get", {"accountId": jmap_client.account_id, "ids": None}, "m0"]
        ])
        assert "methodResponses" in result
        assert result["methodResponses"][0][0] == "Mailbox/get"

    def test_unknown_method_returns_error(self, jmap_client):
        result = jmap_client.call([
            ["NoSuchMethod/get", {}, "x0"]
        ])
        assert result["methodResponses"][0][0] == "error"

    def test_multi_call_in_one_request(self, jmap_client):
        result = jmap_client.call([
            ["Mailbox/get", {"accountId": jmap_client.account_id, "ids": None}, "m0"],
            ["Mailbox/get", {"accountId": jmap_client.account_id, "ids": None}, "m1"],
        ])
        assert len(result["methodResponses"]) == 2


class TestJMAPClientDownloadBlob:
    def test_pdf_blob_returns_pdf_bytes(self, jmap_client):
        data = jmap_client.download_blob("blob-pdf-001", "invoice.pdf", "application/pdf")
        assert data.startswith(b"%PDF")

    def test_png_blob_returns_png_bytes(self, jmap_client):
        data = jmap_client.download_blob("blob-png-001", "image.png", "image/png")
        assert data[:4] == b"\x89PNG"

    def test_returns_bytes_type(self, jmap_client):
        data = jmap_client.download_blob("blob-pdf-001", "f.pdf", "application/pdf")
        assert isinstance(data, bytes)


class TestFindMailbox:
    def test_finds_receipts_mailbox(self, jmap_client):
        mb_id = R.find_mailbox(jmap_client, "Receipts")
        assert mb_id == "mb-receipts"

    def test_finds_inbox_mailbox(self, jmap_client):
        mb_id = R.find_mailbox(jmap_client, "Inbox")
        assert mb_id == "mb-inbox"

    def test_case_insensitive(self, jmap_client):
        assert R.find_mailbox(jmap_client, "receipts") == "mb-receipts"
        assert R.find_mailbox(jmap_client, "RECEIPTS") == "mb-receipts"

    def test_not_found_raises_value_error(self, jmap_client):
        with pytest.raises(ValueError, match="not found"):
            R.find_mailbox(jmap_client, "DoesNotExist")

    def test_error_message_lists_available(self, jmap_client):
        with pytest.raises(ValueError, match="Receipts"):
            R.find_mailbox(jmap_client, "DoesNotExist")


class TestFetchEmails:
    _after = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _before = datetime(2024, 12, 31, tzinfo=timezone.utc)

    def test_returns_list(self, jmap_client):
        result = R.fetch_emails(jmap_client, "mb-receipts", self._after, self._before)
        assert isinstance(result, list)

    def test_returns_all_emails(self, jmap_client):
        result = R.fetch_emails(jmap_client, "mb-receipts", self._after, self._before)
        assert len(result) == len(EMAIL_FIXTURES)

    def test_email_has_expected_fields(self, jmap_client):
        result = R.fetch_emails(jmap_client, "mb-receipts", self._after, self._before)
        email = result[0]
        assert "id" in email
        assert "subject" in email
        assert "from" in email

    def test_pagination_collects_all_pages(self, jmap_client):
        # With _limit=2 and total=3 emails, fetch_emails must make two JMAP calls
        result = R.fetch_emails(jmap_client, "mb-receipts", self._after, self._before, _limit=2)
        assert len(result) == len(EMAIL_FIXTURES)
