"""Build 6.0.0 — V8-governed DoS/ISRO media reports with semantic-analysis fallback.

This worker implements the user-supplied Master Query V8.0 with an optional free hosted open-model analysis chain and a deterministic local fallback.

* Morning: daily 08:00 IST; original first-publication window previous day 20:00 inclusive -> report day 08:00 exclusive.
* Evening: daily 20:00 IST; original first-publication window report day 08:00 inclusive -> 20:00 exclusive.
* Weekly: legacy/manual feature retained, but automatic weekly scheduling is paused under V8.0.

Each generated edition produces an editable DOCX and matching PDF. The existing hourly background monitor and all Build 5.19 monitoring/X features are preserved.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.message import EmailMessage
from html import escape as html_escape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:  # GitHub installs vaderSentiment from requirements.txt; fallback aids local diagnostics.
    SentimentIntensityAnalyzer = None
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
BUILD = "6.0.1"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MASTER_QUERY_FILE = Path(os.getenv(
    "MASTER_QUERY_FILE", "DoS_ISRO_Master_Query_Version_8_0.txt"
))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/140 Safari/537.36 SMI-ScheduledReports/6.0.1"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
SENTIMENT = SentimentIntensityAnalyzer() if SentimentIntensityAnalyzer else None

# Four-institution gate vocabulary.
INSTITUTION_PATTERNS = {
    "ISRO": [
        r"\bisro\b", r"indian space research organi[sz]ation",
        r"\bvssc\b", r"vikram sarabhai space centre", r"\bursc\b",
        r"u\s*r\s*rao satellite centre", r"\bsac\b", r"space applications centre",
        r"\bsdsc\b", r"satish dhawan space centre", r"\blpsc\b",
        r"liquid propulsion systems centre", r"\bistrac\b", r"\bnrsc\b",
        r"national remote sensing centre", r"\bhsfc\b", r"human space flight centre",
        r"\biirs\b", r"indian institute of remote sensing",
    ],
    "IN-SPACe": [
        r"\bin[-‑–—]space\b", r"indian national space promotion and authorization centre",
        r"indian national space promotion and authorisation centre",
    ],
    "NSIL": [r"\bnsil\b", r"newspace india limited"],
    "Department of Space": [
        r"department of space", r"\bdos\b.{0,35}(?:space|isro)",
        r"(?:space|isro).{0,35}\bdos\b",
    ],
}

MISSION_TERMS = [
    "gaganyaan", "chandrayaan", "nisar", "spadex", "pslv", "gslv", "lvm3",
    "sslV", "navic", "nvs-02", "nvs-03", "aditya-l1", "mangalyaan",
    "venus orbiter", "shukrayaan", "bharatiya antariksh station", "bas-01",
    "lupex", "eos-", "oceansat", "project netra", "poem-", "rlv", "trishna",
]

# V8.0 44-theme coverage matrix. Themes are coverage checks, never quotas.
THEMES = [
    "DoS governance", "ISRO leadership", "IN-SPACe authorizations", "NSIL commerce",
    "policy regulation", "parliamentary oversight", "budgets", "bilateral multilateral cooperation",
    "export trade", "launch vehicles", "PSLV", "GSLV", "LVM3", "SSLV",
    "launch infrastructure", "reusable vehicles", "propulsion", "spacecraft manufacturing",
    "Earth observation", "climate", "NavIC", "satcom", "spectrum", "astronomy",
    "lunar", "planetary solar", "Gaganyaan", "crew safety", "space stations",
    "in-orbit milestones", "anomalies delays", "reliability", "debris", "security",
    "disaster geospatial", "academia", "technology transfer", "supply chain",
    "launch startups", "satellite startups", "downstream startups", "private investment mergers",
    "criticism accountability", "emerging issues",
]

# Five substantially distinct V8 discovery passes. This deliberately exceeds the
# minimum 39-query execution gate (5 + 7 + 8 + 9 + 10). Startups are represented
# in every pass, and regional/local-language spellings are searched explicitly.
PASS_QUERIES = {
    1: [
        'ISRO Department of Space mission update private space startup',
        'site:isro.gov.in ISRO mission programme startup industry',
        'site:pib.gov.in ISRO Department of Space space',
        'IN-SPACe authorization startup India space',
        'NSIL commercial launch satellite India',
    ],
    2: [
        'ISRO India latest space startup', 'Department of Space India latest',
        'IN-SPACe NSIL India news', 'ISRO business space economy startup',
        'ISRO Reuters PTI ANI space India', 'ISRO mission technology industry India',
        'Indian private space startup ISRO ecosystem',
    ],
    3: [
        'ISRO regional India space startup', 'ISRO Karnataka Kerala Tamil Nadu Telangana news',
        'ISRO Hindi अंतरिक्ष इसरो निजी अंतरिक्ष स्टार्टअप',
        'ISRO Malayalam ഐഎസ്ആർഒ ബഹിരാകാശ സ്റ്റാർട്ടപ്പ്',
        'ISRO Tamil இஸ்ரோ விண்வெளி ஸ்டார்ட்அப்',
        'ISRO Telugu ఇస్రో అంతరిక్ష స్టార్టప్',
        'ISRO Gujarati ઇસરો અવકાશ સ્ટાર્ટઅપ',
        'ISRO Marathi इस्रो अवकाश स्टार्टअप',
    ],
    4: [
        'ISRO space science specialist startup', 'ISRO international cooperation commercial space',
        'ISRO scientific mission analysis', 'Indian space startup launch technology',
        'Indian satellite startup Earth observation', 'Indian downstream space startup geospatial',
        'IN-SPACe regulation authorization private space', 'NSIL commercial contract launch satellite',
        'India space investment merger funding startup',
    ],
    5: [
        'ISRO latest today mission roadmap', 'Indian Space Research Organisation latest today',
        'Department of Space IN-SPACe NSIL latest', 'Gaganyaan latest roadmap crew safety',
        'PSLV GSLV LVM3 SSLV latest India', 'NavIC satcom spectrum India latest',
        'ISRO propulsion reusable launch vehicle latest', 'India Earth observation climate satellite latest',
        'Indian private space startup latest funding contract', 'ISRO criticism accountability delay anomaly latest',
        'ISRO outreach exhibition student education latest', 'India space technology transfer supply chain latest',
    ],
}

OFFICIAL_DOMAINS = (
    "isro.gov.in", "dos.gov.in", "pib.gov.in", "inspace.gov.in", "inspace.gov.in",
    "nsilindia.co.in", "nsilindia.com",
)

PRIVATE_SPACE_TERMS = [
    "space startup", "space start-up", "space-tech", "space tech", "private space",
    "private-sector space", "private sector space", "commercial space company",
    "skyroot", "agnikul", "pixxel", "bellatrix", "dhruva space", "digantara",
    "galaxeye", "satsure", "pier sight", "piersight", "manastu", "etherealx",
    "orbitaid", "take me 2 space", "takeme2space", "xovian", "astrogate",
]

SPACE_RELEVANCE_TERMS = [
    "space mission", "satellite", "launch vehicle", "rocket", "spacecraft", "earth observation",
    "remote sensing", "navigation satellite", "satcom", "space station", "lunar", "moon mission",
    "planetary mission", "solar mission", "space debris", "space traffic", "space policy",
    "space economy", "space technology", "space science", "astronomy", "orbital",
]

# Native feeds are an independent discovery channel. More feeds can be supplied
# through PUBLISHER_RSS_URLS (comma/newline-separated) without code changes.
DEFAULT_PUBLISHER_RSS = [
    "https://www.thehindu.com/sci-tech/science/feeder/default.rss",
    "https://www.thehindu.com/business/Industry/feeder/default.rss",
    "https://www.thehindu.com/news/national/feeder/default.rss",
]

NEGATIVE_HINTS = {
    "failure", "failed", "delay", "delayed", "problem", "issue", "concern", "risk",
    "sabotage", "resign", "attrition", "vacancy", "shortfall", "underutilisation",
    "investigation", "collision", "debris", "setback", "crash", "anomaly", "criticism",
}
HIGH_IMPACT_HINTS = {
    "failure", "failed", "launch", "gaganyaan", "crew safety", "human rating", "budget",
    "policy", "privatisation", "investigation", "sabotage", "collision", "regulation",
    "spectrum", "approval", "contract", "strategic", "national security", "delay",
}
MEDIUM_IMPACT_HINTS = {
    "mission", "satellite", "technology transfer", "startup", "partnership", "cooperation",
    "manufacturing", "roadmap", "timeline", "test", "spaceport", "launch pad", "navic",
}


@dataclass
class Candidate:
    url: str
    search_title: str = ""
    search_source: str = ""
    search_published: str = ""
    pass_no: int = 0
    query: str = ""


@dataclass
class Article:
    canonical_url: str
    publisher: str
    title: str
    author: str
    published_date: str  # YYYY-MM-DD, publisher/original metadata
    published_time_ist: str  # HH:MM:SS or empty
    published_iso_ist: str  # exact ISO or empty
    time_known: bool
    body_excerpt: str
    institutions: list[str]
    gate_route: str
    support_excerpt: str
    source_pass: int
    discovery_query: str
    concern_ids: list[int]
    tone: str
    impact_level: str
    impact_text: str
    section: str = "main"
    remarks_action: str = "Nil action."
    official_url: str = ""
    official_title: str = ""
    official_excerpt: str = ""
    official_match: bool = False
    official_possible_difference: bool = False
    crux: str = ""
    analysis_source: str = "local-rules"
    catch_up: bool = False

    @property
    def article_key(self):
        material = self.publisher.lower().strip() + "|" + self.canonical_url
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def env(name, default=""):
    return str(os.getenv(name, default) or default).strip()


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except Exception:
        return int(default)


def env_bool(name, default=False):
    return env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on", "y"}


def http_get(url, *, params=None, timeout=30, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=timeout, allow_redirects=True)
            if r.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1.0 + attempt)
                continue
            return r
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"GET failed for {url}: {last}")


def canonicalize_url(url):
    value = html.unescape(str(url or "")).strip()
    if not value:
        return ""
    try:
        p = urlparse(value)
        qs = parse_qs(p.query)
        if "bing.com" in p.netloc:
            for key in ("url", "u", "r"):
                target = unquote((qs.get(key) or [""])[0])
                if target.startswith("http"):
                    value = target
                    p = urlparse(value)
                    break
        if "news.google.com" in p.netloc:
            return value  # resolved later by GET; do not fabricate publisher URL
        kept = []
        for k, vals in parse_qs(p.query, keep_blank_values=True).items():
            lk = k.lower()
            if lk.startswith("utm_") or lk in {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "source"}:
                continue
            for v in vals:
                kept.append((k, v))
        query = "&".join(
            f"{requests.utils.quote(str(k))}={requests.utils.quote(str(v))}" for k, v in kept
        )
        clean = urlunparse((p.scheme or "https", p.netloc.lower(), p.path, "", query, ""))
        return clean.rstrip("/")
    except Exception:
        return value


def parse_rfc_date(value):
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(IST)
    except Exception:
        return None


def google_news_rss(query, limit=12):
    r = http_get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
        timeout=25,
        retries=1,
    )
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.findall(".//item")[:limit]:
        source = item.find("source")
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "source": (source.text.strip() if source is not None and source.text else "Google News"),
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return out


def bing_news_rss(query, limit=10):
    r = http_get(
        "https://www.bing.com/news/search",
        params={"q": query, "format": "RSS", "qft": 'sortbydate="1"'},
        timeout=25,
        retries=1,
    )
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.findall(".//item")[:limit]:
        source = "Bing News"
        for node in list(item):
            if str(node.tag).lower().endswith("source") and node.text:
                source = node.text.strip(); break
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": canonicalize_url(item.findtext("link") or ""),
            "source": source,
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return out


def gdelt_news(query, maxrecords=20, timespan="7d"):
    r = http_get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query, "mode": "artlist", "maxrecords": str(maxrecords),
            "timespan": timespan, "sort": "datedesc", "format": "json",
        },
        timeout=35,
        retries=1,
    )
    if r.status_code != 200:
        return []
    try:
        payload = r.json()
    except Exception:
        return []
    return [{
        "title": str(a.get("title") or "").strip(),
        "url": str(a.get("url") or "").strip(),
        "source": str(a.get("domain") or "GDELT").strip(),
        "published": str(a.get("seendate") or "").strip(),
    } for a in payload.get("articles", []) or []]


def bing_web_rss(query, limit=20):
    """Bing web-search RSS; independent of Bing News RSS."""
    r = http_get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss"},
        timeout=25,
        retries=1,
    )
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.findall(".//item")[:limit]:
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": canonicalize_url(item.findtext("link") or ""),
            "source": "Bing Web RSS",
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return out


def publisher_native_rss(url, limit=80):
    """Fetch a publisher-native RSS/Atom feed as a discovery channel."""
    r = http_get(url, timeout=25, retries=1)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    try:
        root = ET.fromstring(r.content)
    except Exception as exc:
        raise RuntimeError(f"invalid XML: {exc}") from exc
    out = []
    # RSS 2.0
    for item in root.findall(".//item")[:limit]:
        link = (item.findtext("link") or "").strip()
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": canonicalize_url(link),
            "source": urlparse(url).netloc,
            "published": (item.findtext("pubDate") or "").strip(),
        })
    if out:
        return out
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns)[:limit]:
        link = ""
        for node in entry.findall("a:link", ns):
            href = node.get("href") or ""
            if href and (node.get("rel") in {None, "alternate"}):
                link = href
                break
        out.append({
            "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "url": canonicalize_url(link),
            "source": urlparse(url).netloc,
            "published": (entry.findtext("a:published", default="", namespaces=ns) or entry.findtext("a:updated", default="", namespaces=ns) or "").strip(),
        })
    return out


def _configured_publisher_feeds():
    raw = env("PUBLISHER_RSS_URLS")
    extra = [x.strip() for x in re.split(r"[,;\n]+", raw) if x.strip()]
    return list(dict.fromkeys(DEFAULT_PUBLISHER_RSS + extra))


def _candidate_relevance_hint(item):
    text = f"{item.get('title','')} {item.get('source','')}".lower()
    inst = any(re.search(p, text, flags=re.I) for pats in INSTITUTION_PATTERNS.values() for p in pats)
    private = any(t in text for t in PRIVATE_SPACE_TERMS)
    mission = any(t.lower() in text for t in MISSION_TERMS)
    space = any(t in text for t in SPACE_RELEVANCE_TERMS)
    return inst or private or mission or space


def discover_candidates(edition, search_start_ist, report_end_ist):
    """V8 sweep 1: fresh broad discovery for the requested edition.

    Search first, filter second. Every invocation re-runs all five passes and all
    44 theme checks. Search/feed timestamps are only discovery hints; original
    publisher metadata controls final eligibility.
    """
    candidates = {}
    diagnostics = {
        "fresh_scan": True,
        "sweep": 1,
        "scan_started_ist": datetime.now(IST).isoformat(),
        "passes": {},
        "theme_checks": 0,
        "queries": 0,
        "errors": [],
        "channels": {},
        "discovery_window_start": search_start_ist.isoformat(),
        "discovery_window_end": report_end_ist.isoformat(),
    }

    def channel_record(name, ok, hits=0, detail=""):
        rec = diagnostics["channels"].setdefault(name, {"attempts": 0, "successes": 0, "hits": 0, "errors": []})
        rec["attempts"] += 1
        if ok:
            rec["successes"] += 1
            rec["hits"] += int(hits or 0)
        elif detail:
            rec["errors"].append(str(detail)[:280])

    def add_items(items, pass_no, query, channel):
        before = len(candidates)
        for item in items or []:
            url = canonicalize_url(item.get("url"))
            if not url:
                continue
            if url not in candidates:
                candidates[url] = Candidate(
                    url=url,
                    search_title=item.get("title", ""),
                    search_source=item.get("source", channel),
                    search_published=item.get("published", ""),
                    pass_no=pass_no,
                    query=query,
                )
        return len(candidates) - before

    date_hint = search_start_ist.strftime("%Y-%m-%d")
    end_hint = (report_end_ist.date() + timedelta(days=1)).isoformat()
    window_days = max(2, (report_end_ist.date() - search_start_ist.date()).days + 2)

    # Five distinct passes.
    for pass_no in range(1, 6):
        added_before = len(candidates)
        submitted = 0
        for q in PASS_QUERIES[pass_no]:
            submitted += 1
            diagnostics["queries"] += 1
            full_q = f"{q} after:{date_hint} before:{end_hint}"
            source_calls = [
                ("Google News RSS", lambda q=full_q: google_news_rss(q, limit=35)),
                ("Bing News RSS", lambda q=full_q: bing_news_rss(q, limit=30)),
                ("Bing Web RSS", lambda q=full_q: bing_web_rss(q, limit=25)),
                ("GDELT", lambda q=q: gdelt_news(q, maxrecords=35, timespan=f"{window_days}d")),
            ]
            for channel, fn in source_calls:
                try:
                    items = fn()
                    channel_record(channel, True, len(items))
                    add_items(items, pass_no, q, channel)
                except Exception as exc:
                    msg = f"pass {pass_no} {channel} query {q}: {type(exc).__name__}: {exc}"
                    diagnostics["errors"].append(msg)
                    channel_record(channel, False, detail=msg)
        diagnostics["passes"][str(pass_no)] = {
            "queries_required": len(PASS_QUERIES[pass_no]),
            "queries_submitted": submitted,
            "candidates_added": len(candidates) - added_before,
            "cumulative_candidates": len(candidates),
        }

    # 44-theme coverage matrix checks. These are discovery prompts only and do
    # not require every theme to produce a report row.
    for idx, theme in enumerate(THEMES, 1):
        diagnostics["theme_checks"] += 1
        diagnostics["queries"] += 1
        q = f'(ISRO OR "Department of Space" OR "IN-SPACe" OR NSIL OR "Indian space startup") "{theme}"'
        for channel, fn in [
            ("Google News RSS", lambda q=q: google_news_rss(q, limit=12)),
            ("Bing News RSS", lambda q=q: bing_news_rss(q, limit=10)),
        ]:
            try:
                items = fn()
                channel_record(channel, True, len(items))
                add_items(items, 5, f"Theme {idx}: {theme}", channel)
            except Exception as exc:
                msg = f"theme {idx} {channel}: {type(exc).__name__}: {exc}"
                diagnostics["errors"].append(msg)
                channel_record(channel, False, detail=msg)

    # Publisher-native RSS/Atom is attempted independently, not inferred from
    # aggregator results. Filter only by a broad discovery hint here; substantive
    # screening happens after fetching the publisher article.
    for feed in _configured_publisher_feeds():
        try:
            items = publisher_native_rss(feed, limit=100)
            channel_record("Publisher-native RSS/Atom", True, len(items))
            add_items([i for i in items if _candidate_relevance_hint(i)], 4, f"Publisher RSS: {feed}", "Publisher-native RSS/Atom")
        except Exception as exc:
            msg = f"publisher RSS {feed}: {type(exc).__name__}: {exc}"
            diagnostics["errors"].append(msg)
            channel_record("Publisher-native RSS/Atom", False, detail=msg)

    diagnostics["candidate_count"] = len(candidates)
    diagnostics["scan_finished_ist"] = datetime.now(IST).isoformat()
    return list(candidates.values()), diagnostics


def gap_reconciliation_candidates(articles, search_start_ist, report_end_ist, max_events=30):
    """V8 sweep 2: re-search discovered substantive events for missed publishers."""
    candidates = {}
    diag = {"sweep": 2, "queries": 0, "events_researched": 0, "errors": [], "channels": {}}

    def add(items, query):
        for item in items or []:
            url = canonicalize_url(item.get("url"))
            if url and url not in candidates:
                candidates[url] = Candidate(
                    url=url,
                    search_title=item.get("title", ""), search_source=item.get("source", ""),
                    search_published=item.get("published", ""), pass_no=5, query=f"Gap reconciliation: {query}",
                )

    # Use distinct title/event probes. Strip publisher suffix and keep the first
    # informative words; add institution/startup context to encourage multi-publisher results.
    seen = set()
    for a in articles[:max_events]:
        base = re.sub(r"\s+-\s+[^-]{2,80}$", "", a.title).strip()
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]+", base) if len(w) > 2][:12]
        if not words:
            continue
        probe = " ".join(words)
        key = probe.lower()
        if key in seen:
            continue
        seen.add(key)
        diag["events_researched"] += 1
        for q in [f'"{probe}"', f'{probe} ISRO India space']:
            diag["queries"] += 1
            for channel, fn in [
                ("Google News RSS", lambda q=q: google_news_rss(q, limit=20)),
                ("Bing News RSS", lambda q=q: bing_news_rss(q, limit=20)),
                ("Bing Web RSS", lambda q=q: bing_web_rss(q, limit=15)),
            ]:
                rec = diag["channels"].setdefault(channel, {"attempts": 0, "successes": 0, "hits": 0})
                rec["attempts"] += 1
                try:
                    items = fn(); rec["successes"] += 1; rec["hits"] += len(items)
                    add(items, q)
                except Exception as exc:
                    diag["errors"].append(f"{channel} {q}: {type(exc).__name__}: {exc}")
    diag["candidate_count"] = len(candidates)
    return list(candidates.values()), diag


def _first_meta(soup, specs):
    for attrs in specs:
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            return str(node.get("content")).strip()
    return ""


def _jsonld_objects(soup):
    objects = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text("", strip=True)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                objects.append(x)
                if "@graph" in x and isinstance(x["@graph"], list):
                    stack.extend(x["@graph"])
            elif isinstance(x, list):
                stack.extend(x)
    return objects


def _parse_published(raw):
    """Return (date_iso, exact_datetime_ist_or_None)."""
    s = str(raw or "").strip()
    if not s:
        return "", None
    # Date-only is intentionally preserved as time unknown.
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s[:10]) if len(s) == 10 else None
    if m:
        return s, None
    # ISO / RFC-ish formats.
    candidates = [s, s.replace("Z", "+00:00")]
    for value in candidates:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                # Time without reliable timezone is not acceptable as exact IST.
                return dt.date().isoformat(), None
            return dt.astimezone(IST).date().isoformat(), dt.astimezone(IST)
        except Exception:
            pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            return dt.date().isoformat(), None
        dt = dt.astimezone(IST)
        return dt.date().isoformat(), dt
    except Exception:
        pass
    # Common human-readable date only.
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date().isoformat(), None
        except Exception:
            pass
    return "", None


def _norm_title(value):
    value = html.unescape(str(value or "")).lower()
    value = re.sub(r"\s+-\s+[^-]{2,80}$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def resolve_google_candidate(candidate: Candidate):
    """Resolve a Google News candidate to an original publisher URL.

    First try the ordinary redirect. If Google keeps the aggregator URL, perform
    a fresh exact-title Bing News lookup and select the closest title match with
    a non-aggregator URL. This is discovery only; verify_article still fetches
    and validates the original publisher page.
    """
    direct = resolve_google_news_url(candidate.url)
    if direct:
        return direct
    title = re.sub(r"\s+-\s+[^-]{2,80}$", "", candidate.search_title or "").strip()
    if not title:
        return ""
    try:
        results = bing_news_rss(f'"{title}"', limit=12)
    except Exception:
        return ""
    wanted = set(_norm_title(title).split())
    best = (0.0, "")
    for item in results:
        u = canonicalize_url(item.get("url"))
        if not u or any(h in urlparse(u).netloc for h in ["news.google.com", "bing.com"]):
            continue
        got = set(_norm_title(item.get("title")).split())
        score = len(wanted & got) / max(1, len(wanted | got))
        if score > best[0]:
            best = (score, u)
    return best[1] if best[0] >= 0.55 else ""


def resolve_google_news_url(url):
    # Google News article URLs often don't expose publisher URL via a simple
    # redirect. Fetch once and accept only if final host is no longer Google.
    try:
        r = http_get(url, timeout=20, retries=0)
        final = canonicalize_url(r.url)
        if final and "news.google.com" not in urlparse(final).netloc:
            return final
    except Exception:
        pass
    return ""


def _host_is_official(url):
    host = urlparse(url).netloc.lower().replace("www.", "")
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def _is_private_space_story(title, body):
    text = f"{title} {body[:2600]}".lower()
    private = any(term in text for term in PRIVATE_SPACE_TERMS)
    india = any(x in text for x in ["india", "indian", "bengaluru", "hyderabad", "chennai", "ahmedabad", "sriharikota"])
    commercial = any(x in text for x in ["startup", "start-up", "company", "funding", "investment", "contract", "manufacturing", "commercial", "private"])
    return private and (india or commercial)


def _is_private_space_headline(title):
    text = str(title or "").lower()
    return any(term in text for term in PRIVATE_SPACE_TERMS) or ("startup" in text and "space" in text) or ("start-up" in text and "space" in text)


def _is_broader_space_story(title, body):
    text = f"{title} {body[:2200]}".lower()
    return any(term in text for term in SPACE_RELEVANCE_TERMS)


def _is_recruitment_or_tender_only(title, body):
    t = title.lower()
    b = body[:1600].lower()
    recruitment = any(x in t for x in ["recruitment", "vacancy", "apply online", "jobs", "admit card", "results 2026"])
    tender = any(x in t for x in ["tender", "e-procurement", "request for proposal", "rfp"])
    substantive = any(x in b for x in ["mission", "technology", "programme", "program", "policy", "launch", "satellite", "research", "space sector"])
    return (recruitment or tender) and not substantive


def verify_article(candidate: Candidate):
    url = candidate.url
    if "news.google.com" in urlparse(url).netloc:
        url = resolve_google_candidate(candidate)
        if not url:
            return None, "google-news-url-not-resolved-after-title-fallback"

    try:
        r = http_get(url, timeout=28, retries=1)
    except Exception as exc:
        return None, f"fetch-error:{type(exc).__name__}"
    if r.status_code != 200 or not r.text:
        return None, f"http-{r.status_code}"

    final_url = canonicalize_url(r.url)
    if not final_url or any(h in urlparse(final_url).netloc for h in ["news.google.com", "bing.com"]):
        return None, "non-publisher-final-url"

    # Official sources are corroboration/discovery evidence, never independent
    # media rows under V8.
    if _host_is_official(final_url):
        return None, "official-primary-source-excluded"

    soup = BeautifulSoup(r.text, "html.parser")
    jsonld_objects = _jsonld_objects(soup)

    canonical_node = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
    canonical = canonicalize_url(urljoin(final_url, canonical_node.get("href"))) if canonical_node and canonical_node.get("href") else final_url
    if _host_is_official(canonical):
        return None, "official-primary-source-excluded"

    title = _first_meta(soup, [
        {"property": "og:title"}, {"name": "twitter:title"}, {"name": "title"}
    ])
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", html.unescape(title or candidate.search_title)).strip()
    if not title:
        return None, "no-title"

    publisher = _first_meta(soup, [{"property": "og:site_name"}, {"name": "application-name"}])
    if not publisher:
        publisher = urlparse(canonical).netloc.lower().replace("www.", "")

    author = _first_meta(soup, [
        {"name": "author"}, {"property": "article:author"}, {"name": "byl"},
    ])

    published_raw = _first_meta(soup, [
        {"property": "article:published_time"}, {"name": "article:published_time"},
        {"name": "datePublished"}, {"itemprop": "datePublished"},
        {"name": "pubdate"}, {"name": "publish-date"}, {"name": "publish_date"},
        {"name": "date"}, {"name": "parsely-pub-date"}, {"name": "sailthru.date"},
        {"property": "og:published_time"}, {"property": "datePublished"},
    ])
    if not published_raw:
        time_node = soup.find("time", attrs={"datetime": True})
        if time_node and time_node.get("datetime"):
            published_raw = str(time_node.get("datetime")).strip()

    for obj in jsonld_objects:
        typ = str(obj.get("@type") or "").lower()
        if typ in {"newsarticle", "article", "reportagenewsarticle", "analysisnewsarticle"} or "article" in typ:
            if not published_raw:
                published_raw = str(obj.get("datePublished") or "").strip()
            if not author:
                a = obj.get("author")
                if isinstance(a, dict):
                    author = str(a.get("name") or "").strip()
                elif isinstance(a, list):
                    names = [str(item.get("name") or "").strip() for item in a if isinstance(item, dict) and item.get("name")]
                    author = ", ".join(x for x in names if x)
            if publisher == urlparse(canonical).netloc.lower().replace("www.", ""):
                pobj = obj.get("publisher")
                if isinstance(pobj, dict) and pobj.get("name"):
                    publisher = str(pobj.get("name")).strip()
            break

    pub_date, pub_dt = _parse_published(published_raw)
    if not pub_date:
        return None, "original-first-publication-unverified"

    for bad in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
        bad.decompose()

    paragraphs = []
    for node in soup.find_all("p"):
        txt = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if len(txt) >= 35:
            paragraphs.append(txt)
        if sum(len(x) for x in paragraphs) > 15000:
            break
    body = " ".join(paragraphs)
    if len(body) < 80:
        return None, "insufficient-article-body"
    if _is_recruitment_or_tender_only(title, body):
        return None, "recruitment-or-tender-only"

    evidence_text = f"{title} {body}".strip()
    lower = evidence_text.lower()

    institutions = []
    for inst, pats in INSTITUTION_PATTERNS.items():
        if any(re.search(p, lower, flags=re.I) for p in pats):
            institutions.append(inst)

    matched_route = ""
    if institutions:
        matched_route = "INSTITUTION"
    else:
        mission_matches = [m for m in MISSION_TERMS if m.lower() in lower]
        if mission_matches and any(k in lower for k in ["india's space agency", "indian space agency", "space department"]):
            institutions.append("ISRO")
            matched_route = "MISSION_OWNERSHIP"
        elif _is_private_space_story(title, body):
            matched_route = "PRIVATE_SPACE"
        elif _is_broader_space_story(title, body):
            # Retained only as a potential V8 Relevant Space News item; it is not
            # eligible for the strict main in-window table unless institution/private criteria apply.
            matched_route = "BROADER_SPACE"
        else:
            return None, "content-relevance-gate-fail"

    # Guard against a single incidental institution mention when the article is
    # otherwise unrelated. Headline relevance, repeated institutional mentions,
    # mission ownership or a private-space route is sufficient.
    if matched_route == "INSTITUTION":
        headline_has_inst = any(any(re.search(p, title.lower(), flags=re.I) for p in pats) for pats in INSTITUTION_PATTERNS.values())
        total_mentions = sum(len(re.findall(p, lower, flags=re.I)) for pats in INSTITUTION_PATTERNS.values() for p in pats)
        mission_present = any(m.lower() in lower for m in MISSION_TERMS)
        if not headline_has_inst and total_mentions < 2 and not mission_present:
            return None, "incidental-institution-mention"

    support = ""
    for sentence in re.split(r"(?<=[.!?])\s+", evidence_text):
        sl = sentence.lower()
        if any(any(re.search(p, sl, flags=re.I) for p in pats) for pats in INSTITUTION_PATTERNS.values()) or any(t in sl for t in PRIVATE_SPACE_TERMS):
            support = sentence[:420].strip()
            break
    if not support:
        support = title[:420]

    theme_ids = []
    for i, theme in enumerate(THEMES, 1):
        terms = [x for x in re.findall(r"[a-z0-9-]{4,}", theme.lower()) if x not in {"space", "india", "isro"}]
        if terms and sum(1 for t in terms if t in lower) >= min(2, len(terms)):
            theme_ids.append(i)

    # Deterministic fallback first; optional Groq/OpenRouter/Cloudflare semantic
    # analysis can improve meaning-based tone/impact without becoming a dependency.
    tone = classify_tone(title, body[:1800], institutions)
    impact_level, impact_text = classify_impact(title + " " + body[:2000], institutions, theme_ids)
    semantic, analysis_source = semantic_article_analysis(
        title=title, body=body[:5600], publisher=publisher, institutions=institutions
    )
    crux = ""
    if semantic:
        tone = semantic["tone"]
        impact_level = semantic["impact_level"]
        impact_text = semantic["impact_analysis"]
        crux = semantic.get("crux", "")

    headline_institution = any(any(re.search(p, title.lower(), flags=re.I) for p in pats) for pats in INSTITUTION_PATTERNS.values())
    if matched_route == "PRIVATE_SPACE":
        section = "startup"
    elif headline_institution:
        section = "main"
    elif _is_private_space_headline(title):
        section = "startup"
    else:
        section = "main"

    return Article(
        canonical_url=canonical,
        publisher=re.sub(r"\s+", " ", publisher).strip() or "Publisher not stated",
        title=title,
        author=re.sub(r"\s+", " ", author).strip() or "Not stated",
        published_date=pub_date,
        published_time_ist=(pub_dt.strftime("%H:%M:%S") if pub_dt else ""),
        published_iso_ist=(pub_dt.isoformat() if pub_dt else ""),
        time_known=bool(pub_dt),
        body_excerpt=body[:2600],
        institutions=sorted(set(institutions)),
        gate_route=matched_route,
        support_excerpt=support,
        source_pass=candidate.pass_no,
        discovery_query=candidate.query,
        concern_ids=theme_ids,
        tone=tone,
        impact_level=impact_level,
        impact_text=impact_text,
        section=section,
        crux=crux,
        analysis_source=analysis_source,
    ), "ok"


def _truthy_env(name, default="1"):
    return env(name, default).lower() not in {"0", "false", "no", "off", "disabled"}


def _extract_json_object(text):
    """Extract one JSON object from a model response without trusting markdown fences."""
    text = str(text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _validate_semantic_result(obj):
    if not isinstance(obj, dict):
        return None
    tone_raw = re.sub(r"\s+", " ", str(obj.get("tone", "")).strip())
    tone_map = {
        "positive": "Positive",
        "neutral": "Neutral-Mixed",
        "neutral-mixed": "Neutral-Mixed",
        "neutral mixed": "Neutral-Mixed",
        "negative": "Critical-Negative",
        "critical-negative": "Critical-Negative",
        "critical negative": "Critical-Negative",
    }
    tone = tone_map.get(tone_raw.lower())
    impact = str(obj.get("impact_level", "")).strip().title()
    crux = re.sub(r"\s+", " ", str(obj.get("crux", "")).strip())
    analysis = re.sub(r"\s+", " ", str(obj.get("impact_analysis", "")).strip())
    if tone not in {"Positive", "Neutral-Mixed", "Critical-Negative"}:
        return None
    if impact not in {"High", "Medium", "Low"}:
        return None
    if not crux or not analysis:
        return None
    if len(crux) > 360 or len(analysis) > 620:
        return None
    # Keep the reader-facing analysis concise: at most two sentences. We do not
    # rewrite model content here; overlong responses are rejected for fallback.
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", analysis) if x.strip()]
    if len(sentences) > 2:
        return None
    return {"tone": tone, "impact_level": impact, "crux": crux, "impact_analysis": analysis}


def _semantic_prompt(title, body, publisher, institutions):
    inst = ", ".join(institutions or []) or "ISRO / Department of Space / IN-SPACe / NSIL / Indian private-space ecosystem"
    article_text = re.sub(r"\s+", " ", str(body or "")).strip()[:5600]
    return f"""You are preparing a Version 8.0 DoS/ISRO media intelligence report.

