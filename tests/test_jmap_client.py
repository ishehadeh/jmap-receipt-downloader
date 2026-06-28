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


def test_pagination_works_when_total_absent():
    """fetch_emails must page through results even when server omits 'total'.

    The server here deliberately omits 'total' from Email/query responses and
    serves Email/get with the exact IDs from the query page (not a catch-all).
    Without calculateTotal:True in the query, fetch_emails would see total=0
    and stop after the first page, returning only 2 of the 3 emails.
    """
    import json
    from pytest_httpserver import HTTPServer
    from werkzeug.wrappers import Request, Response
    from tests.conftest import EMAIL_FIXTURES, ACCOUNT_ID

    # Track which query IDs each Email/get call receives so we can resolve #ids
    _last_query_ids: list = []

    def api_handler(request: Request) -> Response:
        nonlocal _last_query_ids
        body = request.get_json()
        responses = []
        for method_name, params, tag in body.get("methodCalls", []):
            if method_name == "Email/query":
                position = params.get("position", 0)
                limit = params.get("limit", 50)
                all_ids = list(EMAIL_FIXTURES.keys())
                page_ids = all_ids[position:position + limit]
                _last_query_ids = page_ids
                query_result: dict = {
                    "accountId": ACCOUNT_ID,
                    "ids": page_ids,
                }
                # Only include "total" when the client explicitly requests it
                if params.get("calculateTotal"):
                    query_result["total"] = len(all_ids)
                responses.append(["Email/query", query_result, tag])
            elif method_name == "Email/get":
                # Resolve the #ids back-reference using the last query page
                ids = params.get("ids") if params.get("ids") is not None else _last_query_ids
                responses.append(["Email/get", {
                    "accountId": ACCOUNT_ID,
                    "list": [EMAIL_FIXTURES[i] for i in ids if i in EMAIL_FIXTURES],
                }, tag])
            else:
                responses.append(["error", {"type": "unknownMethod"}, tag])
        return Response(json.dumps({"methodResponses": responses}), content_type="application/json")

    with HTTPServer(host="127.0.0.1") as server:
        base = server.url_for("").rstrip("/")
        server.expect_request("/jmap/session", method="GET").respond_with_json({
            "primaryAccounts": {"urn:ietf:params:jmap:mail": ACCOUNT_ID},
            "apiUrl": f"{base}/jmap/api",
            "downloadUrl": f"{base}/download/{{blobId}}",
        })
        server.expect_request("/jmap/api", method="POST").respond_with_handler(api_handler)

        client = R.JMAPClient(token="test", session_url=server.url_for("/jmap/session"))
        client.connect()

        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        before = datetime(2024, 12, 31, tzinfo=timezone.utc)

        result = R.fetch_emails(client, "mb-receipts", after, before, _limit=2)
        assert len(result) == len(EMAIL_FIXTURES)
