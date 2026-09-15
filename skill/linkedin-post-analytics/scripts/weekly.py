"""Weekly refresh: merge newly discovered posts and a fresh metrics collection, keep snapshots, rebuild.

Usage: python3 weekly.py collected.csv [discovered.json]
   or: python3 weekly.py posting-record-dump-<date>.txt     (the file PR.download() saves; split into runs/<today>/)
Runs in the workspace (current directory, or PR_WORKSPACE).

collected.csv   header: id,activity,impressions,social_engagements,reactions,comments,reposts,saves,sends,
                profile_viewers,followers_gained,link_visits,video_views[,media,images]
                One row per post, produced by the collector run in the browser.
discovered.json list of {"id","activity","url","text","media","images"} for posts not yet in posts.json.
                Text with real newlines. Publish time is derived from the id (LinkedIn ids embed ms since epoch).

What it does:
  1. Appends discovered posts to posts.json (dedup by id).
  2. Writes snapshots/post_metrics_<today>.json and replaces post_metrics.json with the merged latest view.
     Media type for known posts is carried over from the previous post_metrics.json when the CSV has none.
  3. Runs series.py, build.py, db.py (rebuilds posting-record.sqlite) and report.py (dist/posting-report.html).
"""
import csv, json, os, re, subprocess, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import id_time, workspace

scripts = os.path.dirname(os.path.abspath(__file__))
here = workspace()
if len(sys.argv) < 2:
    sys.exit(__doc__)
collected = sys.argv[1]
discovered = sys.argv[2] if len(sys.argv) > 2 else None
today = datetime.now().strftime("%Y-%m-%d")

# A raw dump file from PR.download() or copy(PR.dumpText()): split it into the two inputs.
_head = open(collected, encoding="utf-8", errors="replace").read(20000)
if "COLLECTED_START" in _head:
    raw = open(collected, encoding="utf-8").read()
    def block(tag):
        a, b = raw.find(tag + "_START"), raw.find(tag + "_END")
        return raw[a + len(tag) + 6:b].strip() if a >= 0 and b > a else ""
    # The dump usually sits in ~/Downloads. Split it into the workspace, never next to the
    # input: every script in this skill writes to the workspace and nowhere else.
    base = os.path.join(here, "runs", today, os.path.splitext(os.path.basename(collected))[0])
    os.makedirs(os.path.dirname(base), exist_ok=True)
    open(base + ".collected.csv", "w", encoding="utf-8").write(block("COLLECTED") + "\n")
    open(base + ".discovered.json", "w", encoding="utf-8").write(block("DISCOVERED") or "[]")
    collected, discovered = base + ".collected.csv", base + ".discovered.json"
    print(f"split dump into {collected} and {discovered}")

# 1. posts.json
posts_path = os.path.join(here, "posts.json")
posts = json.load(open(posts_path, encoding="utf-8"))
known = {m.group() for p in posts["posts"] if (m := re.search(r"\d{19}", p.get("url") or ""))}
_pm0 = os.path.join(here, "post_metrics.json")
known_acts = {m["activity"] for m in (json.load(open(_pm0, encoding="utf-8"))["posts"] if os.path.exists(_pm0) else [])}
added = 0
if discovered and os.path.exists(discovered):
    for d in json.load(open(discovered, encoding="utf-8")):
        if d["id"] in known or d.get("activity") in known_acts or not (d.get("text") or "").strip():
            continue
        text = d["text"].strip()
        posts["posts"].append({
            "d": id_time(d["id"]),
            "url": d.get("url") or f"https://www.linkedin.com/feed/update/urn:li:share:{d['id']}",
            "text": text, "len": len(text), "words": len(text.split()),
            "tags": sorted({t.lower() for t in re.findall(r"#(\w+)", text)}),
            "link": bool(re.search(r"lnkd\.in|https?://", text)), "media": d.get("media", "text") != "text",
        })
        known.add(d["id"]); added += 1
    posts["posts"].sort(key=lambda p: p["d"], reverse=True)
    posts["meta"]["authored"] = len(posts["posts"])
    json.dump(posts, open(posts_path, "w", encoding="utf-8"), ensure_ascii=False)