Analyse the ARTICLE by meaning and consequence, never by isolated sentiment words.
Perspective: ISRO, Department of Space (DoS), IN-SPACe, NSIL and, where relevant, India's private-space ecosystem. Detected context: {inst}.

STRICT OUTPUT RULES
1. CRUX: one short plain-English sentence stating what actually happened. No invented facts.
2. TONE must be exactly one of:
   - Positive: favourable/supportive reporting, achievement/success, or clearly beneficial development.
   - Neutral-Mixed: factual/balanced/mixed reporting without a clearly derogatory stance.
   - Critical-Negative: derogatory, accusatory, strongly critical or reputation-damaging coverage of ISRO/DoS/IN-SPACe/NSIL, or coverage materially questioning competence, integrity, governance, performance or decisions.
   A delay, anomaly, accident, failure, risk or investigation is NOT automatically Critical-Negative.
3. IMPACT LEVEL is separate from tone:
   - High: major direct consequence for an important mission/programme, crew/safety, strategic capability, major resources/budget, or institutional functioning.
   - Medium: meaningful indirect or medium-term effect on technology, suppliers, private-space ecosystem, partnership, regulation, commercial activity or planning.
   - Low: little identifiable consequence for current activities.
   Never make High from a single word such as launch, contract, policy, approval, delay or strategic.
