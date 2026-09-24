"""Optional official X API collector for Build 5.14.

Runs safely inside GitHub Actions. If X_BEARER_TOKEN is not configured, it
prints a skip message and exits successfully; stored X/manual-X rows in Supabase
remain available to the intelligence pipeline.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def env(name, default=""):
    return str(os.getenv(name, default) or default).strip()


def supabase_config():
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials are missing")
    return url, key


def upsert(rows):
    if not rows:
        return 0
    url, key = supabase_config()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(
        f"{url}/rest/v1/social_posts",
        headers=headers,
        params={"on_conflict": "external_id"},
        json=rows,
        timeout=60,
    )
    r.raise_for_status()
    return len(rows)


def main():
    token = env("X_BEARER_TOKEN")
    if not token:
        print("X collector: X_BEARER_TOKEN is not configured; skipping live X fetch.")
        return 0

    query = env("X_QUERY", "(ISRO OR @isro OR #ISRO) -is:retweet")
    try:
        max_posts = max(10, min(int(env("X_MAX_POSTS", "100")), 500))
    except Exception:
        max_posts = 100

    endpoint = "https://api.x.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "query": query,
        "max_results": min(100, max_posts),
        "tweet.fields": "created_at,public_metrics,author_id,entities",
        "expansions": "author_id",
        "user.fields": "username,public_metrics",
        "sort_order": "recency",
    }

    rows = []
    next_token = None
    while len(rows) < max_posts:
        call_params = dict(params)
        if next_token:
            call_params["next_token"] = next_token
        r = requests.get(endpoint, headers=headers, params=call_params, timeout=60)
        if r.status_code != 200:
            print(f"::warning::X API collector skipped after HTTP {r.status_code}: {r.text[:500]}")
            return 0
        payload = r.json()
        users = {u.get("id"): u for u in payload.get("includes", {}).get("users", [])}
        for post in payload.get("data", []) or []:
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
                continue
            rows.append({
                "external_id": f"x:{post_id}",
                "platform": "x",
                "timestamp": post.get("created_at"),
                "username": username,
                "text": post.get("text", ""),
                "hashtags": " ".join(tags),
                "likes": int(pm.get("like_count", 0) or 0),
                "reposts": int(pm.get("retweet_count", 0) or 0),
                "replies": int(pm.get("reply_count", 0) or 0),
                "followers": int(upm.get("followers_count", 0) or 0),
                "source_url": f"https://x.com/{username}/status/{post_id}",
                "topic_seed": "ISRO",
            })
            if len(rows) >= max_posts:
                break
        next_token = (payload.get("meta", {}) or {}).get("next_token")
        if not next_token or len(rows) >= max_posts:
            break

    written = upsert(rows)
    summary = {"query": query, "fetched": len(rows), "upserted": written}
    (OUTPUT_DIR / "x_collection_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
