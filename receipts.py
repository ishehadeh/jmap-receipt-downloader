#!/usr/bin/env python3
"""Download receipts from a Fastmail folder via JMAP."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup


JMAP_SESSION_URL = "https://api.fastmail.com/jmap/session"
JMAP_USING = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"]


# ---------------------------------------------------------------------------
# JMAP client
# ---------------------------------------------------------------------------

class JMAPClient:
    def __init__(self, token: str, session_url: str = JMAP_SESSION_URL):
        self.token = token
        self._session_url = session_url
        self.account_id: str | None = None
        self.api_url: str | None = None
        self._download_url_template: str | None = None
        self._http = requests.Session()
        self._http.headers["Authorization"] = f"Bearer {token}"

    def connect(self):
        resp = self._http.get(self._session_url)
        resp.raise_for_status()
        data = resp.json()
        self.account_id = data["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        self.api_url = data["apiUrl"]
        self._download_url_template = data["downloadUrl"]

    def call(self, method_calls: list) -> dict:
        payload = {"using": JMAP_USING, "methodCalls": method_calls}
        resp = self._http.post(self.api_url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def download_blob(self, blob_id: str, name: str = "blob", mime_type: str = "application/octet-stream") -> bytes:
        url = (self._download_url_template
               .replace("{accountId}", self.account_id)
               .replace("{blobId}", blob_id)
               .replace("{name}", name)
               .replace("{type}", mime_type))
        resp = self._http.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Mailbox + email fetching
# ---------------------------------------------------------------------------

def find_mailbox(client: JMAPClient, name: str) -> str:
    result = client.call([
        ["Mailbox/get", {"accountId": client.account_id, "ids": None}, "m0"]
    ])
    mailboxes = result["methodResponses"][0][1]["list"]
    name_lower = name.lower()
    for mb in mailboxes:
        if mb["name"].lower() == name_lower:
            return mb["id"]
    available = [mb["name"] for mb in mailboxes]
    raise ValueError(f"Mailbox '{name}' not found. Available: {available}")


def fetch_emails(client: JMAPClient, mailbox_id: str, after: datetime, before: datetime, _limit: int = 50) -> list:
    filter_ = {
        "inMailbox": mailbox_id,
        "after": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "before": before.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    emails = []
    position = 0
    limit = _limit

    while True:
        result = client.call([
            ["Email/query", {
                "accountId": client.account_id,
                "filter": filter_,
                "sort": [{"property": "receivedAt", "isAscending": True}],
                "position": position,
                "limit": limit,
                "calculateTotal": True,
            }, "q0"],
            ["Email/get", {
                "accountId": client.account_id,
                "#ids": {"resultOf": "q0", "name": "Email/query", "path": "/ids"},
                "properties": [
                    "id", "subject", "from", "receivedAt",
                    "htmlBody", "textBody", "bodyValues", "attachments",
                ],
                "bodyProperties": ["partId", "blobId", "size", "name", "type", "charset", "disposition"],
                "fetchHTMLBodyValues": True,
                "fetchTextBodyValues": True,
                "maxBodyValueBytes": 2097152,
            }, "g0"],
        ])

        query_resp = result["methodResponses"][0][1]
        get_resp = result["methodResponses"][1][1]

        batch = get_resp.get("list", [])
        emails.extend(batch)

        total = query_resp.get("total", 0)
        position += len(batch)
        if position >= total or not batch:
            break

    return emails


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def _from_addresses(email: dict) -> list[str]:
    return [a.get("email", "").lower() for a in email.get("from", [])]


def matches_rule(rule_match: dict, email: dict) -> bool:
    if not rule_match:
        return True

    froms = _from_addresses(email)
    subject = email.get("subject", "")

    if "from" in rule_match:
        target = rule_match["from"].lower()
        if not any(target == f for f in froms):
            return False

    if "from_domain" in rule_match:
        domain = rule_match["from_domain"].lower().lstrip("@")
        if not any(f.endswith("@" + domain) or f.endswith("." + domain) for f in froms):
            return False

    if "from_regex" in rule_match:
        pat = rule_match["from_regex"]
        try:
            if not any(re.search(pat, f, re.IGNORECASE) for f in froms):
                return False
        except re.error as e:
            print(f"  [!] Invalid from_regex {pat!r}: {e}", file=sys.stderr)
            return False

    if "subject" in rule_match:
        if rule_match["subject"].lower() not in subject.lower():
            return False

    if "subject_regex" in rule_match:
        pat = rule_match["subject_regex"]
        try:
            if not re.search(pat, subject, re.IGNORECASE):
                return False
        except re.error as e:
            print(f"  [!] Invalid subject_regex {pat!r}: {e}", file=sys.stderr)
            return False

    return True


def find_rule(rules: list, email: dict) -> dict | None:
    for rule in rules:
        if matches_rule(rule.get("match", {}), email):
            return rule
    return None


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

def _sanitize(s: str, max_len: int = 60) -> str:
    return re.sub(r"[^\w\s-]", "", s).strip().replace(" ", "_")[:max_len]


def make_output_path(out_dir: Path, email: dict, ext: str, label: str = "") -> Path:
    received = email.get("receivedAt", "")
    try:
        dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)

    date_str = dt.strftime("%Y-%m-%d")
    month_str = dt.strftime("%Y-%m")
    subject_slug = _sanitize(email.get("subject", "email"))
    stem = f"{date_str}_{subject_slug}" + (f"_{label}" if label else "")

    folder = out_dir / month_str
    folder.mkdir(parents=True, exist_ok=True)

    # Avoid overwriting existing files
    path = folder / f"{stem}{ext}"
    counter = 1
    while path.exists():
        path = folder / f"{stem}_{counter}{ext}"
        counter += 1
    return path


def _ext_for_mime(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(mime.split(";")[0].strip(), ".bin")


# Magic-byte signatures for formats that servers commonly mis-label as octet-stream.
_MAGIC = [
    (b"%PDF", ".pdf"),
    (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"PK\x03\x04", ".zip"),
]


def _ext_from_content(data: bytes, fallback: str) -> str:
    for magic, ext in _MAGIC:
        if data[:len(magic)] == magic:
            return ext
    return fallback


# ---------------------------------------------------------------------------
# Link extraction (shared by fetch_link and screenshot_link)
# ---------------------------------------------------------------------------

_BARE_URL_RE = re.compile(r'https?://[^\s<>"\']+')


def extract_links(email: dict, pattern: str | None) -> list[str]:
    """Extract links from both the HTML body (<a href>) and text body (bare URLs).
    HTML body is searched first; text body fills in what HTML misses."""
    body_values = email.get("bodyValues", {})
    links = []
    seen: set[str] = set()

    def _add(href: str):
        if href.startswith("http") and href not in seen:
            if pattern is None or re.search(pattern, href):
                links.append(href)
                seen.add(href)

    for part in email.get("htmlBody", []):
        pid = part.get("partId")
        if pid and pid in body_values:
            soup = BeautifulSoup(body_values[pid]["value"], "lxml")
            for a in soup.find_all("a", href=True):
                _add(a["href"])

    for part in email.get("textBody", []):
        pid = part.get("partId")
        if pid and pid in body_values:
            for m in _BARE_URL_RE.finditer(body_values[pid]["value"]):
                _add(m.group().rstrip(".,;)"))

    return links


# ---------------------------------------------------------------------------
# Playwright PDF helpers
# ---------------------------------------------------------------------------

def _html_to_pdf(html: str, path: Path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(path=str(path), format="A4", print_background=True)
        browser.close()


def _url_to_pdf(url: str, path: Path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.pdf(path=str(path), format="A4", print_background=True)
        browser.close()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _get_body(email: dict, part: str) -> str | None:
    """Return the first body value for 'html' or 'text' part, or None."""
    body_values = email.get("bodyValues", {})
    key = "htmlBody" if part == "html" else "textBody"
    for p in email.get(key, []):
        pid = p.get("partId")
        if pid and pid in body_values:
            return body_values[pid]["value"]
    return None


def action_html(client: JMAPClient, email: dict, rule: dict, out_dir: Path) -> list[Path]:
    """Print an email body part to PDF. Defaults to the HTML body; use body_part: text to use the plain-text part instead."""
    opts = rule.get("options", {})
    body_part = opts.get("body_part", "html")
    content = _get_body(email, body_part)
    if content is None:
        print(f"  [!] No {body_part} body found")
        return []
    path = make_output_path(out_dir, email, ".pdf")
    _html_to_pdf(content, path)
    print(f"  -> {path}")
    return [path]


def action_text(client: JMAPClient, email: dict, rule: dict, out_dir: Path) -> list[Path]:
    """Save an email body part as plain text. Defaults to the text body; use body_part: html to use the HTML part instead."""
    opts = rule.get("options", {})
    body_part = opts.get("body_part", "text")
    content = _get_body(email, body_part)
    if content is None:
        raise RuntimeError(f"No {body_part} body found — cannot save as text")
    path = make_output_path(out_dir, email, ".txt")
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")
    return [path]


def action_text_pdf(client: JMAPClient, email: dict, rule: dict, out_dir: Path) -> list[Path]:
    """Wrap the plain-text body in minimal HTML and print it to PDF. Defaults to the text body; use body_part: html to use the HTML part instead."""
    opts = rule.get("options", {})
    body_part = opts.get("body_part", "text")
    content = _get_body(email, body_part)
    if content is None:
        print(f"  [!] No {body_part} body found")
        return []
    import html as html_mod
    escaped = html_mod.escape(content)
    wrapped = (
        "<html><head><meta charset='utf-8'>"
        "<style>body{font-family:monospace;white-space:pre-wrap;margin:2em;font-size:12px;}</style>"
        "</head><body>" + escaped + "</body></html>"
    )
    path = make_output_path(out_dir, email, ".pdf")
    _html_to_pdf(wrapped, path)
    print(f"  -> {path}")
    return [path]


def action_save_attachment(client: JMAPClient, email: dict, rule: dict, out_dir: Path) -> list[Path]:
    opts = rule.get("options", {})
    allowed_types = opts.get("mime_types")  # None = accept all

    attachments = email.get("attachments", [])
    if not attachments:
        print(f"  [!] No attachments")
        return []

    paths = []
    for att in attachments:
        mime = att.get("type", "")
        if allowed_types and mime not in allowed_types:
            continue

        blob_id = att.get("blobId")
        att_name = att.get("name") or "attachment"
        if not blob_id:
            print(f"  [!] Attachment '{att_name}' has no blobId, skipping")
            continue
        ext = Path(att_name).suffix or _ext_for_mime(mime)
        label = Path(att_name).stem if len(attachments) > 1 else ""

        data = client.download_blob(blob_id, att_name, mime)
        path = make_output_path(out_dir, email, ext, label=label)
        path.write_bytes(data)
        print(f"  -> {path}  ({len(data):,} bytes)")
        paths.append(path)

    if not paths:
        print(f"  [!] No attachments matched filter {allowed_types}")
    return paths


def action_fetch_link(client: JMAPClient, email: dict, rule: dict, out_dir: Path, _session: requests.Session | None = None) -> list[Path]:
    opts = rule.get("options", {})
    links = extract_links(email, opts.get("link_pattern"))
    if not links:
        print(f"  [!] No matching links found")
        return []

    url = links[0]
    session = _session or requests.Session()
    try:
        resp = session.get(url, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        ext = _ext_from_content(resp.content, _ext_for_mime(resp.headers.get("Content-Type", "")))

        _sniff = resp.content[:18].lstrip(b" \t\n\r\xef\xbb\xbf")
        if ext in (".html", ".bin") and _sniff.startswith(b"<"):
            path = make_output_path(out_dir, email, ".pdf")
            _url_to_pdf(resp.url, path)
            print(f"  -> {path}  [{resp.url}]")
        else:
            path = make_output_path(out_dir, email, ext)
            path.write_bytes(resp.content)
            print(f"  -> {path}  ({len(resp.content):,} bytes)  [{url}]")

    except requests.RequestException:
        path = make_output_path(out_dir, email, ".pdf")
        _url_to_pdf(url, path)
        print(f"  -> {path}  [browser]  [{url}]")

    return [path]


def action_screenshot_link(client: JMAPClient, email: dict, rule: dict, out_dir: Path) -> list[Path]:
    opts = rule.get("options", {})
    links = extract_links(email, opts.get("link_pattern"))
    if not links:
        print(f"  [!] No matching links found")
        return []

    url = links[0]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [!] Playwright not installed: pip install playwright && playwright install chromium")
        return []

    path = make_output_path(out_dir, email, ".png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.screenshot(path=str(path), full_page=True)
        browser.close()

    print(f"  -> {path}  [{url}]")
    return [path]


ACTIONS = {
    "html": action_html,
    "text": action_text,
    "text_pdf": action_text_pdf,
    "save_attachment": action_save_attachment,
    "fetch_link": action_fetch_link,
    "screenshot_link": action_screenshot_link,
}


# ---------------------------------------------------------------------------
# Manifest (download tracking)
# ---------------------------------------------------------------------------

def load_manifest(out_dir: Path) -> dict:
    path = out_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(out_dir: Path, manifest: dict):
    path = out_dir / "manifest.json"
    tmp = path.with_suffix(".json.tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def record_download(manifest: dict, email: dict, rule: dict, action: str,
                    output_files: list[Path], receipts_dir: Path) -> dict:
    froms = email.get("from", [])
    entry = {
        "email_id": email["id"],
        "subject": email.get("subject", ""),
        "from": froms[0].get("email", "") if froms else "",
        "received_at": email.get("receivedAt", ""),
        "rule_name": rule.get("name", action),
        "action": action,
        "output_files": [str(p.relative_to(receipts_dir)) for p in output_files],
        "downloaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest[email["id"]] = entry
    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download receipts from Fastmail via JMAP")
    parser.add_argument("--token", default=os.environ.get("FASTMAIL_TOKEN"),
                        help="Fastmail API token (or set FASTMAIL_TOKEN env var)")
    parser.add_argument("--folder", default="Receipts",
                        help="Mailbox folder name (default: Receipts)")
    parser.add_argument("--after", required=True,
                        help="Start date inclusive, YYYY-MM-DD")
    parser.add_argument("--before", required=True,
                        help="End date exclusive, YYYY-MM-DD")
    parser.add_argument("--rules", default="rules.yaml",
                        help="Path to rules YAML file (default: rules.yaml)")
    parser.add_argument("--out", default="./out",
                        help="Output directory (default: ./out)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without saving files")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess all emails regardless of manifest")
    args = parser.parse_args()

    if not args.token:
        print("Error: provide --token or set FASTMAIL_TOKEN", file=sys.stderr)
        sys.exit(1)

    after = datetime.strptime(args.after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    before = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out_dir = Path(args.out)

    with open(args.rules) as f:
        config = yaml.safe_load(f)
    rules = config.get("rules", [])

    print(f"Connecting to Fastmail JMAP…")
    client = JMAPClient(args.token)
    client.connect()
    print(f"Account: {client.account_id}")

    print(f"Finding mailbox '{args.folder}'…")
    mailbox_id = find_mailbox(client, args.folder)

    print(f"Fetching emails {args.after} → {args.before}…")
    emails = fetch_emails(client, mailbox_id, after, before)
    print(f"Found {len(emails)} email(s).\n")

    manifest = load_manifest(out_dir)
    receipts_dir = out_dir / "receipts"

    for email in emails:
        subject = email.get("subject", "(no subject)")
        received = email.get("receivedAt", "")[:10]
        froms = email.get("from", [])
        from_str = froms[0].get("email", "?") if froms else "?"

        if not args.force and email["id"] in manifest:
            print(f"[SEEN]  {received}  {from_str}  |  {subject}")
            continue

        rule = find_rule(rules, email)
        if rule is None:
            print(f"[SKIP]  {received}  {from_str}  |  {subject}")
            continue

        action_name = rule.get("action", "text")
        rule_name = rule.get("name", action_name)
        print(f"[{action_name.upper()}]  {received}  {from_str}  |  {subject}  (rule: {rule_name})")

        if args.dry_run:
            continue

        action_fn = ACTIONS.get(action_name)
        if action_fn is None:
            print(f"  [!] Unknown action '{action_name}'")
            continue

        receipts_dir.mkdir(parents=True, exist_ok=True)
        try:
            output_files = action_fn(client, email, rule, receipts_dir)
            if output_files:
                record_download(manifest, email, rule, action_name, output_files, receipts_dir)
                save_manifest(out_dir, manifest)
        except Exception as exc:
            print(f"  [!] {exc}")


if __name__ == "__main__":
    main()
