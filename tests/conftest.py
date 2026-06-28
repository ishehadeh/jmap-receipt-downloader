"""Shared fixtures: JMAP mock server, receipt mock server, connected client."""

import json
import pytest
from werkzeug.wrappers import Request, Response
from pytest_httpserver import HTTPServer

import receipts as R

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

ACCOUNT_ID = "test-account-001"

FAKE_BLOBS = {
    "blob-pdf-001": b"%PDF-1.4 fake receipt pdf",
    "blob-png-001": b"\x89PNG\r\n\x1a\n fake png",
}

FAKE_PDF = b"%PDF-1.4 fake receipt pdf"
FAKE_HTML = b"<html><body><h1>Receipt</h1><p>Total: $42.00</p></body></html>"

_BASE_EMAIL = {
    "subject": "Your Receipt #1234",
    "from": [{"email": "billing@example.com", "name": "Example Co"}],
    "receivedAt": "2024-03-15T10:30:00Z",
    "htmlBody": [{"partId": "h1"}],
    "textBody": [{"partId": "t1"}],
    "bodyValues": {
        "h1": {"value": "<html><body><a href='https://receipts.example.com/r/abc'>View Receipt</a></body></html>"},
        "t1": {"value": "Your receipt: https://receipts.example.com/r/abc"},
    },
    "attachments": [],
}

EMAIL_FIXTURES = {
    "em001": {**_BASE_EMAIL, "id": "em001"},
    "em002": {**_BASE_EMAIL, "id": "em002", "subject": "Your Receipt #1235"},
    "em003": {**_BASE_EMAIL, "id": "em003", "subject": "Your Receipt #1236"},
}


# ---------------------------------------------------------------------------
# JMAP server fixture
# ---------------------------------------------------------------------------

def _make_jmap_api_handler(base_url: str):
    """Returns a Werkzeug handler for POST /jmap/api.

    Dispatches each method in methodCalls by name. Email/get detects the
    #ids backreference and returns all known email fixtures.
    """

    def handle_mailbox_get(params):
        return ["Mailbox/get", {
            "accountId": ACCOUNT_ID,
            "list": [
                {"id": "mb-receipts", "name": "Receipts", "role": "inbox"},
                {"id": "mb-inbox",    "name": "Inbox",    "role": "inbox"},
            ],
        }]

    def handle_email_query(params):
        position = params.get("position", 0)
        limit = params.get("limit", 50)
        all_ids = list(EMAIL_FIXTURES.keys())
        page_ids = all_ids[position:position + limit]
        return ["Email/query", {
            "accountId": ACCOUNT_ID,
            "ids": page_ids,
            "total": len(all_ids),
        }]

    def handle_email_get(params):
        # The real server resolves the #ids backreference; we return all fixtures.
        ids = params.get("ids") or list(EMAIL_FIXTURES.keys())
        return ["Email/get", {
            "accountId": ACCOUNT_ID,
            "list": [EMAIL_FIXTURES[i] for i in ids if i in EMAIL_FIXTURES],
        }]

    dispatch = {
        "Mailbox/get": handle_mailbox_get,
        "Email/query": handle_email_query,
        "Email/get": handle_email_get,
    }

    def api_handler(request: Request) -> Response:
        body = request.get_json()
        responses = []
        for method_name, params, tag in body.get("methodCalls", []):
            if method_name in dispatch:
                name, result = dispatch[method_name](params)
                responses.append([name, result, tag])
            else:
                responses.append(["error", {"type": "unknownMethod"}, tag])
        return Response(
            json.dumps({"methodResponses": responses}),
            content_type="application/json",
        )

    return api_handler


@pytest.fixture(scope="session")
def jmap_server():
    with HTTPServer(host="127.0.0.1") as server:
        base = server.url_for("").rstrip("/")

        session_data = {
            "primaryAccounts": {"urn:ietf:params:jmap:mail": ACCOUNT_ID},
            "apiUrl": f"{base}/jmap/api",
            # Simplified download template: only {blobId} substituted
            "downloadUrl": f"{base}/download/{{blobId}}",
        }
        server.expect_request("/jmap/session", method="GET").respond_with_json(session_data)
        server.expect_request("/jmap/api", method="POST").respond_with_handler(
            _make_jmap_api_handler(base)
        )

        for blob_id, data in FAKE_BLOBS.items():
            server.expect_request(f"/download/{blob_id}", method="GET").respond_with_data(
                data, content_type="application/octet-stream"
            )

        yield server


@pytest.fixture
def jmap_client(jmap_server):
    client = R.JMAPClient(
        token="test-token",
        session_url=jmap_server.url_for("/jmap/session"),
    )
    client.connect()
    return client


# ---------------------------------------------------------------------------
# Receipt (third-party) server fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def receipt_server():
    with HTTPServer(host="127.0.0.1") as server:

        server.expect_request("/receipt.pdf", method="GET").respond_with_data(
            FAKE_PDF, content_type="application/pdf"
        )
        server.expect_request("/receipt.html", method="GET").respond_with_data(
            FAKE_HTML, content_type="text/html"
        )

        def redirect_handler(request: Request) -> Response:
            return Response(
                status=302,
                headers={"Location": server.url_for("/receipt.pdf")},
            )

        server.expect_request("/receipt-redirect", method="GET").respond_with_handler(redirect_handler)
        server.expect_request("/stripe-click-tracker", method="GET").respond_with_handler(redirect_handler)
        server.expect_request("/gone", method="GET").respond_with_data(
            "Not Acceptable", status=406
        )

        yield server
