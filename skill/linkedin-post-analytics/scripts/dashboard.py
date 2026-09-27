"""Build the Posting Record dashboard from the query database.

Usage: python3 dashboard.py
Reads from the workspace (current directory, or PR_WORKSPACE):
  posting-record.sqlite   built by db.py; run that first
  config.json             profile, timezone_label, series name
  notes.json              optional: the three review panels, see below
Writes dist/posting-dashboard.html, a single self-contained file that loads nothing
from the network.

One page, filterable: an account snapshot, the latest collection against the whole
archive, a topic-to-engagement Sankey, the series fingerprint, length against reach,
weekday by length, cadence, quarters, topics, media, and every post with its text.

notes.json is yours to write, and optional. The three panels stay hidden without it:

    {
      "title": "Week of 21 September",
      "period": "Five posts, two editions.",
      "good":  ["The Tuesday essay carried, 2.1x the band median."],
      "bad":   ["Thursday's link post went out at 300 characters and died."],
      "next":  ["Move the link into the first comment and re-test."]
    }
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import html_text, js_data, js_literal, load_config, wpath

cfg = load_config()
db_path = wpath("posting-record.sqlite")
if not os.path.exists(db_path):
    sys.exit("posting-record.sqlite missing. Run db.py first.")

_sd = os.path.dirname(os.path.abspath(__file__))
tpl_dir = next((d for d in (os.path.join(_sd, "templates"), os.path.join(os.path.dirname(_sd), "templates"))
                if os.path.isdir(d)), None)
if not tpl_dir:
    sys.exit("templates/ folder not found next to scripts or one level up")

con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row


def q(sql, *a):
    try:
        return [dict(r) for r in con.execute(sql, a)]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc) or "no such view" in str(exc):
            return []
        raise


# The dashboard reads one row shape for both views, so build it once. Everything here
# already exists in v_posts; nothing is invented.
FIELDS = [
    "id", "activity", "url", "text", "first_line", "collected",
    "date_local", "published_local", "published_utc", "year", "quarter", "month",
    "iso_week", "weekday", "weekday_num", "is_weekend", "hour_local",
    "chars", "words", "paragraphs", "length_band", "hashtags", "hashtag_count",
    "opens_with_number", "opens_with_question", "has_link_in_text", "has_link_in_own_comment",
    "own_comments", "media", "images", "is_series", "series_n", "in_linkedin_top50",
    "impressions", "reactions", "comments", "reposts", "saves", "sends", "followers_gained",
]


def row(r):
    out = {k: r.get(k) for k in FIELDS}
    # topics arrive from v_posts as "a; b"; the page filters and counts them as a list
    out["topics"] = [t.strip() for t in (r.get("topics") or "").split(";") if t.strip()]
    out["has_metrics"] = 1 if r.get("impressions") is not None else 0
    # only used as a weekday fallback for a row with no local date; derived, not stored
    out["published_utc_from_activity_id"] = _utc_from_id(r.get("activity") or r.get("id"))
    return out


def _utc_from_id(pid):
    """LinkedIn ids embed milliseconds since epoch in the top bits."""
    try:
        ms = (int(pid) >> 22) / 1000
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


posts = [row(r) for r in q("SELECT * FROM v_posts ORDER BY date_local DESC")]

# "Latest" is the newest collection, not a fixed number of posts: whichever posts carry
# the most recent collected date. With no metrics at all it is simply empty.
dates = sorted({p["collected"] for p in posts if p["collected"]})
latest = [p for p in posts if p["collected"] and p["collected"] == dates[-1]] if dates else []

daily = q("SELECT date, impressions, engagements, new_followers, posts_published "
          "FROM daily_stats ORDER BY date")

meta = {r["key"]: r["value"] for r in q("SELECT key, value FROM meta")}
followers = meta.get("total_followers")
try:
    followers = int(followers) if followers not in (None, "") else None
except (TypeError, ValueError):
    followers = None

notes = None
notes_path = wpath("notes.json")
if os.path.exists(notes_path):
    try:
        raw = json.load(open(notes_path, encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"notes.json is not valid JSON: {exc}")
    keep = {}
    for k in ("title", "period"):
        if isinstance(raw.get(k), str):
            keep[k] = raw[k]
    for k in ("good", "bad", "next"):
        keep[k] = [str(x) for x in raw.get(k, []) if str(x).strip()]
    if any(keep.get(k) for k in ("good", "bad", "next")):
        notes = keep
    else:
        print("note: notes.json has no good/bad/next entries; the review panels stay hidden",
              file=sys.stderr)

data = {"history": posts, "latest": latest, "daily": daily, "followers": followers}
if notes:
    data["notes"] = notes

html_src = open(os.path.join(tpl_dir, "posting-dashboard.html"), encoding="utf-8").read()
filled = (html_src
          .replace("__DATA__", js_data(data))
          .replace("__TZ_JS__", js_literal(cfg["timezone"]))
          .replace("__SERIES_NAME_JS__", js_literal(cfg["series"]["name"]))
          .replace("__TZ_LABEL__", html_text(cfg["timezone_label"]))
          .replace("__SERIES_NAME__", html_text(cfg["series"]["name"]))
          .replace("__PROFILE__", html_text(cfg["profile"] or "Your LinkedIn")))

left = sorted(set(re.findall(r"__[A-Z_]+__", filled)))
if left:
    print("warning: unfilled placeholders:", left, file=sys.stderr)

out_dir = wpath("dist")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "posting-dashboard.html")
open(out, "w", encoding="utf-8").write(filled)
print(f"wrote dist/posting-dashboard.html ({len(filled)//1024} KB): {len(posts)} posts, "
      f"{sum(p['has_metrics'] for p in posts)} with metrics, {len(latest)} in the latest collection"
      f"{', review panels on' if notes else ''}")
