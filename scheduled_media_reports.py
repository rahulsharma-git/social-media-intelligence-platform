"""Build 5.19.0 — deterministic scheduled DoS/ISRO media reports.

This worker implements the user-supplied master reporting policy without any
ChatGPT/LLM dependency.  It creates and emails three independent editions:

* Morning: daily 08:00 IST, normal window previous day 20:00 -> current day
  08:00, plus a prior-day 08:00 -> 20:00 missed-evening catch-up audit.
* Evening: daily 20:00 IST, same-day 08:00 -> 20:00.
* Weekly: Friday 20:00 IST, previous Friday 20:00 -> current Friday 20:00.

Each edition produces one editable DOCX and one PDF generated from that DOCX,
and sends a separate email to the configured report recipients.  The existing
30-minute background monitor is intentionally untouched.
"""
from __future__ import annotations

import argparse
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
BUILD = "5.19.0"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MASTER_QUERY_FILE = Path(os.getenv(
    "MASTER_QUERY_FILE", "DoS_ISRO_Master_Query_Three_Reports_8AM_8PM_Friday8PM.txt"
))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/140 Safari/537.36 SMI-ScheduledReports/5.19.0"
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
    "lupex", "eos-", "oceansat", "project netra", "poem-", "rlv",
]

# 44 mandatory concern prompts. These are discovery prompts only; the relevance
# gate below still decides eligibility.
CONCERNS = [
    "PSLV failures", "downstream launch delays PSLV failure", "NVS-02 orbit raising problem",
    "NavIC continuity reliability", "Gaganyaan programme schedule", "Gaganyaan crew safety human rating",
    "NISAR technical issues postponement", "SpaDeX docking tests", "launch frequency cadence",
    "transparency failure investigation", "sabotage allegation space programme", "scientist resignation attrition",
    "staff vacancies recruitment", "salary competitiveness talent retention", "promotions career progression",
    "employee representation consultation", "contract staffing employment security", "scientific work culture",
    "privatisation concern institutional response", "PSLV SSLV manufacturing transfer industry",
    "control launch infrastructure", "strategic technology IP safeguards", "future manufacturing role",
    "public sector technical expertise retention", "budget allocation shortfall", "budget underutilisation",
    "Chandrayaan Venus mission progress spending", "Gaganyaan programme cost scope",
    "space grade component electronics supply", "spaceport launch pad development", "startup funding FDI",
    "IN-SPACe approvals regulatory clarity", "space legislation policy", "private operator liability insurance",
    "global launch competitiveness", "satellite broadband approvals delays", "satcom spectrum allocation pricing",
    "satellite communication security data routing", "unequal satcom operator treatment",
    "overseas heavy lift launcher dependence", "orbital conjunction collision risk", "space debris production",
    "controlled disposal re-entry", "space traffic management international coordination",
]

PASS_QUERIES = {
    1: [
        'ISRO official update', 'Department of Space India ISRO', 'IN-SPACe official',
        'NSIL official space India', 'ISRO PIB parliamentary reply',
    ],
    2: [
        'ISRO India news', 'ISRO Indian Express', 'ISRO The Hindu', 'ISRO Economic Times',
        'ISRO Hindustan Times', 'ISRO Reuters India', 'ISRO PTI ANI',
    ],
    3: [
        'ISRO regional news', 'ISRO employee grievance', 'ISRO vacancies attrition',
        'ISRO privatisation outsourcing', 'ISRO technology transfer', 'ISRO safety investigation',
        'ISRO governance criticism', 'ISRO regional language news',
    ],
    4: [
        'ISRO SpaceNews', 'ISRO international cooperation', 'ISRO scientific journal',
        'ISRO startup contract', 'IN-SPACe startup contract', 'NSIL launch contract',
        'ISRO launch technology', 'ISRO satellite communications', 'ISRO regulatory development',
    ],
    5: [
        'ISRO latest today', 'Indian Space Research Organisation latest', 'ISRO centre latest',
        'ISRO mission timeline', 'ISRO upcoming missions', 'Gaganyaan Chandrayaan-4 BAS timeline',
        'Gaganyaan G1 Vyommitra launch', 'Bharatiya Antariksh Station BAS-01 roadmap',
        'ISRO 2026 2027 2028 missions', 'Chandrayaan-4 Moon sample return schedule',
        'Venus Orbiter NISAR NavIC upcoming', 'India space missions explained ISRO',
    ],
}

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


