"""Shared helpers: workspace resolution and config loading.

The workspace is the folder holding config.json and the data files. It is the current
directory, unless PR_WORKSPACE is set. Scripts never write outside it.
"""
import html, json, os, sys

DEFAULTS = {
    "profile": "",
    "timezone": "UTC",
    "timezone_label": "UTC",
    "series": {"name": "Series", "slug": "series", "include": "$^", "exclude": "$^"},
    "topics": [],
    "dashboards": {"posting_record_url": "", "post_explorer_url": ""},
}


def workspace():
    ws = os.environ.get("PR_WORKSPACE") or os.getcwd()
    if not os.path.isdir(ws):
        sys.exit(f"workspace not found: {ws}")
    return os.path.abspath(ws)


def load_config(ws=None):
    ws = ws or workspace()
    path = os.path.join(ws, "config.json")
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(path):
        user = json.load(open(path, encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    else:
        print("note: no config.json in workspace, using defaults (copy config.example.json)", file=sys.stderr)
    return cfg


def wpath(*parts):
    return os.path.join(workspace(), *parts)


def read_json(name, default=None):
    p = wpath(name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def write_json(name, obj, indent=None):
    with open(wpath(name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)


def id_time(pid):
    """LinkedIn ids embed milliseconds since epoch in the top bits."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp((int(pid) >> 22) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
# ---- escaping for generated HTML --------------------------------------------------
# These pages are built to be published. Every value that reaches one has a context, and
# each context needs its own escaping: never the other one, never none.

def js_literal(s):
    """A complete JS string literal, quotes included. '</' is broken up so no value can
    close the <script> block that holds it."""
    return json.dumps(s, ensure_ascii=False).replace("</", "<\\/")


def js_data(obj):
    """A JSON literal for embedding in <script>, with the same '</' protection."""
    return json.dumps(obj, ensure_ascii=False, default=str).replace("</", "<\\/")


def html_text(s):
    """Escape for an HTML text or attribute slot."""
    return html.escape(str(s))
