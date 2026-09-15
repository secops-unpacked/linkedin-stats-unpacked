"""Build the two dashboards from the workspace data and config.

Usage: python3 build.py
Reads from the workspace (current directory, or PR_WORKSPACE):
  config.json         profile, timezone, series rule, topics
  posts.json          from prepare.py, plus anything weekly.py appended
  analytics.json      from analytics.py (optional; daily series, followers, audience)
  post_metrics.json   latest per-post snapshot (optional until the first collector run)
  <series slug>.json  from series.py (optional)
Writes dist/posting-record.html and dist/post-explorer.html.
"""
import html, json, os, re, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import load_config, read_json, wpath

cfg = load_config()
_sd = os.path.dirname(os.path.abspath(__file__))
tpl_dir = next((d for d in (os.path.join(_sd, "templates"), os.path.join(os.path.dirname(_sd), "templates")) if os.path.isdir(d)), None)
if not tpl_dir:
    sys.exit("templates/ folder not found next to scripts or one level up")
out_dir = wpath("dist")

posts = read_json("posts.json")
if posts is None:
    sys.exit("posts.json missing. Run prepare.py first.")
analytics = read_json("analytics.json", {"window": {}, "daily": [], "followers": [], "top": [], "audience": {},
                                         "impressions": 0, "members_reached": 0, "engagements": 0, "total_followers": 0, "new_followers": 0})
pm = read_json("post_metrics.json", {"collected": None, "posts": []})
series = read_json(cfg["series"]["slug"] + ".json", {"summary": {"posts": 0}, "posts": []})

# Let the analytics window cover every post that has metrics, so weekly runs without a
# fresh xlsx still show new posts in the performance layer.
dates = [m["date"] for m in pm["posts"] if m.get("date")]
if dates:
    w = analytics.setdefault("window", {})
    w["start"] = min([w.get("start") or dates[0]] + dates)
    w["end"] = max([w.get("end") or dates[-1]] + dates)

# Snapshot history: every dated collection, so the dashboard can draw reach over time per post.
import glob
history = {}
snap_files = sorted(glob.glob(wpath("snapshots", "post_metrics_*.json")))
for sf in snap_files:
    snap = json.load(open(sf, encoding="utf-8"))
    day = snap.get("collected") or os.path.basename(sf)[13:23]
    for m in snap["posts"]:
        if m.get("impressions") is None:
            continue
        history.setdefault(m["id"], {})[day] = [m["impressions"], m.get("saves") or 0, m.get("social_engagements") or 0, m.get("comments") or 0, m.get("reposts") or 0]
history = {k: [[d, *v] for d, v in sorted(rows.items())] for k, rows in history.items()}

by_id = {m["id"]: m for m in pm["posts"]}
explorer = []
for p in posts["posts"]:
    mm = re.search(r"\d{19}", p.get("url") or "")
    m = by_id.get(mm.group()) if mm else None
    if not m or m.get("impressions") is None:
        continue
    explorer.append({"id": m["id"], "d": p["d"], "url": p["url"], "text": p["text"], "len": p["len"], "words": p["words"], "tags": p["tags"],
                     "imp": m["impressions"], "eng": m["social_engagements"], "react": m["reactions"], "com": m["comments"], "rep": m["reposts"],
                     "sav": m["saves"], "snd": m["sends"], "fol": m["followers_gained"], "pv": m["profile_viewers"], "media": m.get("media", "text"), "images": m.get("images", 0)})
explorer.sort(key=lambda r: r["d"], reverse=True)


# Config values reach a page that is meant to be published. Each substitution site has a
# context (HTML text or JavaScript source) and needs the escaping for that context, never
# the other one. Placeholders ending in _JS_ expand to a complete JS literal, quotes included,
# so the template never wraps one in quotes of its own.
def inline(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def js_literal(s):
    """A complete JS string literal. '</' is broken up so it cannot close the <script> block."""
    return json.dumps(s, ensure_ascii=False).replace("</", "<\\/")


def html_text(s):
    return html.escape(str(s))


def topics_js():
    # [['Name', new RegExp('pattern', 'i')], ...] as a JS literal
    items = [f"[{js_literal(t['name'])}, new RegExp({js_literal(t['pattern'])}, 'i')]" for t in cfg["topics"]]
    return "[" + ", ".join(items) + "]"


def fill(html_src):
    return (html_src.replace("__TZ_JS__", js_literal(cfg["timezone"]))
                    .replace("__SERIES_NAME_JS__", js_literal(cfg["series"]["name"]))
                    .replace("__SERIES_INCLUDE_JS__", js_literal(cfg["series"]["include"]))
                    .replace("__SERIES_SLUG_JS__", js_literal(cfg["series"]["slug"]))
                    .replace("__TZ_LABEL__", html_text(cfg["timezone_label"]))
                    .replace("__SERIES_NAME__", html_text(cfg["series"]["name"]))
                    .replace("__TOPICS_ALL__", topics_js()))


os.makedirs(out_dir, exist_ok=True)
pr = fill(open(os.path.join(tpl_dir, "posting-record.html"), encoding="utf-8").read())
pr = pr.replace("__POSTS__", inline(posts)).replace("__ANALYTICS__", inline(analytics)).replace("__PM__", inline(pm)).replace("__SERIES__", inline(series)).replace("__HISTORY__", inline({"snapshots": [os.path.basename(f)[13:23] for f in snap_files], "posts": history}))
open(os.path.join(out_dir, "posting-record.html"), "w", encoding="utf-8").write(pr)
ex = fill(open(os.path.join(tpl_dir, "post-explorer.html"), encoding="utf-8").read()).replace("__EXPLORER__", inline(explorer))
open(os.path.join(out_dir, "post-explorer.html"), "w", encoding="utf-8").write(ex)
left = [m for m in re.findall(r"__[A-Z_]+__", pr + ex)]
if left:
    print("warning: unfilled placeholders:", sorted(set(left)), file=sys.stderr)
print(f"built dist/posting-record.html ({len(pr)//1024} KB) and dist/post-explorer.html ({len(ex)//1024} KB) "
      f"from {len(posts['posts'])} posts, {len(pm['posts'])} with metrics, {len(series['posts'])} series editions, {len(snap_files)} snapshots, at {datetime.now():%Y-%m-%d %H:%M}")