def discover_candidates(edition, search_start_ist, report_end_ist):
    """Five cumulative passes + mandatory 44-concern audit."""
    max_candidates = env_int("SCHEDULED_REPORT_MAX_CANDIDATES", 0)  # 0 = no retained-candidate cap
    candidates = {}
    diagnostics = {"passes": {}, "concerns_checked": 0, "queries": 0, "errors": []}

    # Keep search engines focused on the report dates without treating their
    # timestamps as proof of original publication time.
    date_hint = search_start_ist.strftime("%Y-%m-%d")
    end_hint = (report_end_ist.date() + timedelta(days=1)).isoformat()

    for pass_no in range(1, 6):
        added_before = len(candidates)
        for q in PASS_QUERIES[pass_no]:
            diagnostics["queries"] += 1
            full_q = f'{q} after:{date_hint} before:{end_hint}'
            try:
                # Alternate engines across queries; union, never replacement.
                sources = google_news_rss(full_q, limit=10)
                if pass_no >= 2:
                    sources += bing_news_rss(full_q, limit=7)
                if pass_no in {1, 4, 5}:
                    sources += gdelt_news(q, maxrecords=10, timespan="7d")
                for item in sources:
                    url = canonicalize_url(item.get("url"))
                    if not url:
                        continue
                    key = url
                    if key not in candidates and (max_candidates <= 0 or len(candidates) < max_candidates):
                        candidates[key] = Candidate(
                            url=url,
                            search_title=item.get("title", ""),
                            search_source=item.get("source", ""),
                            search_published=item.get("published", ""),
                            pass_no=pass_no,
                            query=q,
                        )
            except Exception as exc:
                diagnostics["errors"].append(f"pass {pass_no} query {q}: {type(exc).__name__}: {exc}")
        diagnostics["passes"][str(pass_no)] = {
            "queries_required": len(PASS_QUERIES[pass_no]),
            "candidates_added": len(candidates) - added_before,
            "cumulative_candidates": len(candidates),
        }

    # 44-concern gap check. Use one lightweight Google News RSS query per concern.
    # Every concern is checked even if the retained-candidate ceiling is reached.
    for idx, concern in enumerate(CONCERNS, 1):
        diagnostics["concerns_checked"] += 1
        diagnostics["queries"] += 1
        q = f'(ISRO OR "Department of Space" OR "IN-SPACe" OR NSIL) "{concern}"'
        try:
            for item in google_news_rss(q, limit=5):
                url = canonicalize_url(item.get("url"))
                if not url:
                    continue
                if url not in candidates and (max_candidates <= 0 or len(candidates) < max_candidates):
                    candidates[url] = Candidate(
                        url=url,
                        search_title=item.get("title", ""),
                        search_source=item.get("source", ""),
                        search_published=item.get("published", ""),
                        pass_no=5,
                        query=f"Concern {idx}: {concern}",
                    )
        except Exception as exc:
            diagnostics["errors"].append(f"concern {idx}: {type(exc).__name__}: {exc}")

    return list(candidates.values()), diagnostics


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


