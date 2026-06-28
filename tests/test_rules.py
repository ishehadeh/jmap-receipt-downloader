"""Tests for matches_rule and find_rule."""

import pytest
import receipts as R


# ---------------------------------------------------------------------------
# Sample emails
# ---------------------------------------------------------------------------

def _email(from_addr="billing@example.com", subject="Your Invoice #5678"):
    return {
        "subject": subject,
        "from": [{"email": from_addr, "name": "Sender"}],
    }


EMAIL_BILLING = _email()
EMAIL_SUBDOMAIN = _email("noreply@mail.example.com")
EMAIL_OTHER = _email("user@other.com", subject="Welcome")
EMAIL_MULTI_FROM = {
    "subject": "Invoice",
    "from": [
        {"email": "first@other.com"},
        {"email": "billing@example.com"},
    ],
}


# ---------------------------------------------------------------------------
# matches_rule
# ---------------------------------------------------------------------------

class TestMatchesRule:
    def test_empty_match_always_true(self):
        assert R.matches_rule({}, EMAIL_BILLING)

    # from
    def test_from_exact_match(self):
        assert R.matches_rule({"from": "billing@example.com"}, EMAIL_BILLING)

    def test_from_exact_no_match(self):
        assert not R.matches_rule({"from": "other@example.com"}, EMAIL_BILLING)

    def test_from_case_insensitive(self):
        assert R.matches_rule({"from": "BILLING@EXAMPLE.COM"}, EMAIL_BILLING)

    def test_from_any_sender_matches(self):
        assert R.matches_rule({"from": "billing@example.com"}, EMAIL_MULTI_FROM)

    # from_domain
    def test_from_domain_direct(self):
        assert R.matches_rule({"from_domain": "example.com"}, EMAIL_BILLING)

    def test_from_domain_subdomain(self):
        # mail.example.com should match domain "example.com"
        assert R.matches_rule({"from_domain": "example.com"}, EMAIL_SUBDOMAIN)

    def test_from_domain_no_match(self):
        assert not R.matches_rule({"from_domain": "other.com"}, EMAIL_BILLING)

    def test_from_domain_strips_leading_at(self):
        # from_domain: "@example.com" is the same as "example.com"
        assert R.matches_rule({"from_domain": "@example.com"}, EMAIL_BILLING)

    # from_regex
    def test_from_regex_match(self):
        assert R.matches_rule({"from_regex": r"billing@.*\.com"}, EMAIL_BILLING)

    def test_from_regex_no_match(self):
        assert not R.matches_rule({"from_regex": r"^invoice@"}, EMAIL_BILLING)

    def test_from_regex_case_insensitive(self):
        assert R.matches_rule({"from_regex": r"BILLING"}, EMAIL_BILLING)

    # subject
    def test_subject_substring(self):
        assert R.matches_rule({"subject": "Invoice"}, EMAIL_BILLING)

    def test_subject_case_insensitive(self):
        assert R.matches_rule({"subject": "invoice"}, EMAIL_BILLING)

    def test_subject_no_match(self):
        assert not R.matches_rule({"subject": "Receipt"}, EMAIL_BILLING)

    # subject_regex
    def test_subject_regex_match(self):
        assert R.matches_rule({"subject_regex": r"#\d+"}, EMAIL_BILLING)

    def test_subject_regex_no_match(self):
        assert not R.matches_rule({"subject_regex": r"^Order"}, EMAIL_BILLING)

    # AND conditions
    def test_and_all_pass(self):
        rule = {"from_domain": "example.com", "subject": "Invoice"}
        assert R.matches_rule(rule, EMAIL_BILLING)

    def test_and_one_fails(self):
        rule = {"from_domain": "example.com", "subject": "Welcome"}
        assert not R.matches_rule(rule, EMAIL_BILLING)

    def test_and_all_fail(self):
        rule = {"from_domain": "other.com", "subject": "Welcome"}
        assert not R.matches_rule(rule, EMAIL_BILLING)

    # Edge cases
    def test_missing_subject_key(self):
        email = {"from": [{"email": "billing@example.com"}]}
        assert R.matches_rule({"subject": "Invoice"}, email) is False

    def test_empty_from_list(self):
        email = {"subject": "Invoice", "from": []}
        assert not R.matches_rule({"from": "billing@example.com"}, email)


# ---------------------------------------------------------------------------
# find_rule
# ---------------------------------------------------------------------------

RULES = [
    {"name": "billing", "match": {"from": "billing@example.com"}, "action": "save_attachment"},
    {"name": "example_domain", "match": {"from_domain": "example.com"}, "action": "html"},
    {"name": "catch_all", "match": {}, "action": "text"},
]


class TestFindRule:
    def test_first_rule_wins(self):
        rule = R.find_rule(RULES, EMAIL_BILLING)
        assert rule["name"] == "billing"

    def test_second_rule_fires_when_first_no_match(self):
        rule = R.find_rule(RULES, EMAIL_SUBDOMAIN)
        assert rule["name"] == "example_domain"

    def test_catch_all_fires_last(self):
        rule = R.find_rule(RULES, EMAIL_OTHER)
        assert rule["name"] == "catch_all"

    def test_returns_none_when_no_match(self):
        rules = [{"name": "r1", "match": {"from": "specific@x.com"}, "action": "text"}]
        assert R.find_rule(rules, EMAIL_OTHER) is None

    def test_empty_rules_returns_none(self):
        assert R.find_rule([], EMAIL_BILLING) is None

    def test_match_key_optional(self):
        # Rule with no "match" key defaults to empty match (always true)
        rules = [{"name": "no_match_key", "action": "text"}]
        rule = R.find_rule(rules, EMAIL_BILLING)
        assert rule["name"] == "no_match_key"
