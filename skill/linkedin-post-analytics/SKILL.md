---
name: linkedin-post-analytics
description: "Analyze your own LinkedIn post performance with your own data: parse the data export and analytics xlsx, collect per-post reach and engagement from the user's own logged-in browser, build the Posting Record and Post Explorer dashboards, track a recurring series, and run a weekly refresh."
---

# LinkedIn post analytics (Posting Record)

Own-data analytics for a LinkedIn creator. Use when the user wants stats on their posts, per-post reach and engagement, a refresh of their dashboards, series tracking, trend and correlation analysis, or when a weekly scheduled refresh fires. Own posts only. Never collect other people's posts and never ask for or enter LinkedIn credentials; the browser session the user is already logged into is the only access.

This skill follows the open Agent Skills format and runs in Claude (desktop app with Claude in Chrome), ChatGPT desktop with Computer Use, Claude Code, Codex CLI, or any agent that reads SKILL.md. The scripts are plain Python and run anywhere. The only step that needs a browser is the collector; see the three paths below.

Scripts and templates live in the `scripts/` and `templates/` folders next to this file. Data lives in a **workspace** folder the user chooses (their LinkedIn export folder is a good one). Every script reads and writes the workspace only: run it with the workspace as the current directory, or set `PR_WORKSPACE`.

## Setup, once

1. Copy `config.example.json` to `config.json` in the workspace. Set `profile` (LinkedIn handle), `timezone` (IANA name) and `timezone_label`, the `series` rule if the user runs a recurring post series (name, slug, include regex, exclude regex; set both regexes to `$^` if they do not), and the `topics` list (name plus regex on post text; a post can match several).
2. Ask the user for their LinkedIn data export (Settings > Data privacy > Get a copy of your data; a zip, ready within 24 hours) and, optionally, the analytics export (`linkedin.com/analytics/creator/content/` > Export; an xlsx).
3. `python3 scripts/prepare.py <export zip or folder>` writes `posts.json`. `python3 scripts/analytics.py <xlsx>` writes `analytics.json`.
4. Run the collector (below), then `python3 scripts/weekly.py runs/<date>/collected.csv runs/<date>/discovered.json`, which writes the first metrics snapshot, the series export and `dist/` with both dashboards.
5. Publish `dist/posting-record.html`, `dist/post-explorer.html` and `dist/posting-report.html` with the Artifact tool and store the three URLs in `config.json` under `dashboards` so later runs republish to the same links.

## Data sources and what each lacks

| Source | Gives | Lacks |
|---|---|---|
| Data export zip | Every post ever: date (UTC), URL, text, visibility. Engagement the user gave. | Any performance data. Media type. Can miss the most recent posts. |
| Analytics xlsx | Daily impressions and engagements, followers per day, audience demographics, top 50 posts. | 365-day rolling window. Per-post only for the top 50. Manual click. |
| Per-post analytics page `linkedin.com/analytics/post-summary/urn:li:activity:ID/` | Impressions, reactions, comments, reposts, saves, sends, profile viewers, followers gained, link visits, video views. | Members reached and in/out-network split are client-rendered, not in page source. |
| Activity page `linkedin.com/in/<profile>/recent-activity/all/` | Server-rendered list of the last ~30 activities. | Mixes own posts with reposts and reacted posts; filter by whether the analytics page returns impressions. |
| Post page `linkedin.com/feed/update/urn:li:activity:ID/` | Full text in `<p data-testid="expandable-text-box">...</p>`, media markers, share URN. | No absolute date; derive it from the id. |
| DMA Member Data Portability API | Same as the data export, programmatic, EU-only. | Read-only, no performance data. |

Two facts that make the weekly loop export-free: **LinkedIn ids encode the publish time** (`id >> 22` is milliseconds since epoch), and the activity page plus post page give text, date and metrics for new posts. The xlsx stays an occasional manual step for the daily series and audience.

## Collector: per-post metrics from the user's logged-in browser (`scripts/collector.js`)

Three paths, same script, same output.

**Path A, agent executes JavaScript in the user's tab (Claude in Chrome).** Prerequisite: Chrome open with the Claude extension connected and LinkedIn logged in. `tabs_context_mcp` with createIfEmpty, `tabs_create_mcp`, navigate to `https://www.linkedin.com/analytics/creator/content/`. If the extension is not connected, say so and stop; on a scheduled run never fall back to another browser and never retry. If the page shows a login form, report that LinkedIn is logged out and stop.