def verify_article(candidate: Candidate):
    url = candidate.url
    if "news.google.com" in urlparse(url).netloc:
        url = resolve_google_news_url(url)
        if not url:
            return None, "google-news-url-not-resolved"

    try:
        r = http_get(url, timeout=28, retries=1)
    except Exception as exc:
        return None, f"fetch-error:{type(exc).__name__}"
    if r.status_code != 200 or not r.text:
        return None, f"http-{r.status_code}"

    final_url = canonicalize_url(r.url)
    if not final_url or any(h in urlparse(final_url).netloc for h in ["news.google.com", "bing.com"]):
        return None, "non-publisher-final-url"

    soup = BeautifulSoup(r.text, "html.parser")
    jsonld_objects = _jsonld_objects(soup)

    # Canonical publisher identity.
    canonical_node = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
    canonical = canonicalize_url(urljoin(final_url, canonical_node.get("href"))) if canonical_node and canonical_node.get("href") else final_url

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
        {"name": "pubdate"}, {"name": "publish-date"},
        {"property": "og:published_time"},
    ])

    # JSON-LD is often the most reliable source for publisher first-publication metadata.
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
                    names = []
                    for item in a:
                        if isinstance(item, dict) and item.get("name"):
                            names.append(str(item["name"]).strip())
                    author = ", ".join(names)
            if publisher == urlparse(canonical).netloc.lower().replace("www.", ""):
                p = obj.get("publisher")
                if isinstance(p, dict) and p.get("name"):
                    publisher = str(p.get("name")).strip()
            break

    pub_date, pub_dt = _parse_published(published_raw)
    if not pub_date:
        # Do not use search/RSS timestamps as proof of original publication.
        return None, "original-publication-date-unverified"

    for bad in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
        bad.decompose()

    paragraphs = []
    for p in soup.find_all("p"):
        txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(txt) >= 35:
            paragraphs.append(txt)
        if sum(len(x) for x in paragraphs) > 14000:
            break
    body = " ".join(paragraphs)
    evidence_text = f"{title} {body}".strip()
    if len(body) < 80:
        return None, "insufficient-article-body"

    institutions = []
    matched_route = ""
    lower = evidence_text.lower()
    for inst, pats in INSTITUTION_PATTERNS.items():
        if any(re.search(p, lower, flags=re.I) for p in pats):
            institutions.append(inst)
    if institutions:
        matched_route = "R1/R2"
    else:
        # R3 route: mission/programme must be tied to institutional ownership in article context.
        mission_matches = [m for m in MISSION_TERMS if m.lower() in lower]
        if mission_matches and any(k in lower for k in ["india's space agency", "indian space agency", "space department"]):
            institutions.append("ISRO")
            matched_route = "R3"
    if not institutions:
        return None, "four-institution-gate-fail"

    # Find a short supporting sentence containing an institution term.
    support = ""
    for sentence in re.split(r"(?<=[.!?])\s+", evidence_text):
        sl = sentence.lower()
        if any(any(re.search(p, sl, flags=re.I) for p in pats) for pats in INSTITUTION_PATTERNS.values()):
            support = sentence[:360].strip(); break
    if not support:
        support = title[:360]

    concern_ids = []
    for i, concern in enumerate(CONCERNS, 1):
        terms = [x for x in re.findall(r"[a-z0-9-]{4,}", concern.lower()) if x not in {"space", "india", "isro"}]
        if terms and sum(1 for t in terms if t in lower) >= min(2, len(terms)):
            concern_ids.append(i)

    tone = classify_tone(title + " " + body[:1200])
    impact_level, impact_text = classify_impact(title + " " + body[:1600], institutions, concern_ids)

    return Article(
        canonical_url=canonical,
        publisher=re.sub(r"\s+", " ", publisher).strip() or "Publisher not stated",
        title=title,
        author=re.sub(r"\s+", " ", author).strip() or "Not stated",
        published_date=pub_date,
        published_time_ist=(pub_dt.strftime("%H:%M:%S") if pub_dt else ""),
        published_iso_ist=(pub_dt.isoformat() if pub_dt else ""),
        time_known=bool(pub_dt),
        body_excerpt=body[:2200],
        institutions=sorted(set(institutions)),
        gate_route=matched_route or "R4",
        support_excerpt=support,
        source_pass=candidate.pass_no,
        discovery_query=candidate.query,
        concern_ids=concern_ids,
        tone=tone,
        impact_level=impact_level,
        impact_text=impact_text,
    ), "ok"