# 2. metrics snapshot
pm_path = os.path.join(here, "post_metrics.json")
prev = json.load(open(pm_path, encoding="utf-8")) if os.path.exists(pm_path) else {"posts": []}
prev_by_id = {m["id"]: m for m in prev["posts"]}
prev_by_act = {m["activity"]: m for m in prev["posts"]}
disc_by_id = {d["id"]: d for d in (json.load(open(discovered, encoding="utf-8")) if discovered and os.path.exists(discovered) else [])}
num = lambda v: int(v) if v not in ("", None) else None
rows = []
for r in csv.DictReader(open(collected, newline="", encoding="utf-8")):
    # The activity id is the stable key. The share id parsed from a post page can differ
    # from the one in the data export (quoted or reshared content), so a known activity wins.
    pid = prev_by_act.get(r["activity"], {}).get("id") or r["id"]
    if not pid or pid not in known:
        continue  # not one of our posts, or an activity we cannot map
    old = prev_by_id.get(pid, {}); d = disc_by_id.get(pid, {})
    media = r.get("media") or d.get("media") or old.get("media") or ("video" if num(r.get("video_views")) else "text")
    rows.append({
        "id": pid, "activity": r["activity"], "date": id_time(pid)[:10],
        "impressions": num(r["impressions"]), "social_engagements": num(r["social_engagements"]),
        "reactions": num(r["reactions"]), "comments": num(r["comments"]), "reposts": num(r["reposts"]),
        "saves": num(r["saves"]), "sends": num(r["sends"]), "profile_viewers": num(r["profile_viewers"]),
        "followers_gained": num(r["followers_gained"]), "link_visits": num(r.get("link_visits")), "video_views": num(r.get("video_views")),
        "media": media, "images": int(r.get("images") or d.get("images") or old.get("images") or (1 if media == "image" else 0)),
    })
collected_ids = {r["id"] for r in rows}
# posts not re-collected this week keep their last known figures
rows += [m for m in prev["posts"] if m["id"] not in collected_ids and m["id"] in known]
rows.sort(key=lambda m: m["date"], reverse=True)
snap = {"collected": today, "source": "LinkedIn post analytics pages, collected via browser", "posts": rows}
os.makedirs(os.path.join(here, "snapshots"), exist_ok=True)
json.dump({"collected": today, "posts": [r for r in rows if r["id"] in collected_ids]}, open(os.path.join(here, "snapshots", f"post_metrics_{today}.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(snap, open(pm_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(os.path.join(here, "post_metrics.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

print(f"posts.json: +{added} new posts ({len(posts['posts'])} total). metrics: {len(collected_ids)} collected today, {len(rows)} in latest view, snapshot saved.")

# 3. downstream
# check=False on the optional steps, but never silently: a failure here used to leave the
# previous run's dashboards and report in place while weekly.py still reported success,
# so a stale report could go unnoticed for weeks. Name what failed and exit non-zero.
def run(script, required=False, quiet=False):
    r = subprocess.run([sys.executable, os.path.join(scripts, script)],
                       cwd=here, stdout=subprocess.DEVNULL if quiet else None)
    if r.returncode == 0:
        return True
    msg = f"{script} failed (exit {r.returncode}); its output was NOT regenerated"
    if required:
        sys.exit(f"weekly: {msg}")
    print(f"weekly: {msg}", file=sys.stderr)
    return False

failed = [s for s in (("series.py", False, True), ("build.py", True, False),
                      ("db.py", False, False), ("report.py", False, False))
          if not run(s[0], required=s[1], quiet=s[2])]
if failed:
    sys.exit(f"weekly: finished with {len(failed)} failed step(s): {', '.join(f[0] for f in failed)}")
