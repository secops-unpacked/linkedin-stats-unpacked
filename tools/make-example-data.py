"""Generate a synthetic workspace: fake posts, fake metrics, fake analytics.

Usage: python3 tools/make-example-data.py <target-dir>

Nothing here comes from a real LinkedIn account. It exists so the README screenshot,
and any test or demo, can be produced without putting a real person's posts, follower
counts or audience into a public repo. The seed is fixed, so the output is reproducible.

    python3 tools/make-example-data.py /tmp/demo
    cd /tmp/demo && python3 /path/to/scripts/build.py && open dist/posting-record.html
"""
import datetime
import json
import math
import os
import random
import sys

SEED = 2026
FIRST_POST = datetime.date(2024, 9, 20)
LAST_POST = datetime.date(2026, 9, 8)
WINDOW = (datetime.date(2025, 9, 14), datetime.date(2026, 9, 13))

TOPICS = {
    "SIEM": ["Rebuilding the SIEM pipeline without doubling the bill",
             "What a SIEM migration actually costs you",
             "SIEM is a data problem wearing a security badge"],
    "AI SOC & agents": ["Where AI agents actually help the SOC",
                        "Agentic triage: what worked and what did not",
                        "The AI SOC pitch versus the AI SOC deployment"],
    "Detection engineering": ["Detection as code, one year in",
                              "Why your detection backlog never shrinks",
                              "Detection engineering needs product thinking"],
    "SOAR & automation": ["Automation that survives contact with an analyst",
                          "The playbook nobody runs",
                          "SOAR did not fail, the scope did"],
    "Threat intel & hunting": ["Threat intel you can actually action",
                               "Hunting without a hypothesis is just grepping"],
    "Vendors & market": ["Reading the vendor landscape without the quadrant",
                         "What vendors mean when they say platform"],
    "Metrics & maturity": ["MTTR is the wrong headline metric",
                           "Maturity models and the trap of the average"],
}
SENTENCES = [
    "Most teams get this backwards.",
    "The tooling is not the constraint, the operating model is.",
    "Here is what changed when we stopped treating it as a procurement decision.",
    "Three things mattered more than the platform we picked.",
    "The analysts noticed within a week.",
    "That number looked good on a slide and meant nothing on a shift.",
    "We measured it for two quarters before believing it.",
    "The vendor was not the problem here.",
    "Scope discipline beat tooling every single time.",
    "None of this is novel, it is just rarely done.",
]
# One band has to be well represented and all four have to be non-empty, or the
# within-band comparisons in the dashboards and the report have nothing to say.
BANDS = ([240, 700, 1500, 2300], [1, 3, 6, 3])
BASE_REACH = {240: 1300, 700: 2400, 1500: 5200, 2300: 7600}


def body(n, rnd):
    out = []
    while sum(len(s) + 1 for s in out) < n:
        out.append(rnd.choice(SENTENCES))
    return " ".join(out)[:n].rsplit(" ", 1)[0] + "."