def classify_tone(text):
    lower = text.lower()
    if SENTIMENT:
        score = SENTIMENT.polarity_scores(text[:5000])["compound"]
    else:
        pos = sum(lower.count(w) for w in ("success", "successful", "achievement", "progress", "agreement", "cooperation", "launch"))
        neg = sum(lower.count(w) for w in NEGATIVE_HINTS)
        score = max(-1.0, min(1.0, (pos - neg) / max(3.0, pos + neg)))
    if any(h in lower for h in NEGATIVE_HINTS) and score < 0.20:
        return "Negative"
    if score >= 0.22:
        return "Positive"
    if score <= -0.22:
        return "Negative"
    return "Neutral"


def classify_impact(text, institutions, concern_ids):
    lower = text.lower()
    inst = ", ".join(institutions) if institutions else "the scoped institutions"
    if concern_ids or any(h in lower for h in HIGH_IMPACT_HINTS):
        level = "High"
        reason = f"This development can materially affect {inst}'s programme delivery, policy, safety, schedule or strategic responsibilities."
    elif any(h in lower for h in MEDIUM_IMPACT_HINTS):
        level = "Medium"
        reason = f"This development has a direct operational, mission, partnership or capability implication for {inst}."
    else:
        level = "Low"
        reason = f"This item provides routine or contextual information relevant to {inst} without a clearly material operational change."
    return level, reason


def edition_window(edition, report_date=None):
    now = datetime.now(IST)
    rd = report_date or now.date()
    if edition == "morning":
        end = datetime.combine(rd, dtime(8, 0), IST)
        start = end - timedelta(hours=12)
        search_start = datetime.combine(rd - timedelta(days=1), dtime(8, 0), IST)
        return start, end, search_start
    if edition == "evening":
        start = datetime.combine(rd, dtime(8, 0), IST)
        end = datetime.combine(rd, dtime(20, 0), IST)
        return start, end, start
    if edition == "weekly":
        end = datetime.combine(rd, dtime(20, 0), IST)
        if rd.weekday() != 4:
            raise ValueError("Weekly report date must be Friday")
        start = end - timedelta(days=7)
        return start, end, start
    raise ValueError(f"Unknown edition: {edition}")


def article_in_window(article: Article, edition, start, end, search_start, previous_evening_keys=None):
    prev_keys = previous_evening_keys or set()
    article_date = date.fromisoformat(article.published_date)
    catch_up = False

    if article.time_known:
        dt = datetime.fromisoformat(article.published_iso_ist).astimezone(IST)
        if start <= dt < end:
            return True, False, "exact-in-window"
        if edition == "morning" and search_start <= dt < start and article.article_key not in prev_keys:
            catch_up = True
            return True, True, "previous-evening-catch-up"
        return False, False, "exact-outside-window"

    # Date-only eligibility from master policy.
    if edition == "morning":
        eligible_dates = {start.date(), end.date()}
        if article_date in eligible_dates:
            if article_date == search_start.date() and article.article_key not in prev_keys:
                catch_up = True
            return True, catch_up, "date-only-boundary-eligible"
    elif edition == "evening":
        if article_date == start.date():
            return True, False, "date-only-evening-eligible"
    elif edition == "weekly":
        d = start.date()
        eligible = set()
        while d <= end.date():
            eligible.add(d); d += timedelta(days=1)
        if article_date in eligible:
            return True, False, "date-only-weekly-eligible"
    return False, False, "date-only-outside-window"


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


