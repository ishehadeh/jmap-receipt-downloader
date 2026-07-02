"""Tests for manifest (download tracking) functions."""

import json
from pathlib import Path

import receipts as R


def _email(id="em001", subject="Your Receipt #1234", from_email="billing@example.com"):
    return {
        "id": id,
        "subject": subject,
        "from": [{"email": from_email}],
        "receivedAt": "2024-03-15T10:30:00Z",
    }


class TestLoadManifest:
    def test_returns_empty_dict_when_missing(self, tmp_path):
        assert R.load_manifest(tmp_path) == {}

    def test_loads_existing_manifest(self, tmp_path):
        data = {"em001": {"email_id": "em001", "subject": "Test"}}
        (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        assert R.load_manifest(tmp_path) == data

    def test_returns_empty_dict_on_invalid_json(self, tmp_path):
        (tmp_path / "manifest.json").write_text("not json", encoding="utf-8")
        assert R.load_manifest(tmp_path) == {}


class TestSaveManifest:
    def test_creates_manifest_file(self, tmp_path):
        data = {"em001": {"email_id": "em001"}}
        R.save_manifest(tmp_path, data)
        assert (tmp_path / "manifest.json").exists()

    def test_roundtrip(self, tmp_path):
        data = {"em001": {"email_id": "em001", "subject": "Receipt"}}
        R.save_manifest(tmp_path, data)
        loaded = R.load_manifest(tmp_path)
        assert loaded == data

    def test_overwrites_existing(self, tmp_path):
        R.save_manifest(tmp_path, {"old": {}})
        R.save_manifest(tmp_path, {"new": {}})
        loaded = R.load_manifest(tmp_path)
        assert "new" in loaded
        assert "old" not in loaded

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "a" / "b"
        R.save_manifest(out, {"em001": {}})
        assert (out / "manifest.json").exists()

    def test_no_tmp_file_left_behind(self, tmp_path):
        R.save_manifest(tmp_path, {"em001": {}})
        assert not (tmp_path / "manifest.json.tmp").exists()


class TestRecordDownload:
    def test_adds_entry_keyed_by_email_id(self):
        manifest = {}
        email = _email()
        rule = {"name": "Test Rule", "action": "text"}
        files = [Path("/out/receipts/2024-03/file.txt")]
        R.record_download(manifest, email, rule, "text", files, Path("/out/receipts"))
        assert "em001" in manifest

    def test_entry_fields(self):
        manifest = {}
        email = _email(subject="Invoice #99", from_email="pay@acme.com")
        rule = {"name": "Acme", "action": "save_attachment"}
        files = [Path("/out/receipts/2024-03/file.pdf")]
        entry = R.record_download(manifest, email, rule, "save_attachment", files, Path("/out/receipts"))
        assert entry["email_id"] == "em001"
        assert entry["subject"] == "Invoice #99"
        assert entry["from"] == "pay@acme.com"
        assert entry["received_at"] == "2024-03-15T10:30:00Z"
        assert entry["rule_name"] == "Acme"
        assert entry["action"] == "save_attachment"
        assert entry["output_files"] == ["2024-03\\file.pdf"] or entry["output_files"] == ["2024-03/file.pdf"]
        assert "downloaded_at" in entry

    def test_multiple_output_files(self):
        manifest = {}
        email = _email()
        rule = {"name": "Multi", "action": "save_attachment"}
        files = [Path("/out/receipts/2024-03/a.pdf"), Path("/out/receipts/2024-03/b.pdf")]
        entry = R.record_download(manifest, email, rule, "save_attachment", files, Path("/out/receipts"))
        assert len(entry["output_files"]) == 2

    def test_overwrites_existing_entry(self):
        manifest = {"em001": {"email_id": "em001", "subject": "old"}}
        email = _email(subject="new")
        rule = {"name": "R", "action": "text"}
        files = [Path("/out/receipts/2024-03/f.txt")]
        R.record_download(manifest, email, rule, "text", files, Path("/out/receipts"))
        assert manifest["em001"]["subject"] == "new"

    def test_rule_name_falls_back_to_action(self):
        manifest = {}
        email = _email()
        rule = {"action": "html"}
        entry = R.record_download(manifest, email, rule, "html", [Path("/out/receipts/f.pdf")], Path("/out/receipts"))
        assert entry["rule_name"] == "html"


class TestActionReturnValues:
    def test_action_text_returns_path(self, tmp_path):
        email = {**_email(), "textBody": [{"partId": "t1"}], "bodyValues": {"t1": {"value": "hello"}}}
        result = R.action_text(None, email, {"options": {}}, tmp_path)
        assert len(result) == 1
        assert result[0].exists()

    def test_action_text_returns_empty_on_no_body(self, tmp_path):
        email = {**_email(), "textBody": [], "bodyValues": {}}
        result = R.action_text(None, email, {"options": {}}, tmp_path)
        assert result == []

    def test_action_save_attachment_returns_paths(self, tmp_path, jmap_client):
        email = {
            **_email(),
            "attachments": [{"blobId": "blob-pdf-001", "name": "inv.pdf", "type": "application/pdf"}],
        }
        result = R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        assert len(result) == 1
        assert result[0].exists()

    def test_action_save_attachment_returns_empty_on_no_attachments(self, tmp_path, jmap_client):
        email = {**_email(), "attachments": []}
        result = R.action_save_attachment(jmap_client, email, {"options": {}}, tmp_path)
        assert result == []
