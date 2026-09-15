"""Build posting-record.sqlite: every post joined to its metrics, history, topics and the account-level analytics,
in one file an LLM (or anyone with sqlite3) can query.

Usage: python3 db.py [--export <data export folder or zip>] [--out posting-record.sqlite]
Runs in the workspace (current directory, or PR_WORKSPACE). Reads:
  config.json, posts.json, post_metrics.json, snapshots/post_metrics_*.json, analytics.json, <series slug>.json
  Comments_*.csv from the data export (own comments on own posts, for the link-in-comment flag); found
  automatically in the workspace, its parent, or the folder named in posts.json meta.export_dir.
Writes posting-record.sqlite (rebuilt from scratch every run) and posting-record.schema.md (column docs + example queries).

The file has raw tables (posts, post_metrics, post_metrics_snapshots, post_topics, daily_stats, followers_daily,
audience, linkedin_top_posts, series_editions, engagement_given), precomputed stats tables (medians by length band,
weekday, topic, media, quarter, and the same within length bands; Spearman correlations; top quartile vs rest),
a schema_notes table describing every column, and views (v_posts, v_reach_curve, v_growth, v_series).
Medians are precomputed because SQLite has no median; the stats tables are what the dashboards show.
"""
import csv, glob, json, math, os, re, sqlite3, sys
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import load_config, read_json, wpath, workspace

