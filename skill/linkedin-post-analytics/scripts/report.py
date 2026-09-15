"""Write the Posting Report: one HTML document covering every post, with a dedicated section for the series.

Usage: python3 report.py [--db posting-record.sqlite] [--out dist/posting-report.html]
Runs in the workspace (current directory, or PR_WORKSPACE). Reads posting-record.sqlite (from db.py), config.json and
<series slug>.json (for missed weeks). Writes dist/posting-report.html: a self-contained page with prose, tables and
inline SVG charts. It is a report, not a dashboard: read top to bottom, no filters, every number carries its n.
"""
import html, json, os, re, shutil, sqlite3, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pr_common import load_config, read_json, wpath

args = sys.argv[1:]
def opt(flag, default):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default
db_path = wpath(opt("--db", "posting-record.sqlite"))
out_path = wpath(opt("--out", os.path.join("dist", "posting-report.html")))
if not os.path.exists(db_path):
    sys.exit("posting-record.sqlite missing. Run db.py first.")
cfg = load_config()
series_doc = read_json(cfg["series"]["slug"] + ".json", {"summary": {}})
S = cfg["series"]["name"]
TZL = cfg["timezone_label"]

# SQLite needs locks; bridge and network mounts do not always give them. Read from a temp copy.
tmpdir = tempfile.mkdtemp(prefix="pr-report-")
shutil.copyfile(db_path, os.path.join(tmpdir, "db.sqlite"))
con = sqlite3.connect(os.path.join(tmpdir, "db.sqlite"))
con.row_factory = sqlite3.Row
def q(sql, *a):
    """db.py skips a table whose source data is absent (no analytics xlsx means no daily_stats,
    no series rule means no series_editions). The xlsx is optional, so a missing table is an
    empty result, not an error. Any other SQL error still raises."""
    try:
        return [dict(r) for r in con.execute(sql, a)]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc) or "no such view" in str(exc):
            return []
        raise


one = lambda sql, *a: (q(sql, *a) or [{}])[0]
meta = {r["key"]: r["value"] for r in q("SELECT key, value FROM meta")}

# ---------- helpers ----------
e = html.escape
def n0(v):
    return "–" if v is None else f"{int(round(float(v))):,}"
def n1(v, d=1):
    return "–" if v is None else f"{float(v):,.{d}f}"
def k(v):
    if v is None: return "–"
    v = float(v)
    return f"{v/1000:.1f}k" if v >= 1000 else f"{v:.0f}"
def pc(v, d=1):
    return "–" if v is None else f"{float(v)*100:.{d}f}%"
