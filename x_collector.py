"""Build 5.18 — public-conversation-first free X collector.

Three free acquisition paths are combined every run:

1. Existing/manual X:
   - existing Supabase x/x_manual rows
   - x_inbox/*.csv
   - x_urls.txt

2. Public web-index discovery:
   - Bing RSS/HTML
   - DuckDuckGo HTML fallback
   This discovers public x.com/twitter.com status URLs.

3. X public embed/syndication:
   - public profile timelines for non-official handles discovered from indexed posts
   - optional configured non-official seed handles
   - X CDN tweet-result hydration for discovered URLs
   - official oEmbed fallback for known URLs

Default objective: public conversation ABOUT ISRO. The official @isro publishing
feed is excluded by default so it cannot dominate the monitoring sample.

All rows are real public/stored posts. No synthetic posts are created.
The consolidated pool is deduplicated by X post ID/external_id and sorted
newest-first. The output target is minimum 10 when available, maximum 50.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html as html_lib
from html.parser import HTMLParser
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INBOX_DIR = Path(os.getenv("X_INBOX_DIR", "x_inbox"))
URL_FILE = Path(os.getenv("X_URL_FILE", "x_urls.txt"))

STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/"
    r"(?:(?P<user>[A-Za-z0-9_]{1,15})|i(?:/web)?)/status/"
    r"(?P<id>\d{15,22})",
    re.I,
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


def http_get(url, *, params=None, timeout=35, retries=3, headers=None):
    merged_headers = {
        "User-Agent": "Mozilla/5.0 SMI-Free-X/5.17",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    if headers:
        merged_headers.update(headers)
    last = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.get(
                url,
                params=params,
                headers=merged_headers,
                timeout=timeout,
                allow_redirects=True,
            )
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(1.1 * (attempt + 1))
                continue
            return r
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.1 * (attempt + 1))
    raise RuntimeError(f"GET failed: {type(last).__name__}: {last}")


def safe_int(value):
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def supabase_config():
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials are missing")
    return url, key


def sb_headers(prefer=None):
    _, key = supabase_config()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def status_parts(url):
    m = STATUS_RE.search(str(url or ""))
    if not m:
        return "", ""
    return (m.group("user") or "", m.group("id") or "")


def canonical_status_url(url):
    user, post_id = status_parts(url)
    if not post_id:
        return ""
    if user:
        return f"https://x.com/{user}/status/{post_id}"
    return f"https://x.com/i/status/{post_id}"


def snowflake_timestamp(post_id):
    try:
        ms = (int(str(post_id)) >> 22) + TWITTER_EPOCH_MS
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def iso_utc(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # First try ISO.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    # Common X/Twitter legacy created_at format.
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return None


def hashtags_from_entities(entities, text):
    tags = []
    if isinstance(entities, dict):
        for item in entities.get("hashtags", []) or []:
            if isinstance(item, dict):
                tag = str(item.get("text") or item.get("tag") or "").strip()
            else:
                tag = str(item or "").strip()
            if tag:
                tags.append("#" + tag.lstrip("#"))
    if not tags:
        tags = re.findall(r"(?<!\w)#[\w_]+", str(text or ""), flags=re.UNICODE)
    return " ".join(dict.fromkeys(tags))


def normalize_row(raw, platform="x"):
    raw = dict(raw or {})
    source_url = canonical_status_url(raw.get("source_url")) or str(
        raw.get("source_url") or ""
    ).strip()
    user_from_url, post_id = status_parts(source_url)

    text = str(raw.get("text") or raw.get("full_text") or "").strip()
    if not text:
        return None

    timestamp = iso_utc(raw.get("timestamp") or raw.get("created_at"))
    if not timestamp and post_id:
        timestamp = snowflake_timestamp(post_id)
    if not timestamp:
        return None

    username = str(raw.get("username") or raw.get("screen_name") or "").strip().lstrip("@")
    if not username:
        username = user_from_url or "unknown"

    row = {
        "external_id": str(raw.get("external_id") or "").strip(),
        "platform": str(raw.get("platform") or platform).strip() or platform,
        "timestamp": timestamp,
        "username": username,
        "text": text,
        "hashtags": str(raw.get("hashtags") or "").strip(),
        "likes": safe_int(raw.get("likes", raw.get("favorite_count", 0))),
        "reposts": safe_int(raw.get("reposts", raw.get("retweet_count", 0))),
        "replies": safe_int(
            raw.get("replies", raw.get("reply_count", raw.get("conversation_count", 0)))
        ),
        "followers": safe_int(raw.get("followers", raw.get("followers_count", 0))),
        "source_url": source_url,
        "topic_seed": "ISRO",
    }

    if not row["hashtags"]:
        row["hashtags"] = hashtags_from_entities(raw.get("entities"), text)

    if not row["external_id"]:
        if post_id:
            row["external_id"] = f"x:{post_id}"
        else:
            material = "|".join(
                [
                    row["username"],
                    row["timestamp"],
                    row["text"],
                    row["source_url"],
                ]
            )
            row["external_id"] = (
                "xmanual:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
            )
    return row


def merge_rows(*groups):
    """Deduplicate and retain the richest version of each post."""
    merged = {}
    for group in groups:
        for row in group or []:
            if not row:
                continue
            key = str(row.get("external_id") or "").strip()
            if not key:
                continue
            old = merged.get(key)
            if old is None:
                merged[key] = dict(row)
                continue

            for field in ("username", "text", "hashtags", "source_url", "timestamp"):
                if (not old.get(field)) and row.get(field):
                    old[field] = row[field]
            for field in ("likes", "reposts", "replies", "followers"):
                old[field] = max(safe_int(old.get(field)), safe_int(row.get(field)))
            if old.get("platform") != "x_manual" and row.get("platform") == "x_manual":
                old["platform"] = "x_manual"

    return sorted(
        merged.values(),
        key=lambda r: str(r.get("timestamp") or ""),
        reverse=True,
    )


def load_existing_x(limit=5000):
    url, _ = supabase_config()
    r = requests.get(
        f"{url}/rest/v1/social_posts",
        headers=sb_headers(),
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
        row = normalize_row(raw, platform=str(raw.get("platform") or "x"))
        if row:
            rows.append(row)
    return merge_rows(rows)


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
        headers=sb_headers("resolution=merge-duplicates,return=minimal"),
        params={"on_conflict": "external_id"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return len(payload)


# =====================================================================
# METHOD 1 — existing/manual/CSV/URL inbox
# =====================================================================
def collect_csv_inbox():
    rows = []
    files = []
    if not INBOX_DIR.exists():
        return rows, files

    for path in sorted(INBOX_DIR.glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for raw in csv.DictReader(f):
                    row = normalize_row(raw, platform="x_manual")
                    if row:
                        rows.append(row)
            files.append(path.name)
        except Exception as exc:
            print(f"::warning::CSV inbox import failed for {path.name}: {exc}")
    return merge_rows(rows), files


def load_url_inbox():
    urls = []
    if not URL_FILE.exists():
        return urls
    for line in URL_FILE.read_text(encoding="utf-8").splitlines():
        u = canonical_status_url(line)
        if u:
            urls.append(u)
    return list(dict.fromkeys(urls))


# =====================================================================
# METHOD 2 — free public web-index discovery
# =====================================================================
def _decode_bing_redirect(url):
    value = html_lib.unescape(str(url or "")).strip()
    if not value:
        return value
    try:
        parsed = urlparse(value)
        qs = parse_qs(parsed.query)
        raw = (qs.get("u") or [""])[0]
        if raw.startswith("a1"):
            encoded = raw[2:]
            encoded += "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded.encode()).decode("utf-8", "ignore")
            if decoded.startswith("http"):
                return decoded
        for key in ("url", "r"):
            candidate = (qs.get(key) or [""])[0]
            candidate = unquote(candidate)
            if candidate.startswith("http"):
                return candidate
    except Exception:
        pass
    return value


def _unwrap_search_url(url):
    value = html_lib.unescape(str(url or "")).strip()
    if not value:
        return value
    try:
        parsed = urlparse(value)
        qs = parse_qs(parsed.query)
        if "duckduckgo.com" in parsed.netloc:
            target = unquote((qs.get("uddg") or [""])[0])
            if target.startswith("http"):
                return target
        if "bing.com" in parsed.netloc:
            return _decode_bing_redirect(value)
    except Exception:
        pass
    return value


def extract_status_urls(text):
    raw = html_lib.unescape(str(text or ""))
    candidates = [raw]
    if "<" in raw and ">" in raw:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            candidates.extend(a.get("href", "") for a in soup.find_all("a", href=True))
        except Exception:
            pass
    candidates.extend(re.findall(r'https?://[^\s"\'<>]+', raw))

    found = []
    seen = set()
    for candidate in candidates:
        for value in (candidate, _unwrap_search_url(candidate), unquote(str(candidate))):
            for m in STATUS_RE.finditer(html_lib.unescape(str(value or ""))):
                user = m.group("user")
                post_id = m.group("id")
                url = f"https://x.com/{user}/status/{post_id}" if user else f"https://x.com/i/status/{post_id}"
                if url not in seen:
                    seen.add(url)
                    found.append(url)
    return found


def search_queries(topic):
    """Public-web queries aimed at people discussing ISRO."""
    t = re.sub(r"\s+", " ", str(topic or "ISRO")).strip() or "ISRO"
    return [
        f'site:x.com "{t}" status -site:x.com/isro',
        f'site:x.com "#{t}" status -site:x.com/isro',
        'site:x.com "@isro" status -site:x.com/isro',
        'site:x.com "Indian Space Research Organisation" status -site:x.com/isro',
        f'site:twitter.com "{t}" status -site:twitter.com/isro',
        f'site:x.com "{t}" launch -site:x.com/isro',
        f'site:x.com "{t}" mission -site:x.com/isro',
        f'site:x.com "{t}" satellite -site:x.com/isro',
        'site:x.com "Gaganyaan" ISRO status -site:x.com/isro',
        'site:x.com "Chandrayaan" ISRO status -site:x.com/isro',
        'site:x.com "PSLV" ISRO status -site:x.com/isro',
        'site:x.com "LVM3" ISRO status -site:x.com/isro',
        'site:x.com "NISAR" ISRO status -site:x.com/isro',
    ]


def bing_rss(query):
    r = http_get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss", "count": "50"},
        timeout=30,
        retries=2,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Bing RSS HTTP {r.status_code}")
    root = ET.fromstring(r.content)
    values = []
    for item in root.findall(".//item"):
        values.extend([item.findtext("link") or "", item.findtext("title") or "", item.findtext("description") or ""])
    return extract_status_urls("\n".join(values))


def bing_html(query):
    r = http_get("https://www.bing.com/search", params={"q": query, "count": "50"}, timeout=30, retries=2)
    if r.status_code != 200:
        raise RuntimeError(f"Bing HTML HTTP {r.status_code}")
    return extract_status_urls(r.text)


def ddg_html(query):
    r = http_get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=30, retries=2)
    if r.status_code != 200:
        raise RuntimeError(f"DuckDuckGo HTML HTTP {r.status_code}")
    return extract_status_urls(r.text)


def discover_indexed_urls(topic, limit=200):
    seen = set()
    urls = []
    diagnostics = []
    for query in search_queries(topic):
        qdiag = {"query": query}
        for name, fn in [("bing_rss", bing_rss), ("bing_html", bing_html), ("duckduckgo", ddg_html)]:
            try:
                found = fn(query)
                qdiag[name] = len(found)
                for u in found:
                    if u not in seen:
                        seen.add(u)
                        urls.append(u)
            except Exception as exc:
                qdiag[name] = f"error: {type(exc).__name__}: {exc}"
        diagnostics.append(qdiag)
        if len(urls) >= limit:
            break
    excluded = parse_handle_set(env("X_EXCLUDE_HANDLES", "isro"), default="isro")
    urls = [
        u for u in urls
        if (status_parts(u)[0] or "").lower() not in excluded
    ]
    urls.sort(
        key=lambda u: snowflake_timestamp(status_parts(u)[1]) or "",
        reverse=True,
    )
    return urls[:limit], diagnostics


def text_matches_topic(text, topic="ISRO"):
    t = str(text or "").lower()
    terms = {
        str(topic or "isro").lower(),
        "isro", "@isro", "#isro", "indian space research organisation",
        "gaganyaan", "pslv", "gslv", "lvm3", "chandrayaan", "aditya-l1", "nisar",
    }
    return any(term and term in t for term in terms)



def parse_handle_set(raw, default=""):
    value = str(raw if raw is not None else default)
    return {
        h.strip().lstrip("@").lower()
        for h in re.split(r"[,;\s]+", value)
        if h.strip()
    }


def row_handle(row):
    return str((row or {}).get("username", "") or "").strip().lstrip("@").lower()


def filter_conversation_rows(rows, topic="ISRO", excluded_handles=None):
    excluded = set(excluded_handles or set())
    kept = []
    for row in rows or []:
        if row_handle(row) in excluded:
            continue
        if not text_matches_topic(row.get("text", ""), topic):
            continue
        kept.append(row)
    return merge_rows(kept)


def select_diverse_conversation(rows, max_posts=50, min_posts=10, per_author_cap=3):
    """Newest-first selection that limits domination by one author."""
    max_posts = max(1, min(50, int(max_posts)))
    min_posts = max(1, min(max_posts, int(min_posts)))
    per_author_cap = max(1, min(10, int(per_author_cap)))

    ordered = merge_rows(rows)
    selected, overflow = [], []
    counts = {}
    for row in ordered:
        handle = row_handle(row) or "unknown"
        if counts.get(handle, 0) < per_author_cap and len(selected) < max_posts:
            selected.append(row)
            counts[handle] = counts.get(handle, 0) + 1
        else:
            overflow.append(row)

    if len(selected) < min_posts:
        ids = {r.get("external_id") for r in selected}
        for row in overflow:
            if len(selected) >= min_posts or len(selected) >= max_posts:
                break
            if row.get("external_id") not in ids:
                selected.append(row)
                ids.add(row.get("external_id"))

    return sorted(
        selected,
        key=lambda r: str(r.get("timestamp", "")),
        reverse=True,
    )[:max_posts]


# =====================================================================
# METHOD 3 — X public syndication/embed infrastructure
# =====================================================================
def timeline_row(tweet):
    if not isinstance(tweet, dict):
        return None
    user = tweet.get("user") or {}
    post_id = str(tweet.get("id_str") or tweet.get("id") or "").strip()
    if not post_id:
        return None

    username = str(user.get("screen_name") or "").strip()
    source_url = str(tweet.get("permalink") or "").strip()
    if source_url.startswith("/"):
        source_url = "https://x.com" + source_url
    source_url = canonical_status_url(source_url)
    if not source_url:
        source_url = (
            f"https://x.com/{username}/status/{post_id}"
            if username
            else f"https://x.com/i/status/{post_id}"
        )

    return normalize_row(
        {
            "external_id": f"x:{post_id}",
            "platform": "x",
            "timestamp": tweet.get("created_at") or snowflake_timestamp(post_id),
            "username": username,
            "text": tweet.get("full_text") or tweet.get("text") or "",
            "hashtags": hashtags_from_entities(
                tweet.get("entities"), tweet.get("full_text") or tweet.get("text")
            ),
            "likes": tweet.get("favorite_count", 0),
            "reposts": tweet.get("retweet_count", 0),
            "replies": tweet.get("reply_count", tweet.get("conversation_count", 0)),
            "followers": user.get(
                "followers_count", user.get("normal_followers_count", 0)
            ),
            "source_url": source_url,
        }
    )


def fetch_profile_timeline(handle):
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return [], "empty handle"

    url = (
        "https://syndication.twitter.com/srv/"
        f"timeline-profile/screen-name/{handle}"
    )
    r = http_get(url, timeout=45, retries=3)
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"

    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        r.text,
        flags=re.S | re.I,
    )
    if not m:
        return [], "__NEXT_DATA__ not found"

    try:
        payload = json.loads(html_lib.unescape(m.group(1)))
        entries = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("timeline", {})
            .get("entries", [])
        )
    except Exception as exc:
        return [], f"JSON parse error: {type(exc).__name__}: {exc}"

    rows = []
    for entry in entries or []:
        tweet = ((entry or {}).get("content") or {}).get("tweet")
        row = timeline_row(tweet)
        if row:
            rows.append(row)
    return merge_rows(rows), ""


def tweet_result(post_url):
    user, post_id = status_parts(post_url)
    if not post_id:
        return None, "invalid status URL"

    # X's public embedded-post JSON endpoint currently accepts token=0.
    r = http_get(
        "https://cdn.syndication.twimg.com/tweet-result",
        params={"id": post_id, "token": "0", "lang": "en"},
        headers={"Accept": "application/json"},
        timeout=30,
        retries=2,
    )
    if r.status_code != 200:
        return None, f"tweet-result HTTP {r.status_code}"

    try:
        payload = r.json()
    except Exception as exc:
        return None, f"tweet-result JSON error: {exc}"

    puser = payload.get("user") or {}
    username = str(puser.get("screen_name") or user or "").strip()
    row = normalize_row(
        {
            "external_id": f"x:{post_id}",
            "platform": "x",
            "timestamp": payload.get("created_at") or snowflake_timestamp(post_id),
            "username": username,
            "text": payload.get("text") or "",
            "hashtags": hashtags_from_entities(
                payload.get("entities"), payload.get("text")
            ),
            "likes": payload.get("favorite_count", 0),
            "reposts": payload.get("retweet_count", 0),
            "replies": payload.get(
                "reply_count", payload.get("conversation_count", 0)
            ),
            "followers": puser.get(
                "followers_count", puser.get("normal_followers_count", 0)
            ),
            "source_url": (
                f"https://x.com/{username}/status/{post_id}"
                if username
                else post_url
            ),
        }
    )
    return row, ""


class OEmbedText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_p = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "p" and not self.in_p:
            self.in_p = True

    def handle_endtag(self, tag):
        if tag.lower() == "p":
            self.in_p = False

    def handle_data(self, data):
        if self.in_p:
            self.parts.append(data)


def oembed(post_url):
    user, post_id = status_parts(post_url)
    if not post_id:
        return None, "invalid status URL"

    canonical = (
        f"https://x.com/{user}/status/{post_id}"
        if user
        else f"https://x.com/i/status/{post_id}"
    )
    endpoints = [
        ("https://publish.x.com/oembed", canonical),
        (
            "https://publish.twitter.com/oembed",
            canonical.replace("https://x.com/", "https://twitter.com/"),
        ),
    ]

    last_error = ""
    for endpoint, target in endpoints:
        try:
            r = http_get(
                endpoint,
                params={
                    "url": target,
                    "omit_script": "true",
                    "dnt": "true",
                    "hide_thread": "true",
                },
                timeout=30,
                retries=2,
            )
            if r.status_code != 200:
                last_error = f"{endpoint} HTTP {r.status_code}"
                continue
            payload = r.json()
            parser = OEmbedText()
            parser.feed(str(payload.get("html") or ""))
            text = "".join(parser.parts).strip()
            if not text:
                last_error = "oEmbed returned no post text"
                continue

            author_url = str(payload.get("author_url") or "")
            m = re.search(
                r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})",
                author_url,
                flags=re.I,
            )
            username = (m.group(1) if m else user) or "unknown"
            return normalize_row(
                {
                    "external_id": f"x:{post_id}",
                    "platform": "x",
                    "timestamp": snowflake_timestamp(post_id),
                    "username": username,
                    "text": text,
                    "likes": 0,
                    "reposts": 0,
                    "replies": 0,
                    "followers": 0,
                    "source_url": (
                        f"https://x.com/{username}/status/{post_id}"
                        if username != "unknown"
                        else canonical
                    ),
                }
            ), ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return None, last_error or "oEmbed failed"


def hydrate_urls(urls, limit=150):
    rows = []
    diagnostics = {"tweet_result_ok": 0, "oembed_ok": 0, "failed": 0}
    failures = []

    for url in urls[:limit]:
        row, err = tweet_result(url)
        if row:
            rows.append(row)
            diagnostics["tweet_result_ok"] += 1
            continue

        row2, err2 = oembed(url)
        if row2:
            rows.append(row2)
            diagnostics["oembed_ok"] += 1
        else:
            diagnostics["failed"] += 1
            failures.append(
                {"url": url, "tweet_result_error": err, "oembed_error": err2}
            )
    return merge_rows(rows), diagnostics, failures[:20]


def write_csv(rows):
    path = OUTPUT_DIR / "x_latest_fused.csv"
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
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    return path


def write_summary(summary):
    (OUTPUT_DIR / "x_collection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    min_posts = max(10, env_int("X_MIN_POSTS", 10))
    max_posts = min(50, max(min_posts, env_int("X_MAX_POSTS", 50)))
    topic = env("X_FREE_TOPIC", "ISRO") or "ISRO"
    excluded_handles = parse_handle_set(
        env("X_EXCLUDE_HANDLES", "isro"),
        default="isro",
    )
    handles = [
        h.strip().lstrip("@")
        for h in env("X_PROFILE_HANDLES", "").split(",")
        if h.strip()
        and h.strip().lstrip("@").lower() not in excluded_handles
    ]
    per_author_cap = max(1, min(10, env_int("X_MAX_POSTS_PER_AUTHOR", 3)))

    existing_all = load_existing_x(limit=5000)
    existing = filter_conversation_rows(
        existing_all,
        topic=topic,
        excluded_handles=excluded_handles,
    )
    existing_ids = {r["external_id"] for r in existing}

    # Method 1
    csv_rows, csv_files = collect_csv_inbox()
    url_inbox = load_url_inbox()

    # Method 2: multiple public search indexes/query variants.
    indexed_urls, web_diag = discover_indexed_urls(topic, limit=200)

    # Method 3a: public profile timelines. In addition to configured official
    # handles, expand into a bounded set of handles discovered from indexed
    # status URLs, then retain only their topic-matching posts.
    discovered_handles = []
    for u in indexed_urls:
        user, _ = status_parts(u)
        if user and user.lower() not in {h.lower() for h in handles} and user.lower() not in {x.lower() for x in discovered_handles}:
            discovered_handles.append(user)
    discovered_limit = max(0, min(20, env_int("X_DISCOVERED_PROFILE_LIMIT", 12)))
    expanded_handles = handles + discovered_handles[:discovered_limit]

    timeline_rows = []
    timeline_diag = {}
    configured_lower = {h.lower() for h in handles}
    for handle in expanded_handles:
        if handle.lower() in excluded_handles:
            continue
        rows, err = fetch_profile_timeline(handle)
        rows = [
            r for r in rows
            if row_handle(r) not in excluded_handles
            and text_matches_topic(r.get("text", ""), topic)
        ]
        timeline_rows.extend(rows)
        timeline_diag[handle] = {"rows": len(rows), "error": err or None, "discovered": handle.lower() not in configured_lower}
    timeline_rows = merge_rows(timeline_rows)

    # Method 3b: hydrate discovered/manual status URLs. tweet-result is tried
    # first, with public oEmbed as the fallback.
    candidate_urls = list(dict.fromkeys(url_inbox + indexed_urls))
    hydrated, hydrate_diag, hydrate_failures = hydrate_urls(candidate_urls, limit=200)

    acquired = filter_conversation_rows(
        merge_rows(csv_rows, timeline_rows, hydrated),
        topic=topic,
        excluded_handles=excluded_handles,
    )
    upserted = upsert(acquired) if acquired else 0
    new_ids = {r["external_id"] for r in acquired} - existing_ids

    # Recombine all free methods with prior stored conversation history.
    combined = filter_conversation_rows(
        merge_rows(existing, acquired),
        topic=topic,
        excluded_handles=excluded_handles,
    )
    selected = select_diverse_conversation(
        combined,
        max_posts=max_posts,
        min_posts=min_posts,
        per_author_cap=per_author_cap,
    )
    out_csv = write_csv(selected)

    summary = {
        "status": (
            "success"
            if len(selected) >= min_posts
            else ("partial" if selected else "empty")
        ),
        "mode": "free_x_fusion_v3",
        "minimum_target": min_posts,
        "maximum_target": max_posts,
        "target_met": len(selected) >= min_posts,
        "selected_latest_count": len(selected),
        "new_count": len(new_ids),
        "upserted": upserted,
        "newest_timestamp": selected[0]["timestamp"] if selected else None,
        "oldest_timestamp": selected[-1]["timestamp"] if selected else None,
        "conversation_policy": {
            "purpose": "public conversation about ISRO",
            "excluded_handles": sorted(excluded_handles),
            "per_author_cap": per_author_cap,
            "unique_authors_selected": len(
                {row_handle(r) for r in selected if row_handle(r)}
            ),
            "official_or_offtopic_existing_removed": max(
                0, len(existing_all) - len(existing)
            ),
        },
        "sources": {
            "existing_supabase": len(existing),
            "csv_inbox_rows": len(csv_rows),
            "csv_inbox_files": csv_files,
            "manual_url_inbox": len(url_inbox),
            "web_indexed_urls": len(indexed_urls),
            "discovered_profile_handles": len(discovered_handles[:discovered_limit]),
            "profile_timeline_rows": len(timeline_rows),
            "hydrated_url_rows": len(hydrated),
        },
        "timeline": timeline_diag,
        "web_discovery": web_diag,
        "hydration": hydrate_diag,
        "hydration_failures": hydrate_failures,
        "latest_csv": str(out_csv),
        "note": (
            f"Consolidated latest {len(selected)} real unique public-conversation "
            "rows ABOUT ISRO; official handles are excluded by default."
        ),
    }
    write_summary(summary)

    if len(selected) >= min_posts:
        print(
            f"Free X fusion target met: {len(selected)} latest unique X posts "
            f"(min {min_posts}, max {max_posts})."
        )
    else:
        print(
            f"::warning::Only {len(selected)} real X rows available across all "
            f"three free methods. No synthetic posts were created."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
