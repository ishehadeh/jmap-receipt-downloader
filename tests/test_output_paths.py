"""Tests for _sanitize, make_output_path, _ext_for_mime, and _ext_from_content."""

import pytest
from pathlib import Path
import receipts as R


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_removes_special_chars(self):
        assert R._sanitize("Hello, World!") == "Hello_World"

    def test_replaces_spaces_with_underscores(self):
        assert R._sanitize("hello world") == "hello_world"

    def test_preserves_hyphens(self):
        result = R._sanitize("receipt-2024")
        assert "-" in result

    def test_truncates_to_default_max_len(self):
        result = R._sanitize("a" * 100)
        assert len(result) == 60

    def test_custom_max_len(self):
        result = R._sanitize("abcdefgh", max_len=4)
        assert len(result) <= 4

    def test_empty_string(self):
        assert R._sanitize("") == ""

    def test_only_special_chars_gives_empty(self):
        assert R._sanitize("!!!") == ""

    def test_preserves_digits(self):
        assert "123" in R._sanitize("invoice-123")


# ---------------------------------------------------------------------------
# make_output_path
# ---------------------------------------------------------------------------

def _email(subject="Test Invoice", received="2024-03-15T10:30:00Z"):
    return {"subject": subject, "receivedAt": received}


class TestMakeOutputPath:
    def test_month_directory_created(self, tmp_path):
        R.make_output_path(tmp_path, _email(), ".pdf")
        assert (tmp_path / "2024-03").is_dir()

    def test_path_inside_month_directory(self, tmp_path):
        path = R.make_output_path(tmp_path, _email(), ".pdf")
        assert path.parent.name == "2024-03"

    def test_filename_starts_with_date(self, tmp_path):
        path = R.make_output_path(tmp_path, _email(), ".pdf")
        assert path.name.startswith("2024-03-15_")

    def test_filename_contains_subject_slug(self, tmp_path):
        path = R.make_output_path(tmp_path, _email("Your Receipt"), ".pdf")
        assert "Your_Receipt" in path.name

    def test_correct_extension(self, tmp_path):
        path = R.make_output_path(tmp_path, _email(), ".pdf")
        assert path.suffix == ".pdf"

    def test_label_appended_when_given(self, tmp_path):
        path = R.make_output_path(tmp_path, _email(), ".pdf", label="invoice")
        assert "invoice" in path.name

    def test_no_label_when_empty_string(self, tmp_path):
        path_with = R.make_output_path(tmp_path, _email(), ".pdf", label="x")
        path_without = R.make_output_path(tmp_path, _email(), ".pdf", label="")
        # Without label the path should be shorter
        assert len(path_without.stem) < len(path_with.stem)

    def test_dedup_on_collision(self, tmp_path):
        path1 = R.make_output_path(tmp_path, _email(), ".pdf")
        path1.write_bytes(b"existing")
        path2 = R.make_output_path(tmp_path, _email(), ".pdf")
        assert path2 != path1
        assert path2.name != path1.name

    def test_dedup_counter_increments(self, tmp_path):
        paths = []
        for _ in range(3):
            p = R.make_output_path(tmp_path, _email(), ".pdf")
            p.write_bytes(b"data")
            paths.append(p)
        assert len(set(p.name for p in paths)) == 3

    def test_bad_date_falls_back_to_now(self, tmp_path):
        email = {"subject": "Test", "receivedAt": "not-a-date"}
        path = R.make_output_path(tmp_path, email, ".txt")
        assert path.suffix == ".txt"

    def test_missing_received_at_falls_back(self, tmp_path):
        email = {"subject": "Test"}
        path = R.make_output_path(tmp_path, email, ".txt")
        assert path.suffix == ".txt"


# ---------------------------------------------------------------------------
# _ext_for_mime
# ---------------------------------------------------------------------------

class TestExtForMime:
    @pytest.mark.parametrize("mime,expected", [
        ("application/pdf", ".pdf"),
        ("image/png", ".png"),
        ("image/jpeg", ".jpg"),
        ("image/gif", ".gif"),
        ("image/webp", ".webp"),
        ("text/html", ".html"),
        ("text/plain", ".txt"),
        ("application/octet-stream", ".bin"),
        ("application/zip", ".bin"),          # unknown → .bin
        ("text/html; charset=utf-8", ".html"),  # params stripped
    ])
    def test_known_and_unknown_mime(self, mime, expected):
        assert R._ext_for_mime(mime) == expected


# ---------------------------------------------------------------------------
# _ext_from_content
# ---------------------------------------------------------------------------

class TestExtFromContent:
    @pytest.mark.parametrize("data,expected", [
        (b"%PDF-1.4 content", ".pdf"),
        (b"\x89PNG\r\n\x1a\n content", ".png"),
        (b"\xff\xd8\xff\xe0 content", ".jpg"),
        (b"GIF89a content", ".gif"),
        (b"PK\x03\x04 content", ".zip"),
    ])
    def test_magic_detection(self, data, expected):
        assert R._ext_from_content(data, ".bin") == expected

    def test_unknown_data_returns_fallback(self):
        assert R._ext_from_content(b"\x00\x01\x02\x03", ".xyz") == ".xyz"

    def test_fallback_used_when_no_magic(self):
        assert R._ext_from_content(b"plain text content", ".txt") == ".txt"