def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    n = len(xs); return xs[n//2] if n % 2 else (xs[n//2-1] + xs[n//2]) / 2
def fdate(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d %b %Y") if s else "–"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BANDS = ["<300", "300-1k", "1k-2k", "2k+"]
BAND_LABEL = {"<300": "under 300", "300-1k": "300 to 1k", "1k-2k": "1k to 2k", "2k+": "over 2k"}


def hbar(rows, label_key, value_key, n_key="n", fmt=k, color="var(--s1)", width=560, ordinal=None):
    """Horizontal bars. rows: list of dicts. ordinal: list of colors per row (else single color)."""
    rows = [r for r in rows if r.get(value_key) is not None]
    if not rows: return "<p class='note'>No data.</p>"
    mx = max(float(r[value_key]) for r in rows) or 1
    rh, gap, lw = 22, 8, 150
    h = len(rows) * (rh + gap) + 4
    bw = width - lw - 70
    out = [f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" aria-label="bar chart">']
    for i, r in enumerate(rows):
        y = i * (rh + gap) + 2
        w = max(2, float(r[value_key]) / mx * bw)
        c = ordinal[i] if ordinal else color
        faded = ' opacity=".45"' if n_key and (r.get(n_key) or 0) < 4 else ""
        lab = e(str(r[label_key]))
        out.append(f'<text class="lab" x="{lw-8}" y="{y+rh/2+4}" text-anchor="end">{lab}</text>')
        out.append(f'<rect x="{lw}" y="{y}" width="{w:.1f}" height="{rh}" rx="2" fill="{c}"{faded}></rect>')
        nn = f' <tspan class="dim">n={r[n_key]}</tspan>' if n_key and r.get(n_key) is not None else ""
        out.append(f'<text class="lab strong" x="{lw+w+6:.1f}" y="{y+rh/2+4}">{fmt(r[value_key])}{nn}</text>')
    out.append("</svg>")
    return "".join(out)


def vbar(rows, label_key, value_key, fmt=k, color="var(--s1)", width=760, height=220, n_key=None, every=1, highlight=None, stack_key=None, stack_color="var(--series)"):
    rows = [r for r in rows if r.get(value_key) is not None]
    if not rows: return "<p class='note'>No data.</p>"
    mx = max(float(r[value_key]) for r in rows) or 1
    lp, rp, tp, bp = 44, 8, 24, 30
    iw, ih = width - lp - rp, height - tp - bp
    bw = iw / len(rows)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="bar chart">']
    for t in range(0, 5):
        yv = mx * t / 4; y = tp + ih - ih * t / 4
        out.append(f'<line class="gl" x1="{lp}" x2="{width-rp}" y1="{y:.1f}" y2="{y:.1f}"></line><text class="ax" x="{lp-6}" y="{y+4:.1f}" text-anchor="end">{k(yv)}</text>')
    for i, r in enumerate(rows):
        v = float(r[value_key]); h = v / mx * ih
        x = lp + i * bw + bw * .15; w = bw * .7
        c = highlight(r) if highlight else color
        out.append(f'<rect x="{x:.1f}" y="{tp+ih-h:.1f}" width="{w:.1f}" height="{h:.1f}" rx="1.5" fill="{c}"><title>{e(str(r[label_key]))}: {fmt(v)}{(" (n=" + str(r[n_key]) + ")") if n_key else ""}</title></rect>')
        if stack_key and r.get(stack_key):
            sh = float(r[stack_key]) / mx * ih
            out.append(f'<rect x="{x:.1f}" y="{tp+ih-sh:.1f}" width="{w:.1f}" height="{sh:.1f}" fill="{stack_color}"><title>{e(str(r[label_key]))}: {r[stack_key]} series editions</title></rect>')
        if i % every == 0:
            out.append(f'<text class="ax" x="{x+w/2:.1f}" y="{height-bp+14}" text-anchor="middle">{e(str(r[label_key]))}</text>')
        if n_key and len(rows) <= 16:
            out.append(f'<text class="ax" x="{x+w/2:.1f}" y="{tp+ih-h-4:.1f}" text-anchor="middle">{r[n_key]}</text>')
    out.append("</svg>")
    return "".join(out)


def lines(series_list, width=760, height=240, xlab="days since publish", fmt=k):
    """series_list: [{label, color, points:[(x,y)], width}]"""
    pts = [p for s in series_list for p in s["points"]]
    if not pts: return "<p class='note'>Not enough snapshots yet.</p>"
    mx = max(p[0] for p in pts) or 1; my = max(p[1] for p in pts) or 1
    lp, rp, tp, bp = 48, 12, 12, 34
    iw, ih = width - lp - rp, height - tp - bp
    X = lambda x: lp + x / mx * iw; Y = lambda y: tp + ih - y / my * ih
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="line chart">']
    for t in range(0, 5):
        yv = my * t / 4
        out.append(f'<line class="gl" x1="{lp}" x2="{width-rp}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}"></line><text class="ax" x="{lp-6}" y="{Y(yv)+4:.1f}" text-anchor="end">{fmt(yv)}</text>')
    for t in range(0, 6):
        xv = mx * t / 5
        out.append(f'<text class="ax" x="{X(xv):.1f}" y="{height-bp+16}" text-anchor="middle">{xv:.0f}</text>')
    out.append(f'<text class="lab" x="{lp+iw/2:.1f}" y="{height-4}" text-anchor="middle">{e(xlab)}</text>')
    for s in series_list:
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(s["points"]))
        out.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="{s.get("width", 1.5)}" stroke-linejoin="round" opacity="{s.get("opacity", 1)}"><title>{e(s["label"])}</title></path>')
        x, y = s["points"][-1]
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.5" fill="{s["color"]}"></circle>')
    out.append("</svg>")
    return "".join(out)


def table(cols, rows, cls=""):
    """cols: [(header, key, 'num'|'', fmt)]"""
    out = [f'<div class="tw"><table class="{cls}"><thead><tr>' + "".join(f'<th class="{c[2]}">{e(c[0])}</th>' for c in cols) + "</tr></thead><tbody>"]
    for r in rows:
        tds = []
        for hdr, key, kind, fmt in cols:
            v = r.get(key) if isinstance(key, str) else key(r)
            s = fmt(v) if fmt else ("–" if v is None else e(str(v)))
            tds.append(f'<td class="{kind}">{s}</td>')
        out.append("<tr>" + "".join(tds) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


ORD = ["var(--o1)", "var(--o2)", "var(--o3)", "var(--o4)"]

# ---------- data ----------
posts_all = q("SELECT * FROM v_posts ORDER BY published_utc DESC")
scored = [p for p in posts_all if p["impressions"] is not None]
by_year = q("SELECT year, count(*) n, sum(is_series) series FROM posts GROUP BY year ORDER BY year")
by_month = q("SELECT month, count(*) n, sum(is_series) series FROM posts GROUP BY month ORDER BY month")[-24:]
band_rows = q("SELECT * FROM stats_by_length_band")
band_rows.sort(key=lambda r: BANDS.index(r["group"]))
wd_rows = q("SELECT * FROM stats_by_weekday")
wd_rows.sort(key=lambda r: WEEKDAYS.index(r["group"]))
wd_band = q("SELECT * FROM stats_by_weekday_within_band")
media_rows = q("SELECT * FROM stats_by_media ORDER BY n DESC")
topic_rows = q("SELECT * FROM stats_by_topic ORDER BY median_impressions DESC")
topic_band = q("SELECT * FROM stats_by_topic_within_band")
quarter_rows = q("SELECT * FROM stats_by_quarter ORDER BY \"group\"")
link_band = q("SELECT * FROM stats_by_link_placement_within_band")
tq = q("SELECT * FROM stats_top_quartile_vs_rest")
corr_imp = q("SELECT feature, spearman, n FROM correlations WHERE outcome='impressions' AND spearman IS NOT NULL ORDER BY abs(spearman) DESC")
corr_sav = {r["feature"]: r["spearman"] for r in q("SELECT feature, spearman FROM correlations WHERE outcome='saves_per_1k'")}
hour_rows = q("SELECT * FROM stats_by_hour ORDER BY \"group\"")
growth = q("SELECT * FROM v_growth ORDER BY impressions_delta DESC")
curves = {}
for r in q("SELECT post_id, date_local, days_since_publish, impressions FROM v_reach_curve"):
    curves.setdefault(r["post_id"], []).append((r["days_since_publish"], r["impressions"]))
curves = {pid: sorted(v) for pid, v in curves.items() if len(v) >= 2}
followers_q = q("SELECT substr(date,1,4) || '-Q' || ((cast(substr(date,6,2) as integer)+2)/3) quarter, sum(new_followers) new_followers, sum(impressions) impressions, sum(posts_published) posts FROM daily_stats GROUP BY 1 ORDER BY 1")
top_imp = sorted(scored, key=lambda p: -p["impressions"])[:15]
top_sav = sorted(scored, key=lambda p: -(p["saves_per_1k"] or 0))[:10]
series_rows = q("SELECT e.*, p.hour_local, p.text, p.first_line, p.length_band, p.topics FROM series_editions e LEFT JOIN v_posts p ON p.id = e.post_id ORDER BY e.n")
series_scored = [p for p in scored if p["is_series"]]
other_scored = [p for p in scored if not p["is_series"]]
weeks_missed = (series_doc.get("summary") or {}).get("weeks_missed") or []

# derived sentences
b = {r["group"]: r for r in band_rows}
fri = {r["length_band"]: r for r in wd_band if r["group"] == "Friday"}
fri_all = next((r for r in wd_rows if r["group"] == "Friday"), None)
other_wd = median([p["impressions"] for p in scored if p["weekday"] not in ("Friday", "Saturday", "Sunday")])
wknd = next((r for r in wd_rows if r["group"] in ("Saturday", "Sunday")), None)
top_ratio = {r["metric"]: r for r in tq}
chars_corr = next((r["spearman"] for r in corr_imp if r["feature"] == "chars"), None)
q3 = float(meta.get("impressions_q3") or 0)
share10k = sum(1 for p in scored if p["impressions"] >= 10000) / len(scored) if scored else 0
s_share10k = sum(1 for p in series_scored if p["impressions"] >= 10000) / len(series_scored) if series_scored else 0
window = f"{fdate(meta.get('analytics_window_start'))} to {fdate(meta.get('analytics_window_end'))}"

# series within bands
series_band = []
for band in BANDS:
    a = [p["impressions"] for p in series_scored if p["length_band"] == band]
    o = [p["impressions"] for p in other_scored if p["length_band"] == band]
    if a or o:
        series_band.append({"band": BAND_LABEL[band], "series_n": len(a), "series_med": median(a), "other_n": len(o), "other_med": median(o)})

# topics within band table (pivot: topic x band, median with n)
topics_order = [r["group"] for r in topic_rows if r["group"] != "(no topic)"]
tb = {(r["group"], r["length_band"]): r for r in topic_band}

# ---------- write ----------
css = """
:root{color-scheme:light dark;
  --plane:#f5f6f8; --surface:#ffffff; --ink:#12151a; --ink-2:#565c66; --muted:#858b95;
  --grid:#e5e8ed; --axis:#c5cad2; --ring:rgba(18,21,26,.10); --hover:rgba(42,120,214,.08);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --neg:#c8412e;
  --o1:#86b6ef; --o2:#5598e7; --o3:#2a78d6; --o4:#184f95; --series:#eb6834;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --plane:#0e1013; --surface:#181b20; --ink:#f2f3f5; --ink-2:#b9bdc5; --muted:#868b94;
  --grid:#282c33; --axis:#3a3f48; --ring:rgba(255,255,255,.10); --hover:rgba(57,135,229,.14);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --neg:#e4604d;
  --o1:#86b6ef; --o2:#5598e7; --o3:#3987e5; --o4:#1c5cab; --series:#f07a48;}}
:root[data-theme="dark"]{
  --plane:#0e1013; --surface:#181b20; --ink:#f2f3f5; --ink-2:#b9bdc5; --muted:#868b94;
  --grid:#282c33; --axis:#3a3f48; --ring:rgba(255,255,255,.10); --hover:rgba(57,135,229,.14);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --neg:#e4604d;
  --o1:#86b6ef; --o2:#5598e7; --o3:#3987e5; --o4:#1c5cab; --series:#f07a48;}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.55 "Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;padding-block:40px 80px;padding-inline:clamp(16px,4vw,40px)}
.wrap{max-width:960px;margin:0 auto}
header{margin-bottom:36px;border-bottom:1px solid var(--axis);padding-bottom:24px}
.kicker{font:500 12px/1 "JetBrains Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);margin:0 0 12px}
h1{font-size:clamp(30px,4.5vw,44px);line-height:1.02;letter-spacing:-.025em;margin:0 0 12px;text-wrap:balance;max-width:20ch}
.lede{color:var(--ink-2);margin:0;max-width:66ch;font-size:16px}
.lede b{color:var(--ink);font-weight:600}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:18px;font-size:13px}
nav.toc a{color:var(--ink-2);text-decoration:none;border-bottom:1px solid var(--grid)}
nav.toc a:hover{color:var(--ink);border-color:var(--ink)}
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:0 0 40px}
@media (max-width:820px){.tiles{grid-template-columns:repeat(3,1fr)}}
@media (max-width:460px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:6px;padding:12px 14px 10px}
.tile .k{font-size:11px;color:var(--ink-2);text-transform:uppercase;letter-spacing:.06em;font-weight:500}
.tile .v{font-size:26px;font-weight:600;letter-spacing:-.02em;line-height:1.1;margin-top:4px;font-variant-numeric:tabular-nums}
.tile .d{font-size:12px;color:var(--muted);margin-top:3px}
section{margin:0 0 52px}
section.series{border-top:3px solid var(--series);padding-top:22px}
h2{font-size:24px;font-weight:600;letter-spacing:-.02em;margin:0 0 6px;line-height:1.15;text-wrap:balance}
h3{font-size:15px;font-weight:600;margin:26px 0 8px;letter-spacing:-.01em}
.sub{color:var(--ink-2);margin:0 0 16px;max-width:66ch}
p{max-width:66ch}
p.finding{border-left:3px solid var(--s1);padding:2px 0 2px 14px;margin:14px 0}
section.series p.finding{border-color:var(--series)}
.note{color:var(--muted);font-size:13px;margin:6px 0 0;max-width:70ch}
.chart{width:100%;height:auto;display:block;margin:10px 0 4px}
.ax{font:11px "JetBrains Mono",ui-monospace,monospace;fill:var(--muted)}
.gl{stroke:var(--grid);stroke-width:1}
.lab{font:12px "Public Sans",system-ui,sans-serif;fill:var(--ink-2)}
.lab.strong{fill:var(--ink);font-weight:600}
.dim{fill:var(--muted);font-weight:400;font-size:11px}
.tw{overflow-x:auto;margin:10px 0 4px;border:1px solid var(--ring);border-radius:6px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--ink-2);padding:9px 10px;border-bottom:1px solid var(--axis);white-space:nowrap;background:var(--surface);position:sticky;top:0}
td{padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:0}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.faint{color:var(--muted)}
td.dt{white-space:nowrap}
tr:hover td{background:var(--hover)}
.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:12px}
a{color:var(--s1)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:24px}
@media (max-width:720px){.two{grid-template-columns:1fr}}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--ring);color:var(--ink-2);white-space:nowrap}
.pill.series{border-color:var(--series);color:var(--series)}
.pill.top{background:var(--s1);border-color:var(--s1);color:#fff}
details.ed{border:1px solid var(--ring);border-radius:6px;background:var(--surface);margin:8px 0;padding:0}
details.ed summary{cursor:pointer;padding:10px 14px;display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:baseline;list-style:none}
details.ed summary::-webkit-details-marker{display:none}
details.ed summary .n{font:600 13px "JetBrains Mono",ui-monospace,monospace;color:var(--series)}
details.ed summary .t{font-weight:500}
details.ed summary .m{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}
details.ed .body{padding:4px 14px 14px 58px;border-top:1px solid var(--grid)}
details.ed .meta{font-size:12px;color:var(--muted);margin:8px 0 10px}
details.ed pre{white-space:pre-wrap;font:14px/1.55 "Public Sans",system-ui,sans-serif;margin:0;max-width:66ch;color:var(--ink)}
.archive td.fl{max-width:420px}
.archive td.fl a{color:inherit;text-decoration:none}
.archive td.fl a:hover{text-decoration:underline}
.sort{cursor:pointer;user-select:none}
.sort:after{content:" ↕";color:var(--muted);font-size:10px}
footer{margin-top:60px;color:var(--muted);font-size:13px;border-top:1px solid var(--grid);padding-top:16px}
:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
"""

H = []
add = H.append
# No webfont link: this document is published to a public URL and a font request would
# hand every reader's IP and referrer to a third party. Every rule in css names a system
# fallback, so the page renders standalone with zero external requests.
add(f'<title>Posting Report</title>')
add(f"<style>{css}</style><div class='wrap'>")

# header
add(f"""<header>
<p class="kicker">Posting Report · {e(cfg['profile'])} · built {e(meta.get('built_at','')[:10])}</p>
<h1>{n0(meta.get('posts'))} posts, {n0(meta.get('posts_with_metrics'))} with numbers, one series that carries its weight</h1>
<p class="lede">Every post since {fdate(meta.get('first_post'))}, joined to the reach and engagement LinkedIn shows per post. Performance covers <b>{window}</b>
({n0(meta.get('analytics_impressions'))} impressions, {n0(meta.get('analytics_members_reached'))} members reached). Medians throughout; every figure carries its n. Times in {e(TZL)}.</p>
<nav class="toc"><a href="#cadence">Cadence</a><a href="#reach">What drives reach</a><a href="#engagement">What travels with reach</a><a href="#topics">Topics and media</a><a href="#trend">Trend</a><a href="#growth">Reach over time</a><a href="#series">{e(S)}</a><a href="#best">Best posts</a><a href="#archive">Archive</a></nav>
</header>""")

# tiles
add('<div class="tiles">')
for kk, v, d in [("Posts, all time", n0(meta.get("posts")), f"since {fdate(meta.get('first_post'))}"),
                 ("With metrics", n0(meta.get("posts_with_metrics")), f"collected {fdate(meta.get('metrics_collected'))}"),
                 ("Median reach", k(meta.get("impressions_median")), "impressions per post"),
                 ("Top quartile from", k(q3), "impressions"),
                 ("Followers", n0(meta.get("total_followers")), f"+{n0(meta.get('new_followers_in_window'))} in window"),
                 (f"{S} editions", n0(meta.get("series_posts")), f"{meta.get('series_current_streak_weeks','–')} week streak")]:
    add(f'<div class="tile"><div class="k">{e(kk)}</div><div class="v">{v}</div><div class="d">{e(d)}</div></div>')
add("</div>")

# cadence
yr_last = by_year[-1] if by_year else {}
add(f"""<section id="cadence"><h2>Cadence</h2>
<p class="sub">Posts per year across the whole export, then the last 24 months. Orange marks {e(S)} editions.</p>""")
add(vbar(by_year, "year", "n", fmt=lambda v: f"{v:.0f}", n_key=None, height=180, stack_key="series"))
add(vbar(by_month, "month", "n", fmt=lambda v: f"{v:.0f}", height=200, every=2, stack_key="series"))
add(f"<p class='note'>Monthly, last 24 months. {e(S)} editions in this window: {sum(r['series'] for r in by_month)} of {sum(r['n'] for r in by_month)} posts.</p>")
add("</section>")

# reach drivers
add(f"""<section id="reach"><h2>What drives reach</h2>
<p class="sub">Length first, then the day. Everything else is checked inside length bands before it gets a sentence.</p>
<h3>Length</h3>""")
add(hbar([{"g": BAND_LABEL[r["group"]] + " chars", **r} for r in band_rows], "g", "median_impressions", ordinal=ORD))
add(f"""<p class="finding">Median reach climbs with every band: {k(b['<300']['median_impressions'])} under 300 characters, {k(b['300-1k']['median_impressions'])} at 300 to 1k,
{k(b['1k-2k']['median_impressions'])} at 1k to 2k, {k(b['2k+']['median_impressions'])} over 2k. Spearman with impressions {n1(chars_corr, 2)}, the strongest single feature.
Share of posts over 10k impressions: {pc(b['<300']['share_over_10k'], 0)}, {pc(b['300-1k']['share_over_10k'], 0)}, {pc(b['1k-2k']['share_over_10k'], 0)}, {pc(b['2k+']['share_over_10k'], 0)} by band.</p>""")
add(table([("Band", lambda r: BAND_LABEL[r["group"]] + " chars", "", None), ("n", "n", "num", n0), ("Median", "median_impressions", "num", n0), ("P25", "p25_impressions", "num", n0), ("P75", "p75_impressions", "num", n0),
           ("Over 10k", "share_over_10k", "num", lambda v: pc(v, 0)), ("Eng. rate", "median_engagement_rate", "num", pc), ("Saves /1k", "median_saves_per_1k", "num", n1), ("Sends /1k", "median_sends_per_1k", "num", lambda v: n1(v, 2))], band_rows))

add("<h3>Weekday</h3>")
add(hbar(wd_rows, "group", "median_impressions", color="var(--s1)"))
fri_txt = ", ".join(f"{BAND_LABEL[bd]} {k(fri[bd]['median_impressions'])} (n={fri[bd]['n']})" for bd in BANDS if bd in fri and fri[bd]["n"] >= 4)
add(f"""<p class="finding">Friday runs {k(fri_all['median_impressions'] if fri_all else None)} median against {k(other_wd)} for Monday to Thursday. It holds inside length bands: {fri_txt}.
Weekends are the one negative signal{': ' + k(wknd['median_impressions']) + ' median (n=' + str(wknd['n']) + ')' if wknd else ''}.</p>""")
piv = []
for wd in WEEKDAYS:
    row = {"wd": wd}
    for bd in BANDS:
        r = next((x for x in wd_band if x["group"] == wd and x["length_band"] == bd), None)
        row[bd] = f"{k(r['median_impressions'])} <span class='dim'>n={r['n']}</span>" if r else "–"
        row[bd + "_n"] = r["n"] if r else 0
    piv.append(row)
add('<div class="tw"><table><thead><tr><th>Weekday</th>' + "".join(f'<th class="num">{BAND_LABEL[bd]}</th>' for bd in BANDS) + "</tr></thead><tbody>")
for row in piv:
    add(f"<tr><td>{row['wd']}</td>" + "".join(f"<td class='num{' faint' if row[bd + '_n'] < 4 else ''}'>{row[bd]}</td>" for bd in BANDS) + "</tr>")
add("</tbody></table></div><p class='note'>Median impressions by weekday within each length band. Faded cells have fewer than 4 posts.</p>")

hr = [r for r in hour_rows if r["n"] >= 4]
add("<h3>Hour of day</h3>")
add(hbar([{"g": f"{int(r['group']):02d}:00", **r} for r in hr], "g", "median_impressions"))
add(f"<p class='note'>Hours with at least 4 posts, {e(TZL)} time. Hour correlates {n1(next((r['spearman'] for r in corr_imp if r['feature']=='hour_local'), None), 2)} with reach: noise. Pick the hour for the audience, not the algorithm.</p>")

lk = []
for bd in BANDS:
    for g in ("link in text", "link in own comment", "no link"):
        r = next((x for x in link_band if x["length_band"] == bd and x["group"] == g), None)
        if r: lk.append({"band": BAND_LABEL[bd], "placement": g, **r})
add("<h3>Link placement</h3>")
add(table([("Band", "band", "", None), ("Placement", "placement", "", None), ("n", "n", "num", n0), ("Median", "median_impressions", "num", n0), ("Eng. rate", "median_engagement_rate", "num", pc), ("Saves /1k", "median_saves_per_1k", "num", n1)], lk))
l12 = {r["placement"]: r for r in lk if r["band"] == BAND_LABEL["1k-2k"]}
if len(l12) >= 2:
    add(f"<p class='finding'>In the 1k to 2k band, where most posts sit, link in text {k(l12.get('link in text',{}).get('median_impressions'))}, link in own comment {k(l12.get('link in own comment',{}).get('median_impressions'))}, no link {k(l12.get('no link',{}).get('median_impressions'))}. Placement is not a lever. Put the link where the reader needs it.</p>")
add("</section>")

# engagement mix
add(f"""<section id="engagement"><h2>What travels with reach</h2>
<p class="sub">Top-quartile posts (over {k(q3)} impressions, n={top_ratio.get('saves_per_1k',{}).get('n_top','–')}) against the rest, per 1,000 impressions.</p>""")
tq_rows = [r for r in tq if r["ratio"] is not None]
tq_rows.sort(key=lambda r: -r["ratio"])
add(hbar([{"g": r["metric"].replace("_per_1k", "").replace("_", " "), **r} for r in tq_rows], "g", "ratio", n_key=None, fmt=lambda v: f"{v:.2f}x",
         ordinal=["var(--s1)" if r["ratio"] >= 1.3 else "var(--axis)" for r in tq_rows]))
add(table([("Metric", lambda r: r["metric"].replace("_", " "), "", None), ("Top quartile", "top_quartile_median", "num", lambda v: n1(v, 2)), ("Rest", "rest_median", "num", lambda v: n1(v, 2)), ("Ratio", "ratio", "num", lambda v: "–" if v is None else f"{v:.2f}x")], tq))
sv, sn, cm, rp = (top_ratio.get(x, {}).get("ratio") for x in ("saves_per_1k", "sends_per_1k", "comments_per_1k", "reposts_per_1k"))
add(f"""<p class="finding">Saves run {n1(sv,2)}x and sends {n1(sn,2)}x per 1k in the top quartile. Comments ({n1(cm,2)}x) and reposts ({n1(rp,2)}x) do not separate winners from the rest.
The top quartile is also longer: {n0(top_ratio.get('chars',{}).get('top_quartile_median'))} characters and {n0(top_ratio.get('paragraphs',{}).get('top_quartile_median'))} paragraphs median against {n0(top_ratio.get('chars',{}).get('rest_median'))} and {n0(top_ratio.get('paragraphs',{}).get('rest_median'))}.
Write the thing someone bookmarks, not the thing someone argues with.</p>""")
add("<h3>Correlations with reach</h3>")
cr = [r for r in corr_imp if abs(r["spearman"]) >= 0.05][:14]
add(hbar([{"g": r["feature"].replace("topic:", "").replace("_", " "), "v": abs(r["spearman"]), "s": r["spearman"], **{"n": r["n"]}} for r in cr], "g", "v", n_key=None,
         fmt=lambda v: f"{v:.2f}", ordinal=["var(--s1)" if r["spearman"] > 0 else "var(--neg)" for r in cr]))
add("<p class='note'>Spearman rank correlation, absolute value shown; red bars are negative. Direction, not cause: length sits underneath most of the topic and series bars.</p>")
add("</section>")

# topics & media
add(f"""<section id="topics"><h2>Topics and media</h2>
<p class="sub">Topics are keyword rules from config.json; a post can match several. Media comes from the post page.</p><h3>Topics</h3>""")
add(hbar(topic_rows, "group", "median_impressions"))
add('<div class="tw"><table><thead><tr><th>Topic</th><th class="num">n</th><th class="num">Median</th>' + "".join(f'<th class="num">{BAND_LABEL[bd]}</th>' for bd in BANDS) + '<th class="num">Saves /1k</th></tr></thead><tbody>')
for t in topic_rows:
    cells = ""
    for bd in BANDS:
        r = tb.get((t["group"], bd))
        cells += f"<td class='num{' faint' if not r or r['n'] < 4 else ''}'>{(k(r['median_impressions']) + ' <span class=dim>n=' + str(r['n']) + '</span>') if r else '–'}</td>"
    add(f"<tr><td>{e(t['group'])}</td><td class='num'>{t['n']}</td><td class='num'>{n0(t['median_impressions'])}</td>{cells}<td class='num'>{n1(t['median_saves_per_1k'])}</td></tr>")
add("</tbody></table></div><p class='note'>Median impressions per topic, overall and within each length band.</p>")
tt = {r["group"]: r for r in topic_rows}
add("<h3>Media</h3>")
add(hbar(media_rows, "group", "median_impressions"))
add(table([("Media", "group", "", None), ("n", "n", "num", n0), ("Median", "median_impressions", "num", n0), ("Eng. rate", "median_engagement_rate", "num", pc), ("Saves /1k", "median_saves_per_1k", "num", n1), ("Median chars", "median_chars", "num", n0)], media_rows))
mi, mt = (next((r for r in media_rows if r["group"] == g), None) for g in ("image", "text"))
if mi and mt:
    add(f"<p class='finding'>Image posts {k(mi['median_impressions'])} median (n={mi['n']}) against {k(mt['median_impressions'])} for text (n={mt['n']}), with {n1(mi['median_saves_per_1k'])} saves per 1k against {n1(mt['median_saves_per_1k'])}. The images are mostly frameworks and maps, so the two effects are tangled. Attach the framework.</p>")
add("</section>")

# trend
add(f"""<section id="trend"><h2>Trend by quarter</h2>
<p class="sub">Median reach per post by quarter of publishing, and followers gained per quarter from the daily series.</p>""")
add(vbar(quarter_rows, "group", "median_impressions", n_key="n", height=220))
add(table([("Quarter", "group", "", None), ("Posts", "n", "num", n0), ("Median", "median_impressions", "num", n0), ("P75", "p75_impressions", "num", n0), ("Over 10k", "share_over_10k", "num", lambda v: pc(v, 0)), ("Eng. rate", "median_engagement_rate", "num", pc), ("Saves /1k", "median_saves_per_1k", "num", n1), ("Median chars", "median_chars", "num", n0)], quarter_rows))
fq = [r for r in followers_q if r["new_followers"] is not None]
if fq:
    add("<h3>Followers gained per quarter</h3>")
    add(vbar(fq, "quarter", "new_followers", fmt=n0, height=180, color="var(--s3)"))
    add(table([("Quarter", "quarter", "", None), ("New followers", "new_followers", "num", n0), ("Impressions", "impressions", "num", n0), ("Posts", "posts", "num", n0)], fq))
add("</section>")

# growth
add(f"""<section id="growth"><h2>Reach over time</h2>
<p class="sub">Each weekly collection adds a point to every post. {meta.get('snapshots','0')} snapshot(s) so far: {e(meta.get('snapshot_dates',''))}.</p>""")
if curves:
    pid_first = {p["id"]: (p["first_line"] or "")[:60] for p in posts_all}
    ser = [{"label": pid_first.get(pid, pid), "color": "var(--series)" if next((p for p in posts_all if p["id"] == pid), {}).get("is_series") else "var(--s1)", "points": pts, "opacity": .85} for pid, pts in curves.items()]
    add(lines(ser))
    add("<p class='note'>Impressions by days since publishing, one line per post with two or more collections. Orange lines are series editions.</p>")
if growth:
    add("<h3>Grew most since the previous collection</h3>")
    add(table([("Published", "date_local", "dt", fdate), ("Post", "first_line", "", lambda v: e((v or "")[:90])), ("Before", "prev_impressions", "num", n0), ("Now", "last_impressions", "num", n0), ("Δ impressions", "impressions_delta", "num", lambda v: ("+" if v and v > 0 else "") + n0(v)), ("Δ saves", "saves_delta", "num", lambda v: ("+" if v and v > 0 else "") + n0(v))], growth[:12]))
else:
    add("<p class='note'>Growth needs two collections of the same post. The next weekly run fills this in.</p>")
add("</section>")

# ---------- series section ----------
s_med = median([p["impressions"] for p in series_scored]); o_med = median([p["impressions"] for p in other_scored])
s_sav = median([p["saves_per_1k"] for p in series_scored]); o_sav = median([p["saves_per_1k"] for p in other_scored])
title_of = {r["post_id"]: r["title"] for r in series_rows}
best = max(series_scored, key=lambda p: p["impressions"]) if series_scored else None
worst = min(series_scored, key=lambda p: p["impressions"]) if series_scored else None
s_hours = [p["hour_local"] for p in series_scored]
add(f"""<section id="series" class="series"><p class="kicker" style="color:var(--series)">Dedicated section</p><h2>{e(S)}</h2>
<p class="sub">{n0(meta.get('series_posts'))} editions from {fdate(meta.get('series_first'))} to {fdate(meta.get('series_latest'))}: {meta.get('series_weeks_with_post','–')} of {meta.get('series_weeks_since_first','–')} weeks covered,
longest streak {meta.get('series_longest_streak_weeks','–')} weeks, current streak {meta.get('series_current_streak_weeks','–')}. Rule: <span class="mono">{e(cfg['series']['include'])}</span>.</p>""")
add('<div class="tiles">')
for kk, v, d in [("Editions", n0(len(series_rows)), f"{len(series_scored)} with metrics"), ("Median reach", k(s_med), f"vs {k(o_med)} other posts"), ("Over 10k", pc(s_share10k, 0), f"vs {pc(share10k, 0)} of all posts"),
                 ("Saves /1k", n1(s_sav), f"vs {n1(o_sav)} other posts"), ("Median length", n0(meta.get("series_median_chars")), "characters"), ("Total reach", k(meta.get("series_impressions_total")), f"{k(meta.get('series_engagements_total'))} engagements")]:
    add(f'<div class="tile"><div class="k">{e(kk)}</div><div class="v">{v}</div><div class="d">{e(d)}</div></div>')
add("</div>")
add("<h3>Reach per edition</h3>")
ed_rows = [{"n": f"#{r['n']}", "imp": r["impressions"], "t": r["title"], "top": r["in_linkedin_top50"]} for r in series_rows if r["impressions"] is not None]
add(vbar(ed_rows, "n", "imp", height=220, color="var(--series)", every=1 if len(ed_rows) <= 20 else 2))
if best and worst:
    add(f"""<p class="finding">Best edition: {fdate(best['date_local'])}, {k(best['impressions'])} impressions, {n0(best['chars'])} chars, {n1(best['saves_per_1k'])} saves per 1k ("{e((title_of.get(best['id']) or best['first_line'] or '')[:90])}").
Weakest: {fdate(worst['date_local'])}, {k(worst['impressions'])} impressions, {n0(worst['chars'])} chars. Editions publish at {min(s_hours) if s_hours else '–'}:00 to {max(s_hours) if s_hours else '–'}:00 {e(TZL)}, most often {max(set(s_hours), key=s_hours.count) if s_hours else '–'}:00.</p>""")
add("<h3>Series against everything else, within length bands</h3>")
add(table([("Band", "band", "", lambda v: v + " chars"), (S + " n", "series_n", "num", n0), (S + " median", "series_med", "num", n0), ("Other n", "other_n", "num", n0), ("Other median", "other_med", "num", n0),
           ("Ratio", lambda r: (r["series_med"] / r["other_med"]) if r["series_med"] and r["other_med"] else None, "num", lambda v: "–" if v is None else f"{v:.2f}x")], series_band))
sb = next((r for r in series_band if r["band"] == BAND_LABEL["1k-2k"] and r["series_n"] >= 4), None)
if sb:
    add(f"<p class='finding'>At 1k to 2k characters, editions run {k(sb['series_med'])} against {k(sb['other_med'])} for other posts of the same length (n={sb['series_n']} vs {sb['other_n']}). The slot and the format carry reach beyond what length explains.</p>")
if weeks_missed:
    add(f"<p class='note'>Weeks missed since the first edition: {', '.join(e(w) for w in weeks_missed)}.</p>")
add("<h3>All editions</h3>")
add(table([("#", "n", "num", n0), ("Date", "date", "dt", fdate), ("Title", "title", "", lambda v: e(v or "")), ("Chars", "chars", "num", n0), ("Impressions", "impressions", "num", n0), ("Eng.", "engagements", "num", n0),
           ("Saves", "saves", "num", n0), ("Sends", "sends", "num", n0), ("Followers", "followers_gained", "num", n0), ("Media", "media", "", lambda v: e(v or "–")), ("Top 50", "in_linkedin_top50", "", lambda v: "yes" if v else "")], series_rows))
add("<h3>Full text of every edition</h3><p class='note'>Click an edition to expand it.</p>")
for r in series_rows:
    m = f"{k(r['impressions'])} imp · {n0(r['saves'])} saves · {n0(r['sends'])} sends" if r["impressions"] is not None else "no metrics"
    add(f"""<details class="ed"><summary><span class="n">#{r['n']}</span><span class="t">{e(r['title'] or '')}</span><span class="m">{m}</span></summary>
<div class="body"><div class="meta">{fdate(r['date'])} · {e(r['weekday'] or '')} {r['hour_local'] if r['hour_local'] is not None else ''}:00 · {n0(r['chars'])} chars · {e(r['media'] or '')} · {e(r['topics'] or '')} · <a href="{e(r['url'])}" target="_blank" rel="noopener">open on LinkedIn</a></div>
<pre>{e(r['text'] or '')}</pre></div></details>""")
add("</section>")

# best posts
add(f"""<section id="best"><h2>Best posts</h2><p class="sub">Top 15 by impressions, then top 10 by saves per 1k, the metric that travels with reach.</p><h3>By impressions</h3>""")
fl = lambda p: f"<a href='{e(p['url'])}' target='_blank' rel='noopener'>{e((p['first_line'] or '')[:110])}</a>" + (" <span class='pill series'>series</span>" if p["is_series"] else "")
cols_best = [("Date", "date_local", "dt", fdate), ("Day", "weekday", "", lambda v: v[:3]), ("Post", fl, "fl", lambda v: v), ("Chars", "chars", "num", n0), ("Media", "media", "", None), ("Impressions", "impressions", "num", n0), ("Saves /1k", "saves_per_1k", "num", n1), ("Sends /1k", "sends_per_1k", "num", lambda v: n1(v, 2)), ("Comments", "comments", "num", n0)]
add(table(cols_best, top_imp, cls="archive"))
add("<h3>By saves per 1k</h3>")
add(table(cols_best, top_sav, cls="archive"))
add("</section>")

# archive
add(f"""<section id="archive"><h2>Archive</h2><p class="sub">Every post, newest first. Click a header to sort. Posts before the analytics window have no numbers.</p>""")
add('<div class="tw"><table class="archive" id="arch"><thead><tr><th class="sort" data-k="0">Date</th><th>Day</th><th>Post</th><th class="num sort" data-k="3">Chars</th><th>Band</th><th>Topics</th><th>Media</th><th class="num sort" data-k="7">Impressions</th><th class="num sort" data-k="8">Saves /1k</th><th class="num sort" data-k="9">Eng. rate</th></tr></thead><tbody>')
for p in posts_all:
    pill = " <span class='pill series'>series</span>" if p["is_series"] else ""
    pill += " <span class='pill top'>top 50</span>" if p["in_linkedin_top50"] else ""
    add(f"<tr><td class='dt' data-v='{p['published_utc']}'>{fdate(p['date_local'])}</td><td>{p['weekday'][:3]}</td><td class='fl'><a href='{e(p['url'])}' target='_blank' rel='noopener'>{e((p['first_line'] or '')[:120])}</a>{pill}</td>"
        f"<td class='num' data-v='{p['chars']}'>{n0(p['chars'])}</td><td>{e(p['length_band'])}</td><td>{e(p['topics'] or '')}</td><td>{e(p['media'] or '')}</td>"
        f"<td class='num' data-v='{p['impressions'] if p['impressions'] is not None else -1}'>{n0(p['impressions'])}</td><td class='num' data-v='{p['saves_per_1k'] if p['saves_per_1k'] is not None else -1}'>{n1(p['saves_per_1k'])}</td><td class='num' data-v='{p['engagement_rate'] if p['engagement_rate'] is not None else -1}'>{pc(p['engagement_rate'])}</td></tr>")
add("</tbody></table></div></section>")
add(f"""<footer>Built {e(meta.get('built_at',''))} from posting-record.sqlite (db.py). Metrics collected {fdate(meta.get('metrics_collected'))} from the per-post analytics pages of the account's own posts; daily series and audience from the analytics export ({window}); post text and dates from the data export plus posts discovered by the weekly run. Medians, not means. n on everything.</footer></div>
<script>
(function(){{
  var t=document.getElementById('arch'); if(!t) return;
  var dir={{}};
  t.querySelectorAll('th.sort').forEach(function(th){{
    th.addEventListener('click',function(){{
      var kx=+th.dataset.k, asc=dir[kx]=!dir[kx];
      var rows=Array.prototype.slice.call(t.tBodies[0].rows);
      rows.sort(function(a,b){{
        var va=a.cells[kx].dataset.v, vb=b.cells[kx].dataset.v;
        var na=parseFloat(va), nb=parseFloat(vb);
        var c=(!isNaN(na)&&!isNaN(nb))?na-nb:String(va).localeCompare(String(vb));
        return asc?c:-c;
      }});
      rows.forEach(function(r){{t.tBodies[0].appendChild(r)}});
    }});
  }});
}})();
</script>""")

os.makedirs(os.path.dirname(out_path), exist_ok=True)
open(out_path, "w", encoding="utf-8").write("\n".join(H))
con.close()
shutil.rmtree(tmpdir, ignore_errors=True)
print(f"wrote {os.path.relpath(out_path, wpath())} ({os.path.getsize(out_path)//1024} KB): {len(posts_all)} posts, {len(scored)} with metrics, {len(series_rows)} {S} editions")
