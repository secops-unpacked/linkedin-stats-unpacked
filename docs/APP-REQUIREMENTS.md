# LinkedIn Post Analytics: requirements and build plan

Working name: **Posting Record**. Own-data analytics for LinkedIn creators. Everything we did by hand in this session, packaged so it runs again next month without me.

## 1. The constraint that shapes everything

LinkedIn does not expose personal post performance through any API. This decides the architecture before any feature does.

| Source | What it gives | How you get it | Limits |
|---|---|---|---|
| Data export (zip) | Every post you ever wrote: date, URL, text, visibility. Comments and reactions you gave. | Settings > Get a copy of your data. Manual, takes up to 24h. | No performance data at all. |
| Analytics export (xlsx) | Daily impressions and engagements, followers per day, audience demographics, top 50 posts. | Analytics page > Export. Manual click. | 365-day window. Per-post only for the top 50. |
| Per-post analytics page | Impressions, reactions, comments, reposts, saves, sends, profile views, followers gained, media. | One page per post, behind your login. | No export. Must be read from the page. Automating this sits in a grey area of LinkedIn's User Agreement. |
| DMA Member Data Portability API | Same as the data export, programmatic. | OAuth app, EU/EEA members only. | Read-only. No performance data. Snapshot pinned to API version 202312. |

Consequences:

1. **The app cannot log into LinkedIn for the user.** Any product that asks for LinkedIn credentials is a non-starter. Ingestion is upload-based, plus an optional browser extension that reads pages while the user is already logged in.
2. **The 365-day window rolls.** Every analytics export overwrites the last one. The app's real value over a one-off analysis is keeping history: snapshots accumulate, so in a year you have two years of per-post data and can see how a post's reach grows over its first week.
3. **Lovable is the right tool for the dashboard and the wrong tool for collection.** It builds a React front end with Supabase storage well. It cannot build a Chrome extension, and it should not be where a scraper lives. Split the product accordingly.

## 2. Product shape

Three pieces, one repository, MIT licensed.

```
posting-record/
  collector/     Chrome extension (MV3). Reads per-post analytics while you are logged in. Exports JSON.
  pipeline/      Python CLI. Parses the data export, the analytics xlsx and collector output into one dataset.
  app/           Web dashboard. Reads the dataset. Lovable-built or static.
```

The pipeline already exists in `scripts/`: `prepare.py`, `analytics.py`, `series.py`, plus the collector. Phase 1 is packaging, not invention.

Two deployment modes, same code:

- **Local-first (default).** User runs the pipeline on their machine, opens the dashboard as a static page. No account, no server, nothing leaves the laptop. This is the open-source story.
- **Hosted (optional).** Lovable app with Supabase. User uploads the three files, the app runs the pipeline in an edge function, dashboards read from Postgres. Multi-user, shareable links, history kept server-side.

Build local-first first. Hosted is a wrapper around a working pipeline.

## 3. Functional requirements

### Ingestion
- R1. Accept the LinkedIn data export as a zip or an unzipped folder. Parse `Shares_*.csv`, `Comments_*.csv`, `Reactions_*.csv`, `InstantReposts_*.csv`, `Rich_Media.csv`. Handle LinkedIn's per-line quoting inside multi-line posts.
- R2. Accept the analytics xlsx. Parse all six sheets. Detect the window from the DISCOVERY sheet.
- R3. Accept collector JSON: one record per post per collection date.
- R4. Every ingestion is a snapshot with a collection date. Never overwrite. Metrics for the same post on two dates are two rows.
- R5. Idempotent: re-uploading the same file changes nothing.
- R6. Join on the 19-digit activity or share ID. Store the share-to-activity mapping the collector discovers, since the two differ and the analytics page uses the activity form.

### Data model
- Posts: id, activity_id, published_at (UTC), url, text, chars, words, media_type, image_count, is_reshare.
- Post metrics snapshots: post_id, collected_at, impressions, reactions, comments, reposts, saves, sends, profile_views, followers_gained, link_visits, video_views, in_network_pct, out_of_network_pct, members_reached.
- Daily stats: date, impressions, engagements, new_followers.
- Audience demographics: snapshot date, dimension, value, share.
- Own engagement: reactions and comments the user gave, with target activity ID and whether the target is their own post.
- Topics: rule name, regex, ordered. User-editable. A post can match many.
- Series: name, inclusion rule, exclusion rule. One recurring series is supported to start with.

### Analysis (all of this exists in the session artifacts and must be reproduced)
- R7. Cadence: posts per month, per weekday, per hour in the user's timezone. Length distribution by year.
- R8. Reach and engagement per post; medians by length band, weekday, hour band, media type, topic.
- R9. Engagement mix: per-1,000-impression rates for each action, top quartile against the rest.
- R10. Correlation panel: Spearman between features (length, paragraphs, hour, weekday flags, media, hashtags, question opener, number opener, link placement, topics) and any outcome.
- R11. Controlled comparisons: any binary split, within length bands.
- R12. Topic trends by month against the all-posts median.
- R13. Series tracker: editions, weeks covered, streaks, per-edition metrics, exportable.
- R14. Link placement: link in text vs link in own comment vs none, joined from the comments export.
- R15. New with history: reach curve per post (day 1, 3, 7, 30 from snapshots); quarter-over-quarter per-post reach; audience drift.