**Path B, agent operates the user's browser through the screen (ChatGPT Computer Use or similar).** You can see and click the user's logged-in browser but cannot inject JavaScript. Do what a person would: open `https://www.linkedin.com/analytics/creator/content/`, open DevTools with Cmd+Option+J (macOS) or F12 (Windows) and select the Console tab, put the contents of `collector.js` on the clipboard from the shell (`pbcopy < scripts/collector.js` on macOS, `Get-Content scripts/collector.js | Set-Clipboard` on Windows), paste into the console and press Enter, then type `PR.start({known: [...], profile: '<handle>', refreshKnown: true})` and Enter. Every few minutes type `PR.status()` and read the result from the screen. When `running` is false, type `PR.download()`: it saves `posting-record-dump-<date>.txt` to the Downloads folder. Move that file into `runs/<date>/` and pass it straight to `weekly.py`, which splits it. Do not read the numbers off the screen and do not use `PR.dump()` on this path. Never enter credentials; if the page shows a login form, stop and tell the user.

**Path C, the user runs it (any agent without a browser tool, Codex included).** Ask the user to do the Path B steps themselves in the browser where they are logged in, ending with `PR.download()`, and to tell you where the file landed (or drop it into `runs/<date>/`). `copy(PR.dumpText())` in the DevTools console is an alternative that puts the dump on the clipboard. Never ask them for cookies, tokens or a password.

1. Read the known activity ids from the workspace: `python3 -c "import json;print(json.dumps([m['activity'] for m in json.load(open('post_metrics.json'))['posts']]))"` (empty list on the first run; then also pass `seed: [share or ugcPost ids from posts.json]`, see the file header).
2. Paste the full contents of `collector.js` into `javascript_tool` once to define `window.PR`.
3. Run `PR.start({known: [...], profile: '<handle from config>', refreshKnown: true})`. It discovers new activities from the activity page, keeps those whose analytics page returns impressions, reads text and media for new posts, and re-collects every known post. About 4 seconds per post.
4. Poll `PR.status()` with Bash `sleep` between polls (3 to 5 minutes). Do not poll with repeated browser calls.
5. When `running` is false, run `PR.dump()` then `get_page_text` (Path A). Two blocks: `COLLECTED_START..COLLECTED_END` (csv) and `DISCOVERED_START..DISCOVERED_END` (json). Save them as `runs/<date>/collected.csv` and `runs/<date>/discovered.json` in the workspace, or save the whole text as `runs/<date>/dump.txt` and let `weekly.py` split it. Close the tab.

Gotchas already handled in collector.js, in case it must be rewritten:
- The analytics URL needs the **activity** URN, not the share id. The post page HTML contains `urn:li:activity:\d{19}`.
- Both pages are server-rendered; `fetch(url, {credentials:'include'})` returns the numbers. Never navigate per post.
- `DOMParser` on fetched HTML returns an empty document in that page context; strip tags with regex.
- `javascript_tool` output is cut near 1 KB and the extension blocks results that look like cookie strings. Never return raw page text; write to a `<pre>` and read with `get_page_text`. A call that awaits more than about 40 seconds times out; run loops async and poll.
- Post text: capture `data-testid="expandable-text-box"[^>]*>([\s\S]*?)<\/p>`. Stopping at `</span>` truncates at the first mention or link.
- The share URN on a post page can differ from the export's share id for the same activity. **The activity id is the stable key**; `weekly.py` maps activity to id through the previous snapshot and drops rows it cannot map.
- Media, from the post page: `video_views` present means video; `chunked-pdf|sanitized-pdf|feedshare-document` means document; more than 100 `poll` hits means poll; ugcPost with 2+ distinct `dms/image/v2/<asset>/feedshare-` assets means image. GIF is not detectable from source.

## Merge and rebuild (`scripts/weekly.py`)

`python3 scripts/weekly.py runs/<date>/collected.csv runs/<date>/discovered.json` (or `python3 scripts/weekly.py runs/<date>/posting-record-dump-<date>.txt`, it splits the dump itself) in the workspace: appends discovered posts to `posts.json` (dedup by id and activity, date from the id), writes `snapshots/post_metrics_<date>.json`, replaces `post_metrics.json` with the merged latest view (posts not re-collected keep their last numbers), then runs `series.py`, `build.py` and `db.py`. Snapshots are the point: with two or more, per-post reach curves and week-over-week growth become measurable.

## Query database (`scripts/db.py`)

`python3 scripts/db.py [--export <export folder or zip>]` rebuilds `posting-record.sqlite` in the workspace from every file above, plus `posting-record.schema.md`. Use it whenever a question needs more than the dashboards show, when the user wants "posts like this one", or when another agent or LLM needs the data: one file, standard SQLite, no server.

