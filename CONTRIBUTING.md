# Contributing

Issues and pull requests welcome. Two things to know before you open one.

## This repo ships agent instructions

`.claude/skills/` and `.agents/skills/` symlink to `skill/linkedin-post-analytics/`. Cloning this repo and opening it in an agent loads `SKILL.md` into that agent's context automatically. A pull request that edits `SKILL.md` is a pull request that changes what other people's agents do, so it gets read line by line and it will take longer to merge than a code change of the same size. Say in the PR description what behaviour you are changing and why.

The same applies to `scripts/collector.js`: it runs inside a reader's authenticated LinkedIn session.

## Design constraints, not preferences

Please do not break these in a PR. If you think one is wrong, open an issue first.

1. **Own posts only.** The collector reads the author's own activity, post and analytics pages. It does not read other people's posts, and it does not write, post, react, message or follow. This is the line between own-data analytics and scraping.
2. **No credentials, ever.** No password, cookie or token is asked for, stored, logged or passed between processes. The browser session the user already has is the only access.
3. **linkedin.com only.** Every URL the collector builds has a hardcoded `https://www.linkedin.com/` prefix. The host portion of a URL is never data-dependent.
4. **The generated dashboards make no external requests.** No CDN fonts, no analytics, no remote scripts or styles. They are published to public URLs and must not phone anywhere.
5. **Escape for the context.** Anything from `config.json`, a post, or a collector dump that reaches generated HTML gets `html.escape` in an HTML slot and a complete JSON literal (with `</` broken up) in a JavaScript slot. See `fill()` in `scripts/build.py`.
6. **Workspace only.** Scripts read and write the workspace folder (the current directory, or `PR_WORKSPACE`) and nothing else. Never write next to an input file.
7. **Keep the dependency surface at zero.** Standard library Python plus a pinned `openpyxl`. No npm, no lockfile, no GitHub Actions, no vendored JavaScript.
8. **No real account data in the repo.** No screenshots, exports, dumps or dashboards containing a real person's posts, follower counts or audience. Use `tools/make-example-data.py` for anything that needs to look like a populated dashboard. Git history is forever on a public repo, and deleting a file in a later commit does not remove it: the blob stays reachable by SHA long after the file disappears from the tree.

## Running it

```bash
pip install -r requirements.txt
cd my-workspace
cp /path/to/config.example.json config.json   # then edit it
python3 /path/to/scripts/prepare.py ~/Downloads/Complete_LinkedInDataExport.zip
python3 /path/to/scripts/build.py
```

There is no test suite yet. If you change a parser or the escaping in `build.py`, say in the PR how you checked it.
