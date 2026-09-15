"""Turn LinkedIn's AggregateAnalytics_*.xlsx into analytics.json for the Posting Record dashboard.

Usage: python3 analytics.py AggregateAnalytics_....xlsx
Writes analytics.json into the workspace (current directory, or PR_WORKSPACE).
Sheets read: DISCOVERY, ENGAGEMENT, TOP POSTS, FOLLOWERS, AUDIENCE DEMOGRAPHICS, CONTENT DEMOGRAPHICS.
Post IDs are the 19-digit activity ids, which match the ShareLink urns in the data export.
"""
import json, os, re, sys, warnings
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import wpath

warnings.filterwarnings("ignore")
import openpyxl

if len(sys.argv) < 2:
    sys.exit(__doc__)
src = sys.argv[1]
out = wpath("analytics.json")
wb = openpyxl.load_workbook(src, read_only=True, data_only=True)

def rows(name):
    return [r for r in wb[name].iter_rows(values_only=True)]

def iso(us):  # 9/14/2025 -> 2025-09-14
    return datetime.strptime(str(us), "%m/%d/%Y").strftime("%Y-%m-%d")

def num(v):
    try: return int(str(v).replace(",", ""))
    except (TypeError, ValueError): return None

disc = {str(r[0]): r[1] for r in rows("DISCOVERY") if r and r[0]}
period = str(disc.get("Overall Performance", ""))
m = re.match(r"(\S+) - (\S+)", period)
window = {"start": iso(m.group(1)), "end": iso(m.group(2))} if m else {}

daily = [{"d": iso(r[0]), "imp": num(r[1]), "eng": num(r[2])} for r in rows("ENGAGEMENT")[1:] if r and r[0]]

idr = re.compile(r"(\d{19})")
top = {}
for r in rows("TOP POSTS")[3:]:
    for url, date, val, key in ((r[0], r[1], r[2], "eng"), (r[4], r[5], r[6], "imp")):
        if not url: continue
        pid = idr.search(url).group(1)
        e = top.setdefault(pid, {"id": pid, "url": url, "d": iso(date)})
        e[key] = num(val)
top = sorted(top.values(), key=lambda e: -(e.get("imp") or 0))

frows = rows("FOLLOWERS")
total_followers = num(frows[0][1]) if frows else None
followers = [{"d": iso(r[0]), "new": num(r[1])} for r in frows[3:] if r and r[0] and r[1] is not None]

def demo(name):
    out = {}
    for r in rows(name)[1:]:
        if not r or not r[0]: continue
        out.setdefault(str(r[0]), []).append({"v": str(r[1]), "pct": str(r[2])})
    return out

data = {
    "generated": datetime.now().isoformat(timespec="seconds"),
    "source": os.path.basename(src),
    "window": window,
    "impressions": num(disc.get("Impressions")),
    "members_reached": num(disc.get("Members reached")),
    "engagements": sum(d["eng"] or 0 for d in daily),
    "total_followers": total_followers,
    "new_followers": sum(f["new"] or 0 for f in followers),
    "followers": followers,
    "daily": daily,
    "top": top,
    "audience": demo("AUDIENCE DEMOGRAPHICS"),
    "content_audience": demo("CONTENT DEMOGRAPHICS"),
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)
print(f"{len(daily)} days, {len(top)} top posts, {len(followers)} follower days -> {out}")
print(json.dumps({k: v for k, v in data.items() if not isinstance(v, (list, dict))}, indent=1))