4. IMPACT ANALYSIS: one or two short, concrete, simple-English sentences explaining institutional, policy, mission, commercial or reputational significance from the ISRO/DoS perspective. Avoid generic phrases. If little effect is evident, say so plainly.
5. Use only information supported by the article. Cautious immediate implications are allowed; speculation is not.

Return ONLY valid JSON in exactly this shape:
{{"crux":"...","tone":"Positive|Neutral-Mixed|Critical-Negative","impact_level":"High|Medium|Low","impact_analysis":"..."}}

PUBLISHER: {publisher}
TITLE: {title}
ARTICLE: {article_text}
"""


def _openai_compatible_chat(url, api_key, model, prompt, extra_headers=None, timeout=45):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return concise valid JSON only. Follow the user's classification definitions exactly."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 420,
    }
    r = SESSION.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
    return _validate_semantic_result(_extract_json_object(content))


def _analyse_with_groq(prompt):
    key = env("GROQ_API_KEY")
    if not key:
        return None
    model = env("GROQ_MODEL", "openai/gpt-oss-20b")
    return _openai_compatible_chat(
        "https://api.groq.com/openai/v1/chat/completions", key, model, prompt
    )


def _analyse_with_openrouter(prompt):
    key = env("OPENROUTER_API_KEY")
    if not key:
        return None
    # openrouter/free guarantees a zero-priced route; no paid model ID is used.
    model = env("OPENROUTER_MODEL", "openrouter/free")
    headers = {
        "HTTP-Referer": env("OPENROUTER_SITE_URL", "https://github.com/"),
        "X-Title": env("OPENROUTER_APP_NAME", "DoS-ISRO Media Intelligence"),
    }
    return _openai_compatible_chat(
        "https://openrouter.ai/api/v1/chat/completions", key, model, prompt,
        extra_headers=headers, timeout=60,
    )


def _analyse_with_cloudflare(prompt):
    token = env("CLOUDFLARE_API_TOKEN")
    account = env("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        return None
    model = env("CLOUDFLARE_AI_MODEL", "@cf/google/gemma-4-26b-a4b-it")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    payload = {
        "messages": [
            {"role": "system", "content": "Return concise valid JSON only. Follow the classification definitions exactly."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 420,
    }
    r = SESSION.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    result = data.get("result") or {}
    content = result.get("response") or result.get("text") or ""
    if not content and isinstance(result.get("choices"), list):
        content = (((result.get("choices") or [{}])[0].get("message") or {}).get("content"))
    return _validate_semantic_result(_extract_json_object(content))


# Per-run provider circuit breaker and article cache. A rate-limited free provider
# must not be retried for every article in the same scheduled edition.
_SEMANTIC_DISABLED_PROVIDERS = set()
_SEMANTIC_RESULT_CACHE = {}


def _is_provider_quota_error(exc):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 402, 403, 404, 429) or (isinstance(status, int) and status >= 500)


def semantic_article_analysis(title, body, publisher, institutions):
    """Try free hosted open-model providers in order, then return None for local fallback.

    Provider failures are deliberately non-fatal. This function never prevents a report
    from being generated. Set ENABLE_FREE_LLM_ANALYSIS=0 to disable all remote analysis.
    """
    if not _truthy_env("ENABLE_FREE_LLM_ANALYSIS", "1"):
        return None, "local-rules-disabled-remote"
    cache_key = hashlib.sha256(json.dumps(
        [title, body, publisher, institutions], ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if cache_key in _SEMANTIC_RESULT_CACHE:
        return _SEMANTIC_RESULT_CACHE[cache_key]
    prompt = _semantic_prompt(title, body, publisher, institutions)
    providers = [
        ("groq-free", _analyse_with_groq),
        ("openrouter-free", _analyse_with_openrouter),
        ("cloudflare-workers-ai-free", _analyse_with_cloudflare),
    ]
    for name, fn in providers:
        if name in _SEMANTIC_DISABLED_PROVIDERS:
            continue
        try:
            result = fn(prompt)
            if result:
                print(f"[semantic-analysis] provider={name} title={title[:90]!r}")
                _SEMANTIC_RESULT_CACHE[cache_key] = (result, name)
                return result, name
        except Exception as exc:
            if _is_provider_quota_error(exc):
                _SEMANTIC_DISABLED_PROVIDERS.add(name)
                print(f"[semantic-analysis] {name} disabled for remainder of run: HTTP quota/auth/service error")
            else:
                print(f"[semantic-analysis] {name} unavailable: {type(exc).__name__}: {str(exc)[:140]}")
    _SEMANTIC_RESULT_CACHE[cache_key] = (None, "local-rules")
    return None, "local-rules"

def classify_tone(title, body="", institutions=None):
    """Classify media tone from the perspective of ISRO/DoS/IN-SPACe/NSIL.

    This is intentionally *not* generic word sentiment.  The label answers:
    "How is the covered development portraying or affecting the standing of the
    four institutions?"

    Negative is reserved for coverage that is derogatory, accusatory,
    disparaging, or materially questions competence, credibility, integrity,
    governance, performance, or decisions of ISRO/DoS/IN-SPACe/NSIL.

    A delay, anomaly, failure, accident, risk, investigation, or other adverse
    event is NOT automatically Negative.  Without a reputational/critical stance
    toward the institution it normally remains Neutral, while operational impact
    is assessed separately by classify_impact().

    Positive covers direct praise/achievement as well as clearly beneficial
    ecosystem developments (for example successful missions, useful partnerships,
    technology transfer, or growth of India's private space ecosystem).
    """
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    body = re.sub(r"\s+", " ", str(body or "")).strip()
    full = (title + ". " + body).strip()
    lower = full.lower()

    # Re-use the same institution vocabulary used by the four-institution gate,
    # so the stance must be tied to ISRO/DoS/IN-SPACe/NSIL or an ISRO centre.
    inst_pattern = "(?:" + "|".join(
        p for patterns in INSTITUTION_PATTERNS.values() for p in patterns
    ) + ")"

    def _sentences(text):
        return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\s*[;|]\s*", text) if x.strip()]

    # Patterns encode *direction of stance*, not just vocabulary.  For example,
    # "ISRO criticised X" does not match the negative passive/object patterns,
    # while "ISRO was criticised" and "criticism of ISRO" do.
    negative_patterns = [
        rf"{inst_pattern}.{{0,55}}\b(?:was|is|has been|had been|were|are)\s+(?:sharply\s+|strongly\s+)?(?:criticis(?:ed|zed)|slammed|accused|blamed|questioned|condemned|mocked|ridiculed)",
        rf"{inst_pattern}.{{0,65}}\b(?:faces?|faced|draws?|drew|attracts?|attracted|came under|is under|remains under)\s+(?:sharp\s+|strong\s+|growing\s+)?(?:criticism|scrutiny|fire|backlash|questions?|accusations?|allegations?)",
        rf"\b(?:criticis(?:ed|zed)|slammed|accused|blamed|questioned|condemned|mocked|ridiculed)\b.{{0,70}}{inst_pattern}",
        rf"\b(?:criticism|accusations?|allegations?|backlash|questions?|charges?)\s+(?:of|against|over|towards?|about)\s+.{{0,45}}{inst_pattern}",
        rf"{inst_pattern}.{{0,70}}\b(?:mismanagement|incompetence|negligence|corruption|opacity|lack of transparency|poor governance|poor handling|mishandling|wastefulness|wasteful spending|unaccountability|serious lapse|major lapse)",
        rf"{inst_pattern}.{{0,50}}\b(?:failed to|failure to)\b.{{0,65}}\b(?:explain|deliver|prevent|address|respond|disclose|manage|meet|protect|maintain)",
        rf"\b(?:audit|report|committee|court|experts?|critics?|opposition|employees?|scientists?)\b.{{0,80}}\b(?:faulted|criticis(?:ed|zed)|blamed|questioned|accused)\b.{{0,55}}{inst_pattern}",
        rf"\b(?:under fire|in the dock|faces backlash)\b.{{0,45}}{inst_pattern}",
    ]

    positive_patterns = [
        rf"{inst_pattern}.{{0,55}}\b(?:was|is|has been|were|are)\s+(?:widely\s+|internationally\s+)?(?:praised|lauded|hailed|commended|applauded|congratulated|recognised|recognized)",
        rf"\b(?:praised|lauded|hailed|commended|applauded|congratulated)\b.{{0,70}}{inst_pattern}",
        rf"{inst_pattern}.{{0,75}}\b(?:successfully|success|achievement|milestone|breakthrough|record|historic|first-of-its-kind|demonstrated|validated|completed|accomplished)\b",
        rf"{inst_pattern}.{{0,60}}\b(?:strengthens?|boosts?|enhances?|expands?|advances?)\b.{{0,70}}\b(?:capability|capacity|mission|technology|cooperation|partnership|ecosystem|self-reliance|leadership)",
    ]

    neg_score = 0
    pos_score = 0

    title_lower = title.lower()
    for pat in negative_patterns:
        if re.search(pat, title_lower, flags=re.I):
            neg_score += 4  # headline stance is especially important
    for pat in positive_patterns:
        if re.search(pat, title_lower, flags=re.I):
            pos_score += 4

    for sentence in _sentences(body[:1600]):
        sl = sentence.lower()
        # Ignore sentences with no institutional anchor for reputational stance.
        if not re.search(inst_pattern, sl, flags=re.I):
            continue
        neg_score += sum(2 for pat in negative_patterns if re.search(pat, sl, flags=re.I))
        pos_score += sum(2 for pat in positive_patterns if re.search(pat, sl, flags=re.I))

    # Clearly beneficial developments can be Positive from ISRO's perspective
    # even when the article is not explicitly praising ISRO.  These are event
    # combinations, not isolated positive words.
    beneficial_event_rules = [
        # Indian private-space ecosystem growth, funding, production or capability.
        (
            ("startup", "start-up", "private space", "private-sector", "private sector", "space-tech", "space tech"),
            ("growth", "expansion", "investment", "funding", "manufacturing", "production", "capacity", "indigenous", "fund raise", "raised"),
        ),
        # Technology transfer / commercialisation of domestic space capability.
        (
            ("technology transfer", "tech transfer", "licensing", "technology licence", "technology license"),
            ("industry", "company", "startup", "commercial", "manufacturing", "production"),
        ),
        # Cooperation or partnership involving one of the institutions.
        (
            ("partnership", "cooperation", "collaboration", "agreement", "memorandum of understanding", "mou"),
            ("joint mission", "technology", "data", "launch", "satellite", "research", "industry", "international"),
        ),
    ]
    institution_present = bool(re.search(inst_pattern, lower, flags=re.I))
    private_ecosystem_story = _is_private_space_story(title, body)
    if institution_present or private_ecosystem_story:
        for left, right in beneficial_event_rules:
            if any(x in lower for x in left) and any(x in lower for x in right):
                pos_score += 2

    # Operationally bad events are deliberately excluded from the negative tone
    # decision unless one of the directed critical/derogatory patterns matched.
    # This keeps "launch delayed by weather" Neutral but "ISRO criticised over
    # repeated delays" Negative.
    if neg_score >= 3 and neg_score > pos_score:
        return "Critical-Negative"
    if pos_score >= 2 and pos_score > neg_score:
        return "Positive"
    return "Neutral-Mixed"


def classify_impact(text, institutions, concern_ids):
    """Assess impact on ISRO/DoS/IN-SPACe/NSIL from likely consequences.

    Impact level is deliberately separate from tone.  A critical article can be
    low operational impact, while a neutral technical event can be high impact.
    No single keyword is allowed to force High.  High requires evidence of a
    direct and material consequence for missions, safety, resources, strategy,
    institutional functioning, or long-duration programme execution.
    """
    lower = re.sub(r"\s+", " ", (text or "").lower()).strip()

    def has(*terms):
        return any(term in lower for term in terms)

    def count_groups(groups):
        return sum(1 for group in groups if any(term in lower for term in group))

    # Direct institutional relevance.  Articles that explicitly name one of the
    # four institutions have more potential impact than general sector stories.
    direct = bool(institutions) or has(
        "isro", "indian space research organisation", "indian space research organization",
        "department of space", "in-space", "in‑space", "in–space", "nsil",
        "newspace india limited",
    )

    # Evidence groups describe consequences, not sentiment words.
    severe_event = has(
        "mission failure", "launch failure", "failed mission", "vehicle failure",
        "serious anomaly", "major anomaly", "crash", "collision", "sabotage",
        "loss of mission", "loss of satellite", "explosion", "fatal", "fatality",
        "crew safety", "human rating failure", "safety breach",
        "programme cancelled", "program cancelled", "mission cancelled",
        "programme suspended", "mission suspended",
    )
    material_delay = has(
        "months delay", "month delay", "six-month", "six month", "year delay",
        "indefinitely postponed", "long delay", "major delay", "schedule slippage",
        "repeated delay", "repeated delays",
    )
    minor_delay = has("delay", "delayed", "postponed", "slipped", "deferred") and not material_delay

    mission_effect = count_groups([
        ("gaganyaan", "chandrayaan", "nisar", "navic", "pslv", "gslv", "lvm3", "sslv", "aditya-l1"),
        ("mission readiness", "launch readiness", "mission schedule", "launch schedule", "critical test", "qualification test"),
        ("launch pad", "spaceport", "ground segment", "tracking network", "propulsion", "flight hardware"),
    ])
    resource_effect = count_groups([
        ("budget cut", "budget reduction", "funding cut", "funding shortfall", "cost overrun", "major allocation", "budget increase"),
        ("staff shortage", "critical vacancy", "mass resignation", "attrition", "workforce shortage"),
        ("major contract", "strategic contract", "large contract", "procurement delay", "supply disruption", "supply chain disruption"),
    ])
    strategic_effect = count_groups([
        ("national security", "strategic capability", "defence", "defense", "military space"),
        ("spectrum allocation", "satcom spectrum", "foreign launcher dependence", "technology denial", "export restriction"),
        ("space policy", "space act", "space legislation", "major regulatory change", "privatisation", "privatization"),
    ])
    institutional_effect = count_groups([
        ("investigation into isro", "investigation into nsil", "investigation into in-space", "official inquiry"),
        ("leadership resignation", "chairman resignation", "governance failure", "institutional crisis"),
        ("operations suspended", "facility shutdown", "centre shutdown", "center shutdown"),
    ])

    # Ecosystem developments are usually meaningful but indirect.  They should
    # normally be Medium, not High, unless paired with a direct severe effect.
    commercial_event = has("contract", "order", "procurement", "tender")

    ecosystem = has(
        "startup", "start-up", "space-tech", "space tech", "private sector",
        "private-sector", "private company", "private companies", "industry participation",
        "commercial space", "technology transfer", "tech transfer", "manufacturing",
        "supplier", "supply base", "partnership", "cooperation", "collaboration",
        "memorandum of understanding", "mou", "funding", "investment",
    )

    # High impact requires either a severe direct event, or multiple material
    # consequence dimensions with direct institutional relevance.  A concern
    # query hit is only supporting evidence; it never forces High by itself.
    consequence_dimensions = (
        (1 if severe_event or material_delay else 0)
        + (1 if mission_effect else 0)
        + (1 if resource_effect else 0)
        + (1 if strategic_effect else 0)
        + (1 if institutional_effect else 0)
    )

    if direct and severe_event:
        level = "High"
    elif direct and material_delay and mission_effect:
        level = "High"
    elif direct and consequence_dimensions >= 2 and (mission_effect or resource_effect or strategic_effect or institutional_effect):
        level = "High"
    elif material_delay or ecosystem or mission_effect or resource_effect or strategic_effect or institutional_effect or concern_ids or (direct and commercial_event):
        level = "Medium"
    else:
        level = "Low"

    # Private-space/start-up ecosystem: describe the concrete ecosystem effect.
    if (has("startup", "start-up", "space-tech", "space tech", "private sector", "private-sector",
            "private company", "private companies", "industry participation", "commercial space")
            and not has("major regulatory change", "space act", "space legislation", "spectrum allocation")):
        if has("funding", "investment", "fund raise", "raises", "venture", "capital"):
            reason = (
                "More funding for Indian space companies can strengthen ISRO's supplier and innovation ecosystem. "
                "It may help private firms take on more technology, manufacturing and service work around ISRO missions."
            )
        elif has("manufacturing", "production", "factory", "supply chain", "supplier"):
            reason = (
                "Greater private manufacturing capacity can broaden ISRO's domestic supply base. "
                "This can support scaling of launch, satellite and mission hardware while ISRO focuses on advanced R&D and missions."
            )
        else:
            reason = (
                "Growth in India's private space sector can strengthen ISRO's innovation and supplier ecosystem. "
                "It may allow industry to take on more commercial and production work while ISRO focuses on advanced missions."
            )

    elif has("technology transfer", "tech transfer", "technology licence", "technology license", "licensing"):
        reason = (
            "This can move ISRO-developed technology into wider industry use and build domestic capability. "
            "It may also reduce ISRO's need to handle routine production itself."
        )

    elif has("contract", "order", "procurement", "tender"):
        reason = (
            "This could affect how ISRO sources hardware or services and how much work industry takes on. "
            "The impact depends on the contract's scale, mission relevance and delivery schedule."
        )

    elif has("partnership", "cooperation", "collaboration", "agreement", "memorandum of understanding", "mou"):
        reason = (
            "The cooperation can give ISRO access to complementary technology, data or mission capability. "
            "It may reduce development burden and support future joint missions."
        )

    elif severe_event or has("failed", "failure", "anomaly", "setback", "crash"):
        reason = (
            "The event may require technical review, additional testing or corrective action before the programme moves ahead. "
            "Its impact depends on whether mission readiness or future schedules are materially affected."
        )
    elif material_delay:
        reason = (
            "The delay could materially affect the programme schedule and dependent activities. "
            "ISRO may need to re-sequence testing, launch slots or follow-on milestones."
        )
    elif minor_delay:
        reason = (
            "The delay may shift the immediate schedule, but the wider impact appears limited unless it affects dependent milestones."
        )
    elif has("test", "trial", "demonstration"):
        reason = (
            "The result is relevant to readiness for the next development or mission milestone. "
            "Its impact depends on whether further testing or design changes are required."
        )
    elif has("launch", "mission", "gaganyaan", "chandrayaan", "aditya", "nisar", "navic", "satellite"):
        reason = (
            "This is relevant to ISRO's mission planning or execution. "
            "The impact depends on whether it changes readiness, resources or the timing of follow-on activities."
        )

    elif has("crew safety", "human rating", "safety", "accident"):
        reason = (
            "This could affect ISRO's safety and mission-assurance work. "
            "A material safety issue may require additional reviews, testing or safeguards."
        )

    elif has("policy", "regulation", "regulatory", "spectrum", "approval", "privatisation", "privatization"):
        reason = (
            "The policy or regulatory development may affect the operating environment around ISRO and India's space sector. "
            "Its significance depends on whether it changes approvals, commercial participation or programme planning."
        )

    elif has("budget", "funding", "allocation", "expenditure"):
        reason = (
            "The funding development may influence the pace of missions, infrastructure or technology programmes. "
            "Its impact depends on the size and duration of the change."
        )

    elif has("vacancy", "vacancies", "attrition", "recruitment", "staff", "workforce", "employee"):
        reason = (
            "This may affect ISRO's programme execution capacity if it creates sustained gaps in specialised teams. "
            "A limited or temporary staffing issue would have a smaller impact."
        )

    elif has("national security", "strategic", "defence", "defense", "military"):
        reason = (
            "This may affect the strategic importance or prioritisation of India's space capabilities. "
            "The impact depends on whether ISRO programmes, infrastructure or services need to change in response."
        )

    elif level == "High":
        reason = (
            "The development appears to have a direct and material consequence for ISRO's programmes, resources or strategic priorities. "
            "It may require significant follow-up or changes to execution."
        )
    elif level == "Medium":
        reason = (
            "The development is relevant to ISRO's planning, ecosystem or external environment, but no major immediate disruption is evident."
        )
    else:
        reason = "The update is relevant to ISRO, but no significant operational or strategic consequence is evident from the report."

    return level, reason


def edition_window(edition, report_date=None):
    """Return strict V8 publication window plus 24-hour discovery lookback start."""
    now = datetime.now(IST)
    rd = report_date or now.date()
    if edition == "morning":
        end = datetime.combine(rd, dtime(8, 0), IST)
        start = datetime.combine(rd - timedelta(days=1), dtime(20, 0), IST)
        search_start = end - timedelta(hours=24)
        return start, end, search_start
    if edition == "evening":
        start = datetime.combine(rd, dtime(8, 0), IST)
        end = datetime.combine(rd, dtime(20, 0), IST)
        search_start = end - timedelta(hours=24)
        return start, end, search_start
    if edition == "weekly":
        # Legacy/manual feature retained from Build 5.19, but V8 automatic weekly
        # scheduling is paused. A manual dispatch is an explicit user action.
        end = datetime.combine(rd, dtime(20, 0), IST)
        if rd.weekday() != 4:
            raise ValueError("Weekly report date must be Friday")
        start = end - timedelta(days=7)
        return start, end, start
    raise ValueError(f"Unknown edition: {edition}")


def article_in_window(article: Article, edition, start, end, search_start, previous_evening_keys=None):
    """V8 first-publication eligibility.

    Search/RSS crawl timestamps never decide eligibility. If the original
    publisher time is unavailable, the candidate is held for verification rather
    than guessed into a boundary window.
    """
    if not article.time_known:
        return False, False, "hold-original-publication-time-unverified"

    dt = datetime.fromisoformat(article.published_iso_ist).astimezone(IST)
    if start <= dt < end:
        # Broader-space-only stories are not main-window DoS/ISRO/private rows.
        if article.gate_route == "BROADER_SPACE":
            return False, False, "broader-space-not-main-window-eligible"
        return True, False, "exact-in-window"

    # V8 Relevant Space News: verified, not in strict main window, within the
    # preceding 24-hour discovery lookback. Archive dedup is applied separately.
    if edition in {"morning", "evening"} and search_start <= dt < end:
        article.section = "relevant"
        return True, True, "relevant-space-lookback"

    return False, False, "exact-outside-window"


def dedupe_article_level(articles):
    """Only identical original publisher article identity is deduplicated."""
    out = {}
    for a in articles:
        # canonical URL is the primary identity; publisher keeps identity explicit.
        key = (a.publisher.lower().strip(), a.canonical_url)
        old = out.get(key)
        if old is None or a.source_pass < old.source_pass:
            out[key] = a
    return list(out.values())


def _read_archive_text(path):
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            doc = Document(str(path))
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    parts.extend(cell.text for cell in row.cells)
            return "\n".join(parts)
        if suffix == ".pdf":
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def load_archive_registry():
    """Load only the most recent configured consolidated archive as V8 registry.

    The archive is never modified automatically. Exact publisher+canonical URL is
    primary. Structured CSV/JSON rows may also supply the V8 fallback identity:
    publisher + normalized headline + verified first-publication date.
    """
    configured = env("CONSOLIDATED_ARCHIVE_FILE")
    path = Path(configured) if configured else None
    if not path or not path.exists():
        candidates = []
        for pattern in ("*consolidated*archive*", "*media*archive*", "*news*archive*"):
            candidates.extend([x for x in Path('.').glob(pattern) if x.is_file()])
        # Do not treat our generated evidence/diagnostic files as archives.
        candidates = [x for x in candidates if "diagnostic" not in x.name.lower() and "evidence" not in x.name.lower()]
        path = max(candidates, key=lambda x: x.stat().st_mtime) if candidates else None
    if not path or not path.exists():
        return {"available": False, "path": "", "urls": set(), "fallback": set(), "error": "latest consolidated archive unavailable"}

    urls = set()
    fallback = set()
    text = _read_archive_text(path)
    for u in re.findall(r"https?://[^\s<>\]\[\)\(\"']+", text):
        cu = canonicalize_url(u.rstrip(".,;"))
        if cu:
            urls.add(cu)

    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                for row in csv.DictReader(f):
                    low = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
                    url = next((low[k] for k in low if "url" in k or "link" in k), "")
                    publisher = next((low[k] for k in low if "publisher" in k or "media house" in k or "source" == k), "")
                    title = next((low[k] for k in low if "title" in k or "description" in k), "")
                    pubdate = next((low[k] for k in low if "published" in k or k == "date"), "")
                    if url:
                        cu = canonicalize_url(url)
                        if cu: urls.add(cu)
                    d, _ = _parse_published(pubdate)
                    if publisher and title and d:
                        fallback.add((publisher.lower().strip(), _norm_title(title), d))
        elif path.suffix.lower() == ".json":
            payload = json.loads(text)
            rows = payload if isinstance(payload, list) else payload.get("articles", []) if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                url = row.get("canonical_url") or row.get("url") or row.get("link") or ""
                publisher = row.get("publisher") or row.get("source") or ""
                title = row.get("title") or row.get("headline") or ""
                pubdate = row.get("published_date") or row.get("date") or row.get("published") or ""
                if url:
                    cu = canonicalize_url(url)
                    if cu: urls.add(cu)
                d, _ = _parse_published(str(pubdate))
                if publisher and title and d:
                    fallback.add((str(publisher).lower().strip(), _norm_title(title), d))
    except Exception as exc:
        return {"available": True, "path": str(path), "urls": urls, "fallback": fallback, "error": f"structured archive parse warning: {exc}"}

    return {"available": True, "path": str(path), "urls": urls, "fallback": fallback, "error": ""}


def archive_match(article, registry):
    if not registry.get("available"):
        return False, "archive-unavailable"
    cu = canonicalize_url(article.canonical_url)
    if cu and cu in registry.get("urls", set()):
        return True, "archive-canonical-url"
    key = (article.publisher.lower().strip(), _norm_title(article.title), article.published_date)
    if key in registry.get("fallback", set()):
        return True, "archive-publisher-title-date"
    return False, "archive-new-article"


def _official_page_extract(url):
    try:
        r = http_get(url, timeout=24, retries=1)
        if r.status_code != 200 or not r.text:
            return None
        final = canonicalize_url(r.url)
        if not _host_is_official(final):
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title = _first_meta(soup, [{"property": "og:title"}, {"name": "title"}])
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        published_raw = _first_meta(soup, [
            {"property": "article:published_time"}, {"name": "datePublished"},
            {"itemprop": "datePublished"}, {"name": "date"},
        ])
        pub_date, _ = _parse_published(published_raw)
        for bad in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
            bad.decompose()
        paras = []
        for node in soup.find_all("p"):
            t = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            if len(t) > 30:
                paras.append(t)
            if sum(map(len, paras)) > 4000:
                break
        return {"url": final, "title": re.sub(r"\s+", " ", title or "").strip(), "date": pub_date, "excerpt": " ".join(paras)[:1800]}
    except Exception:
        return None


def check_official_sources(article):
    """V8 official-source-first comparison, retained in the private audit only."""
    title_words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]+", article.title) if len(w) >= 4]
    stop = {"this", "that", "with", "from", "will", "india", "indian", "space", "news", "says", "report", "latest"}
    probe_words = [w for w in title_words if w.lower() not in stop][:10]
    if not probe_words:
        probe_words = title_words[:8]
    probe = " ".join(probe_words)

    domains = ["isro.gov.in"]
    if "IN-SPACe" in article.institutions or "in-space" in article.body_excerpt.lower():
        domains.append("inspace.gov.in")
    if "NSIL" in article.institutions or "nsil" in article.body_excerpt.lower():
        domains.extend(["nsilindia.co.in", "nsilindia.com"])
    domains.append("pib.gov.in")

    leads = []
    attempts = []
    for domain in dict.fromkeys(domains):
        q = f"site:{domain} {probe}"
        try:
            items = bing_web_rss(q, limit=8)
            attempts.append({"domain": domain, "query": q, "status": "ok", "hits": len(items)})
            for item in items:
                u = canonicalize_url(item.get("url"))
                if u and _host_is_official(u):
                    leads.append(u)
        except Exception as exc:
            attempts.append({"domain": domain, "query": q, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    # Score by title-token overlap after fetching the actual official page.
    wanted = set(_norm_title(article.title).split())
    best = (0.0, None)
    seen = set()
    for u in leads[:6]:
        if u in seen:
            continue
        seen.add(u)
        page = _official_page_extract(u)
        if not page:
            continue
        got = set(_norm_title(page.get("title", "")).split())
        score = len(wanted & got) / max(1, len(wanted | got))
        # Mission-name overlap is a strong match even when wording differs.
        mission_overlap = any(m.lower() in (article.title + " " + article.body_excerpt).lower() and m.lower() in (page.get("title", "") + " " + page.get("excerpt", "")).lower() for m in MISSION_TERMS)
        if mission_overlap:
            score += 0.25
        if score > best[0]:
            best = (score, page)

    page = best[1] if best[0] >= 0.18 else None
    possible_difference = False
    if page:
        media_nums = set(re.findall(r"\b\d[\d.,:/-]*\b", article.title + " " + article.body_excerpt[:1400]))
        official_nums = set(re.findall(r"\b\d[\d.,:/-]*\b", page.get("title", "") + " " + page.get("excerpt", "")))
        # Only flag as a possible difference, never assert an error automatically.
        if media_nums and official_nums and not (media_nums & official_nums) and article.impact_level == "High":
            possible_difference = True

    return {
        "matched": bool(page),
        "score": round(best[0], 3),
        "url": page.get("url", "") if page else "",
        "title": page.get("title", "") if page else "",
        "date": page.get("date", "") if page else "",
        "excerpt": page.get("excerpt", "") if page else "",
        "possible_difference": possible_difference,
        "attempts": attempts,
    }


def assign_remarks_action(article, official):
    """Apply V8 non-directive website-focused action language."""
    text = f"{article.title} {article.body_excerpt[:2200]}".lower()
    article.official_match = bool(official.get("matched"))
    article.official_url = official.get("url", "")
    article.official_title = official.get("title", "")
    article.official_excerpt = official.get("excerpt", "")[:1200]
    article.official_possible_difference = bool(official.get("possible_difference"))

    association_claim = any(x in text for x in [
        "with isro", "isro association", "isro-associated", "isro backed", "isro-backed",
        "isro contract", "isro partnership", "partnered with isro", "working with isro",
    ]) and _is_private_space_story(article.title, article.body_excerpt)
    if association_claim and not article.official_match:
        article.remarks_action = "Suggested action: Any reported ISRO association may be verified before public acknowledgement."
        return

    # Routine reporting already covered by official information requires no repeat circulation/update.
    if article.official_match and not article.official_possible_difference and article.tone != "Critical-Negative":
        article.remarks_action = "Nil action."
        return

    if article.official_possible_difference:
        article.remarks_action = "Suggested action: A factual clarification may be considered with reference to the information on the ISRO website."
        return

    if article.tone == "Critical-Negative":
        if "navic" in text and any(x in text for x in ["service", "status", "availability", "continuity"]):
            article.remarks_action = "Suggested action: The NavIC service-status assertion may be verified. A factual article on the ISRO website may be considered for clarity, if required."
        elif article.impact_level in {"High", "Medium"}:
            article.remarks_action = "Suggested action: The reported assertion may be verified. A clarification may be considered for publication on the ISRO website, subject to approval."
        else:
            article.remarks_action = "Nil action."
        return

    technical = any(x in text for x in ["anomaly", "failure", "failed", "qualification", "test", "engine", "orbit", "separation", "propulsion", "crew", "launch vehicle"])
    if technical and article.impact_level == "High" and not article.official_match:
        article.remarks_action = "Suggested action: The technical details may be verified before any website update is considered."
        return

    significant_new = article.impact_level == "High" and bool(article.institutions) and article.tone == "Positive"
    if significant_new and not article.official_match:
        article.remarks_action = "Suggested action: A suitable update on the ISRO website may be considered."
        return

    article.remarks_action = "Nil action."


def supabase_config():
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_KEY")
    return url, key


def sb_headers(prefer=None):
    url, key = supabase_config()
    if not url or not key:
        return None
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def load_previous_evening_keys(report_date):
    """Return previous calendar day's evening article identities when available."""
    url, _ = supabase_config(); headers = sb_headers()
    if not headers:
        return set(), False
    prev = report_date - timedelta(days=1)
    try:
        r = SESSION.get(
            f"{url}/rest/v1/scheduled_media_report_articles",
            headers=headers,
            params={
                "select": "article_key",
                "edition": "eq.evening",
                "report_date": f"eq.{prev.isoformat()}",
                "limit": "2000",
            },
            timeout=20,
        )
        if r.status_code in {404, 400}:
            return set(), False
        r.raise_for_status()
        return {str(x.get("article_key") or "") for x in r.json() or [] if x.get("article_key")}, True
    except Exception:
        return set(), False


def persist_run(report_key, edition, report_date, start, end, articles, docx_name, pdf_name, email_status, diagnostics):
    url, _ = supabase_config(); headers = sb_headers("resolution=merge-duplicates,return=minimal")
    if not headers:
        return
    run = {
        "report_key": report_key,
        "edition": edition,
        "report_date": report_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": datetime.now(IST).isoformat(),
        "article_count": len(articles),
        "docx_filename": docx_name,
        "pdf_filename": pdf_name,
        "email_status": email_status,
        "diagnostics": diagnostics,
    }
    r = SESSION.post(
        f"{url}/rest/v1/scheduled_media_report_runs",
        headers=headers,
        params={"on_conflict": "report_key"},
        json=[run], timeout=30,
    )
    if r.status_code >= 300:
        print(f"::warning::Could not persist scheduled report run: HTTP {r.status_code} {r.text[:300]}")

    article_payload = []
    for a in articles:
        article_payload.append({
            "report_key": report_key,
            "edition": edition,
            "report_date": report_date.isoformat(),
            "article_key": a.article_key,
            "publisher": a.publisher,
            "canonical_url": a.canonical_url,
            "title": a.title,
            "published_date": a.published_date,
            "published_time_ist": a.published_time_ist or None,
            "catch_up": bool(a.catch_up),
        })
    if article_payload:
        rr = SESSION.post(
            f"{url}/rest/v1/scheduled_media_report_articles",
            headers=headers,
            params={"on_conflict": "report_key,article_key"},
            json=article_payload, timeout=30,
        )
        if rr.status_code >= 300:
            print(f"::warning::Could not persist scheduled article ledger: HTTP {rr.status_code} {rr.text[:300]}")


def already_emailed(report_key):
    if env_bool("FORCE_SCHEDULED_SEND", False):
        return False
    url, _ = supabase_config(); headers = sb_headers()
    if not headers:
        return False
    try:
        r = SESSION.get(
            f"{url}/rest/v1/scheduled_media_report_runs",
            headers=headers,
            params={"select": "email_status", "report_key": f"eq.{report_key}", "limit": "1"},
            timeout=15,
        )
        if r.status_code >= 300:
            return False
        rows = r.json() or []
        return bool(rows and str(rows[0].get("email_status") or "").startswith("sent:"))
    except Exception:
        return False


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd"); tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_table_borders(table, color="404040", size="6"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders"); tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = "w:" + edge
        el = borders.find(qn(tag))
        if el is None:
            el = OxmlElement(tag); borders.append(el)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), size)
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)


