"""Turn a LinkedIn data export into posts.json.

Usage: python3 prepare.py <export_dir_or_zip>
Writes posts.json into the workspace (current directory, or PR_WORKSPACE).
Reads Shares_*.csv, Comments_*.csv, Reactions_*.csv, InstantReposts_*.csv.
Keeps only shares with your own commentary (drops empty reshares).
"""
import csv, glob, json, os, re, sys, tempfile, zipfile
from collections import Counter
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import wpath

if len(sys.argv) < 2:
    sys.exit(__doc__)
export = sys.argv[1]
if export.lower().endswith(".zip") and zipfile.is_zipfile(export):
    tmp = tempfile.mkdtemp(prefix="li-export-")
    zipfile.ZipFile(export).extractall(tmp)
    export = tmp
out = wpath("posts.json")

def first(pattern):
    m = sorted(glob.glob(os.path.join(export, pattern)))
    return m[0] if m else None

def rows(path):
    if not path: return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def clean_line(line):
    # LinkedIn wraps every line of a multi-line post in its own quotes: 'a"\n"b"\n""\n"c'.
    line = line.rstrip()
    if line.startswith('"'): line = line[1:]
    if line.endswith('"'): line = line[:-1]
    return line.rstrip()

shares = rows(first("Shares_*.csv"))
if not shares:
    sys.exit(f"no Shares_*.csv found in {export}")
posts = []
for r in shares:
    text = (r.get("ShareCommentary") or "").strip()
    text = "\n".join(clean_line(l) for l in text.split("\n")).strip()
    if not text: continue
    posts.append({
        "d": r["Date"][:19],
        "url": r.get("ShareLink") or "",
        "text": text,
        "len": len(text),
        "words": len(text.split()),
        "tags": sorted({t.lower() for t in re.findall(r"#(\w+)", text)}),
        "link": bool(r.get("SharedUrl")),
        "media": bool(r.get("MediaUrl")),
    })
posts.sort(key=lambda p: p["d"], reverse=True)

def per_year(path, datecol="Date"):
    c = Counter()
    for r in rows(path):
        y = (r.get(datecol) or "")[:4]
        if y.isdigit(): c[y] += 1
    return dict(sorted(c.items()))

rtype = Counter(r.get("Type", "") for r in rows(first("Reactions_*.csv")))
meta = {
    "generated": datetime.now().isoformat(timespec="seconds"),
    "export_dir": os.path.basename(os.path.abspath(sys.argv[1])),
    "total_shares": len(shares),
    "authored": len(posts),
    "reposts_per_year": per_year(first("InstantReposts_*.csv")),
    "comments_given_per_year": per_year(first("Comments_*.csv")),
    "reactions_given_per_year": per_year(first("Reactions_*.csv")),
    "reactions_given_by_type": dict(rtype.most_common()),
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"meta": meta, "posts": posts}, fh, ensure_ascii=False)
print(f"{len(posts)} authored posts of {len(shares)} shares -> {out}")