args = sys.argv[1:]
def opt(flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default
export_hint = opt("--export")
out_name = opt("--out", "posting-record.sqlite")

cfg = load_config()
ws = workspace()
posts_doc = read_json("posts.json")
if posts_doc is None:
    sys.exit("posts.json missing. Run prepare.py first.")
pm = read_json("post_metrics.json", {"collected": None, "posts": []})
analytics = read_json("analytics.json", {})
series = read_json(cfg["series"]["slug"] + ".json", {"summary": {}, "posts": []})

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(cfg["timezone"])
except Exception:
    print(f"note: timezone {cfg['timezone']} unavailable, local times fall back to UTC", file=sys.stderr)
    TZ = timezone.utc

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BANDS = [(0, 300, "<300"), (300, 1000, "300-1k"), (1000, 2000, "1k-2k"), (2000, 10**9, "2k+")]
BAND_ORDER = {b[2]: i for i, b in enumerate(BANDS)}
METRICS = ["impressions", "social_engagements", "reactions", "comments", "reposts", "saves", "sends",
           "profile_viewers", "followers_gained", "link_visits", "video_views"]


def band(chars):
    return next(name for lo, hi, name in BANDS if lo <= chars < hi)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 8 or len({p[0] for p in pairs}) < 2 or len({p[1] for p in pairs}) < 2:
        return None, len(pairs)
    rx, ry = rank([p[0] for p in pairs]), rank([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return (num / den if den else None), len(pairs)


def per1k(v, imp):
    return round(v / imp * 1000, 3) if v is not None and imp else None


# ---------- own comments from the data export (link placed in a comment on the post) ----------
def find_comments_csv():
    cands = []
    if export_hint:
        cands.append(export_hint)
    cands += [ws, os.path.dirname(ws)]
    ed = (posts_doc.get("meta") or {}).get("export_dir")
    if ed:
        cands += [os.path.join(os.path.dirname(ws), ed), os.path.join(ws, ed)]
    for c in cands:
        if os.path.isfile(c) and c.lower().endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(c) as z:
                for n in z.namelist():
                    if os.path.basename(n).startswith("Comments_") and n.endswith(".csv"):
                        return z.open(n).read().decode("utf-8", "replace")
        elif os.path.isdir(c):
            hits = glob.glob(os.path.join(c, "Comments_*.csv"))
            if hits:
                return open(hits[0], encoding="utf-8", errors="replace").read()
    return None


own_comments = {}  # key (activity or post id) -> {"count": n, "with_link": n, "first_link_at": ts}
_raw = find_comments_csv()
if _raw:
    import io
    for r in csv.DictReader(io.StringIO(_raw)):
        m = re.search(r"\d{19}", unquote(r.get("Link") or ""))
        if not m:
            continue
        msg = r.get("Message") or ""
        e = own_comments.setdefault(m.group(), {"count": 0, "with_link": 0})
        e["count"] += 1
        if re.search(r"https?://|lnkd\.in/", msg):
            e["with_link"] += 1
else:
    print("note: no Comments_*.csv found; has_link_in_own_comment stays NULL (pass --export <folder or zip>)", file=sys.stderr)

# ---------- posts ----------
by_id = {m["id"]: m for m in pm["posts"]}
act_to_id = {m["activity"]: m["id"] for m in pm["posts"] if m.get("activity")}
top_ids = {t["id"] for t in analytics.get("top", [])}
series_by_url = {s["url"]: s for s in series.get("posts", [])}
inc = re.compile(cfg["series"]["include"], re.I)
exc = re.compile(cfg["series"]["exclude"], re.I)
topics = [(t["name"], re.compile(t["pattern"], re.I)) for t in cfg["topics"]]

posts, post_topics, seen = [], [], set()
for p in posts_doc["posts"]:
    m = re.search(r"\d{19}", p.get("url") or "")
    pid = m.group() if m else None
    if not pid or pid in seen:  # the export repeats some old posts verbatim; keep one row per id
        continue
    seen.add(pid)
    met = by_id.get(pid)
    activity = (met or {}).get("activity") or p.get("activity")
    if activity and activity not in act_to_id:
        act_to_id[activity] = pid
    utc = datetime.strptime(p["d"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    loc = utc.astimezone(TZ)
    text = p["text"] or ""
    first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    paragraphs = len([b for b in re.split(r"\n\s*\n", text) if b.strip()])
    tags = p.get("tags") or []
    oc = own_comments.get(activity or "") or own_comments.get(pid)
    has_link_text = 1 if re.search(r"https?://|lnkd\.in/", text) or p.get("link") else 0
    is_series = 1 if inc.search(text) and not exc.search(text) else 0
    se = series_by_url.get(p["url"])
    media = (met or {}).get("media") or ("attachment" if p.get("media") else "text")
    posts.append({
        "id": pid, "activity": activity, "published_utc": utc.strftime("%Y-%m-%d %H:%M:%S"),
        "published_local": loc.strftime("%Y-%m-%d %H:%M:%S"), "date_local": loc.strftime("%Y-%m-%d"),
        "year": loc.year, "quarter": f"{loc.year}-Q{(loc.month - 1) // 3 + 1}", "month": loc.strftime("%Y-%m"),
        "iso_week": f"{loc.isocalendar()[0]}-W{loc.isocalendar()[1]:02d}",
        "weekday": WEEKDAYS[loc.weekday()], "weekday_num": loc.weekday() + 1, "is_weekend": 1 if loc.weekday() >= 5 else 0,
        "hour_local": loc.hour, "url": p["url"], "text": text, "first_line": first[:300],
        "chars": p["len"], "words": p["words"], "paragraphs": paragraphs, "length_band": band(p["len"]),
        "opens_with_number": 1 if re.match(r"^\W*\d", first) else 0, "opens_with_question": 1 if first.rstrip().endswith("?") else 0,
        "hashtags": ",".join(tags), "hashtag_count": len(tags),
        "has_link_in_text": has_link_text,
        "has_link_in_own_comment": (1 if oc and oc["with_link"] else 0) if _raw and activity else None,
        "own_comments": oc["count"] if oc else (0 if _raw and activity else None),
        "media": media, "images": (met or {}).get("images") or 0,
        "is_series": is_series, "series_n": se["n"] if se else None,
        "in_linkedin_top50": 1 if pid in top_ids else 0, "has_metrics": 1 if met and met.get("impressions") is not None else 0,
    })
    for name, rx in topics:
        if rx.search(text):
            post_topics.append((pid, name))
posts.sort(key=lambda r: r["published_utc"], reverse=True)
post_by_id = {r["id"]: r for r in posts}

# ---------- metrics, latest and snapshots ----------
def enrich(m, collected):
    imp = m.get("impressions")
    row = {"post_id": m["id"], "collected": collected}
    for k in METRICS:
        row[k] = m.get(k)
    row["engagement_rate"] = round(m.get("social_engagements", 0) / imp, 5) if imp else None
    for k in ("saves", "sends", "comments", "reposts", "reactions", "followers_gained", "profile_viewers", "link_visits"):
        row[k + "_per_1k"] = per1k(m.get(k), imp)
    p = post_by_id.get(m["id"])
    if p and collected:
        pub = datetime.strptime(p["published_utc"], "%Y-%m-%d %H:%M:%S").date()
        row["days_since_publish"] = (datetime.strptime(collected[:10], "%Y-%m-%d").date() - pub).days
    else:
        row["days_since_publish"] = None
    return row


latest = [enrich(m, pm.get("collected")) for m in pm["posts"] if m.get("impressions") is not None and m["id"] in post_by_id]
imps = sorted(r["impressions"] for r in latest)
if imps:
    q1, q2, q3 = pct(imps, .25), pct(imps, .5), pct(imps, .75)
    for r in latest:
        v = r["impressions"]
        r["impressions_quartile"] = 4 if v >= q3 else 3 if v >= q2 else 2 if v >= q1 else 1
        r["is_top_quartile"] = 1 if v >= q3 else 0
        r["rank_by_impressions"] = None
    for i, r in enumerate(sorted(latest, key=lambda r: -r["impressions"]), 1):
        r["rank_by_impressions"] = i

snapshots = []
snap_files = sorted(f for f in glob.glob(wpath("snapshots", "post_metrics_*.json")))
for sf in snap_files:
    snap = json.load(open(sf, encoding="utf-8"))
    day = snap.get("collected") or os.path.basename(sf)[13:23]
    for m in snap["posts"]:
        if m.get("impressions") is not None and m["id"] in post_by_id:
            snapshots.append(enrich(m, day))
if not snapshots and latest:  # first run before weekly.py ever wrote a snapshot
    snapshots = [dict(r) for r in latest]

# ---------- stats (medians, precomputed; mirrors the dashboards) ----------
metrics_by_post = {r["post_id"]: r for r in latest}
scored = [(post_by_id[r["post_id"]], r) for r in latest]
topic_of = {}
for pid, t in post_topics:
    topic_of.setdefault(pid, []).append(t)


def summarize(rows):
    imp = [r["impressions"] for _, r in rows]
    return {
        "n": len(rows),
        "median_impressions": median(imp), "p25_impressions": pct(imp, .25), "p75_impressions": pct(imp, .75),
        "max_impressions": max(imp) if imp else None, "total_impressions": sum(imp),
        "share_over_10k": round(sum(1 for v in imp if v >= 10000) / len(imp), 3) if imp else None,
        "median_engagement_rate": median([r["engagement_rate"] for _, r in rows]),
        "median_saves_per_1k": median([r["saves_per_1k"] for _, r in rows]),
        "median_sends_per_1k": median([r["sends_per_1k"] for _, r in rows]),
        "median_comments_per_1k": median([r["comments_per_1k"] for _, r in rows]),
        "median_reposts_per_1k": median([r["reposts_per_1k"] for _, r in rows]),
        "median_followers_gained": median([r["followers_gained"] for _, r in rows]),
        "median_chars": median([p["chars"] for p, _ in rows]),
    }


def grouped(keyfn, multi=False):
    g = {}
    for p, r in scored:
        keys = keyfn(p) if multi else [keyfn(p)]
        for k in keys:
            if k is not None:
                g.setdefault(k, []).append((p, r))
    return [{"group": k, **summarize(v)} for k, v in g.items()]


stats = {
    "stats_by_length_band": sorted(grouped(lambda p: p["length_band"]), key=lambda r: BAND_ORDER[r["group"]]),
    "stats_by_weekday": sorted(grouped(lambda p: p["weekday"]), key=lambda r: WEEKDAYS.index(r["group"])),
    "stats_by_hour": sorted(grouped(lambda p: p["hour_local"]), key=lambda r: r["group"]),
    "stats_by_media": sorted(grouped(lambda p: p["media"]), key=lambda r: -r["n"]),
    "stats_by_topic": sorted(grouped(lambda p: topic_of.get(p["id"]) or ["(no topic)"], multi=True), key=lambda r: -r["n"]),
    "stats_by_quarter": sorted(grouped(lambda p: p["quarter"]), key=lambda r: r["group"]),
    "stats_by_month": sorted(grouped(lambda p: p["month"]), key=lambda r: r["group"]),
    "stats_by_link_placement": grouped(lambda p: "link in text" if p["has_link_in_text"] else "link in own comment" if p["has_link_in_own_comment"] else "no link"),
    "stats_series_vs_rest": grouped(lambda p: cfg["series"]["name"] if p["is_series"] else "other posts"),
}
# the same, within length bands (length confounds everything)
def within_bands(keyfn, multi=False):
    out = []
    for lo, hi, name in BANDS:
        sub = [(p, r) for p, r in scored if p["length_band"] == name]
        g = {}
        for p, r in sub:
            for k in (keyfn(p) if multi else [keyfn(p)]):
                if k is not None:
                    g.setdefault(k, []).append((p, r))
        out += [{"length_band": name, "group": k, **summarize(v)} for k, v in g.items()]
    return out


stats["stats_by_weekday_within_band"] = within_bands(lambda p: p["weekday"])
stats["stats_by_topic_within_band"] = within_bands(lambda p: topic_of.get(p["id"]) or ["(no topic)"], multi=True)
stats["stats_by_media_within_band"] = within_bands(lambda p: p["media"])
stats["stats_by_link_placement_within_band"] = within_bands(lambda p: "link in text" if p["has_link_in_text"] else "link in own comment" if p["has_link_in_own_comment"] else "no link")

# top quartile vs rest: which engagement type travels with reach
top = [(p, r) for p, r in scored if r.get("is_top_quartile")]
rest = [(p, r) for p, r in scored if not r.get("is_top_quartile")]
tq = []
for k in ("saves", "sends", "comments", "reposts", "reactions", "followers_gained", "profile_viewers"):
    a, b = median([r[k + "_per_1k"] for _, r in top]), median([r[k + "_per_1k"] for _, r in rest])
    tq.append({"metric": k + "_per_1k", "top_quartile_median": a, "rest_median": b, "ratio": round(a / b, 2) if a and b else None, "n_top": len(top), "n_rest": len(rest)})
tq.append({"metric": "chars", "top_quartile_median": median([p["chars"] for p, _ in top]), "rest_median": median([p["chars"] for p, _ in rest]), "ratio": None, "n_top": len(top), "n_rest": len(rest)})
tq.append({"metric": "paragraphs", "top_quartile_median": median([p["paragraphs"] for p, _ in top]), "rest_median": median([p["paragraphs"] for p, _ in rest]), "ratio": None, "n_top": len(top), "n_rest": len(rest)})

# Spearman correlations: features vs outcomes
features = {
    "chars": lambda p: p["chars"], "words": lambda p: p["words"], "paragraphs": lambda p: p["paragraphs"],
    "hour_local": lambda p: p["hour_local"], "is_friday": lambda p: 1 if p["weekday"] == "Friday" else 0,
    "is_weekend": lambda p: p["is_weekend"], "has_link_in_text": lambda p: p["has_link_in_text"],
    "has_link_in_own_comment": lambda p: p["has_link_in_own_comment"], "hashtag_count": lambda p: p["hashtag_count"],
    "opens_with_number": lambda p: p["opens_with_number"], "opens_with_question": lambda p: p["opens_with_question"],
    "is_series": lambda p: p["is_series"], "images": lambda p: p["images"],
    "media_image": lambda p: 1 if p["media"] == "image" else 0, "media_video": lambda p: 1 if p["media"] == "video" else 0,
    "media_document": lambda p: 1 if p["media"] == "document" else 0, "media_poll": lambda p: 1 if p["media"] == "poll" else 0,
}
for name, _ in topics:
    features["topic:" + name] = (lambda n: lambda p: 1 if n in (topic_of.get(p["id"]) or []) else 0)(name)
outcomes = {"impressions": lambda r: r["impressions"], "engagement_rate": lambda r: r["engagement_rate"],
            "saves_per_1k": lambda r: r["saves_per_1k"], "sends_per_1k": lambda r: r["sends_per_1k"],
            "comments_per_1k": lambda r: r["comments_per_1k"], "followers_gained": lambda r: r["followers_gained"]}
corr = []
for fname, ff in features.items():
    for oname, of in outcomes.items():
        rho, n = spearman([ff(p) for p, _ in scored], [of(r) for _, r in scored])
        corr.append({"feature": fname, "outcome": oname, "spearman": round(rho, 3) if rho is not None else None, "n": n})

# ---------- account-level ----------
posts_per_day = {}
for p in posts:
    posts_per_day[p["date_local"]] = posts_per_day.get(p["date_local"], 0) + 1
fol = {f["d"]: f["new"] for f in analytics.get("followers", [])}
daily = [{"date": d["d"], "impressions": d.get("imp"), "engagements": d.get("eng"), "new_followers": fol.get(d["d"]),
          "posts_published": posts_per_day.get(d["d"], 0)} for d in analytics.get("daily", [])]
followers_daily = [{"date": f["d"], "new_followers": f["new"]} for f in analytics.get("followers", [])]


def pct_num(s):
    s = (s or "").strip()
    if s.startswith("<"):
        return 0.5
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


audience = []
for scope, key in (("followers", "audience"), ("content_viewers", "content_audience")):
    for dim, rows in (analytics.get(key) or {}).items():
        for i, r in enumerate(rows, 1):
            audience.append({"scope": scope, "dimension": dim, "rank": i, "value": r["v"], "pct": pct_num(r["pct"]), "pct_text": r["pct"]})
top_posts = [{"post_id": t["id"], "url": t["url"], "date": t["d"], "impressions": t.get("imp"), "engagements": t.get("eng")} for t in analytics.get("top", [])]

meta = posts_doc.get("meta") or {}
given = []
for kind, key in (("reposts", "reposts_per_year"), ("comments", "comments_given_per_year"), ("reactions", "reactions_given_per_year")):
    for y, n in (meta.get(key) or {}).items():
        given.append({"year": int(y), "kind": kind, "count": n})
for t, n in (meta.get("reactions_given_by_type") or {}).items():
    given.append({"year": None, "kind": "reaction_type:" + t, "count": n})

series_rows = []
for s in series.get("posts", []):
    m = re.search(r"\d{19}", s["url"])
    series_rows.append({"n": s["n"], "post_id": m.group() if m else None, "date": s["date"], "iso_week": s["iso_week"], "weekday": s["weekday"],
                        "title": s["title"], "chars": s["chars"], "words": s["words"], "impressions": s.get("impressions"),
                        "engagements": s.get("engagements"), "reactions": s.get("reactions"), "comments": s.get("comments"),
                        "reposts": s.get("reposts"), "saves": s.get("saves"), "sends": s.get("sends"),
                        "followers_gained": s.get("followers_gained"), "media": s.get("media"), "in_linkedin_top50": 1 if s.get("in_linkedin_top50") else 0, "url": s["url"]})

# ---------- schema notes (the docs live in the DB too) ----------
NOTES = {
    "posts": ("One row per post you authored (from the data export plus posts discovered by the weekly run). Text and timing; no performance numbers here.", {
        "id": "LinkedIn share or ugcPost id (19 digits). Primary key. The publish time is encoded in it (id >> 22 = ms since epoch).",
        "activity": "Activity id used by the analytics page. Stable key for the collector. NULL for posts never collected.",
        "published_utc": "Publish time, UTC.", "published_local": f"Publish time in {cfg['timezone']}.",
        "date_local": "Local publish date YYYY-MM-DD.", "year": "Local year.", "quarter": "Local quarter, e.g. 2026-Q3.", "month": "Local month YYYY-MM.",
        "iso_week": "ISO week, e.g. 2026-W37.", "weekday": "Monday..Sunday, local.", "weekday_num": "1 = Monday .. 7 = Sunday.",
        "is_weekend": "1 if Saturday or Sunday.", "hour_local": "Local hour 0-23.", "url": "Post URL.", "text": "Full post text with newlines.",
        "first_line": "First non-empty line (what shows before 'see more').", "chars": "Character count.", "words": "Word count.",
        "paragraphs": "Blank-line separated blocks.", "length_band": "<300, 300-1k, 1k-2k, 2k+ characters. Use it to control for length before comparing anything else.",
        "opens_with_number": "1 if the first line starts with a digit.", "opens_with_question": "1 if the first line ends with '?'.",
        "hashtags": "Comma-separated lowercase hashtags.", "hashtag_count": "Number of hashtags.",
        "has_link_in_text": "1 if the body contains a URL or lnkd.in link (or the export flagged a shared URL).",
        "has_link_in_own_comment": "1 if you commented on the post with a link (from the export's Comments file). NULL when the export was not found or the post has no activity id.",
        "own_comments": "How many comments you left on your own post.",
        "media": "text, image, video, document, poll (from the collector); 'attachment' when the export flagged media but the post was never collected.",
        "images": "Distinct image assets detected (0 for non-image posts).",
        "is_series": f"1 if the text matches the series rule ({cfg['series']['name']}).", "series_n": "Edition number within the series, NULL otherwise.",
        "in_linkedin_top50": "1 if LinkedIn's analytics export listed it among the top 50.", "has_metrics": "1 if post_metrics has a row for it.",
    }),
    "post_metrics": ("Latest collected numbers per post (one row per post that has been collected). Join to posts on post_id = posts.id. Rates are per 1,000 impressions.", {
        "post_id": "posts.id", "collected": "Date of the latest collection YYYY-MM-DD.", "impressions": "Impressions shown by LinkedIn's post analytics page.",
        "social_engagements": "Reactions + comments + reposts as LinkedIn counts them.", "reactions": "", "comments": "", "reposts": "", "saves": "", "sends": "Sends on LinkedIn (private shares).",
        "profile_viewers": "Profile views attributed to the post.", "followers_gained": "Followers attributed to the post.", "link_visits": "Clicks on links in the post.",
        "video_views": "NULL unless a video post.", "engagement_rate": "social_engagements / impressions.",
        "saves_per_1k": "saves / impressions * 1000. The metric that separates top posts in this account.", "sends_per_1k": "", "comments_per_1k": "", "reposts_per_1k": "",
        "reactions_per_1k": "", "followers_gained_per_1k": "", "profile_viewers_per_1k": "", "link_visits_per_1k": "",
        "days_since_publish": "Age of the post at collection, days.", "impressions_quartile": "1 (bottom 25%) .. 4 (top 25%) among all collected posts.",
        "is_top_quartile": "1 if impressions_quartile = 4.", "rank_by_impressions": "1 = highest impressions.",
    }),
    "post_metrics_snapshots": ("Every dated collection of every post; one row per (post, collection date). Use it for reach curves and week-over-week growth. Same columns as post_metrics minus quartile and rank.", {
        "post_id": "posts.id", "collected": "Collection date.", "days_since_publish": "Post age at that collection.",
    }),
    "post_topics": ("Topic tags from the regexes in config.json; a post can have several rows or none.", {"post_id": "posts.id", "topic": "Topic name."}),
    "topics": ("The topic rules.", {"name": "Topic name.", "pattern": "Case-insensitive regex applied to the post text."}),
    "daily_stats": ("Account-level daily series from the analytics xlsx (365-day window), plus how many posts were published that day.", {
        "date": "YYYY-MM-DD", "impressions": "All impressions that day, all posts.", "engagements": "", "new_followers": "", "posts_published": "Posts you published that local day.",
    }),
    "followers_daily": ("New followers per day from the analytics xlsx.", {"date": "", "new_followers": ""}),
    "audience": ("Audience demographics from the analytics xlsx.", {
        "scope": "'followers' (who follows you) or 'content_viewers' (who saw your content in the window).",
        "dimension": "Company, Location, Company size, Seniority, Job title, Industry.", "rank": "Order within the dimension.", "value": "",
        "pct": "Share in percent; 0.5 stands for LinkedIn's '< 1%'.", "pct_text": "As exported.",
    }),
    "linkedin_top_posts": ("The top posts as listed by LinkedIn's analytics export (two top-50 lists, by impressions and by engagements, merged).", {
        "post_id": "posts.id", "url": "", "date": "", "impressions": "", "engagements": "",
    }),
    "series_editions": (f"The {cfg['series']['name']} series, one row per edition, from series.py.", {
        "n": "Edition number.", "post_id": "posts.id", "title": "Working title (first sentence after the series marker).", "in_linkedin_top50": "",
    }),
    "engagement_given": ("Engagement you gave to others per year (reposts, comments, reactions) and reactions by type, from the data export.", {
        "year": "NULL for the reaction-type totals.", "kind": "reposts, comments, reactions, or reaction_type:<TYPE>.", "count": "",
    }),
    "stats_by_length_band": ("Medians per length band across collected posts. This is the strongest single split; read it first.", {}),
    "stats_by_weekday": ("Medians per local weekday. Confounded by length; see stats_by_weekday_within_band.", {}),
    "stats_by_hour": ("Medians per local hour. Small n per cell; treat as noise unless n >= 8.", {}),
    "stats_by_media": ("Medians per media type.", {}),
    "stats_by_topic": ("Medians per topic; a post counts in every topic it matches.", {}),
    "stats_by_quarter": ("Medians per local quarter of publishing.", {}),
    "stats_by_month": ("Medians per local month of publishing.", {}),
    "stats_by_link_placement": ("link in text vs link in own comment vs no link. Raw split; the within-band table is the one to cite.", {}),
    "stats_series_vs_rest": ("Series editions against everything else.", {}),
    "stats_by_weekday_within_band": ("Weekday medians inside each length band. Cite these, not the raw weekday table.", {}),
    "stats_by_topic_within_band": ("Topic medians inside each length band.", {}),
    "stats_by_media_within_band": ("Media medians inside each length band.", {}),
    "stats_by_link_placement_within_band": ("Link placement inside each length band.", {}),
    "stats_top_quartile_vs_rest": ("Median per-1k engagement of top-quartile posts (by impressions) against the rest. ratio > 1.3 means that engagement type travels with reach.", {
        "metric": "", "top_quartile_median": "", "rest_median": "", "ratio": "top / rest", "n_top": "", "n_rest": "",
    }),
    "correlations": ("Spearman rank correlation of each post feature with each outcome, across collected posts. Direction, not cause. NULL when n < 8 or a feature is constant.", {
        "feature": "Post feature (chars, is_friday, media_image, topic:<name>, ...).", "outcome": "impressions, engagement_rate, saves_per_1k, sends_per_1k, comments_per_1k, followers_gained.", "spearman": "-1..1", "n": "Posts used.",
    }),
    "meta": ("Build facts: dates, counts, window, timezone, profile.", {"key": "", "value": ""}),
}
STATS_COLS = {
    "group": "Group value (band, weekday, topic, media, quarter, ...).", "length_band": "Length band the row is restricted to.",
    "n": "Posts in the group. Fade anything under 4.", "median_impressions": "", "p25_impressions": "", "p75_impressions": "", "max_impressions": "",
    "total_impressions": "", "share_over_10k": "Share of posts with 10k+ impressions.", "median_engagement_rate": "", "median_saves_per_1k": "",
    "median_sends_per_1k": "", "median_comments_per_1k": "", "median_reposts_per_1k": "", "median_followers_gained": "", "median_chars": "",
}

# ---------- write ----------
out = wpath(out_name)
# Build in a temp dir: SQLite needs file locks, which network and bridge mounts do not always give.
import shutil, tempfile
tmpdir = tempfile.mkdtemp(prefix="pr-db-")
tmp = os.path.join(tmpdir, os.path.basename(out))
con = sqlite3.connect(tmp)
cur = con.cursor()


def create(name, rows, pk=None, cols=None):
    if not rows and not cols:
        return
    cols = cols or list(rows[0].keys())
    def typ(c):
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if not vals:
            return "TEXT"
        if all(isinstance(v, bool) or isinstance(v, int) for v in vals):
            return "INTEGER"
        if all(isinstance(v, (int, float)) for v in vals):
            return "REAL"
        return "TEXT"
    defs = ", ".join(f'"{c}" {typ(c)}' for c in cols)
    if pk:
        defs += f", PRIMARY KEY ({', '.join(pk)})"
    cur.execute(f'CREATE TABLE "{name}" ({defs})')
    cur.executemany(f'INSERT OR REPLACE INTO "{name}" VALUES ({", ".join("?" * len(cols))})', [[r.get(c) for c in cols] for r in rows])


create("posts", posts, pk=["id"])
create("post_metrics", latest, pk=["post_id"])
snap_cols = [c for c in (latest[0].keys() if latest else []) if c not in ("impressions_quartile", "is_top_quartile", "rank_by_impressions")]
create("post_metrics_snapshots", snapshots, pk=["post_id", "collected"], cols=snap_cols or None)
create("post_topics", [{"post_id": a, "topic": b} for a, b in post_topics], pk=["post_id", "topic"])
create("topics", [{"name": t["name"], "pattern": t["pattern"]} for t in cfg["topics"]], pk=["name"])
create("daily_stats", daily, pk=["date"])
create("followers_daily", followers_daily, pk=["date"])
create("audience", audience, pk=["scope", "dimension", "rank"])
create("linkedin_top_posts", top_posts, pk=["post_id"])
create("series_editions", series_rows, pk=["n"])
create("engagement_given", given)
for name, rows in stats.items():
    create(name, rows)
create("stats_top_quartile_vs_rest", tq, pk=["metric"])
create("correlations", corr, pk=["feature", "outcome"])

meta_rows = [
    ("built_at", datetime.now().strftime("%Y-%m-%d %H:%M")), ("profile", cfg["profile"]), ("timezone", cfg["timezone"]),
    ("series_name", cfg["series"]["name"]), ("posts", len(posts)), ("posts_with_metrics", len(latest)),
    ("first_post", posts[-1]["date_local"] if posts else None), ("latest_post", posts[0]["date_local"] if posts else None),
    ("metrics_collected", pm.get("collected")), ("snapshots", len(snap_files)), ("snapshot_dates", ",".join(os.path.basename(f)[13:23] for f in snap_files)),
    ("analytics_window_start", (analytics.get("window") or {}).get("start")), ("analytics_window_end", (analytics.get("window") or {}).get("end")),
    ("analytics_impressions", analytics.get("impressions")), ("analytics_members_reached", analytics.get("members_reached")),
    ("analytics_engagements", analytics.get("engagements")), ("total_followers", analytics.get("total_followers")), ("new_followers_in_window", analytics.get("new_followers")),
    ("impressions_q1", q1 if imps else None), ("impressions_median", q2 if imps else None), ("impressions_q3", q3 if imps else None),
    ("comments_csv_found", 1 if _raw else 0),
]
for k, v in (series.get("summary") or {}).items():
    if not isinstance(v, (list, dict)):
        meta_rows.append(("series_" + k, v))
create("meta", [{"key": k, "value": v} for k, v in meta_rows], pk=["key"])

notes = []
for t, (desc, cols) in NOTES.items():
    notes.append({"table_name": t, "column_name": None, "description": desc})
    all_cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')] if cur.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() else []
    for c in all_cols:
        d = cols.get(c)
        if d is None and t.startswith("stats_"):
            d = STATS_COLS.get(c, "")
        if d is None and t == "post_metrics_snapshots":
            d = NOTES["post_metrics"][1].get(c, "")
        notes.append({"table_name": t, "column_name": c, "description": d or ""})
create("schema_notes", notes)

cur.executescript("""
CREATE VIEW v_posts AS
SELECT p.*, m.collected, m.impressions, m.social_engagements, m.reactions, m.comments, m.reposts, m.saves, m.sends,
       m.profile_viewers, m.followers_gained, m.link_visits, m.video_views, m.engagement_rate, m.saves_per_1k, m.sends_per_1k,
       m.comments_per_1k, m.reposts_per_1k, m.impressions_quartile, m.is_top_quartile, m.rank_by_impressions,
       (SELECT group_concat(topic, '; ') FROM post_topics t WHERE t.post_id = p.id) AS topics
FROM posts p LEFT JOIN post_metrics m ON m.post_id = p.id;

CREATE VIEW v_reach_curve AS
SELECT s.post_id, p.date_local, p.chars, p.length_band, p.weekday, s.collected, s.days_since_publish, s.impressions, s.saves, s.social_engagements
FROM post_metrics_snapshots s JOIN posts p ON p.id = s.post_id
ORDER BY s.post_id, s.collected;

CREATE VIEW v_growth AS
SELECT a.post_id, p.date_local, p.first_line, a.collected AS prev_collected, b.collected AS last_collected,
       a.impressions AS prev_impressions, b.impressions AS last_impressions, b.impressions - a.impressions AS impressions_delta,
       b.saves - a.saves AS saves_delta, b.social_engagements - a.social_engagements AS engagements_delta
FROM post_metrics_snapshots b
JOIN post_metrics_snapshots a ON a.post_id = b.post_id
  AND a.collected = (SELECT max(collected) FROM post_metrics_snapshots x WHERE x.post_id = b.post_id AND x.collected < b.collected)
JOIN posts p ON p.id = b.post_id
WHERE b.collected = (SELECT max(collected) FROM post_metrics_snapshots);

CREATE VIEW v_series AS
SELECT e.n, e.date, e.iso_week, e.title, e.chars, e.impressions, e.engagements, e.saves, e.sends, e.media, e.in_linkedin_top50, p.hour_local, p.first_line, e.url
FROM series_editions e LEFT JOIN posts p ON p.id = e.post_id ORDER BY e.n;
""")
con.commit()
con.close()

# ---------- schema markdown (same notes, for agents that read files rather than tables) ----------
con = sqlite3.connect(tmp)
md = [f"# {os.path.basename(out)}: schema and how to query it", "",
      f"Built {meta_rows[0][1]} from the workspace `{os.path.basename(ws)}`. {len(posts)} posts, {len(latest)} with metrics, {len(snap_files)} snapshot(s). "
      f"Times in `*_local` columns are {cfg['timezone']}. Every table and column is also described inside the file: `SELECT * FROM schema_notes`.", "",
      "## Rules for reading it", "",
      "Use medians, never means: reach is heavy-tailed. Every stats table carries `n`; treat cells under 4 posts as anecdotes. Length confounds day, topic, media and link placement, "
      "so cite the `*_within_band` tables when comparing those. `correlations` are Spearman and describe direction, not cause. `post_metrics` is the latest collection; "
      "`post_metrics_snapshots` keeps every collection, so growth and reach curves come from there. Per-1k rates are per 1,000 impressions.", "",
      "## Tables", ""]
for t, (desc, _) in NOTES.items():
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
        continue
    n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    md += [f"### {t} ({n} rows)", "", desc, ""]
    for _, col, d in con.execute("SELECT table_name, column_name, description FROM schema_notes WHERE table_name=? AND column_name IS NOT NULL", (t,)):
        md.append(f"- `{col}`" + (f": {d}" if d else ""))
    md.append("")
md += ["### Views", "",
       "- `v_posts`: one row per post with its latest metrics and topics joined. Start here for any per-post question.",
       "- `v_reach_curve`: every snapshot of every post with `days_since_publish`, for plotting reach over time.",
       "- `v_growth`: change per post between the last two collections.",
       "- `v_series`: series editions with local hour and first line.", "",
       "## Example queries", "",
       "```sql",
       "-- What separates the top quarter",
       "SELECT * FROM stats_top_quartile_vs_rest ORDER BY ratio DESC;",
       "",
       "-- Reach by length band (the strongest split)",
       "SELECT \"group\" AS length_band, n, median_impressions, median_saves_per_1k, share_over_10k FROM stats_by_length_band;",
       "",
       "-- Friday vs other weekdays, controlled for length",
       "SELECT length_band, \"group\" AS weekday, n, median_impressions FROM stats_by_weekday_within_band WHERE n >= 4 ORDER BY length_band, median_impressions DESC;",
       "",
       "-- Top 10 posts by saves per 1k with their text opening",
       "SELECT date_local, chars, impressions, saves_per_1k, first_line FROM v_posts WHERE impressions IS NOT NULL ORDER BY saves_per_1k DESC LIMIT 10;",
       "",
       "-- Median impressions for a topic and length band, computed on the fly (SQLite median via window functions)",
       "WITH x AS (SELECT v.impressions, row_number() OVER (ORDER BY v.impressions) rn, count(*) OVER () cnt",
       "           FROM v_posts v JOIN post_topics t ON t.post_id = v.id WHERE t.topic = 'SIEM' AND v.length_band = '2k+' AND v.impressions IS NOT NULL)",
       "SELECT avg(impressions) AS median_impressions, max(cnt) AS n FROM x WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2);",
       "",
       "-- Strongest correlates of reach",
       "SELECT feature, spearman, n FROM correlations WHERE outcome = 'impressions' AND spearman IS NOT NULL ORDER BY abs(spearman) DESC LIMIT 10;",
       "",
       "-- Which posts grew most since the previous collection",
       "SELECT date_local, impressions_delta, last_impressions, first_line FROM v_growth ORDER BY impressions_delta DESC LIMIT 10;",
       "",
       "-- Reach curve of one post",
       "SELECT collected, days_since_publish, impressions, saves FROM v_reach_curve WHERE post_id = '<id>';",
       "",
       "-- Posts like the one I am drafting: same topic, same band, last 12 months, with full text",
       "SELECT date_local, weekday, impressions, saves_per_1k, text FROM v_posts v JOIN post_topics t ON t.post_id = v.id",
       "WHERE t.topic = 'SOAR & automation' AND v.length_band = '1k-2k' AND v.impressions IS NOT NULL ORDER BY impressions DESC LIMIT 5;",
       "",
       "-- Cadence: posts per month, all time",
       "SELECT month, count(*) AS posts, sum(is_series) AS series_editions FROM posts GROUP BY month ORDER BY month;",
       "```", ""]
open(os.path.splitext(out)[0] + ".schema.md", "w", encoding="utf-8").write("\n".join(md))
con.close()
shutil.copyfile(tmp, out)
shutil.rmtree(tmpdir, ignore_errors=True)
print(f"built {os.path.basename(out)}: {len(posts)} posts, {len(latest)} with metrics, {len(snapshots)} snapshot rows over {len(snap_files)} snapshot(s), "
      f"{len(post_topics)} topic tags, {len(daily)} daily rows, {len(series_rows)} series editions, own comments {'found' if _raw else 'not found'}; "
      f"schema in {os.path.basename(os.path.splitext(out)[0])}.schema.md")