def set_fixed_table_layout(table, widths):
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout"); tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")
    grid = table._tbl.tblGrid
    for col, width in zip(grid.gridCol_lst, widths):
        col.set(qn("w:w"), str(width.twips))
    for i, width in enumerate(widths):
        table.columns[i].width = width


def set_cell_width(cell, width):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.first_child_found_in("w:tcW")
    if tc_w is None:
        tc_w = OxmlElement("w:tcW"); tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(width.twips))
    tc_w.set(qn("w:type"), "dxa")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def add_hyperlink(paragraph, text, url):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink"); hyperlink.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts"); fonts.set(qn("w:ascii"), "Arial"); fonts.set(qn("w:hAnsi"), "Arial"); fonts.set(qn("w:eastAsia"), "Arial")
    color = OxmlElement("w:color"); color.set(qn("w:val"), "0563C1")
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single")
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), "24")
    r_pr.append(fonts); r_pr.append(color); r_pr.append(u); r_pr.append(sz); new_run.append(r_pr)
    t = OxmlElement("w:t"); t.text = text; new_run.append(t)
    hyperlink.append(new_run); paragraph._p.append(hyperlink)


def _set_run_font(run, bold=False, size=12, color=None):
    run.font.name = "Arial"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)


def _set_para_compact(paragraph, before=0, after=0, line=1.0):
    pf = paragraph.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line


