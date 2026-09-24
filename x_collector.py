"""Build 5.15 — Free X Fusion Collector.

Combines three no-paid-X-API acquisition paths:

1) Assisted/manual CSV inbox:
   Put compatible CSV files in x_inbox/*.csv and commit them to GitHub.
2) Free web-indexed X discovery:
   Query public Bing web-search RSS/HTML results for x.com/twitter.com status URLs.
3) Official public X oEmbed hydration:
   Resolve discovered or manually supplied public post URLs through X's
   keyless publish.x.com/publish.twitter.com oEmbed service.

The collector merges all REAL rows with existing Supabase X records,
deduplicates by X post ID / external_id, sorts newest-first, and targets:
- minimum report pool: 10 posts when at least 10 real stored/discovered rows exist
- maximum: 50 latest posts by default

No post is fabricated. Engagement/follower metrics that are not exposed by the
free source are stored as 0.
"""
from __future__ import annotations

import csv
import hashlib
import html as html_lib
from html.parser import HTMLParser
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import xml.etree.ElementTree as ET

import requests

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INBOX_DIR = Path(os.getenv("X_INBOX_DIR", "x_inbox"))
URL_FILE = Path(os.getenv("X_URL_FILE", "x_urls.txt"))

X_STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/"
    r"(?:(?P<user>[A-Za-z0-9_]{1,15})|i(?:/web)?)/status/"
    r"(?P<id>\d{15,22})",
    re.IGNORECASE,
)

TWITTER_EPOCH_MS = 1288834974657


def env(name, default=""):
    return str(os.getenv(name, default) or default).strip()


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except Exception:
        return int(default)


