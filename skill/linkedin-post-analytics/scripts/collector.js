// Posting Record collector. Runs inside a logged-in LinkedIn tab (Claude in Chrome javascript_tool).
// Step A: paste this whole file once to define window.PR.
// Step B: run  PR.start({known: [...activity ids already in post_metrics.json], profile: 'your-handle'})
//         First run, no metrics yet: also pass  seed: [...share/ugcPost ids from posts.json for the posts you want metrics for]
//         (LinkedIn keeps per-post analytics for roughly the last 12 months). Seeds are resolved to activity ids via the post page.
// Step C: poll  PR.status()  until running is false, then either
//           PR.dump()      write the result into the page (for agents that read page text, e.g. Claude in Chrome), or
//           PR.download()  save posting-record-dump-<date>.txt to your Downloads folder (DevTools console, GUI agents), or
//           copy(PR.dumpText())  in the DevTools console to put it on the clipboard.
//         The dump has two blocks: COLLECTED_START..COLLECTED_END (csv) and DISCOVERED_START..DISCOVERED_END (json);
//         weekly.py accepts the whole dump file directly.
// Own posts only. One request every ~1.5 s. Never returns raw page HTML through the tool.

window.PR = (() => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const dec = x => x.replace(/&amp;/g, '&').replace(/&#39;|&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ');
  const fetchText = async url => (await fetch(url, {credentials: 'include'})).text();

  function parseAnalytics(html) {
    const body = html.slice(html.indexOf('<body'));
    const s = body.replace(/<script[\s\S]*?<\/script>/gi, ' ').replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, '\n');
    const lines = s.split('\n').map(l => dec(l).replace(/\s+/g, ' ').trim()).filter(Boolean);
    const num = v => { if (v == null) return null; const m = String(v).replace(/,/g, '').match(/-?\d+(\.\d+)?/); return m ? +m[0] : null; };
    const idx = (label, start = 0) => lines.findIndex((l, i) => i >= start && l === label);
    const before = (label, start = 0) => { const i = idx(label, start); return i > 0 ? lines[i - 1] : null; };
    const out = {};
    const disc = Math.max(0, idx('Discovery'));
    out.impressions = num(before('Impressions', disc));
    out.profile_viewers = num(before('Profile viewers from this post'));
    out.followers_gained = num(before('Followers gained from this post'));
    out.social_engagements = num(before('Social engagements'));
    const eng = Math.max(0, idx('Engagement'));
    for (const k of ['Reactions', 'Comments', 'Reposts', 'Saves', 'Sends on LinkedIn']) { const i = idx(k, eng); out[k.toLowerCase().replace(/ /g, '_')] = i >= 0 ? num(lines[i + 1]) : null; }
    out.link_visits = num(before('Visits to links from this post'));
    out.video_views = num(before('Video views'));
    return out;
  }

  function parsePost(html) {
    const share = (html.match(/urn:li:(?:share|ugcPost):\d{19}/) || [null])[0];
    const m = html.match(/data-testid="expandable-text-box"[^>]*>([\s\S]*?)<\/p>/);
    let text = '';
    if (m) text = dec(m[1].replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '')).replace(/[ \t]+\n/g, '\n').trim();
    const c = re => (html.match(re) || []).length;
    const assets = new Set((html.match(/dms\/image\/v2\/([A-Za-z0-9_-]+)\/feedshare-/g) || []).map(x => x.split('/')[3]));
    const isUgc = /urn:li:ugcPost:/.test(share || '');
    const media = /chunked-pdf|sanitized-pdf|feedshare-document/.test(html) ? 'document'
      : c(/playlist\/vid|feedshare-ambry/g) ? 'video'
      : c(/poll/gi) > 100 ? 'poll'
      : isUgc && assets.size >= 2 ? 'image'
      : isUgc ? 'media_other' : 'text';
    return {share, text, media, images: media === 'image' ? Math.max(1, assets.size - 1) : 0};
  }

  const st = {running: false, done: 0, total: 0, phase: '', collected: {}, discovered: [], errors: {}};

  async function start({known = [], seed = [], profile = '', refreshKnown = true} = {}) {
    st.running = true; st.done = 0; st.phase = 'discover'; st.collected = {}; st.discovered = []; st.errors = {};
    const knownSet = new Set(known.map(String));
    // seeds: posts known from the data export but never collected. Resolve share id -> activity id, then treat as new.
    const seeded = [];
    st.total = seed.length; st.phase = 'seed';
    for (const sid of seed.map(String)) {
      if (!st.running) break;
      try {
        const urn = 'urn:li:' + (sid.startsWith('urn:') ? sid.slice(7) : 'share:' + sid);
        const h = await fetchText('https://www.linkedin.com/feed/update/' + urn + '/');
        const act = (h.match(/urn:li:activity:(\d{19})/) || [null, null])[1];
        if (act) seeded.push({activity: act, isNew: true, seedId: sid.replace(/^urn:li:\w+:/, '')}); else st.errors[sid] = 'seed: no activity urn';
      } catch (e) { st.errors[sid] = String(e); }
      st.done++;
      await sleep(700 + Math.random() * 400);
    }
    // A. discover recent activity ids from the server-rendered activity page
    let recent = [];
    try {
      const h = await fetchText(`https://www.linkedin.com/in/${profile}/recent-activity/all/`);
      recent = [...new Set((h.match(/urn:li:activity:(\d{19})/g) || []).map(x => x.slice(-19)))];
    } catch (e) { st.errors.discover = String(e); }
    const seededActs = new Set(seeded.map(t => t.activity));
    const candidates = recent.filter(a => !knownSet.has(a) && !seededActs.has(a));
    const targets = [...seeded, ...candidates.map(a => ({activity: a, isNew: true})), ...(refreshKnown ? known.map(a => ({activity: String(a), isNew: false})) : [])];
    st.total = targets.length; st.done = 0; st.phase = 'collect';
    for (const t of targets) {
      if (!st.running) break;
      try {
        const an = parseAnalytics(await fetchText(`https://www.linkedin.com/analytics/post-summary/urn:li:activity:${t.activity}/`));
        if (an.impressions == null) { if (!t.isNew) st.errors[t.activity] = 'no impressions parsed'; st.done++; await sleep(700); continue; } // not our post, or page changed
        let post = null;
        if (t.isNew) {
          await sleep(600);
          post = parsePost(await fetchText(`https://www.linkedin.com/feed/update/urn:li:activity:${t.activity}/`));
          if (!post.text) { st.errors[t.activity] = 'new post but no text parsed'; }
          const id = t.seedId || (post.share ? post.share.slice(-19) : t.activity);
          st.discovered.push({id, activity: t.activity, url: post.share ? `https://www.linkedin.com/feed/update/${post.share}` : `https://www.linkedin.com/feed/update/urn:li:activity:${t.activity}`, text: post.text, media: post.media, images: post.images});
          st.collected[id] = {...an, activity: t.activity, media: post.media, images: post.images};
        } else {
          st.collected['A' + t.activity] = {...an, activity: t.activity};
        }
      } catch (e) { st.errors[t.activity] = String(e); }
      st.done++;
      await sleep(900 + Math.random() * 600);
    }
    st.running = false; st.phase = 'done';
  }

  function status() { return {running: st.running, phase: st.phase, done: st.done, total: st.total, discovered: st.discovered.length, errors: Object.keys(st.errors).length}; }

  // Known posts are keyed by activity id ('A' prefix) because the collector never saw their share id;
  // weekly.py maps activity -> id through the previous post_metrics.json. New posts carry their share id.
  function dumpText() {
    const cols = ['id', 'activity', 'impressions', 'social_engagements', 'reactions', 'comments', 'reposts', 'saves', 'sends_on_linkedin', 'profile_viewers', 'followers_gained', 'link_visits', 'video_views', 'media', 'images'];
    const csv = ['id,activity,impressions,social_engagements,reactions,comments,reposts,saves,sends,profile_viewers,followers_gained,link_visits,video_views,media,images']
      .concat(Object.entries(st.collected).map(([k, r]) => [k.startsWith('A') ? '' : k, r.activity, ...cols.slice(2).map(c => r[c] ?? '')].join(','))).join('\n');
    return 'COLLECTED_START\n' + csv + '\nCOLLECTED_END\nDISCOVERED_START\n' + JSON.stringify(st.discovered) + '\nDISCOVERED_END\nERRORS ' + JSON.stringify(st.errors);
  }
  function dump() {
    document.querySelectorAll('main > *').forEach(e => e.remove());
    const pre = document.createElement('pre'); pre.id = 'prdump'; pre.style.cssText = 'font-size:10px;white-space:pre-wrap;word-break:break-all';
    pre.textContent = dumpText();
    (document.querySelector('main') || document.body).prepend(pre);
    return status();
  }
  function download() {
    const name = 'posting-record-dump-' + new Date().toISOString().slice(0, 10) + '.txt';
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([dumpText()], {type: 'text/plain'})); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    return name;
  }

  return {start, status, dump, dumpText, download, stop: () => { st.running = false; }, _st: st};
})();
'PR defined';