def _add_section_table(doc, section_title, articles, generated_at, show_empty=False):
    psec = doc.add_paragraph()
    psec.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_para_compact(psec, before=2, after=4)
    _set_run_font(psec.add_run(section_title), bold=True, size=12)

    headers = [
        "No.", "Description / Title of News", "Publisher / author",
        "Publication date / Time", "Tone", "Impact Analysis", "Remarks / Action"
    ]
    table = doc.add_table(rows=1, cols=7)
    table.autofit = False
    # Approximately 273 mm usable width on A4 landscape with ~0.46-inch side margins.
    widths = [Mm(12), Mm(60), Mm(40), Mm(32), Mm(26), Mm(64), Mm(39)]
    set_fixed_table_layout(table, widths)
    set_table_borders(table, "404040", "6")
    for i, cell in enumerate(table.rows[0].cells):
        set_cell_width(cell, widths[i])
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_shading(cell, "D9EAF7")
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_para_compact(para)
        _set_run_font(para.add_run(headers[i]), bold=True, size=12)
    set_repeat_table_header(table.rows[0])

    rows = articles
    if not rows and show_empty:
        row = table.add_row(); prevent_row_split(row)
        values = [
            "1", "No qualifying verified news was found for the reporting period.",
            "Not applicable\nAuthor: Not stated", "Published: Not applicable\nTime: Not stated",
            "Neutral-Mixed", "Low Impact.\nNo qualifying article was verified within the strict publication window.", "Nil action."
        ]
        for i, value in enumerate(values):
            cell = row.cells[i]; set_cell_width(cell, widths[i]); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for j, line in enumerate(value.split("\n")):
                para = cell.paragraphs[0] if j == 0 else cell.add_paragraph()
                _set_para_compact(para)
                _set_run_font(para.add_run(line), size=12)
        return table

    for idx, a in enumerate(rows, 1):
        row = table.add_row(); prevent_row_split(row)
        for i, cell in enumerate(row.cells):
            set_cell_width(cell, widths[i])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for para in cell.paragraphs:
                _set_para_compact(para)

        pno = row.cells[0].paragraphs[0]; pno.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(pno.add_run(str(idx)), size=12)

        ptitle = row.cells[1].paragraphs[0]
        _set_run_font(ptitle.add_run(a.title), bold=False, size=12)
        psrc = row.cells[1].add_paragraph(); _set_para_compact(psrc, before=2, after=0)
        _set_run_font(psrc.add_run("Source: "), size=12)
        add_hyperlink(psrc, "Article", a.canonical_url)

        ppub = row.cells[2].paragraphs[0]
        _set_run_font(ppub.add_run(a.publisher), size=12)
        pauth = row.cells[2].add_paragraph(); _set_para_compact(pauth)
        _set_run_font(pauth.add_run(f"Author: {a.author or 'Not stated'}"), size=12)

        pub_date = date.fromisoformat(a.published_date).strftime("%d %B %Y")
        pdate = row.cells[3].paragraphs[0]
        _set_run_font(pdate.add_run(f"Published: {pub_date}"), size=12)
        ptime = row.cells[3].add_paragraph(); _set_para_compact(ptime)
        if a.time_known:
            time_text = datetime.strptime(a.published_time_ist, "%H:%M:%S").strftime("%I:%M %p") + " IST"
        else:
            time_text = "Not stated"
        _set_run_font(ptime.add_run(f"Time: {time_text}"), size=12)

        ptone = row.cells[4].paragraphs[0]
        _set_run_font(ptone.add_run(a.tone), size=12)

        pimp = row.cells[5].paragraphs[0]
        _set_run_font(pimp.add_run(f"{a.impact_level} Impact."), bold=True, size=12)
        ptxt = row.cells[5].add_paragraph(); _set_para_compact(ptxt, before=1, after=0)
        _set_run_font(ptxt.add_run(a.impact_text), size=12)

        pact = row.cells[6].paragraphs[0]
        _set_run_font(pact.add_run(a.remarks_action or "Nil action."), size=12)

    return table


