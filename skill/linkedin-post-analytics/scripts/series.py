"""Extract a recurring content series from posts.json, join per-post metrics,
and write <slug>.json + <slug>.csv for a website or tracker.

Usage: python3 series.py
Reads config.json "series" from the workspace: name, slug, include (regex on post text),
exclude (regex; a match removes the post, e.g. an edition that announced a skip).
"""
import csv, json, os, re, sys
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import load_config, wpath

cfg = load_config()
S = cfg["series"]
posts_path = wpath("posts.json")
analytics_path = wpath("analytics.json")
out_dir = wpath()

SERIES = re.compile(S["include"], re.I)
SKIP = re.compile(S.get("exclude") or "$^", re.I)
ID = re.compile(r"\d{19}")

posts = json.load(open(posts_path, encoding="utf-8"))["posts"]
top = {}
window = {}
if os.path.exists(analytics_path):
    an = json.load(open(analytics_path, encoding="utf-8"))
    top = {t["id"]: t for t in an["top"]}
    window = an.get("window", {})
# post_metrics.json (per-post numbers collected from each post's analytics page) overrides the top-50 list
metrics_path = os.path.join(os.path.dirname(os.path.abspath(analytics_path)), "post_metrics.json")
full = {}
if os.path.exists(metrics_path):
    full = {m["id"]: m for m in json.load(open(metrics_path, encoding="utf-8"))["posts"]}

BOILER = re.compile(S["include"] + r"|this week|another week|week in the books|back from|in case you missed|hot off|conference week|^probably|^and we have|^here is|^here's|edition number", re.I)

def opener(text):
    """A title-like line: the first sentence that is not series boilerplate."""
    for line in text.strip().split("\n"):
        line = line.strip().strip('"“”>*').strip()
        if len(line) < 20:
            continue
        sent = re.split(r"(?<=[.!?:])\s+", line)[0].strip()
        if BOILER.search(sent):
            rest = re.split(r"(?<=[.!?:])\s+", line)[1:]
            sent = next((x for x in rest if len(x) >= 20 and not BOILER.search(x)), "")
            if not sent:
                continue
        sent = re.sub(r"^(so|and|but|ok|okay)[,\s]+", "", sent, flags=re.I).strip(" ,.:;-")
        if len(sent) > 120:
            sent = sent[:117].rsplit(" ", 1)[0] + "…"
        return sent[0].upper() + sent[1:]
    return text.strip().split("\n")[0][:120]

series = []
for p in sorted((p for p in posts if SERIES.search(p["text"]) and not SKIP.search(p["text"])), key=lambda p: p["d"]):
    pid = ID.search(p["url"] or "")
    t = top.get(pid.group()) if pid else None
    f = full.get(pid.group()) if pid else None
    if f:
        t = {"imp": f["impressions"], "eng": f["social_engagements"]}
    d = date.fromisoformat(p["d"][:10])
    series.append({
        "n": len(series) + 1,
        "date": p["d"][:10],
        "iso_week": f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}",
        "weekday": d.strftime("%A"),
        "url": p["url"],
        "title": opener(p["text"]),
        "excerpt": re.sub(r"\s+", " ", p["text"])[:240].rstrip() + ("…" if len(p["text"]) > 240 else ""),
        "chars": p["len"],
        "words": p["words"],
        "impressions": t["imp"] if t else None,
        "engagements": t["eng"] if t else None,
        "in_linkedin_top50": bool(pid and pid.group() in top),
        "reactions": f["reactions"] if f else None,
        "comments": f["comments"] if f else None,
        "reposts": f["reposts"] if f else None,
        "saves": f["saves"] if f else None,
        "sends": f["sends"] if f else None,
        "followers_gained": f["followers_gained"] if f else None,
        "media": f["media"] if f else None,
        "text": p["text"],
    })

if not series:
    sys.exit(f"no posts match the series rule {S['include']!r}; check config.json")

# cadence: weeks covered since the first post
first = date.fromisoformat(series[0]["date"])
last = date.fromisoformat(series[-1]["date"])
weeks_posted = {s["iso_week"] for s in series}
all_weeks, cursor = [], first
while cursor <= last:
    all_weeks.append(f"{cursor.isocalendar()[0]}-W{cursor.isocalendar()[1]:02d}")
    cursor += timedelta(days=7)
streak = best = 0
for w in all_weeks:
    streak = streak + 1 if w in weeks_posted else 0
    best = max(best, streak)
current = 0
for w in reversed(all_weeks):
    if w in weeks_posted: current += 1
    else: break

ranked = [s for s in series if s["impressions"]]
in_window = [s for s in series if window and window["start"] <= s["date"] <= window["end"]]
summary = {
    "generated": datetime.now().isoformat(timespec="seconds"),
    "name": S["name"],
    "rule": SERIES.pattern,
    "posts": len(series),
    "first": series[0]["date"],
    "latest": series[-1]["date"],
    "weeks_since_first": len(all_weeks),
    "weeks_with_post": len(weeks_posted),
    "weeks_missed": [w for w in all_weeks if w not in weeks_posted],
    "longest_streak_weeks": best,
    "current_streak_weeks": current,
    "median_chars": sorted(s["chars"] for s in series)[len(series) // 2],
    "analytics_window": window or None,
    "in_window": len(in_window),
    "with_metrics": len(ranked),
    "impressions_total": sum(s["impressions"] for s in ranked),
    "engagements_total": sum(s["engagements"] for s in ranked),
    "median_impressions": sorted(s["impressions"] for s in ranked)[len(ranked) // 2] if ranked else None,
}

os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, S["slug"] + ".json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": summary, "posts": series}, fh, ensure_ascii=False, indent=2)
cols = ["n", "date", "iso_week", "weekday", "title", "chars", "words", "impressions", "engagements", "reactions", "comments", "reposts", "saves", "sends", "followers_gained", "media", "in_linkedin_top50", "url", "excerpt"]
with open(os.path.join(out_dir, S["slug"] + ".csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader(); w.writerows(series)

print(f"{len(series)} series posts, {summary['weeks_with_post']}/{summary['weeks_since_first']} weeks, "
      f"{len(ranked)} with metrics -> {S['slug']}.json, {S['slug']}.csv")
for s in series: print(f"  {s['n']:>2} {s['date']} {str(s['impressions'] or '-'):>6}  {s['title'][:80]}")
