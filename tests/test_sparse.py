"""Run the whole pipeline against deliberately awkward datasets.

Usage: python3 tests/test_sparse.py

The report and the dashboards were written against one account's data and picked up its
shape as an assumption: every length band populated, an analytics xlsx present, a series
that matches something. Each case below breaks one of those. All four scripts must exit 0
and produce their files, whatever the data looks like.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skill", "linkedin-post-analytics", "scripts")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("gen", os.path.join(ROOT, "tools", "make-example-data.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

CONFIG = json.load(open(os.path.join(ROOT, "config.example.json")))


def workspace(mutate=None, config=None):
    ws = tempfile.mkdtemp(prefix="pr-test-")
    gen.build(ws)
    json.dump(config or CONFIG, open(os.path.join(ws, "config.json"), "w"))
    if mutate:
        mutate(ws)
    return ws


def load(ws, name):
    return json.load(open(os.path.join(ws, name)))


def save(ws, name, obj):
    json.dump(obj, open(os.path.join(ws, name), "w"))


# ---------- the awkward cases ----------

def only_one_band(ws):
    """Every post the same length: no band comparison is possible."""
    d = load(ws, "posts.json")
    for p in d["posts"]:
        p["text"] = p["text"][:250].ljust(250, ".")
        p["len"] = len(p["text"]); p["words"] = len(p["text"].split())
    save(ws, "posts.json", d)


def no_analytics(ws):
    """The xlsx is documented as optional, so daily_stats and audience never exist."""
    os.remove(os.path.join(ws, "analytics.json"))


def no_snapshots(ws):
    """First ever run: no history, so no reach curves and no growth table."""
    shutil.rmtree(os.path.join(ws, "snapshots"))


def single_post(ws):
    d = load(ws, "posts.json"); m = load(ws, "post_metrics.json")
    keep = d["posts"][0]["url"].rsplit(":", 1)[1]
    d["posts"] = d["posts"][:1]
    m["posts"] = [x for x in m["posts"] if x["id"] == keep]
    save(ws, "posts.json", d); save(ws, "post_metrics.json", m)
    shutil.rmtree(os.path.join(ws, "snapshots"), ignore_errors=True)


def no_metrics(ws):
    """Export parsed but the collector has never run."""
    os.remove(os.path.join(ws, "post_metrics.json"))
    shutil.rmtree(os.path.join(ws, "snapshots"), ignore_errors=True)


def one_weekday_one_media(ws):
    d = load(ws, "posts.json"); m = load(ws, "post_metrics.json")
    for p in d["posts"]:
        p["d"] = "2026-03-02 09:00:00"       # every post on a Monday
        p["media"] = False
    for x in m["posts"]:
        x["media"] = "text"; x["images"] = 0
    save(ws, "posts.json", d); save(ws, "post_metrics.json", m)


def no_links(ws):
    d = load(ws, "posts.json")
    for p in d["posts"]:
        p["link"] = False
        p["text"] = p["text"].replace("http", "hxxp")
    save(ws, "posts.json", d)


CASES = [
    ("all posts in one length band", only_one_band, None),
    ("no analytics.json (optional xlsx absent)", no_analytics, None),
    ("no snapshots (first run)", no_snapshots, None),
    ("a single post", single_post, None),
    ("no metrics collected yet", no_metrics, None),
    ("one weekday, one media type", one_weekday_one_media, None),
    ("no links in any post", no_links, None),
    ("series rule matches nothing", None, {**CONFIG, "series": {"name": "Nothing", "slug": "nothing", "include": "$^", "exclude": "$^"}}),
    ("no topics configured", None, {**CONFIG, "topics": []}),
    ("baseline (everything present)", None, None),
]


def main():
    failures = []
    for label, mutate, config in CASES:
        ws = workspace(mutate, config)
        broke = []
        for script in ("series.py", "build.py", "db.py", "report.py"):
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)],
                               cwd=ws, capture_output=True, text=True)
            if r.returncode != 0:
                tail = (r.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                broke.append(f"{script}: {tail}")
        expected = ["dist/posting-record.html", "dist/post-explorer.html"]
        if os.path.exists(os.path.join(ws, "posting-record.sqlite")):
            expected.append("dist/posting-report.html")
        missing = [f for f in expected if not os.path.exists(os.path.join(ws, f))]
        if broke or missing:
            failures.append((label, broke, missing))
            print(f"FAIL  {label}")
            for b in broke:
                print(f"        {b}")
            for m in missing:
                print(f"        missing {m}")
        else:
            print(f"ok    {label}")
        shutil.rmtree(ws, ignore_errors=True)
    print()
    if failures:
        print(f"{len(failures)} of {len(CASES)} cases failed")
        return 1
    print(f"all {len(CASES)} cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