def build_docx(edition, report_date, start, end, articles, output_path, generated_at=None):
    """Build the mandatory V8 seven-column reader-facing DOCX."""
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    sec.left_margin = Mm(11.7); sec.right_margin = Mm(11.7)
    sec.top_margin = Mm(14.5); sec.bottom_margin = Mm(15.0)

    styles = doc.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    styles["Normal"].font.size = Pt(12)

    edition_label = {"morning": "Morning Edition", "evening": "Evening Edition", "weekly": "Weekly Edition"}[edition]
    p1 = doc.add_paragraph(); p1.alignment = WD_ALIGN_PARAGRAPH.CENTER; _set_para_compact(p1, after=2)
    if edition in {"morning", "evening"}:
        coverage = f"Publication Coverage: {start.strftime('%d %B %Y, %I:%M %p')} to {end.strftime('%d %B %Y, %I:%M %p')} IST"
        reporting_time = "08:00 AM IST" if edition == "morning" else "08:00 PM IST"
        line1 = f"{report_date.strftime('%d %B %Y')} — {edition_label} — Reporting Time: {reporting_time} — {coverage}"
    else:
        line1 = f"{report_date.strftime('%d %B %Y')} — {edition_label} — Publication Coverage: {start.strftime('%d %B %Y, %I:%M %p')} to {end.strftime('%d %B %Y, %I:%M %p')} IST"
    _set_run_font(p1.add_run(line1), size=12)

    p2 = doc.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER; _set_para_compact(p2, after=5)
    _set_run_font(p2.add_run("DoS / ISRO — Daily Major News / Media News and Impact Analysis Report"), bold=True, size=12)

    main = [a for a in articles if a.section == "main"]
    startup = [a for a in articles if a.section == "startup"]
    relevant = [a for a in articles if a.section == "relevant"]

    _add_section_table(doc, "DoS / ISRO Major News", main, generated_at, show_empty=(not main and not startup))

    if startup:
        spacer = doc.add_paragraph(); _set_para_compact(spacer, after=2)
        _add_section_table(doc, "Indian Private Space / Startup News", startup, generated_at, show_empty=False)

    if relevant:
        # Highest-priority V8 pagination rule: explicit Word page break before heading.
        doc.add_page_break()
        _add_section_table(doc, "Relevant Space News", relevant, generated_at, show_empty=False)

    generated = generated_at or datetime.now(IST)
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_para_compact(footer)
    _set_run_font(footer.add_run(f"Report generated: {generated.strftime('%d %B %Y, %I:%M:%S %p')} IST"), size=9)

    doc.save(output_path)