def build_docx(edition, report_date, start, end, articles, output_path):
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    sec.top_margin = Mm(10); sec.bottom_margin = Mm(12); sec.left_margin = Mm(10); sec.right_margin = Mm(10)

    styles = doc.styles
    styles["Normal"].font.name = "Arial"; styles["Normal"].font.size = Pt(12)

    edition_label = {"morning": "Morning", "evening": "Evening", "weekly": "Weekly"}[edition]
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if edition == "weekly":
        line = f"{edition_label} Report — {report_date.strftime('%d %B %Y')} — {start.strftime('%d %B %Y, %I:%M %p')} to {end.strftime('%d %B %Y, %I:%M %p')} IST"
    else:
        line = f"{edition_label} Report — {report_date.strftime('%d %B %Y')} — {end.strftime('%I:%M %p')} IST"
    _set_run_font(p.add_run(line), bold=False, size=12)
    p2 = doc.add_paragraph(); p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(p2.add_run("DoS / ISRO Media News and Impact Analysis Report"), bold=True, size=12)

    headers = [
        "S. No.", "Description / Title of News", "Electronic / Print / Media House / Author",
        "Positive / Negative / Neutral", "Impact Analysis", "Remarks (Search Engine)"
    ]
    table = doc.add_table(rows=1, cols=6)
    table.autofit = False
    widths = [Mm(13), Mm(72), Mm(62), Mm(29), Mm(75), Mm(26)]
    set_fixed_table_layout(table, widths)
    for i, cell in enumerate(table.rows[0].cells):
        set_cell_width(cell, widths[i])
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_shading(cell, "D9EAF7")
        para = cell.paragraphs[0]; para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(para.add_run(headers[i]), bold=True, size=12)
    set_repeat_table_header(table.rows[0])
    set_table_borders(table, "404040", "6")  # 0.75pt printed borders for all editions.

    if not articles:
        row = table.add_row(); prevent_row_split(row)
        values = ["1", "No qualifying verified news was found for the reporting period.", "Not applicable\nAuthor: Not stated\nPublished: Not applicable\nTime: Not stated", "Neutral", "Low Impact.\nNo qualifying institution-grounded article was verified in the reporting window.", ""]
        for i, value in enumerate(values):
            cell = row.cells[i]; set_cell_width(cell, widths[i])
            for pi, line in enumerate(value.split("\n")):
                para = cell.paragraphs[0] if pi == 0 else cell.add_paragraph()
                _set_run_font(para.add_run(line), size=12)
    else:
        for idx, a in enumerate(articles, 1):
            r = table.add_row(); prevent_row_split(r)
            for i, cell in enumerate(r.cells):
                set_cell_width(cell, widths[i])
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

            _set_run_font(r.cells[0].paragraphs[0].add_run(str(idx)), size=12)

            title = a.title
            if a.catch_up:
                title += "\nCatch-up from previous evening"
            for j, line in enumerate(title.split("\n")):
                para = r.cells[1].paragraphs[0] if j == 0 else r.cells[1].add_paragraph()
                _set_run_font(para.add_run(line), bold=(j == 0), size=12)

            pub_date = date.fromisoformat(a.published_date).strftime("%d %B %Y")
            media_lines = [
                a.publisher,
                f"Author: {a.author or 'Not stated'}",
                f"Published: {pub_date}",
                (f"Time: {datetime.strptime(a.published_time_ist, '%H:%M:%S').strftime('%I:%M %p')} IST" if a.time_known else "Time: Not stated"),
            ]
            for j, line in enumerate(media_lines):
                para = r.cells[2].paragraphs[0] if j == 0 else r.cells[2].add_paragraph()
                _set_run_font(para.add_run(line), size=12)

            _set_run_font(r.cells[3].paragraphs[0].add_run(a.tone), size=12)

            p_imp = r.cells[4].paragraphs[0]
            _set_run_font(p_imp.add_run(f"{a.impact_level} Impact."), bold=True, size=12)
            p_imp2 = r.cells[4].add_paragraph()
            _set_run_font(p_imp2.add_run(a.impact_text), size=12)

            add_hyperlink(r.cells[5].paragraphs[0], "Article", a.canonical_url)

    # Footer on every page.
    generated = datetime.now(IST)
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(footer.add_run(f"Report generated: {generated.strftime('%d %B %Y, %I:%M %p')} IST"), size=10)

    doc.save(output_path)


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
    """Automated all-page render + text/content validation for non-LLM GitHub runs."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        raise RuntimeError("Generated PDF has no pages")
    extracted = " ".join((page.extract_text() or "") for page in reader.pages)
    norm_pdf = re.sub(r"\s+", " ", extracted).lower()
    for article in articles:
        probe = re.sub(r"\s+", " ", article.title).strip().lower()[:45]
        if probe and probe[:24] not in norm_pdf:
            raise RuntimeError(f"PDF content validation failed for article title: {article.title[:80]}")

    # Render every PDF page to PNG when pdftoppm is available. This catches
    # malformed/clipped conversion failures without requiring ChatGPT/vision.
    render_bin = env("PDFTOPPM_BIN", "pdftoppm")
    render_dir = OUTPUT_DIR / (pdf_path.stem + "_rendercheck")
    render_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [render_bin, "-png", "-r", "90", str(pdf_path), str(render_dir / "page")],
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


def send_edition_email(edition, report_date, docx_path, pdf_path):
    sender = env("SMTP_EMAIL")
    password = env("SMTP_APP_PASSWORD").replace(" ", "")
    recips = recipients()
    if not recips:
        return {"sent": [], "failed": {}, "skipped": "no recipients configured"}
    if not sender or not password:
        raise RuntimeError("SMTP_EMAIL and SMTP_APP_PASSWORD are required for scheduled report email delivery")

    label = {"morning": "Morning", "evening": "Evening", "weekly": "Weekly"}[edition]
    subject = f"DoS / ISRO {label} Media Report — {report_date.strftime('%d %B %Y')}"
    body = (
        f"Please find attached the {label.lower()} DoS / ISRO Media News and Impact Analysis Report "
        f"for {report_date.strftime('%d %B %Y')}.\n\n"
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
    suffix = {"morning": "AM", "evening": "PM", "weekly": "WEEKLY"}[edition]
    base = f"{ymd}_{suffix}_DoS_ISRO_Media_Report"
    return OUTPUT_DIR / f"{base}.docx", OUTPUT_DIR / f"{base}.pdf"


def run(edition, report_date=None, send_email=True):
    rd = report_date or datetime.now(IST).date()
    start, end, search_start = edition_window(edition, rd)
    report_key = f"{rd.isoformat()}:{edition}"

    previous_evening_keys, ledger_available = (set(), False)
    if edition == "morning":
        previous_evening_keys, ledger_available = load_previous_evening_keys(rd)

    candidates, discovery_diag = discover_candidates(edition, search_start, end)
    verified = []
    excluded = {}
    evidence_register = []

    max_verify = env_int("SCHEDULED_REPORT_VERIFY_LIMIT", 0)
    verify_candidates = candidates if max_verify <= 0 else candidates[:max_verify]
    for idx, candidate in enumerate(verify_candidates, 1):
        article, reason = verify_article(candidate)
        if not article:
            excluded[reason] = excluded.get(reason, 0) + 1
            evidence_register.append({
                "candidate_url": candidate.url, "search_title": candidate.search_title,
                "source_pass": candidate.pass_no, "query": candidate.query,
                "eligibility": "FAIL", "reason": reason,
            })
            continue
        ok, catch_up, window_reason = article_in_window(
            article, edition, start, end, search_start, previous_evening_keys
        )
        if not ok:
            excluded[window_reason] = excluded.get(window_reason, 0) + 1
            record = asdict(article); record.update({"eligibility": "FAIL", "reason": window_reason})
            evidence_register.append(record)
            continue
        article.catch_up = bool(catch_up)
        verified.append(article)
        record = asdict(article); record.update({"eligibility": "PASS", "reason": window_reason})
        evidence_register.append(record)

    articles = dedupe_article_level(verified)

    def sort_key(a):
        if a.time_known:
            return datetime.fromisoformat(a.published_iso_ist).timestamp()
        return datetime.combine(date.fromisoformat(a.published_date), dtime(12, 0), IST).timestamp()
    articles.sort(key=sort_key, reverse=True)

    docx_path, expected_pdf = report_filenames(edition, rd)
    build_docx(edition, rd, start, end, articles, docx_path)
    pdf_path = convert_docx_to_pdf(docx_path, OUTPUT_DIR)
    if pdf_path != expected_pdf:
        expected_pdf.write_bytes(pdf_path.read_bytes())
        pdf_path = expected_pdf
    validate_pdf_matches_articles(pdf_path, articles)

    email_result = {"sent": [], "failed": {}, "skipped": "email disabled"}
    if send_email:
        if already_emailed(report_key):
            email_result = {"sent": [], "failed": {}, "skipped": "already emailed; use FORCE_SCHEDULED_SEND=true to resend"}
        else:
            email_result = send_edition_email(edition, rd, docx_path, pdf_path)

    email_status = (
        "sent:" + ",".join(email_result.get("sent", []))
        if email_result.get("sent")
        else "skipped:" + str(email_result.get("skipped", ""))
    )
    if email_result.get("failed"):
        email_status += ";failed:" + json.dumps(email_result["failed"], ensure_ascii=False)

    matched_concerns = sorted({cid for a in articles for cid in a.concern_ids})
    concern_audit = [
        {"id": i, "concern": concern, "status": ("FOUND" if i in matched_concerns else "CHECKED_NOT_FOUND")}
        for i, concern in enumerate(CONCERNS, 1)
    ]
    evidence_path = OUTPUT_DIR / f"{report_key.replace(':','_')}_candidate_evidence_register.json"
    evidence_path.write_text(json.dumps({
        "report_key": report_key,
        "articles": evidence_register,
        "concern_audit": concern_audit,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    diagnostics = {
        "build": BUILD,
        "master_query_file": str(MASTER_QUERY_FILE),
        "edition": edition,
        "report_date": rd.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "search_start": search_start.isoformat(),
        "candidate_count": len(candidates),
        "verified_included_count": len(articles),
        "excluded_counts": excluded,
        "discovery": discovery_diag,
        "previous_evening_ledger_available": ledger_available,
        "previous_evening_article_keys": len(previous_evening_keys),
        "email": email_result,
        "docx": docx_path.name,
        "pdf": pdf_path.name,
        "candidate_evidence_register": evidence_path.name,
        "concern_audit_checked": len(concern_audit),
        "concerns_with_included_matches": matched_concerns,
    }
    diag_path = OUTPUT_DIR / f"{report_key.replace(':','_')}_scheduled_media_diagnostics.json"
    diag_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
    persist_run(report_key, edition, rd, start, end, articles, docx_path.name, pdf_path.name, email_status, diagnostics)

    print(json.dumps({
        "report_key": report_key,
        "edition": edition,
        "article_count": len(articles),
        "docx": str(docx_path),
        "pdf": str(pdf_path),
        "email": email_result,
    }, ensure_ascii=False, indent=2))
    return diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=["morning", "evening", "weekly"], required=True)
    parser.add_argument("--report-date", help="YYYY-MM-DD in IST; defaults to current IST date")
    parser.add_argument("--no-email", action="store_true")
    args = parser.parse_args()
    rd = date.fromisoformat(args.report_date) if args.report_date else None
    run(args.edition, report_date=rd, send_email=not args.no_email)


if __name__ == "__main__":
    main()