- `posts` (one row per post: local publish time, weekday, hour, quarter, chars, words, paragraphs, length band, first line, hashtags, link in text, link in own comment from the export's Comments file, media, series edition, topics via `post_topics`), `post_metrics` (latest numbers with per-1k rates, engagement rate, quartile and rank), `post_metrics_snapshots` (every collection), `daily_stats`, `followers_daily`, `audience`, `linkedin_top_posts`, `series_editions`, `engagement_given`.
- Precomputed medians in `stats_by_length_band`, `stats_by_weekday`, `stats_by_hour`, `stats_by_media`, `stats_by_topic`, `stats_by_quarter`, `stats_by_month`, `stats_by_link_placement`, `stats_series_vs_rest`, the same `*_within_band`, `stats_top_quartile_vs_rest` and Spearman `correlations`. These are the dashboard numbers; cite them rather than recomputing with AVG.
- Views `v_posts` (post + latest metrics + topics; start here), `v_reach_curve`, `v_growth`, `v_series`. `schema_notes` documents every column from inside the file: `SELECT * FROM schema_notes`.
- Run it after any change to `config.json` topics or the series rule. `weekly.py` runs it automatically. Query with `python3 -c "import sqlite3..."` when the `sqlite3` binary is missing. SQLite cannot lock files on bridge or network mounts: db.py builds in a temp folder and copies the file in; copy it out before querying it from such a mount.

## Report (`scripts/report.py`)

`python3 scripts/report.py` reads the database and writes `dist/posting-report.html`: a single document, read top to bottom, covering cadence since the first post, reach by length, weekday (within bands), hour, link placement, what travels with reach (top quartile vs rest, correlations), topics and media, quarterly trend and followers, reach over time from the snapshots, a dedicated series section (tiles, reach per edition, series vs other posts within length bands, missed weeks, the editions table, and the full text of every edition), best posts, and a sortable archive of every post. `weekly.py` runs it after `db.py`. Publish it as a third artifact and keep its URL in `config.json` under `dashboards.posting_report_url`.

## Publish

In Claude: stage `dist/posting-record.html` and `dist/post-explorer.html` into the cloud workspace and publish with the Artifact tool. On later runs pass the URLs from `config.json` (`dashboards.posting_record_url`, `dashboards.post_explorer_url`) so links stay stable; read each artifact once before publishing to it. Elsewhere: the two files in `dist/` are self-contained HTML with no external requests of any kind; open them locally or put them on any static host (GitHub Pages, Netlify, an S3 bucket). No server, no build step.

Publishing puts the full text and numbers of every post on a public URL. Say so before publishing, and offer the local `dist/` files as the alternative.

Posting Record: tiles, Performance (impressions per day with publish ticks, reach by length and weekday, engagement mix top quarter vs rest, media type, followers, audience), Quarterly trends, series tracker, cadence charts, hashtags by era, engagement given, searchable archive. Post Explorer: outcome selector, filters (topic, weekday, media, length), scatter, topic by length and weekday by hour matrices, Spearman correlation bars, controlled comparisons within length bands, topic trends by month.

## Weekly scheduled run

In Claude, create a scheduled task bound to the user's computer (it needs Chrome with the extension) that: runs the collector, runs `weekly.py`, republishes both dashboards by URL, and reports in five lines: new posts and their reach, best and worst post of the past 7 days with chars and weekday, whether the latest series edition landed, collector errors, and which of last week's posts grew most against the prior snapshot. Preconditions the user must keep: computer awake, Chrome open with the extension, LinkedIn logged in. If any is missing the run reports and stops. Never delete files.

ChatGPT desktop has no scheduler per OpenAI's docs: the user asks for the weekly run and Path B does the rest. Without any browser tool (Codex, plain terminal): Path C by hand, then `weekly.py`. A cron entry can run `weekly.py` once the dump file has landed in `runs/<date>/`.

When writing files to the user's computer, write through the shell (heredoc or base64); a second file-bridge commit to the same path can deliver a stale copy.

## Analysis rules

- Medians, never means. Reach is heavy-tailed; the top 10 posts typically carry about 30% of impressions.
- Every cell shows n. Fade cells under 4 posts, hide correlation rows under 8.
- Length confounds everything. Any claim about day, topic, media or link placement is shown within length bands before it is stated.
- Convert publish times from UTC to the user's timezone at analysis time.
- Topics are keyword regexes on text; a post can match several. Keep the list user-editable in `config.json`.
- Palette: single blue for one series, ordinal blue ramp for length bands, categorical blue/orange/aqua/yellow for up to four topics with Other in grey, diverging blue/red for correlations. Light and dark tokens both on :root.

## What to expect (one account's baseline, ~150 posts over 12 months)

Length was the strongest single feature (Spearman 0.51 with impressions, 0.61 with saves per 1k); each length band roughly doubled median reach. A weekly series slot held up within length bands. Saves and sends separated top-quartile posts from the rest; comments and reposts did not. Link in comment beat link in text only in raw numbers; within a length band placements were within 5%. Hour of day, hashtags, question openers and numbers in the first line were noise. Treat these as hypotheses to test on the user's data, not conclusions.