def validate_docx_v8(docx_path, has_relevant=False):
    doc = Document(str(docx_path))
    if not doc.tables:
        raise RuntimeError("V8 DOCX validation failed: no report table")
    for table in doc.tables:
        if len(table.columns) != 7:
            raise RuntimeError(f"V8 DOCX validation failed: expected 7 columns, found {len(table.columns)}")
        hdr = " | ".join(c.text.strip() for c in table.rows[0].cells)
        required = ["No.", "Description / Title of News", "Publisher / author", "Publication date / Time", "Tone", "Impact Analysis", "Remarks / Action"]
        if any(x not in hdr for x in required):
            raise RuntimeError(f"V8 DOCX validation failed: unexpected header row: {hdr}")
    footer_text = " ".join(p.text for sec in doc.sections for p in sec.footer.paragraphs)
    if not re.search(r"Report generated: \d{2} \w+ \d{4}, \d{2}:\d{2}:\d{2} [AP]M IST", footer_text):
        raise RuntimeError("V8 DOCX validation failed: footer does not contain actual generation time with seconds")
    xml = doc._element.xml
    if has_relevant:
        if "Relevant Space News" not in xml or 'w:type="page"' not in xml:
            raise RuntimeError("V8 DOCX validation failed: Relevant Space News lacks explicit page break")
    return True