def env_bool(name, default=False):
    value = env(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on", "y"}


def supabase_config():
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials are missing")
    return url, key


def supabase_headers(prefer=None):
    _, key = supabase_config()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _safe_int(value):
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def _iso_utc(value):
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def timestamp_from_post_id(post_id):
    """Derive UTC creation time from an X/Twitter Snowflake post ID."""
    try:
        snowflake = int(str(post_id))
        ms = (snowflake >> 22) + TWITTER_EPOCH_MS
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def extract_status_url(value):
    if not value:
        return None
    m = X_STATUS_RE.search(html_lib.unescape(str(value)))
    if not m:
        return None
    username = m.group("user")
    post_id = m.group("id")
    if username:
        return f"https://x.com/{username}/status/{post_id}"
    return f"https://x.com/i/status/{post_id}"


def status_id_from_url(url):
    m = X_STATUS_RE.search(str(url or ""))
    return m.group("id") if m else None


def status_user_from_url(url):
    m = X_STATUS_RE.search(str(url or ""))
    if not m:
        return ""
    return m.group("user") or ""


def external_id_for_row(row):
    url = row.get("source_url", "")
    post_id = status_id_from_url(url)
    if post_id:
        return f"x:{post_id}"
    material = "|".join(
        [
            str(row.get("username", "")),
            str(row.get("timestamp", "")),
            str(row.get("text", "")),
            str(url),
        ]
    )
    return "xmanual:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_row(row, default_platform="x"):
    text = str(row.get("text", "") or "").strip()
    username = str(row.get("username", "") or "").strip().lstrip("@")
    source_url = extract_status_url(row.get("source_url", "")) or str(
        row.get("source_url", "") or ""
    ).strip()
    post_id = status_id_from_url(source_url)

    timestamp = _iso_utc(row.get("timestamp"))
    if not timestamp and post_id:
        timestamp = timestamp_from_post_id(post_id)
    if not timestamp or not text:
        return None

    if not username and source_url:
        username = status_user_from_url(source_url)
    if not username:
        username = "unknown"

    hashtags = str(row.get("hashtags", "") or "").strip()
    if not hashtags:
        hashtags = " ".join(re.findall(r"(?<!\w)#[\w_]+", text, flags=re.UNICODE))

    result = {
        "external_id": str(row.get("external_id", "") or "").strip(),
        "platform": str(row.get("platform", "") or default_platform).strip()
        or default_platform,
        "timestamp": timestamp,
        "username": username,
        "text": text,
        "hashtags": hashtags,
        "likes": _safe_int(row.get("likes")),
        "reposts": _safe_int(row.get("reposts")),
        "replies": _safe_int(row.get("replies")),
        "followers": _safe_int(row.get("followers")),
        "source_url": source_url,
        "topic_seed": "ISRO",
    }
    if not result["external_id"]:
        result["external_id"] = external_id_for_row(result)
    return result


def load_existing_x(limit=5000):
    url, _ = supabase_config()
    r = requests.get(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers(),
        params={
            "select": (
                "external_id,platform,timestamp,username,text,hashtags,"
                "likes,reposts,replies,followers,source_url"
            ),
            "platform": "in.(x,x_manual)",
            "order": "timestamp.desc",
            "limit": str(limit),
        },
        timeout=45,
    )
    r.raise_for_status()
    rows = []
    for raw in r.json() or []:
        row = normalize_row(raw, default_platform=str(raw.get("platform") or "x"))
        if row:
            rows.append(row)
    return rows


def upsert(rows):
    if not rows:
        return 0
    url, _ = supabase_config()
    payload = []
    for row in rows:
        payload.append(
            {
                "external_id": row["external_id"],
                "platform": row["platform"],
                "timestamp": row["timestamp"],
                "username": row["username"],
                "text": row["text"],
                "hashtags": row["hashtags"],
                "likes": row["likes"],
                "reposts": row["reposts"],
                "replies": row["replies"],
                "followers": row["followers"],
                "source_url": row["source_url"] or None,
            }
        )
    r = requests.post(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers("resolution=merge-duplicates,return=minimal"),
        params={"on_conflict": "external_id"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return len(payload)


def merge_rows(*groups):
    """Deduplicate while preferring rows with richer engagement/text metadata."""
    merged = {}
    for rows in groups:
        for row in rows or []:
            if not row:
                continue
            key = str(row.get("external_id", "")).strip()
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(row)
                continue

            # Preserve the richer existing/manual metrics; fill blanks from new row.
            for field in ["username", "text", "hashtags", "source_url", "timestamp"]:
                if not existing.get(field) and row.get(field):
                    existing[field] = row[field]
            for field in ["likes", "reposts", "replies", "followers"]:
                existing[field] = max(_safe_int(existing.get(field)), _safe_int(row.get(field)))
            # Prefer manual platform marker if either source is manual.
            if row.get("platform") == "x_manual" or existing.get("platform") == "x_manual":
                existing["platform"] = "x_manual"

    return sorted(
        merged.values(),
        key=lambda x: str(x.get("timestamp", "")),
        reverse=True,
    )


# ---------------------------------------------------------------------
# FREE OPTION 1: assisted/manual CSV inbox
# ---------------------------------------------------------------------
def collect_csv_inbox():
    rows = []
    files = []
    if not INBOX_DIR.exists():
        return rows, files

    for path in sorted(INBOX_DIR.glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    row = normalize_row(raw, default_platform="x_manual")
                    if row:
                        rows.append(row)
            files.append(path.name)
        except Exception as exc:
            print(
                f"::warning::Could not import {path}: "
                f"{type(exc).__name__}: {exc}"
            )
    return merge_rows(rows), files


# ---------------------------------------------------------------------
# FREE OPTION 2: public web-index discovery
# ---------------------------------------------------------------------
def _bing_queries(topic):
    topic = topic or "ISRO"
    return [
        f'site:x.com "{topic}" status',
        f'site:twitter.com "{topic}" status',
        f'site:x.com "@isro" status',
        f'site:x.com "#ISRO" status',
    ]


def _extract_urls_from_text(*values):
    found = []
    seen = set()
    for value in values:
        if not value:
            continue
        for m in X_STATUS_RE.finditer(html_lib.unescape(str(value))):
            user = m.group("user")
            post_id = m.group("id")
            url = (
                f"https://x.com/{user}/status/{post_id}"
                if user
                else f"https://x.com/i/status/{post_id}"
            )
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def bing_rss_discover(query):
    """Best-effort public Bing RSS discovery. No Bing API key is used."""
    r = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss", "count": "50"},
        headers={"User-Agent": "Mozilla/5.0 SMI-Free-X-Fusion/1.0"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Bing RSS HTTP {r.status_code}")

    root = ET.fromstring(r.content)
    urls = []
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        title = item.findtext("title") or ""
        desc = item.findtext("description") or ""
        urls.extend(_extract_urls_from_text(link, title, desc))
    return list(dict.fromkeys(urls))


def bing_html_discover(query):
    """Fallback if RSS returns no usable X status URLs."""
    r = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "count": "50"},
        headers={"User-Agent": "Mozilla/5.0 SMI-Free-X-Fusion/1.0"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Bing HTML HTTP {r.status_code}")
    return _extract_urls_from_text(r.text)


def discover_web_indexed_urls(topic, max_urls=100):
    all_urls = []
    diagnostics = []
    seen = set()

    for query in _bing_queries(topic):
        query_urls = []
        rss_error = None
        html_error = None
        try:
            query_urls = bing_rss_discover(query)
        except Exception as exc:
            rss_error = f"{type(exc).__name__}: {exc}"

        if not query_urls:
            try:
                query_urls = bing_html_discover(query)
            except Exception as exc:
                html_error = f"{type(exc).__name__}: {exc}"

        for url in query_urls:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)
                if len(all_urls) >= max_urls:
                    break

        diagnostics.append(
            {
                "query": query,
                "urls": len(query_urls),
                "rss_error": rss_error,
                "html_error": html_error,
            }
        )
        if len(all_urls) >= max_urls:
            break

    # Newest-first using the post-ID timestamp even before hydration.
    all_urls.sort(
        key=lambda u: timestamp_from_post_id(status_id_from_url(u)) or "",
        reverse=True,
    )
    return all_urls[:max_urls], diagnostics


# ---------------------------------------------------------------------
# FREE OPTION 3: official keyless X oEmbed hydration
# ---------------------------------------------------------------------
class _PostTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_p = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "p" and not self.in_p:
            self.in_p = True

    def handle_endtag(self, tag):
        if tag.lower() == "p" and self.in_p:
            self.in_p = False

    def handle_data(self, data):
        if self.in_p:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts).strip()


def oembed_hydrate(post_url):
    post_id = status_id_from_url(post_url)
    if not post_id:
        return None, "not_status_url"

    username = status_user_from_url(post_url)
    canonical = (
        f"https://x.com/{username}/status/{post_id}"
        if username
        else post_url
    )

    endpoints = [
        "https://publish.x.com/oembed",
        "https://publish.twitter.com/oembed",
    ]

    last_error = ""
    for endpoint in endpoints:
        # Legacy endpoint can be more tolerant of twitter.com canonical URLs.
        embed_url = canonical
        if "publish.twitter.com" in endpoint:
            embed_url = canonical.replace("https://x.com/", "https://twitter.com/")

        try:
            r = requests.get(
                endpoint,
                params={
                    "url": embed_url,
                    "omit_script": "true",
                    "dnt": "true",
                    "hide_thread": "true",
                },
                headers={"User-Agent": "Mozilla/5.0 SMI-Free-X-Fusion/1.0"},
                timeout=30,
                allow_redirects=True,
            )
            if r.status_code != 200:
                last_error = f"{endpoint} HTTP {r.status_code}"
                continue

            payload = r.json()
            parser = _PostTextParser()
            parser.feed(str(payload.get("html", "") or ""))
            text = parser.text()
            if not text:
                last_error = f"{endpoint} returned no post text"
                continue

            author_url = str(payload.get("author_url", "") or "")
            author_name = str(payload.get("author_name", "") or "").strip()
            author_handle = status_user_from_url(author_url)
            if not author_handle:
                # author_url is often a profile URL, not a status URL
                m = re.search(
                    r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)",
                    author_url,
                    flags=re.I,
                )
                if m:
                    author_handle = m.group(1)

            username = author_handle or username or author_name or "unknown"
            source_url = (
                f"https://x.com/{username}/status/{post_id}"
                if username and username != "unknown"
                else canonical
            )

            row = normalize_row(
                {
                    "external_id": f"x:{post_id}",
                    "platform": "x",
                    "timestamp": timestamp_from_post_id(post_id),
                    "username": username,
                    "text": text,
                    "hashtags": " ".join(
                        re.findall(r"(?<!\w)#[\w_]+", text, flags=re.UNICODE)
                    ),
                    "likes": 0,
                    "reposts": 0,
                    "replies": 0,
                    "followers": 0,
                    "source_url": source_url,
                },
                default_platform="x",
            )
            return row, ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    return None, last_error or "oembed_failed"


def load_manual_url_file():
    urls = []
    if not URL_FILE.exists():
        return urls
    try:
        for line in URL_FILE.read_text(encoding="utf-8").splitlines():
            url = extract_status_url(line)
            if url:
                urls.append(url)
    except Exception as exc:
        print(
            f"::warning::Could not read {URL_FILE}: "
            f"{type(exc).__name__}: {exc}"
        )
    return list(dict.fromkeys(urls))


def hydrate_urls(urls, limit=100):
    rows = []
    errors = []
    for url in urls[:limit]:
        row, err = oembed_hydrate(url)
        if row:
            rows.append(row)
        else:
            errors.append({"url": url, "error": err})
    return merge_rows(rows), errors


def write_latest_csv(rows, filename="x_latest_fused.csv"):
    path = OUTPUT_DIR / filename
    fields = [
        "timestamp",
        "username",
        "text",
        "hashtags",
        "likes",
        "reposts",
        "replies",
        "followers",
        "platform",
        "source_url",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    return path


def write_summary(summary):
    path = OUTPUT_DIR / "x_collection_summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    topic = env("X_FREE_TOPIC", env("X_QUERY", "ISRO"))
    # Strip any paid-API query operators from the free web-search topic.
    if " OR " in topic or "-is:" in topic or "@" in topic or "#" in topic:
        topic = "ISRO"

    min_posts = max(10, env_int("X_MIN_POSTS", 10))
    max_posts = min(50, max(min_posts, env_int("X_MAX_POSTS", 50)))
    discovery_limit = max(max_posts * 3, 60)
    enable_web = env_bool("X_ENABLE_WEB_INDEX", True)
    enable_oembed = env_bool("X_ENABLE_OEMBED", True)

    existing = load_existing_x(limit=5000)
    existing_ids = {r["external_id"] for r in existing}

    # Option 1
    inbox_rows, inbox_files = collect_csv_inbox()

    # Option 2
    indexed_urls = []
    discovery_diag = []
    if enable_web:
        try:
            indexed_urls, discovery_diag = discover_web_indexed_urls(
                topic, max_urls=discovery_limit
            )
        except Exception as exc:
            discovery_diag.append(
                {
                    "query": topic,
                    "urls": 0,
                    "rss_error": f"{type(exc).__name__}: {exc}",
                    "html_error": None,
                }
            )

    # Manual URL list is also free and useful when search indexing misses a post.
    manual_urls = load_manual_url_file()
    candidate_urls = list(dict.fromkeys(manual_urls + indexed_urls))

    # Option 3
    oembed_rows = []
    oembed_errors = []
    if enable_oembed and candidate_urls:
        oembed_rows, oembed_errors = hydrate_urls(
            candidate_urls,
            limit=discovery_limit,
        )

    acquired = merge_rows(inbox_rows, oembed_rows)
    upserted = upsert(acquired) if acquired else 0

    new_ids = {r["external_id"] for r in acquired} - existing_ids
    combined = merge_rows(existing, acquired)
    selected = combined[:max_posts]

    latest_csv = write_latest_csv(selected)

    newest = selected[0]["timestamp"] if selected else None
    oldest = selected[-1]["timestamp"] if selected else None

    summary = {
        "status": (
            "success"
            if len(selected) >= min_posts
            else ("partial" if selected else "empty")
        ),
        "mode": "free_x_fusion",
        "minimum_target": min_posts,
        "maximum_target": max_posts,
        "target_met": len(selected) >= min_posts,
        "selected_latest_count": len(selected),
        "existing_supabase_count": len(existing),
        "csv_inbox": {
            "files": inbox_files,
            "rows": len(inbox_rows),
        },
        "web_index": {
            "enabled": enable_web,
            "discovered_urls": len(indexed_urls),
            "diagnostics": discovery_diag,
        },
        "oembed": {
            "enabled": enable_oembed,
            "candidate_urls": len(candidate_urls),
            "hydrated_rows": len(oembed_rows),
            "failed_urls": len(oembed_errors),
            "sample_errors": oembed_errors[:10],
        },
        "fetched": len(acquired),
        "new_count": len(new_ids),
        "upserted": upserted,
        "newest_timestamp": newest,
        "oldest_timestamp": oldest,
        "latest_csv": str(latest_csv),
        "note": (
            f"Using the latest {len(selected)} real unique X rows from all free "
            "sources combined. No synthetic posts were created."
        ),
    }
    write_summary(summary)

    if len(selected) >= min_posts:
        print(
            f"Free X fusion target met: {len(selected)} latest unique real posts "
            f"available (minimum {min_posts}, maximum {max_posts})."
        )
    else:
        print(
            f"::warning::Only {len(selected)} real unique X posts are available "
            f"across free sources; minimum target is {min_posts}. No rows were fabricated."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
