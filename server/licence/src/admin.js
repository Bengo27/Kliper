// The admin dashboard page (served at /admin). One page, no libraries; data comes from /admin/api/*.
export const ADMIN_HTML = (signedIn) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Genie Admin</title>
<style>
:root{--bg:#14121c;--card:#1d1a29;--line:#2e2a40;--text:#eeeaf8;--dim:#9a94b3;--acc:#8b5cf6;--acc2:#a78bfa;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0;flex:1}header .me{color:var(--dim)}
main{max-width:1150px;margin:0 auto;padding:18px 16px 60px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile b{display:block;font-size:24px}.tile span{color:var(--dim);font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:18px}
section h2{font-size:15px;margin:0 0 10px}
.sw{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.sw label{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:9px}
.sw small{display:block;color:var(--dim);font-size:12px}
input[type=text],input[type=number],input[type=search]{background:#110f18;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit}
input[type=number]{width:70px}
.btn{background:#2a2540;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:5px 10px;font:inherit;cursor:pointer}
.btn:hover{border-color:var(--acc2)}.btn.p{background:var(--acc);border-color:var(--acc)}.btn.r{color:#fca5a5}
.tog{appearance:none;width:42px;height:24px;margin:0;border-radius:12px;cursor:pointer;flex:none;background:radial-gradient(circle at 12px 12px,#fff 8px,transparent 9px),#3a3550}
.tog:checked{background:radial-gradient(circle at 30px 12px,#fff 8px,transparent 9px),var(--acc)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--dim);font-weight:500;font-size:12px}td .acts{display:flex;flex-wrap:wrap;gap:4px}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#2a2540}
.pill.owner{background:#4c1d95}.pill.paid{background:#14532d}.pill.trial{background:#3a3550}.pill.used{background:#78350f}.pill.banned{background:#7f1d1d}
.dim{color:var(--dim)}.row{display:flex;gap:8px;align-items:center;margin-bottom:10px}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--acc);padding:8px 14px;border-radius:9px;display:none}
.login{display:grid;place-items:center;min-height:80vh;text-align:center}
.twitch{background:#9146ff;color:#fff;border:0;border-radius:10px;padding:12px 20px;font-size:16px;text-decoration:none;display:inline-block}
.scroll{overflow-x:auto}
</style></head><body>
${signedIn ? `
<header><h1>Clip Genie Admin</h1><span class="me" id="me"></span><a class="btn" href="/admin/logout">Sign out</a></header>
<main>
  <div class="tiles" id="tiles"></div>
  <section><h2>Switches</h2><div class="sw">
    <label><span>Clip Genie is free<small>On = free for everyone: no stream limit, no trial mark, every feature on (the trial switches below do nothing while this is on).</small></span><input class="tog" type="checkbox" data-k="FREE_MODE"></label>
    <label><span>Built-in AI<small>On = everyone gets the AI with no key of their own (uses your OpenRouter key on this server).</small></span><input class="tog" type="checkbox" data-k="AI_OPEN"></label>
    <label><span>AI uses per user per day<small>One stream uses about one per 6 minutes of VOD, plus a few. Owners have no cap.</small></span><input type="number" min="0" max="9999" data-k="AI_DAILY"></label>
    <label><span>AI models<small>OpenRouter model names, tried in order (up to 3). FREE ONLY: models without ":free" are ignored, so this can never cost money.</small></span><input type="text" size="34" data-k="AI_MODELS"></label>
    <label><span>Beta running<small>KILL SWITCH. Off = the beta is over: Clip Genie stops for every tester (paid users and you keep working).</small></span><input class="tog" type="checkbox" data-k="BETA_OPEN"></label>
    <label><span>Pro features in the trial<small>On = everything is unlocked while the trial lasts. Locked once it's used up.</small></span><input class="tog" type="checkbox" data-k="TRIAL_PRO"></label>
    <label><span>Free trials open<small>Off = nobody new gets a trial. Paid users keep working.</small></span><input class="tog" type="checkbox" data-k="TRIALS_OPEN"></label>
    <label><span>Continue without Twitch<small>On = people can start the trial without a Twitch login (counted per PC). Off = Twitch sign-in only.</small></span><input class="tog" type="checkbox" data-k="GUEST_OPEN"></label>
    <label><span>Free streams per user<small>How many streams / clips the trial gives.</small></span><input type="number" min="0" max="999" data-k="TRIAL_LIMIT"></label>
    <label><span>Trial watermark<small>Clip Genie FREE TRIAL logo on videos once the trial is used up.</small></span><input class="tog" type="checkbox" data-k="TRIAL_WATERMARK"></label>
    <label><span>AI in the trial<small>On = trial users can use the AI too.</small></span><input class="tog" type="checkbox" data-k="TRIAL_AI"></label>
    <label><span>Oldest version allowed<small>Older copies must update (e.g. 2.37.0). Empty = any.</small></span><input type="text" size="9" data-k="MIN_VERSION" placeholder="any"></label>
    <label><span>Payment link<small>Shown when a trial is used up.</small></span><input type="text" size="22" data-k="BUY_URL" placeholder="https://..."></label>
    <label><span>Newest version<small>Copies older than this show "Update available" with a Download button (e.g. 0.1.1).</small></span><input type="text" size="9" data-k="LATEST_VERSION" placeholder="none"></label>
    <label><span>Update download link<small>Where the Download button goes (Google Drive, GitHub...).</small></span><input type="text" size="22" data-k="UPDATE_URL" placeholder="https://..."></label>
    <label><span>Message to everyone<small>Shown at the top of every Clip Genie. Empty = none. 200 characters max.</small></span><input type="text" size="22" maxlength="200" data-k="NOTICE" placeholder="none"></label>
  </div></section>
  <section><h2>Users</h2>
    <div class="row"><input type="search" id="q" placeholder="Search Twitch name" size="24"><span class="dim" id="count"></span></div>
    <div class="scroll"><table><thead><tr><th>Twitch</th><th>Plan</th><th>Used</th><th>PCs</th><th>Joined</th><th>Last stream</th><th></th></tr></thead><tbody id="users"></tbody></table></div>
  </section>
  <section><h2>Latest streams added</h2><div class="scroll"><table><thead><tr><th>When</th><th>Who</th><th>Stream</th></tr></thead><tbody id="recent"></tbody></table></div></section>
</main>
<div id="toast"></div>
<script>
const $ = (s) => document.querySelector(s);
const el = (tag, txt, cls) => { const e = document.createElement(tag); if (txt != null) e.textContent = txt; if (cls) e.className = cls; return e; };
const day = (t) => t ? new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" }) : "-";
const ago = (t, now) => { if (!t) return "-"; const s = now - t; return s < 3600 ? Math.max(1, Math.round(s / 60)) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; };
let D = null, timer = 0;
function toast(m, bad) { const t = $("#toast"); t.textContent = m; t.style.background = bad ? "#b91c1c" : ""; t.style.display = "block"; clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = "none", 2200); }
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "content-type": "application/json", "x-cg-admin": "1" }, body: JSON.stringify(body) } : {});
  if (r.status === 401) { location.reload(); throw new Error("signed out"); }
  const j = await r.json(); if (!r.ok) throw new Error(j.error || "Failed"); return j;
}
async function load() {
  D = await api("/admin/api/overview?q=" + encodeURIComponent($("#q").value));
  $("#me").textContent = D.me.name;
  const s = D.stats, tiles = [["Users", s.users], ["New this week", s.new_week], ["Streams this week", s.uses_week], ["Paid", s.paid], ["Trial used up", s.used_up], ["PCs", s.pcs]];
  $("#tiles").replaceChildren(...tiles.map(([n, v]) => { const d = el("div", null, "tile"); d.append(el("b", v), el("span", n)); return d; }));
  document.querySelectorAll("[data-k]").forEach((i) => { if (document.activeElement === i) return; const v = D.cfg[i.dataset.k] ?? "";
    if (i.type === "checkbox") i.checked = v === "1"; else i.value = i.dataset.k === "MIN_VERSION" && v === "0" ? "" : v; });
  drawUsers(); drawRecent();
}
function plan(u) {
  if (u.banned) return ["Blocked", "banned"]; if (u.owner) return ["Owner", "owner"];
  if (u.paid_until > D.now) return ["Paid to " + day(u.paid_until), "paid"];
  if (D.cfg.FREE_MODE === "1") return ["Free", "trial"];
  return u.used >= +D.cfg.TRIAL_LIMIT ? ["Trial used up", "used"] : ["Trial", "trial"];
}
function drawUsers() {
  $("#count").textContent = D.users.length + " shown";
  $("#users").replaceChildren(...D.users.map((u) => {
    const guest = u.twitch_id.startsWith("pc_"), nm = (u.display_name || u.login || "Guest") + (guest ? " (no Twitch)" : "");
    const tr = el("tr"), [pl, pc] = plan(u), who = el("td"); who.append(el("b", u.display_name || u.login || "Guest"), el("div", guest ? "No Twitch login (name typed, not checked)" : u.login, "dim"));
    const ptd = el("td"); ptd.append(el("span", pl, "pill " + pc));
    const acts = el("div", null, "acts"), add = (txt, fn, cls) => { const b = el("button", txt, "btn " + (cls || "")); b.onclick = fn; acts.append(b); };
    const act = async (action, extra, ask) => { if (ask && !confirm(ask)) return; try { await api("/admin/api/user", { twitch_id: u.twitch_id, action, ...extra }); toast("Done"); load(); } catch (e) { toast(e.message, 1); } };
    add("+30 days paid", () => act("paid_days", { days: 30 }), "p");
    add("+1 year", () => act("paid_days", { days: 365 }));
    if (u.paid_until > D.now) add("End paid", () => act("unpaid", {}, "End " + nm + "'s paid time now?"));
    if (u.used) add("Reset trial", () => act("reset_trial", {}, "Give " + nm + " a fresh trial? (Their PC still counts its own streams.)"));
    add(u.owner ? "Remove owner" : "Make owner", () => act(u.owner ? "unowner" : "owner", {}, u.owner ? null : "Give " + nm + " unlimited use forever?"));
    add(u.banned ? "Unblock" : "Block", () => act(u.banned ? "unban" : "ban", {}, u.banned ? null : "Block " + nm + "? They can't add streams and get signed out."), u.banned ? "" : "r");
    const atd = el("td"); atd.append(acts);
    tr.append(who, ptd, el("td", u.used + (u.owner || u.paid_until > D.now ? "" : " / " + D.cfg.TRIAL_LIMIT)), el("td", u.pcs), el("td", day(u.created)), el("td", ago(u.last_use, D.now)), atd);
    return tr;
  }));
}
function drawRecent() {
  $("#recent").replaceChildren(...(D.recent.length ? D.recent.map((r) => { const tr = el("tr"); tr.append(el("td", ago(r.at, D.now)), el("td", r.display_name || r.login || "?"), el("td", r.item, "dim")); return tr; })
    : [(() => { const tr = el("tr"); const td = el("td", "Nothing yet.", "dim"); td.colSpan = 3; tr.append(td); return tr; })()]));
}
document.querySelectorAll("[data-k]").forEach((i) => i.addEventListener("change", async () => {
  const value = i.type === "checkbox" ? (i.checked ? "1" : "0") : i.value.trim();
  if (i.dataset.k === "BETA_OPEN" && value === "0" &&
      !confirm("End the beta? Clip Genie stops working for every tester within a minute. You can turn it back on any time.")) { i.checked = true; return; }
  try { await api("/admin/api/config", { key: i.dataset.k, value }); toast("Saved"); load(); } catch (e) { toast(e.message, 1); load(); }
}));
$("#q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
load().catch((e) => toast(e.message, 1));
setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 30000);
</script>` : `
<div class="login"><div><h1>Clip Genie Admin</h1><p class="dim">Only for Clip Genie admins.</p>
<a class="twitch" href="/admin/login">Sign in with Twitch</a></div></div>`}
</body></html>`;
