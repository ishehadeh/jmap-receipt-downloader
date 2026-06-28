"""Tests for extract_links."""

import pytest
import receipts as R


def _email(html=None, text=None):
    """Build a minimal email dict with the given body content."""
    email = {"htmlBody": [], "textBody": [], "bodyValues": {}}
    if html is not None:
        email["htmlBody"] = [{"partId": "h1"}]
        email["bodyValues"]["h1"] = {"value": html}
    if text is not None:
        email["textBody"] = [{"partId": "t1"}]
        email["bodyValues"]["t1"] = {"value": text}
    return email


class TestExtractLinks:
    # Basic extraction
    def test_html_anchor_extracted(self):
        email = _email(html='<a href="https://example.com/receipt">View</a>')
        assert "https://example.com/receipt" in R.extract_links(email, None)

    def test_text_bare_url_extracted(self):
        email = _email(text="See your receipt at https://example.com/r/abc")
        assert "https://example.com/r/abc" in R.extract_links(email, None)

    def test_http_url_extracted(self):
        email = _email(text="See http://example.com/r/abc")
        assert "http://example.com/r/abc" in R.extract_links(email, None)

    def test_mailto_excluded(self):
        email = _email(html='<a href="mailto:user@example.com">email</a>')
        assert R.extract_links(email, None) == []

    def test_relative_link_excluded(self):
        email = _email(html='<a href="/relative/path">link</a>')
        assert R.extract_links(email, None) == []

    # Both bodies
    def test_html_and_text_both_searched(self):
        email = _email(
            html='<a href="https://html.example.com">click</a>',
            text="Also https://text.example.com/extra",
        )
        links = R.extract_links(email, None)
        assert "https://html.example.com" in links
        assert "https://text.example.com/extra" in links

    def test_dedup_across_bodies(self):
        url = "https://example.com/receipt"
        email = _email(
            html=f'<a href="{url}">link</a>',
            text=f"Visit {url} for details",
        )
        links = R.extract_links(email, None)
        assert links.count(url) == 1

    def test_html_searched_before_text(self):
        email = _email(
            html='<a href="https://first.example.com">first</a>',
            text="https://second.example.com",
        )
        links = R.extract_links(email, None)
        assert links[0] == "https://first.example.com"

    # Ordering
    def test_multiple_html_anchors_ordered(self):
        email = _email(html='<a href="https://a.com">1</a><a href="https://b.com">2</a>')
        links = R.extract_links(email, None)
        assert links == ["https://a.com", "https://b.com"]

    # Pattern filtering
    def test_pattern_includes_matching(self):
        email = _email(html='<a href="https://stripe.com/pay/abc">pay</a>')
        links = R.extract_links(email, r"stripe\.com")
        assert len(links) == 1
        assert "stripe.com" in links[0]

    def test_pattern_excludes_non_matching(self):
        email = _email(html='<a href="https://other.com/page">link</a>')
        links = R.extract_links(email, r"stripe\.com")
        assert links == []

    def test_pattern_applied_to_text_urls(self):
        email = _email(text="https://squareup.com/r/abc and https://other.com")
        links = R.extract_links(email, r"squareup\.com")
        assert links == ["https://squareup.com/r/abc"]

    # Trailing punctuation
    def test_trailing_period_stripped(self):
        email = _email(text="See https://example.com/r/abc.")
        links = R.extract_links(email, None)
        assert links[0] == "https://example.com/r/abc"

    def test_trailing_comma_stripped(self):
        email = _email(text="Here: https://example.com/r/abc, and more")
        links = R.extract_links(email, None)
        assert links[0] == "https://example.com/r/abc"

    # Edge cases
    def test_empty_email_returns_empty(self):
        email = {"htmlBody": [], "textBody": [], "bodyValues": {}}
        assert R.extract_links(email, None) == []

    def test_missing_part_id_in_body_values_skipped(self):
        email = {
            "htmlBody": [{"partId": "missing"}],
            "textBody": [],
            "bodyValues": {},
        }
        assert R.extract_links(email, None) == []