def convert_docx_to_pdf(docx_path, output_dir):
    converter = env("LIBREOFFICE_BIN", "libreoffice")
    cmd = [converter, "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(docx_path)]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:
        raise RuntimeError("LibreOffice is required to create a PDF from the generated DOCX") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed: {proc.stderr or proc.stdout}")
    pdf = Path(output_dir) / (Path(docx_path).stem + ".pdf")
    if not pdf.exists() or pdf.stat().st_size < 500:
        raise RuntimeError("LibreOffice did not create a valid PDF")
    return pdf


def validate_pdf_matches_articles(pdf_path, articles):
    """Automated V8 PDF content/pagination/render validation."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        raise RuntimeError("Generated PDF has no pages")
    page_texts = [(page.extract_text() or "") for page in reader.pages]
    extracted = " ".join(page_texts)
    norm_pdf = re.sub(r"\s+", " ", extracted).lower()

    required_headers = ["description / title of news", "publisher / author", "publication date / time", "tone", "impact analysis", "remarks / action"]
    if any(h not in norm_pdf for h in required_headers):
        raise RuntimeError("V8 PDF validation failed: seven-column header text is incomplete")
    if "late-discovered news" in norm_pdf:
        raise RuntimeError("V8 PDF validation failed: legacy Late-Discovered heading is present")

    # PDF extraction can drop or reorder Devanagari glyphs even when the PDF
    # displays correctly. Validate every row by its *canonical clickable source
    # URL* instead of relying on extracted headline bytes for Indic scripts.
    # For Latin headlines retain the additional readable-title check.
    from urllib.parse import unquote as _url_unquote
    pdf_links = set()
    for page in reader.pages:
        for annot_ref in (page.get("/Annots") or []):
            try:
                annot = annot_ref.get_object()
                action = annot.get("/A")
                if action:
                    uri = action.get("/URI")
                    if uri:
                        pdf_links.add(str(uri).strip())
            except Exception:
                continue
    for article in articles:
        url = str(article.canonical_url or "").strip()
        if not url or url not in pdf_links:
            raise RuntimeError(f"PDF validation failed: source hyperlink missing for article: {article.title[:80]}")
        # Exact headline extraction is reliable only for predominantly Latin
        # titles; do not make Marathi/Hindi font encoding a fatal report error.
        probe = re.sub(r"\s+", " ", article.title).strip().lower()[:45]
        latin_letters = len(re.findall(r"[a-zA-Z]", probe))
        all_letters = sum(ch.isalpha() for ch in probe)
        if probe and all_letters and latin_letters / all_letters >= 0.85:
            if probe[:24] not in norm_pdf:
                raise RuntimeError(f"PDF content validation failed for article title: {article.title[:80]}")

    relevant = [a for a in articles if a.section == "relevant"]
    if relevant:
        rel_pages = [i for i, txt in enumerate(page_texts) if "Relevant Space News" in txt]
        if not rel_pages:
            raise RuntimeError("V8 PDF validation failed: Relevant Space News heading missing")
        first_rel = rel_pages[0]
        # Explicit page break means the Relevant heading must begin on a page that
        # contains no article title from main/startup sections before it.
        rel_txt = page_texts[first_rel]
        for a in [x for x in articles if x.section != "relevant"]:
            probe = re.sub(r"\s+", " ", a.title).strip()[:32]
            if probe and probe in rel_txt:
                raise RuntimeError("V8 PDF validation failed: Relevant Space News shares a page with prior-section article content")

    render_bin = env("PDFTOPPM_BIN", "pdftoppm")
    render_dir = OUTPUT_DIR / (pdf_path.stem + "_rendercheck")
    render_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [render_bin, "-png", "-r", "100", str(pdf_path), str(render_dir / "page")],
            check=False, capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"PDF page render validation failed: {proc.stderr or proc.stdout}")
        rendered = sorted(render_dir.glob("page-*.png"))
        if len(rendered) != len(reader.pages):
            raise RuntimeError(f"PDF render page-count mismatch: expected {len(reader.pages)}, rendered {len(rendered)}")
        if any(x.stat().st_size < 1000 for x in rendered):
            raise RuntimeError("One or more rendered PDF pages are unexpectedly small")
    except FileNotFoundError:
        print("::warning::pdftoppm not installed; skipping all-page PNG render validation")
    finally:
        for x in render_dir.glob("*"):
            try: x.unlink()
            except Exception: pass
        try: render_dir.rmdir()
        except Exception: pass


def recipients():
    # Same addresses as the existing 30-minute report by default. Optional
    # scheduled-specific override is allowed without requiring it.
    raw = env("REPORT_RECIPIENTS_SCHEDULED") or env("REPORT_RECIPIENTS_30MIN") or env("REPORT_RECIPIENTS")
    vals = [x.strip() for x in re.split(r"[,;\n]+", raw) if x.strip()]
    return list(dict.fromkeys(vals))


def format_email_article_list(articles):
    """Produce plain-text and HTML email lists from the exact final report articles.

    Numbers restart per report section, matching the DOCX. No source lookup or
    additional filtering occurs here; the list cannot drift from the report.
    """
    section_labels = {"main": "DoS / ISRO Major News", "startup": "Space Startups", "relevant": "Relevant Space News"}
    groups = {}
    for article in (articles or []):
        groups.setdefault(article.section, []).append(article)
    plain, html = [], []
    for section in ("main", "startup", "relevant"):
        entries = groups.get(section, [])
        if not entries:
            continue
        label = section_labels[section]
        plain.append(label)
        html.append(f"<h3>{html_escape(label)}</h3><ol>")
        for index, article in enumerate(entries, 1):
            try:
                published = date.fromisoformat(article.published_date).strftime("%d %B %Y")
            except (ValueError, TypeError):
                published = "Not stated"
            time_text = "Not stated"
            if article.time_known and article.published_time_ist:
                try:
                    time_text = datetime.strptime(article.published_time_ist, "%H:%M:%S").strftime("%I:%M %p") + " IST"
                except ValueError:
                    time_text = "Not stated"
            title = str(article.title or "Not stated")
            publisher = str(article.publisher or "Not stated")
            author = str(article.author or "Not stated")
            plain.append(f"{index}. {title}\n   Publisher: {publisher}; Author: {author}\n   Published: {published}; Time: {time_text}")
            url = str(article.canonical_url or "")
            safe_url = html_escape(url, quote=True)
            linked_title = (f'<a href="{safe_url}">{html_escape(title)}</a>' if url.startswith(("https://", "http://")) else html_escape(title))
            html.append(f'<li style="margin-bottom:10px">{linked_title}<div>Publisher: {html_escape(publisher)}; Author: {html_escape(author)}</div><div>Published: {html_escape(published)}; Time: {html_escape(time_text)}</div></li>')
        html.append("</ol>")
        plain.append("")
    if not plain:
        return "News items: Nil qualifying articles.", "<p>News items: Nil qualifying articles.</p>"
    return "\n".join(plain).rstrip(), "".join(html)


def send_edition_email(edition, report_date, docx_path, pdf_path, run_status="COMPLETE", limitations=None, articles=None):
    sender = env("SMTP_EMAIL")
    password = env("SMTP_APP_PASSWORD").replace(" ", "")
    recips = recipients()
    if not recips:
        return {"sent": [], "failed": {}, "skipped": "no recipients configured"}
    if not sender or not password:
        raise RuntimeError("SMTP_EMAIL and SMTP_APP_PASSWORD are required for scheduled report email delivery")

    label = {"morning": "Morning", "evening": "Evening", "weekly": "Weekly"}[edition]
    prefix = "[PROVISIONAL] " if run_status == "PROVISIONAL" else ""
    subject = f"{prefix}DoS / ISRO {label} Media Report — {report_date.strftime('%d %B %Y')}"
    body = (
        f"Please find attached the {label.lower()} DoS / ISRO Media News and Impact Analysis Report "
        f"for {report_date.strftime('%d %B %Y')}.\n\n"
    )
    if run_status == "PROVISIONAL":
        body += "Status: PROVISIONAL. The report document itself remains reader-facing and contains no process notes.\n"
        for item in (limitations or [])[:6]:
            body += f"- {item}\n"
        body += "\n"
    news_plain, news_html = format_email_article_list(articles)
    body += "News items:\n\n" + news_plain + "\n\n"
    body += (
        "Attachments:\n"
        f"- {docx_path.name}\n"
        f"- {pdf_path.name}\n"
    )
    attachments = [
        (docx_path.name, docx_path.read_bytes(), "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (pdf_path.name, pdf_path.read_bytes(), "application", "pdf"),
    ]
    sent, failed = [], {}
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=45) as smtp:
        smtp.login(sender, password)
        for recipient in recips:
            try:
                msg = EmailMessage()
                msg["From"] = sender; msg["To"] = recipient; msg["Subject"] = subject
                msg.set_content(body)
                html_body = (
                    '<html><body style="font-family:Arial,sans-serif;font-size:12pt">'
                    f'<p>Please find attached the {html_escape(label.lower())} DoS / ISRO Media News and Impact Analysis Report '
                    f'for {html_escape(report_date.strftime("%d %B %Y"))}.</p>'
                    + (f'<p>Status: PROVISIONAL.</p><p>{html_escape("; ".join((limitations or [])[:6]))}</p>' if run_status == "PROVISIONAL" else "")
                    + '<h2>News items</h2>' + news_html
                    + '<p>Attachments:<br>' + html_escape(docx_path.name) + '<br>' + html_escape(pdf_path.name) + '</p></body></html>'
                )
                msg.add_alternative(html_body, subtype="html")
                for fn, payload, mt, st in attachments:
                    msg.add_attachment(payload, maintype=mt, subtype=st, filename=fn)
                refused = smtp.send_message(msg, from_addr=sender, to_addrs=[recipient])
                if refused:
                    failed[recipient] = str(refused)
                else:
                    sent.append(recipient)
            except Exception as exc:
                failed[recipient] = f"{type(exc).__name__}: {exc}"
    return {"sent": sent, "failed": failed}


def report_filenames(edition, report_date):
    ymd = report_date.strftime("%Y%m%d")
    suffix = {"morning": "Morning", "evening": "Evening", "weekly": "WEEKLY"}[edition]
    base = f"{ymd}_{suffix}_DoS_ISRO_Media_Report"
    return OUTPUT_DIR / f"{base}.docx", OUTPUT_DIR / f"{base}.pdf"


def run(edition, report_date=None, send_email=True):
    rd = report_date or datetime.now(IST).date()
    start, end, search_start = edition_window(edition, rd)
    report_key = f"{rd.isoformat()}:{edition}"
    run_started_mono = time.monotonic()
    run_started_ist = datetime.now(IST)

    archive_registry = load_archive_registry()
    candidates, discovery_diag = discover_candidates(edition, search_start, end)
    verified = []
    excluded = {}
    evidence_register = []
    attempted_urls = set()

    def reject(reason):
        excluded[reason] = excluded.get(reason, 0) + 1

    def process_candidate(candidate, sweep_no):
        attempted_urls.add(canonicalize_url(candidate.url) or candidate.url)
        article, reason = verify_article(candidate)
        if not article:
            reject(reason)
            evidence_register.append({
                "sweep": sweep_no,
                "candidate_url": candidate.url,
                "search_title": candidate.search_title,
                "source_pass": candidate.pass_no,
                "query": candidate.query,
                "eligibility": "FAIL",
                "reason": reason,
            })
            return None

        ok, lookback, window_reason = article_in_window(article, edition, start, end, search_start)
        if not ok:
            reject(window_reason)
            record = asdict(article); record.update({"sweep": sweep_no, "eligibility": "FAIL", "reason": window_reason})
            evidence_register.append(record)
            return None

        archived, archive_reason = archive_match(article, archive_registry)
        if archived:
            reject(archive_reason)
            record = asdict(article); record.update({"sweep": sweep_no, "eligibility": "FAIL", "reason": archive_reason})
            evidence_register.append(record)
            return None

        article.catch_up = bool(lookback)  # legacy field retained; reader-facing report never labels catch-up.
        record = asdict(article); record.update({"sweep": sweep_no, "eligibility": "PASS", "reason": window_reason, "archive_reason": archive_reason})
        evidence_register.append(record)
        return article

    # Sweep 1: verify every discovered candidate. No legacy max-candidate cap.
    for candidate in candidates:
        article = process_candidate(candidate, 1)
        if article:
            verified.append(article)

    first_pass_articles = dedupe_article_level(verified)

    # Sweep 2: cross-source/event reconciliation after first verification and
    # article-identity deduplication, as mandated by V8.
    gap_candidates, gap_diag = gap_reconciliation_candidates(first_pass_articles, search_start, end)
    for candidate in gap_candidates:
        cu = canonicalize_url(candidate.url) or candidate.url
        if cu in attempted_urls:
            continue
        article = process_candidate(candidate, 2)
        if article:
            verified.append(article)

    articles = dedupe_article_level(verified)

    # Mandatory official-site-first check before drafting each action. This is
    # private audit evidence; official releases are never independent media rows.
    official_audit = []
    for article in articles:
        official = check_official_sources(article)
        assign_remarks_action(article, official)
        official_audit.append({
            "article_key": article.article_key,
            "media_url": article.canonical_url,
            "media_title": article.title,
            "official": official,
            "remarks_action": article.remarks_action,
        })

    def sort_key(a):
        if a.time_known:
            return datetime.fromisoformat(a.published_iso_ist).timestamp()
        return 0
    articles.sort(key=sort_key, reverse=True)

    # V8 completion assessment BEFORE publication. Do not force a 20-minute
    # sleep; assess the target honestly and label the external delivery provisional
    # when the environment completed too quickly to satisfy the normal research dwell target.
    dwell_target = env_int("MIN_RESEARCH_DWELL_SECONDS", 1200)
    elapsed_before_output = int(time.monotonic() - run_started_mono)
    limitations = []
    if discovery_diag.get("queries", 0) < 39:
        limitations.append(f"Only {discovery_diag.get('queries', 0)} distinct discovery/theme queries were submitted; V8 requires at least 39.")
    if discovery_diag.get("theme_checks", 0) != 44:
        limitations.append(f"Only {discovery_diag.get('theme_checks', 0)} of 44 V8 themes were checked.")
    if gap_diag.get("sweep") != 2:
        limitations.append("Second gap-reconciliation sweep was not completed.")
    if not archive_registry.get("available"):
        limitations.append("Latest consolidated archive was unavailable; historical article-identity deduplication could not be confirmed.")
    if dwell_target > 0 and elapsed_before_output < dwell_target:
        limitations.append(f"Research dwell-time target was not met ({elapsed_before_output}s completed vs {dwell_target}s target).")

    # The original V7 DOCX template may not be committed in GitHub. V8 explicitly
    # permits faithful reproduction of its seven-column style, but the limitation
    # must be disclosed outside the publication.
    template_path = Path(env("V8_TEMPLATE_FILE", "DoS_ISRO_Media_Report_V7_0_Template.docx"))
    template_available = template_path.exists()
    external_notes = list(limitations)
    if not template_available:
        external_notes.append("The original V7 DOCX template file was unavailable; Build 6.0 reproduced the specified V8 seven-column A4-landscape style programmatically.")

    run_status = "PROVISIONAL" if limitations else "COMPLETE"

    docx_path, expected_pdf = report_filenames(edition, rd)
    generated_at = datetime.now(IST)
    build_docx(edition, rd, start, end, articles, docx_path, generated_at=generated_at)
    validate_docx_v8(docx_path, has_relevant=any(a.section == "relevant" for a in articles))
    pdf_path = convert_docx_to_pdf(docx_path, OUTPUT_DIR)
    if pdf_path != expected_pdf:
        expected_pdf.write_bytes(pdf_path.read_bytes())
        pdf_path = expected_pdf
    validate_pdf_matches_articles(pdf_path, articles)

    # Visual/structural shipping checks have passed at this point.
    email_result = {"sent": [], "failed": {}, "skipped": "email disabled"}
    if send_email:
        if already_emailed(report_key):
            email_result = {"sent": [], "failed": {}, "skipped": "already emailed; use FORCE_SCHEDULED_SEND=true to resend"}
        else:
            email_result = send_edition_email(
                edition, rd, docx_path, pdf_path, run_status=run_status, limitations=external_notes, articles=articles
            )

    email_status = (
        "sent:" + ",".join(email_result.get("sent", []))
        if email_result.get("sent")
        else "skipped:" + str(email_result.get("skipped", ""))
    )
    if email_result.get("failed"):
        email_status += ";failed:" + json.dumps(email_result["failed"], ensure_ascii=False)

    matched_themes = sorted({cid for a in articles for cid in a.concern_ids})
    theme_audit = [
        {"id": i, "theme": theme, "status": ("FOUND_IN_INCLUDED_ARTICLE" if i in matched_themes else "CHECKED_NO_INCLUDED_MATCH")}
        for i, theme in enumerate(THEMES, 1)
    ]

    evidence_path = OUTPUT_DIR / f"{report_key.replace(':','_')}_candidate_evidence_register.json"
    evidence_path.write_text(json.dumps({
        "report_key": report_key,
        "run_status": run_status,
        "articles": evidence_register,
        "theme_audit": theme_audit,
        "official_source_checks": official_audit,
        "archive_registry": {
            "available": archive_registry.get("available"),
            "path": archive_registry.get("path"),
            "url_count": len(archive_registry.get("urls", set())),
            "fallback_identity_count": len(archive_registry.get("fallback", set())),
            "error": archive_registry.get("error"),
        },
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    diagnostics = {
        "build": BUILD,
        "master_query_file": str(MASTER_QUERY_FILE),
        "master_query_version": "8.0",
        "run_status": run_status,
        "limitations_outside_publication": external_notes,
        "edition": edition,
        "report_date": rd.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "late_discovery_lookback_start": search_start.isoformat(),
        "run_started_ist": run_started_ist.isoformat(),
        "generated_at_ist": generated_at.isoformat(),
        "research_elapsed_seconds_before_output": elapsed_before_output,
        "research_dwell_target_seconds": dwell_target,
        "fresh_scan": True,
        "sweep_1": discovery_diag,
        "sweep_2": gap_diag,
        "candidate_count_sweep1": len(candidates),
        "candidate_count_sweep2": len(gap_candidates),
        "verified_included_count": len(articles),
        "section_counts": {
            "main": sum(a.section == "main" for a in articles),
            "startup": sum(a.section == "startup" for a in articles),
            "relevant": sum(a.section == "relevant" for a in articles),
        },
        "excluded_counts": excluded,
        "archive_available": archive_registry.get("available"),
        "archive_path": archive_registry.get("path"),
        "template_available": template_available,
        "template_path": str(template_path),
        "official_checks_attempted": len(official_audit),
        "email": email_result,
        "docx": docx_path.name,
        "pdf": pdf_path.name,
        "candidate_evidence_register": evidence_path.name,
        "theme_checks": len(theme_audit),
        "themes_with_included_matches": matched_themes,
        "free_semantic_analysis_enabled": _truthy_env("ENABLE_FREE_LLM_ANALYSIS", "1"),
    }
    diag_path = OUTPUT_DIR / f"{report_key.replace(':','_')}_scheduled_media_diagnostics.json"
    diag_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
    persist_run(report_key, edition, rd, start, end, articles, docx_path.name, pdf_path.name, email_status, diagnostics)

    print(json.dumps({
        "report_key": report_key,
        "edition": edition,
        "run_status": run_status,
        "article_count": len(articles),
        "section_counts": diagnostics["section_counts"],
        "docx": str(docx_path),
        "pdf": str(pdf_path),
        "email": email_result,
        "limitations": external_notes,
    }, ensure_ascii=False, indent=2))
    return diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=["morning", "evening", "weekly"], required=True)
    parser.add_argument("--report-date", help="YYYY-MM-DD in IST; defaults to current IST date")
    parser.add_argument("--no-email", action="store_true")
    args = parser.parse_args()
    rd = None
    if args.report_date:
        raw_date = args.report_date.strip()
        if raw_date.lower() in {"interim", "today", "current"}:
            # Historical manual dispatches used "interim" in the date input.
            # Treat this as today's IST date rather than crashing the workflow.
            print(f"[scheduled-report] --report-date={raw_date!r} is not a calendar date; using current IST date.")
        else:
            try:
                rd = date.fromisoformat(raw_date)
            except ValueError:
                parser.error("--report-date must be YYYY-MM-DD or blank (interim/today/current also select today IST)")
    run(args.edition, report_date=rd, send_email=not args.no_email)


if __name__ == "__main__":
    main()