### Output
- R16. Dashboard with the sections built in this session: Posting Record (summary), Performance, Quarterly trends, Series, Explorer (filters, scatter, matrices, correlations, controlled comparisons).
- R17. Export any table as CSV and the full dataset as JSON. The website feed for a recurring series is one of these exports.
- R18. Every chart states its n. Small cells are faded, not hidden.

### Non-functional
- N1. No LinkedIn credentials ever stored or requested. The extension uses the existing browser session and nothing else.
- N2. Local-first works with no network.
- N3. Collector rate limit: one request per 1.5 to 2 seconds, randomized, with a stop button. Roughly 4 seconds per post end to end; that is the right speed.
- N4. Parsing must survive column renames. LinkedIn changes export headers without notice. Match headers case- and punctuation-insensitively, keep raw rows.
- N5. Timezone is a user setting, applied at analysis time, not at ingestion.
- N6. One command reproduces the whole dashboard from raw files.
- N7. Clear terms notice: the collector reads your own analytics pages. Users decide whether that is acceptable to them under LinkedIn's agreement.

## 4. Implementation steps

### Phase 1: pipeline as a package (1 to 2 weekends)
1. Move `prepare.py`, `analytics.py`, `series.py` into a `posting_record` Python package with a CLI: `pr ingest export.zip`, `pr ingest analytics.xlsx`, `pr ingest collector.json`, `pr build`.
2. Replace ad-hoc JSON files with SQLite (or DuckDB if you prefer analytics-shaped queries). One file, portable, no server.
3. Implement snapshot semantics (R4, R5). Add a migration for the existing `post_metrics.json` as snapshot date 2026-09-13.
4. Port the topic rules and series rules to a `rules.yaml` the user edits.
5. Port the analysis functions. Pin the tests to a fixture dataset so the numbers stay the reference across refactors.
6. `pr build` writes `dashboard.html` from a template with the dataset inlined, which is exactly how the current artifacts work.

### Phase 2: collector extension (1 weekend)
1. Chrome MV3 extension, no external requests, no analytics of its own.
2. Popup: paste or load a list of post URLs (the pipeline exports this list). Button: Collect. Progress bar. Stop.
3. For each post: fetch the post page with the session cookie, extract the activity URN, fetch the analytics page, parse the server-rendered fields. This is the exact code that ran in this session, moved into a content script.
4. Optional second pass that opens the page in a hidden tab to read the client-side fields: members reached, in-network split. Slower. Off by default.
5. Media detection from the post page: video views present means video, chunked-pdf markers mean document, poll markers mean poll, image asset counts for images. GIF is not detectable from source; leave it as image.
6. Output: one JSON file per run with a collection timestamp. Feeds `pr ingest`.

### Phase 3: dashboard (Lovable or static, 1 weekend)
Static path: the Phase 1 template already is the dashboard. Add a file picker so the HTML loads a dataset instead of inlining it.

Lovable path:
1. Prompt Lovable with the data model from section 3 and ask for Supabase tables plus an upload page for three file types.
2. Port the pipeline to a Supabase edge function (Deno) or keep Python and call it from a small worker. Keep Python; the parsing edge cases are already solved there.
3. Rebuild the five dashboard sections as React pages. Give Lovable the current artifact HTML as the reference for each chart, panel by panel. It reproduces layout well from a working example and badly from a description.
4. Auth via Supabase, one user per workspace. Shareable read-only links per dashboard.
5. Keep the static export: a hosted user should still be able to download the same `dashboard.html`.

### Phase 4: what history unlocks (after two or more collections)
1. Reach curves: same post, several snapshots. Answer "how much of a post's reach arrives in the first 48 hours" with your own data.
2. Alerting: a post that crosses 10k, a series week missed, follower growth off trend. Local notification or email from the hosted version.
3. Compare periods: this quarter's per-post medians against last quarter's, with the same length and topic mix, so the drift in Q1 2026 becomes measurable rather than eyeballed.

## 5. Decisions to make before starting

- **Python or TypeScript for the pipeline.** Python is done and tested. TypeScript would let Lovable and the extension share parsing code. Recommendation: keep Python for Phase 1, revisit only if the hosted version becomes the main product.
- **SQLite or DuckDB.** SQLite if the hosted version will live in Postgres anyway, since the schema ports 1:1. DuckDB if local-first stays primary and you want fast ad-hoc queries over snapshots.
- **Scope of the collector.** Own posts only, by design. Do not generalize it to other people's posts; that is the line between own-data analytics and scraping.
- **Open source first or Lovable first.** Open source. The pipeline and collector are the hard part and are useful to anyone; the dashboard is replaceable. A repo with a working `pr build` and a Chrome extension gets contributors. A Lovable app with no collector gets a nice upload form.

## 6. What already exists

Everything in `scripts/` and `templates/` in this repo: the parsers, the collector, the two dashboards and the SQLite build. A run produces `posts.json`, `analytics.json`, `post_metrics.json`, the series export and `dist/`. The two dashboards are self-contained HTML with the dataset inlined and are the visual reference for Phase 3.