def build(target):
    rnd = random.Random(SEED)
    posts, metrics = [], []
    day = FIRST_POST
    while day < LAST_POST:
        head = rnd.choice(TOPICS[rnd.choice(list(TOPICS))])
        series = day.weekday() == 4 and rnd.random() < 0.72
        band = rnd.choices(*BANDS)[0]
        text = ("Weekly Roundup: " if series else "") + head + "\n\n" + body(band, rnd)
        when = datetime.datetime.combine(day, datetime.time(rnd.choice([7, 8, 9, 11, 14]), rnd.choice([0, 15, 30])))
        ts = int(when.timestamp() * 1000)
        pid = str(ts << 22 | rnd.getrandbits(20))          # LinkedIn ids embed ms since epoch
        media = rnd.choices(["text", "image", "video", "document"], weights=[64, 20, 8, 8])[0]
        imp = int(BASE_REACH[band] * rnd.uniform(.55, 1.9) * (1.25 if series else 1.0) * (1.3 if media == "image" else 1.0))
        posts.append({
            "d": f"{day} {when.hour:02d}:00:00",
            "url": f"https://www.linkedin.com/feed/update/urn:li:share:{pid}",
            "text": text, "len": len(text), "words": len(text.split()),
            "tags": ["secops"] if rnd.random() < .3 else [],
            "link": rnd.random() < .3, "media": media != "text",
        })
        metrics.append({
            "id": pid, "activity": pid, "date": str(day), "impressions": imp,
            "social_engagements": int(imp * rnd.uniform(.015, .04)), "reactions": int(imp * rnd.uniform(.010, .028)),
            "comments": int(imp * rnd.uniform(.0008, .004)), "reposts": int(imp * rnd.uniform(.0004, .003)),
            "saves": int(imp * rnd.uniform(.002, .009)), "sends": int(imp * rnd.uniform(.0003, .002)),
            "profile_viewers": int(imp * rnd.uniform(.002, .01)), "followers_gained": int(imp * rnd.uniform(.0004, .003)),
            "link_visits": int(imp * rnd.uniform(0, .004)), "video_views": imp if media == "video" else None,
            "media": media, "images": rnd.randint(1, 4) if media == "image" else 0,
        })
        day += datetime.timedelta(days=rnd.choice([2, 2, 3, 3, 4]))

    # prepare.py and weekly.py both leave posts.json newest first, and the dashboards rely on it.
    posts.sort(key=lambda p: p["d"], reverse=True)
    metrics.sort(key=lambda m: m["date"], reverse=True)

    d0, d1 = WINDOW
    per_day = {}
    for m in metrics:
        per_day[m["date"]] = per_day.get(m["date"], 0) + m["impressions"]
    daily, followers = [], []
    for k in range((d1 - d0).days + 1):
        dd = d0 + datetime.timedelta(days=k)
        organic = 2100 + 700 * rnd.random() + 420 * math.sin(k / 11)
        daily.append({"d": str(dd), "imp": int(organic + per_day.get(str(dd), 0) * .55), "eng": int(40 + rnd.random() * 70)})
        followers.append({"d": str(dd), "new": rnd.randint(4, 34)})

    os.makedirs(os.path.join(target, "snapshots"), exist_ok=True)
    w = lambda name, obj: json.dump(obj, open(os.path.join(target, name), "w", encoding="utf-8"), ensure_ascii=False)

    w("posts.json", {"meta": {
        "generated": "2026-09-14T09:00:00", "export_dir": "Example_LinkedInDataExport_09-14-2026",
        "total_shares": len(posts) + 180, "authored": len(posts),
        "reposts_per_year": {"2025": 64, "2026": 71},
        "comments_given_per_year": {"2025": 310, "2026": 402},
        "reactions_given_per_year": {"2025": 880, "2026": 1140},
        "reactions_given_by_type": {"LIKE": 1420, "PRAISE": 310, "EMPATHY": 190, "INTEREST": 100},
    }, "posts": posts})
    w("post_metrics.json", {"collected": "2026-09-14", "posts": metrics})
    w("analytics.json", {
        "window": {"start": str(d0), "end": str(d1)}, "daily": daily, "followers": followers, "top": [],
        "audience": {
            "job_titles": [{"v": "Security Analyst", "pct": "19%"}, {"v": "Security Engineer", "pct": "16%"},
                           {"v": "SOC Manager", "pct": "11%"}, {"v": "CISO", "pct": "8%"},
                           {"v": "Detection Engineer", "pct": "7%"}],
            "industries": [{"v": "Computer and Network Security", "pct": "38%"}, {"v": "Information Technology", "pct": "21%"},
                           {"v": "Financial Services", "pct": "9%"}, {"v": "Software Development", "pct": "8%"}],
            "locations": [{"v": "Greater London", "pct": "9%"}, {"v": "New York", "pct": "7%"}, {"v": "Amsterdam", "pct": "5%"}],
            "seniority": [{"v": "Senior", "pct": "34%"}, {"v": "Manager", "pct": "18%"},
                          {"v": "Director", "pct": "12%"}, {"v": "CXO", "pct": "9%"}],
        },
        "impressions": sum(d["imp"] for d in daily), "members_reached": int(sum(d["imp"] for d in daily) * .17),
        "engagements": sum(d["eng"] for d in daily), "total_followers": 11840,
        "new_followers": sum(f["new"] for f in followers),
    })
    # Three snapshots, so the reach-over-time and week-over-week growth sections have something to plot.
    for snap, factor in (("2026-08-31", .86), ("2026-09-07", .94), ("2026-09-14", 1.0)):
        rows = [dict(m, impressions=int(m["impressions"] * factor), saves=int(m["saves"] * factor))
                for m in metrics if m["date"] <= snap]
        w(os.path.join("snapshots", f"post_metrics_{snap}.json"), {"collected": snap, "posts": rows})

    lens = sorted(p["len"] for p in posts)
    bands = {"<300": sum(1 for x in lens if x < 300), "300-1k": sum(1 for x in lens if 300 <= x < 1000),
             "1k-2k": sum(1 for x in lens if 1000 <= x < 2000), "2k+": sum(1 for x in lens if x >= 2000)}
    print(f"{len(posts)} synthetic posts -> {target}")
    print("length bands:", ", ".join(f"{k} {v}" for k, v in bands.items()))
    print("next: copy config.example.json to config.json there, then run build.py, db.py and report.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    build(sys.argv[1])
