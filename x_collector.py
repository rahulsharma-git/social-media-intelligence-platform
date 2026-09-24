"""Official X recent-search collector — Build 5.14.3.

Goal:
- Prefer original matching posts.
- Target at least X_MIN_POSTS real recent posts (default 10) when the X API
  actually returns that many.
- If original-only search yields fewer than the minimum, make one broader
  pass that allows reposts, then deduplicate by post ID.
- Never fabricate posts. If the API/access/source returns fewer than the
  minimum, store and report only what was really returned.
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

import requests

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

X_ENDPOINT = "https://api.x.com/2/tweets/search/recent"


def env(name, default=""):
    return str(os.getenv(name, default) or default).strip()


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except Exception:
        return int(default)


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


def upsert(rows):
    if not rows:
        return 0
    url, _ = supabase_config()
    r = requests.post(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers("resolution=merge-duplicates,return=minimal"),
        params={"on_conflict": "external_id"},
        json=rows,
        timeout=60,
    )
    r.raise_for_status()
    return len(rows)


def existing_x_ids():
    """Return existing X IDs to calculate true newly discovered posts."""
    url, _ = supabase_config()
    r = requests.get(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers(),
        params={
            "select": "external_id",
            "platform": "in.(x,x_manual)",
            "limit": "5000",
        },
        timeout=45,
    )
    r.raise_for_status()
    return {
        str(row.get("external_id", "")).strip()
        for row in (r.json() or [])
        if str(row.get("external_id", "")).strip()
    }


def write_summary(summary):
    path = OUTPUT_DIR / "x_collection_summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _strip_retweet_exclusion(query: str) -> str:
    q = re.sub(r"(?i)(?:^|\s)-is:retweet(?:\s|$)", " ", query or "")
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _row_from_post(post, users):
    author = users.get(post.get("author_id"), {})
    username = author.get("username") or post.get("author_id") or "unknown"
    pm = post.get("public_metrics", {}) or {}
    upm = author.get("public_metrics", {}) or {}

    tags = []
    for item in (post.get("entities", {}) or {}).get("hashtags", []) or []:
        tag = str(item.get("tag", "")).strip()
        if tag:
            tags.append("#" + tag.lstrip("#"))

    post_id = str(post.get("id", "")).strip()
    if not post_id:
        return None

    return {
        "external_id": f"x:{post_id}",
        "platform": "x",
        "timestamp": post.get("created_at"),
        "username": username,
        "text": post.get("text", ""),
        "hashtags": " ".join(tags),
        "likes": int(pm.get("like_count", 0) or 0),
        "reposts": int(pm.get("repost_count", pm.get("retweet_count", 0)) or 0),
        "replies": int(pm.get("reply_count", 0) or 0),
        "followers": int(upm.get("followers_count", 0) or 0),
        "source_url": f"https://x.com/{username}/status/{post_id}",
        "topic_seed": "ISRO",
    }


def search_recent(token, query, wanted, hard_limit):
    """Fetch real X posts newest-first, following pagination as needed."""
    headers = {"Authorization": f"Bearer {token}"}
    page_size = max(10, min(100, max(wanted, 10)))
    base_params = {
        "query": query,
        "max_results": page_size,
        "tweet.fields": "created_at,public_metrics,author_id,entities",
        "expansions": "author_id",
        "user.fields": "username,public_metrics",
        "sort_order": "recency",
    }

    rows = []
    seen = set()
    next_token = None
    pages = 0
    api_result_count = 0

    while len(rows) < hard_limit:
        call_params = dict(base_params)
        if next_token:
            call_params["next_token"] = next_token

        r = requests.get(
            X_ENDPOINT,
            headers=headers,
            params=call_params,
            timeout=60,
        )
        pages += 1

        if r.status_code != 200:
            return {
                "ok": False,
                "status": int(r.status_code),
                "error": r.text[:1000],
                "rows": rows,
                "pages": pages,
                "result_count": api_result_count,
            }

        payload = r.json()
        meta = payload.get("meta", {}) or {}
        api_result_count += int(meta.get("result_count", 0) or 0)

        users = {
            str(u.get("id")): u
            for u in payload.get("includes", {}).get("users", []) or []
        }

        for post in payload.get("data", []) or []:
            row = _row_from_post(post, users)
            if not row:
                continue
            eid = row["external_id"]
            if eid in seen:
                continue
            seen.add(eid)
            rows.append(row)
            if len(rows) >= hard_limit:
                break

        # Stop as soon as the requested minimum is available. The collector's
        # purpose is freshness, not exhaustive archive harvesting.
        if len(rows) >= wanted:
            break

        next_token = meta.get("next_token")
        if not next_token:
            break

    return {
        "ok": True,
        "status": 200,
        "error": "",
        "rows": rows,
        "pages": pages,
        "result_count": api_result_count,
    }


def merge_unique(*row_sets):
    by_id = {}
    for rows in row_sets:
        for row in rows or []:
            eid = str(row.get("external_id", "")).strip()
            if eid and eid not in by_id:
                by_id[eid] = row
    # X created_at is ISO-8601 UTC, so lexical ordering is chronological.
    return sorted(
        by_id.values(),
        key=lambda r: str(r.get("timestamp", "")),
        reverse=True,
    )


def write_latest_csv(rows):
    """Diagnostic/app-compatible CSV of the latest collected posts."""
    path = OUTPUT_DIR / "x_latest_collected.csv"
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
            writer.writerow({k: row.get(k, "") for k in fields})
    return path


def main():
    token = env("X_BEARER_TOKEN")
    min_posts = max(10, min(env_int("X_MIN_POSTS", 10), 100))
    max_posts = max(min_posts, min(env_int("X_MAX_POSTS", 25), 500))

    configured_query = env(
        "X_QUERY",
        "(ISRO OR @isro OR #ISRO) -is:retweet",
    )

    if not token:
        write_summary({
            "status": "disabled",
            "reason": "X_BEARER_TOKEN is not configured",
            "query": configured_query,
            "minimum_target": min_posts,
            "target_met": False,
            "fetched": 0,
            "new_count": 0,
            "upserted": 0,
        })
        print(
            "::warning::X collector disabled: X_BEARER_TOKEN is not configured. "
            "Stored X fallback will be used."
        )
        return 0

    attempts = []

    # Pass 1: prefer original/non-retweet posts.
    primary = search_recent(
        token=token,
        query=configured_query,
        wanted=min_posts,
        hard_limit=max_posts,
    )
    attempts.append({
        "query": configured_query,
        "http_status": primary["status"],
        "rows": len(primary["rows"]),
        "pages": primary["pages"],
    })

    if not primary["ok"]:
        write_summary({
            "status": "api_error",
            "http_status": primary["status"],
            "reason": primary["error"],
            "query": configured_query,
            "minimum_target": min_posts,
            "target_met": False,
            "query_attempts": attempts,
            "fetched": len(primary["rows"]),
            "new_count": 0,
            "upserted": 0,
        })
        print(
            f"::warning::X API primary search failed with HTTP "
            f"{primary['status']}: {primary['error'][:500]}"
        )
        return 0

    rows = list(primary["rows"])

    # Pass 2: if fewer than minimum original posts were returned, allow
    # reposts. These are still real X posts; they are used only to fill the
    # minimum target and are deduplicated by post ID.
    if len(rows) < min_posts:
        inclusive_query = _strip_retweet_exclusion(configured_query)
        if not inclusive_query:
            inclusive_query = "(ISRO OR @isro OR #ISRO)"

        if inclusive_query != configured_query:
            broader = search_recent(
                token=token,
                query=inclusive_query,
                wanted=min_posts,
                hard_limit=max_posts,
            )
            attempts.append({
                "query": inclusive_query,
                "http_status": broader["status"],
                "rows": len(broader["rows"]),
                "pages": broader["pages"],
            })
            if broader["ok"]:
                rows = merge_unique(rows, broader["rows"])[:max_posts]
            else:
                print(
                    f"::warning::X broader fallback search failed with HTTP "
                    f"{broader['status']}; keeping primary results."
                )

    # Final safeguard: everything must still be a real row returned by X.
    rows = merge_unique(rows)[:max_posts]

    try:
        before_ids = existing_x_ids()
    except Exception as exc:
        before_ids = set()
        print(
            f"::warning::Could not read existing X IDs before upsert: "
            f"{type(exc).__name__}: {exc}"
        )

    fetched_ids = {
        str(row.get("external_id", "")).strip()
        for row in rows
        if str(row.get("external_id", "")).strip()
    }
    new_count = len(fetched_ids - before_ids) if before_ids else len(fetched_ids)

    written = upsert(rows)
    csv_path = write_latest_csv(rows)

    timestamps = [
        str(row.get("timestamp", "")).strip()
        for row in rows
        if str(row.get("timestamp", "")).strip()
    ]

    target_met = len(rows) >= min_posts
    status = "success" if target_met else "partial"

    summary = {
        "status": status,
        "query": configured_query,
        "minimum_target": min_posts,
        "target_met": target_met,
        "fetched": len(rows),
        "new_count": int(new_count),
        "upserted": written,
        "newest_timestamp": max(timestamps) if timestamps else None,
        "oldest_timestamp": min(timestamps) if timestamps else None,
        "query_attempts": attempts,
        "latest_csv": str(csv_path),
        "note": (
            "Minimum target met."
            if target_met
            else "Fewer than the target were returned by the available X recent-search access; no posts were fabricated."
        ),
    }
    write_summary(summary)

    if target_met:
        print(f"X collector: minimum target met ({len(rows)} real posts).")
    else:
        print(
            f"::warning::X collector returned only {len(rows)} real posts; "
            f"target was {min_posts}. No synthetic rows were created."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
