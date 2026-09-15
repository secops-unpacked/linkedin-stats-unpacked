# Security policy

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/secops-unpacked/linkedin-stats-unpacked/security/advisories/new) on this repository. Please do not open a public issue for anything exploitable.

Expect an acknowledgement within 5 working days. This is a personal project, not a funded one, so there is no bounty and no formal SLA beyond that.

## What this project is, in threat-model terms

Three properties are worth attacking, and worth defending:

**1. The collector runs inside your authenticated LinkedIn session.**
`scripts/collector.js` is pasted into a tab you are already logged into and issues `fetch(..., {credentials: 'include'})`. Every URL it builds has a hardcoded `https://www.linkedin.com/` prefix, so no value from `config.json` or a data file can redirect a credentialed request to another origin. It reads only your own activity page, your own post pages and your own per-post analytics pages. It never writes, posts, reacts, messages or follows, and it never handles a password, cookie or token.

A change that introduces a fetch to any other host, or that makes the host portion of a URL data-dependent, is a vulnerability in this project. Report it.

**2. The dashboards are built to be published.**
`build.py` and `report.py` inline your post text and metrics into HTML that the skill tells you to publish. Values from `config.json` are escaped for the context they land in: `html.escape` for HTML text, a complete JSON literal with `</` broken up for JavaScript source. Post text is escaped at render time with `esc()` in the templates and `html.escape` in `report.py`.

An input that reaches the generated HTML unescaped is a vulnerability. Report it.

**3. This repo ships agent instructions that load automatically.**
`.claude/skills/` and `.agents/skills/` are symlinks into `skill/linkedin-post-analytics/`. Anyone who clones this repo and opens it in Claude Code, Codex CLI or a comparable agent has `SKILL.md` loaded into that agent's context without an explicit action. A change to `SKILL.md` therefore changes what other people's agents do.

For that reason, `skill/` is owned in `.github/CODEOWNERS` and changes to it need review. If you are reviewing a pull request that touches `SKILL.md`, read it as executable instruction, not as documentation.

## Out of scope

- LinkedIn's own rate limits and User Agreement. Automating your own account is a grey area; the README says so and the rate is deliberately slow.
- Anything about a dashboard you chose to publish. The link is the access control.
- Regex patterns you put in your own `config.json` hanging your own machine.
