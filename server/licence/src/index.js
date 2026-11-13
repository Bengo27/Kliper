// Clip Genie licence server: "Sign in with Twitch" + the free trial count (Cloudflare Worker + D1),
// and a private admin dashboard (/admin) for the creator.
// The trial is counted HERE, never on the user's PC, so reinstalling or wiping Clip Genie doesn't reset it.
// Limit: TRIAL_LIMIT new streams/clips per Twitch account AND per PC (a new Twitch account on the same PC
// doesn't get a new trial). Adding the same stream again is free. owner / paid_until = unlimited.
import { ADMIN_HTML } from "./admin.js";

const now = () => Math.floor(Date.now() / 1000);
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const rand = (n = 32) => hex(crypto.getRandomValues(new Uint8Array(n)));
const sha = async (s) => hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
const json = (o, status = 200, headers = {}) =>
  new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json", "cache-control": "no-store", ...headers } });
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// "0.1" = "0.1.0". Public numbering restarted at 0.1 (12 Nov): the early 2.x tester builds count as the oldest.
const verNum = (v) => {
  const p = String(v || "0").split(".").map((x) => parseInt(x, 10) || 0).slice(0, 3);
  while (p.length < 3) p.push(0);
  if (p[0] >= 2) return 0;
  return p.reduce((a, x) => a * 1000 + x, 0);
};

// Switches: wrangler.toml [vars] are the defaults; the admin dashboard saves overrides in the settings table.
//   TRIAL_LIMIT   free streams per account / PC
//   TRIALS_OPEN   "0" = no more free trials (paid / owner keep working)
//   MIN_VERSION   older Clip Genie copies must update before adding a stream (stops old builds)
//   TRIAL_WATERMARK "1" = trial videos get the Clip Genie FREE TRIAL logo;  TRIAL_AI "1" = AI allowed in the trial
//   BUY_URL       payment page shown when the trial is used up
//   TRIAL_PRO     "1" = every Pro feature is free while the trial lasts (locked once it's used up)
//   BETA_OPEN     "0" = KILL SWITCH: the beta is over, Clip Genie stops for everyone except paid / owner
//   LATEST_VERSION / UPDATE_URL  newest Clip Genie + where to download it: older copies show "update available"
//   NOTICE        a short message shown at the top of everyone's Clip Genie ("" = none)
//   GUEST_OPEN    "1" = "Continue without Twitch" allowed (the trial is then counted per PC only)
//   FREE_MODE     "1" = Clip Genie is FREE (the creator, 12 Nov): no stream limit, no per-stream limit, no trial mark,
//                 every feature on. Streams are still counted (admin stats). The reply keeps plan "trial" with a big
//                 "left" so copies built before this switch also unlock everything; new copies show "Free".
const FREE_LEFT = 999;
//   AI_OPEN       "1" = the built-in AI is on for everyone (needs the secret OPENROUTER_KEY)
//   AI_DAILY      most AI calls per user per day (one stream = roughly one call per 6 minutes of VOD + a few)
//   AI_MODELS     OpenRouter models, tried in order (free ones first, a cheap paid one last as the backup)
const CFG_KEYS = ["TRIAL_LIMIT", "TRIALS_OPEN", "MIN_VERSION", "TRIAL_WATERMARK", "TRIAL_AI", "BUY_URL", "TRIAL_PRO", "BETA_OPEN",
  "LATEST_VERSION", "UPDATE_URL", "NOTICE", "GUEST_OPEN", "FREE_MODE", "AI_OPEN", "AI_DAILY", "AI_MODELS"];

// Built-in AI (the creator, 12 Nov: "all in one app", no key for users). The app sends a Claude-style request here;
// the OpenRouter key stays on this server (secret OPENROUTER_KEY), never in the app. Each user gets AI_DAILY calls
// a day. OpenRouter tries the models in order ("rerouting"): busy/failed free model -> next one -> paid backup.
function toOpenAI(content) {
  if (typeof content === "string") return content;
  return (Array.isArray(content) ? content : []).map((b) =>
    b && b.type === "image" && b.source && b.source.data
      ? { type: "image_url", image_url: { url: `data:${b.source.media_type || "image/jpeg"};base64,${b.source.data}` } }
      : { type: "text", text: String((b && b.text) || "") });
}
async function aiCall(env, c, s, body) {
  if (c.AI_OPEN !== "1" || !env.OPENROUTER_KEY) return json({ error: "ai_off" }, 403);
  const req = body.body || {};
  const msgs = Array.isArray(req.messages) ? req.messages.slice(0, 20) : [];
  if (!msgs.length) return json({ error: "no messages" }, 400);
  const daily = Math.max(0, parseInt(c.AI_DAILY || "150", 10) || 0);
  const day = Math.floor(now() / 86400);
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS ai_uses (twitch_id TEXT NOT NULL, day INTEGER NOT NULL, n INTEGER NOT NULL,
                        PRIMARY KEY (twitch_id, day))`).run();
  const row = await env.DB.prepare("SELECT n FROM ai_uses WHERE twitch_id = ? AND day = ?").bind(s.twitch_id, day).first();
  if (!s.owner && (row ? row.n : 0) >= daily) return json({ error: "ai_daily", daily }, 429);
  await env.DB.prepare(`INSERT INTO ai_uses (twitch_id, day, n) VALUES (?, ?, 1)
                        ON CONFLICT (twitch_id, day) DO UPDATE SET n = n + 1`).bind(s.twitch_id, day).run();
  // FREE MODELS ONLY (the creator, 12 Nov: "I don't want my money to get drained"): any model without ":free" is
  // dropped here, whatever the admin page or wrangler.toml says. Paid models only with the env var ALLOW_PAID_AI="1".
  const models = String(c.AI_MODELS || "").split(",").map((x) => x.trim()).filter(Boolean)
    .filter((m) => env.ALLOW_PAID_AI === "1" || m.endsWith(":free")).slice(0, 3);
  if (!models.length) return json({ error: "ai_busy", detail: ["no free model set"] }, 503);
  const out = {
    model: models[0], models,
    max_tokens: Math.max(100, Math.min(4000, parseInt(req.max_tokens, 10) || 2000)),
    messages: [...(req.system ? [{ role: "system", content: String(req.system).slice(0, 60000) }] : []),
               ...msgs.map((m) => ({ role: m.role === "assistant" ? "assistant" : "user", content: toOpenAI(m.content) }))],
  };
  if (env.DEV_FAKE_AI === "1") {   // local testing only (.dev.vars): echo what OpenRouter would have been sent
    const imgs = out.messages.flatMap((m) => Array.isArray(m.content) ? m.content : []).filter((b) => b.type === "image_url").length;
    return json({ content: [{ type: "text", text: JSON.stringify({ fake: true, models, images: imgs, max_tokens: out.max_tokens,
      system_mentions_channel: /hotgamerdude/i.test(out.messages[0].content || "") }) }] });
  }
  // Reroute: one model at a time, so a model that is busy, fails OR answers empty (some spend all their tokens
  // "thinking") hands over to the next one. Little thinking, and room for it, so the answer itself gets written.
  delete out.models;
  out.max_tokens = Math.max(1500, Math.min(8000, out.max_tokens * 2));
  out.reasoning = { effort: "low", exclude: true };
  const tried = [];
  for (const m of models) {
    const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST", body: JSON.stringify({ ...out, model: m }),
      headers: { Authorization: `Bearer ${env.OPENROUTER_KEY}`, "content-type": "application/json", "X-Title": "Clip Genie" },
    }).catch(() => null);
    const j = r ? await r.json().catch(() => ({})) : {};
    const ch = (j.choices && j.choices[0]) || {};
    const text = ch.message ? String(ch.message.content || "") : "";
    if (r && r.ok && text.trim())   // answer in the Claude shape the app already reads
      return json({ content: [{ type: "text", text }], model: j.model || m });
    tried.push(`${m}: ${(j.error && j.error.message) || ch.finish_reason || ch.native_finish_reason || (r ? r.status : "no answer")}`.slice(0, 200));
  }
  return json({ error: "ai_busy", detail: tried }, 503);
}
// "Continue without Twitch" (the creator, 12 Nov): one guest account per PC (id "pc_<pc id>"), so the trial is the PC's.
// Signing in as a guest again on the same PC = the same account; a Twitch account on that PC shares the PC's count.
// Against faked PCs (made-up hardware ids sent straight to the API): at most GUEST_PER_IP new guest PCs per internet
// connection per 30 days.
const GUEST_PER_IP = 2;
async function guestSignIn(env, req, body) {
  const c = await cfg(env);
  if (c.GUEST_OPEN === "0") return json({ error: "Please sign in with Twitch." }, 403);
  const parts = cleanParts(body.parts);
  if (!parts.length) return json({ error: "This PC couldn't be identified." }, 400);
  const pc = await pcId(env, parts);
  const id = "pc_" + pc.slice(0, 40);
  const name = String(body.name || "").replace(/[^A-Za-z0-9_]/g, "").slice(0, 25);
  const t = now();
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS guest_ips (ip_hash TEXT NOT NULL, pc_id TEXT NOT NULL, created INTEGER NOT NULL,
                        PRIMARY KEY (ip_hash, pc_id))`).run();
  const known = await env.DB.prepare("SELECT 1 FROM accounts WHERE twitch_id = ?").bind(id).first();
  const ip = await sha("ip:" + (req.headers.get("cf-connecting-ip") || "local"));
  if (!known) {
    const n = (await env.DB.prepare("SELECT COUNT(*) AS n FROM guest_ips WHERE ip_hash = ? AND created > ? AND pc_id != ?")
      .bind(ip, t - 30 * 86400, pc).first()).n;
    if (n >= GUEST_PER_IP)
      return json({ error: "Too many new free trials from this internet connection. Please sign in with Twitch instead." }, 429);
  }
  const token = rand(32);
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO accounts (twitch_id, login, display_name, created) VALUES (?, '', ?, ?)
                    ON CONFLICT (twitch_id) DO UPDATE SET display_name = CASE WHEN excluded.display_name != 'Guest'
                    THEN excluded.display_name ELSE accounts.display_name END`).bind(id, name || "Guest", t),
    env.DB.prepare("INSERT OR IGNORE INTO guest_ips (ip_hash, pc_id, created) VALUES (?, ?, ?)").bind(ip, pc, t),
    env.DB.prepare("INSERT INTO sessions (token_hash, twitch_id, pc_id, created) VALUES (?, ?, ?, ?)").bind(await sha(token), id, pc, t),
  ]);
  const acc = await env.DB.prepare("SELECT display_name FROM accounts WHERE twitch_id = ?").bind(id).first();
  return json({ token, user: { id, login: "", name: acc.display_name, guest: true } });
}
async function cfg(env) {
  const c = {};
  for (const k of CFG_KEYS) c[k] = env[k] ?? "";
  const rows = await env.DB.prepare("SELECT key, value FROM settings").all();
  for (const r of rows.results || []) if (CFG_KEYS.includes(r.key)) c[r.key] = r.value;
  return c;
}

function page(title, msg) {
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Genie</title><body style="font-family:system-ui,sans-serif;background:#14121c;color:#eee;display:grid;place-items:center;min-height:90vh;margin:0">
<div style="text-align:center;max-width:420px;padding:24px"><h2>${esc(title)}</h2><p style="color:#bbb">${esc(msg)}</p></div></body>`,
    { headers: { "content-type": "text/html; charset=utf-8" } });
}

function cleanParts(parts) {
  // hashed PC parts from the app: 64-hex strings only, at most 6
  return [...new Set((Array.isArray(parts) ? parts : []).filter((p) => typeof p === "string" && /^[0-9a-f]{64}$/.test(p)))].slice(0, 6);
}

async function pcId(env, parts) {
  // one PC = any known part matches (Windows reinstall changes its id, the board/disk don't)
  if (!parts.length) throw new Error("no pc");
  const q = parts.map(() => "?").join(",");
  const hit = await env.DB.prepare(`SELECT pc_id FROM pcs WHERE part IN (${q}) ORDER BY created LIMIT 1`).bind(...parts).first();
  const id = hit ? hit.pc_id : parts[0];
  const t = now();
  await env.DB.batch(parts.map((p) => env.DB.prepare("INSERT OR IGNORE INTO pcs (part, pc_id, created) VALUES (?, ?, ?)").bind(p, id, t)));
  return id;
}

async function session(env, token) {
  if (typeof token !== "string" || token.length < 32) return null;
  return env.DB.prepare(
    "SELECT s.twitch_id, s.pc_id, a.login, a.display_name, a.paid_until, a.owner, a.banned FROM sessions s JOIN accounts a ON a.twitch_id = s.twitch_id WHERE s.token_hash = ?"
  ).bind(await sha(token)).first();
}

async function status(env, c, s, pc) {
  const limit = parseInt(c.TRIAL_LIMIT || "3", 10);
  const unlimited = !!s.owner || s.paid_until > now();
  const a = await env.DB.prepare("SELECT COUNT(*) AS n FROM uses WHERE twitch_id = ?").bind(s.twitch_id).first();
  const p = await env.DB.prepare("SELECT COUNT(DISTINCT item) AS n FROM uses WHERE pc_id = ?").bind(pc).first();
  const used = Math.max(a.n, p.n);
  if (c.FREE_MODE === "1" && !unlimited)
    return {
      user: { id: s.twitch_id, login: s.login, name: s.display_name, guest: s.twitch_id.startsWith("pc_") },
      plan: "trial", free: true, limit: FREE_LEFT, used, left: FREE_LEFT, buy_url: "", trials_open: true,
      limits: { watermark: false, ai: c.AI_OPEN === "1" && !!env.OPENROUTER_KEY, pro: true, per_stream: 0 },
    };
  return {
    user: { id: s.twitch_id, login: s.login, name: s.display_name, guest: s.twitch_id.startsWith("pc_") },
    plan: s.owner ? "owner" : unlimited ? "paid" : "trial",
    limit, used, left: unlimited ? null : c.TRIALS_OPEN === "0" ? 0 : Math.max(0, limit - used), buy_url: c.BUY_URL || "",
    trials_open: c.TRIALS_OPEN !== "0",
    // what the trial leaves out (the creator): the Clip Genie FREE TRIAL logo on videos, and no AI
    limits: unlimited ? { watermark: false, ai: c.AI_OPEN === "1" && !!env.OPENROUTER_KEY, pro: true }
      : { watermark: c.TRIAL_WATERMARK !== "0", ai: c.TRIAL_AI === "1" && c.AI_OPEN === "1" && !!env.OPENROUTER_KEY, pro: c.TRIAL_PRO !== "0" },
  };
}

function isAdminLogin(env, login) {
  return String(env.ADMIN_LOGINS || "").toLowerCase().split(",").map((x) => x.trim()).filter(Boolean).includes(String(login || "").toLowerCase());
}

async function finishLogin(env, url, state, user) {
  const st = await env.DB.prepare("SELECT parts, created FROM auth_states WHERE state = ?").bind(state).first();
  if (!st || now() - st.created > 600) return page("Sign-in expired", "Go back and press Sign in with Twitch again.");
  const t = now();
  const upsert = env.DB.prepare(`INSERT INTO accounts (twitch_id, login, display_name, created) VALUES (?, ?, ?, ?)
                    ON CONFLICT (twitch_id) DO UPDATE SET login = excluded.login, display_name = excluded.display_name`)
    .bind(user.id, user.login, user.display_name, t);
  if (state.startsWith("adm")) {   // admin dashboard sign-in
    await env.DB.prepare("DELETE FROM auth_states WHERE state = ?").bind(state).run();
    if (!isAdminLogin(env, user.login)) return page("Not allowed", `${user.display_name} isn't a Clip Genie admin.`);
    const token = rand(32);
    await env.DB.batch([upsert, env.DB.prepare("INSERT INTO admin_sessions (token_hash, twitch_id, created) VALUES (?, ?, ?)").bind(await sha(token), user.id, t)]);
    const secure = url.protocol === "https:" ? "; Secure" : "";
    return new Response(null, { status: 302, headers: {
      Location: "/admin", "Set-Cookie": `cg_admin=${token}; Path=/admin; Max-Age=604800; HttpOnly; SameSite=Lax${secure}` } });
  }
  const pc = await pcId(env, JSON.parse(st.parts));
  const token = rand(32);
  await env.DB.batch([
    upsert,
    env.DB.prepare("INSERT INTO sessions (token_hash, twitch_id, pc_id, created) VALUES (?, ?, ?, ?)").bind(await sha(token), user.id, pc, t),
    env.DB.prepare("UPDATE auth_states SET token = ?, user = ? WHERE state = ?").bind(token, JSON.stringify(user), state),
  ]);
  return page(`Signed in as ${user.display_name}`, "You can close this tab and go back to Clip Genie.");
}

function twitchUrl(env, url, state) {
  const q = new URLSearchParams({ client_id: env.TWITCH_CLIENT_ID, redirect_uri: `${url.origin}/auth/callback`,
                                  response_type: "code", scope: "", state });
  return `https://id.twitch.tv/oauth2/authorize?${q}`;
}

async function adminOf(env, req) {
  const m = /(?:^|;\s*)cg_admin=([0-9a-f]{64})/.exec(req.headers.get("cookie") || "");
  if (!m) return null;
  const a = await env.DB.prepare(
    "SELECT s.twitch_id, s.created, a.login, a.display_name FROM admin_sessions s JOIN accounts a ON a.twitch_id = s.twitch_id WHERE s.token_hash = ?"
  ).bind(await sha(m[1])).first();
  if (!a || now() - a.created > 604800 || !isAdminLogin(env, a.login)) return null;
  return a;
}

async function adminApi(env, req, url, path, body, admin) {
  const c = await cfg(env);
  if (path === "/admin/api/overview") {
    const q = (url.searchParams.get("q") || "").trim().toLowerCase();
    const limit = parseInt(c.TRIAL_LIMIT || "3", 10), t = now(), week = t - 7 * 86400;
    const users = await env.DB.prepare(`SELECT a.twitch_id, a.login, a.display_name, a.created, a.paid_until, a.owner, a.banned,
        (SELECT COUNT(*) FROM uses u WHERE u.twitch_id = a.twitch_id) AS used,
        (SELECT MAX(at) FROM uses u WHERE u.twitch_id = a.twitch_id) AS last_use,
        (SELECT COUNT(*) FROM (SELECT pc_id FROM sessions s WHERE s.twitch_id = a.twitch_id
                                UNION SELECT pc_id FROM uses u WHERE u.twitch_id = a.twitch_id)) AS pcs
      FROM accounts a WHERE (? = '' OR LOWER(a.login) LIKE ? OR LOWER(a.display_name) LIKE ?)
      ORDER BY a.created DESC LIMIT 300`).bind(q, `%${q}%`, `%${q}%`).all();
    const one = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()).n;
    const stats = {
      users: await one("SELECT COUNT(*) AS n FROM accounts"),
      new_week: await one("SELECT COUNT(*) AS n FROM accounts WHERE created >= ?", week),
      uses_week: await one("SELECT COUNT(*) AS n FROM uses WHERE at >= ?", week),
      paid: await one("SELECT COUNT(*) AS n FROM accounts WHERE paid_until > ? AND owner = 0", t),
      used_up: await one(`SELECT COUNT(*) AS n FROM accounts a WHERE a.owner = 0 AND a.paid_until <= ?
                          AND (SELECT COUNT(*) FROM uses u WHERE u.twitch_id = a.twitch_id) >= ?`, t, limit),
      pcs: await one("SELECT COUNT(DISTINCT pc_id) AS n FROM pcs"),
    };
    const recent = await env.DB.prepare(`SELECT u.item, u.at, a.login, a.display_name FROM uses u
      LEFT JOIN accounts a ON a.twitch_id = u.twitch_id ORDER BY u.at DESC LIMIT 25`).all();
    return json({ me: { login: admin.login, name: admin.display_name }, now: t, cfg: c, stats,
                  users: users.results || [], recent: recent.results || [] });
  }
  if (req.method !== "POST") return json({ error: "not found" }, 404);
  if (path === "/admin/api/user") {
    const id = String(body.twitch_id || "");
    const acc = await env.DB.prepare("SELECT twitch_id, paid_until FROM accounts WHERE twitch_id = ?").bind(id).first();
    if (!acc) return json({ error: "No such user" }, 404);
    const a = body.action, t = now();
    if (a === "paid_days") {
      const days = Math.max(1, Math.min(3650, parseInt(body.days, 10) || 30));
      await env.DB.prepare("UPDATE accounts SET paid_until = ? WHERE twitch_id = ?").bind(Math.max(t, acc.paid_until) + days * 86400, id).run();
    } else if (a === "unpaid") await env.DB.prepare("UPDATE accounts SET paid_until = 0 WHERE twitch_id = ?").bind(id).run();
    else if (a === "owner") await env.DB.prepare("UPDATE accounts SET owner = 1 WHERE twitch_id = ?").bind(id).run();
    else if (a === "unowner") await env.DB.prepare("UPDATE accounts SET owner = 0 WHERE twitch_id = ?").bind(id).run();
    else if (a === "reset_trial") await env.DB.prepare("DELETE FROM uses WHERE twitch_id = ?").bind(id).run();
    else if (a === "ban") await env.DB.batch([
      env.DB.prepare("UPDATE accounts SET banned = 1 WHERE twitch_id = ?").bind(id),
      env.DB.prepare("DELETE FROM sessions WHERE twitch_id = ?").bind(id)]);
    else if (a === "unban") await env.DB.prepare("UPDATE accounts SET banned = 0 WHERE twitch_id = ?").bind(id).run();
    else return json({ error: "Unknown action" }, 400);
    return json({ ok: true });
  }
  if (path === "/admin/api/config") {
    const k = String(body.key || ""), v = String(body.value ?? "").trim();
    if (!CFG_KEYS.includes(k)) return json({ error: "Unknown setting" }, 400);
    if (k === "TRIAL_LIMIT" && !/^\d{1,3}$/.test(v)) return json({ error: "Free streams must be a number (0-999)" }, 400);
    if (k === "AI_DAILY" && !/^\d{1,4}$/.test(v)) return json({ error: "AI per day must be a number (0-9999)" }, 400);
    if (k === "AI_MODELS" && !/^[A-Za-z0-9._:\/-]+(\s*,\s*[A-Za-z0-9._:\/-]+){0,2}$/.test(v)) return json({ error: "Up to 3 model names, comma separated" }, 400);
    if (["TRIALS_OPEN", "TRIAL_WATERMARK", "TRIAL_AI", "TRIAL_PRO", "BETA_OPEN", "GUEST_OPEN", "FREE_MODE", "AI_OPEN"].includes(k) && !["0", "1"].includes(v)) return json({ error: "Must be on or off" }, 400);
    if (k === "MIN_VERSION" && v && !/^\d{1,3}(\.\d{1,3}){0,2}$/.test(v)) return json({ error: "Version like 0.2.0" }, 400);
    if (k === "UPDATE_URL" && v && !/^https:\/\/[^\s"'<>]{3,500}$/.test(v)) return json({ error: "Download link must start with https://" }, 400);
    if (k === "LATEST_VERSION" && v && !/^\d{1,3}(\.\d{1,3}){0,2}$/.test(v)) return json({ error: "Version like 0.1.1" }, 400);
    if (k === "NOTICE" && (v.length > 200 || /[<>]/.test(v))) return json({ error: "Message: 200 characters max, no < or >" }, 400);
    if (k === "BUY_URL" && v &&!/^https:\/\/[^\s"'<>]{3,300}$/.test(v)) return json({ error: "Payment link must start with https://" }, 400);
    await env.DB.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value").bind(k, v).run();
    return json({ ok: true, cfg: await cfg(env) });
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;
    const body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
    const local = ["localhost", "127.0.0.1"].includes(url.hostname);
    try {
      // ---------- admin dashboard ----------
      if (path === "/admin" || path.startsWith("/admin/")) {
        if (path === "/admin/login") {
          const state = "adm" + rand(24);
          await env.DB.prepare("INSERT INTO auth_states (state, parts, created) VALUES (?, '[]', ?)").bind(state, now()).run();
          const to = env.DEV_FAKE_LOGIN === "1" && local ? `${url.origin}/auth/fake?state=${state}&login=${encodeURIComponent(url.searchParams.get("as") || "admin")}`
                                                         : twitchUrl(env, url, state);
          return new Response(null, { status: 302, headers: { Location: to } });
        }
        const admin = await adminOf(env, req);
        if (path === "/admin/logout") {
          const m = /(?:^|;\s*)cg_admin=([0-9a-f]{64})/.exec(req.headers.get("cookie") || "");
          if (m) await env.DB.prepare("DELETE FROM admin_sessions WHERE token_hash = ?").bind(await sha(m[1])).run();
          return new Response(null, { status: 302, headers: { Location: "/admin", "Set-Cookie": "cg_admin=; Path=/admin; Max-Age=0" } });
        }
        if (path === "/admin") {
          return new Response(ADMIN_HTML(!!admin), { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
            "x-frame-options": "DENY", "content-security-policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:" } });
        }
        if (!admin) return json({ error: "signed_out" }, 401);
        // admin actions must come from the dashboard page itself (custom header = no cross-site form posts)
        if (req.method === "POST" && req.headers.get("x-cg-admin") !== "1") return json({ error: "forbidden" }, 403);
        return adminApi(env, req, url, path, body, admin);
      }
      // ---------- news for every copy (no sign-in): newest version, its download link, a message ----------
      if (path === "/news") {
        const c = await cfg(env);
        return json({ latest: c.LATEST_VERSION || "", url: c.UPDATE_URL || "", notice: c.NOTICE || "", min_version: c.MIN_VERSION || "",
                     free: c.FREE_MODE === "1" });
      }
      // ---------- app sign-in ----------
      if (path === "/auth/start" && req.method === "POST") {
        const parts = cleanParts(body.parts);
        if (!parts.length) return json({ error: "This PC couldn't be identified." }, 400);
        const state = rand(24);
        await env.DB.prepare("DELETE FROM auth_states WHERE created < ?").bind(now() - 900).run();
        await env.DB.prepare("INSERT INTO auth_states (state, parts, created) VALUES (?, ?, ?)").bind(state, JSON.stringify(parts), now()).run();
        if (env.DEV_FAKE_LOGIN === "1" && local)   // local testing only: no real Twitch
          return json({ state, url: `${url.origin}/auth/fake?state=${state}` });
        return json({ state, url: twitchUrl(env, url, state) });
      }
      if (path === "/auth/guest" && req.method === "POST") return guestSignIn(env, req, body);
      if (path === "/auth/fake" && env.DEV_FAKE_LOGIN === "1" && local) {
        const login = (url.searchParams.get("login") || "testuser").toLowerCase();
        return finishLogin(env, url, url.searchParams.get("state") || "", { id: "fake_" + login, login, display_name: login });
      }
      if (path === "/auth/callback") {
        const code = url.searchParams.get("code"), state = url.searchParams.get("state") || "";
        if (!code) return page("Sign-in cancelled", (url.searchParams.get("error_description") || "") + " Go back and try again.");
        const tr = await fetch("https://id.twitch.tv/oauth2/token", {
          method: "POST",
          body: new URLSearchParams({ client_id: env.TWITCH_CLIENT_ID, client_secret: env.TWITCH_CLIENT_SECRET, code,
                                      grant_type: "authorization_code", redirect_uri: `${url.origin}/auth/callback` }),
        }).then((r) => r.json());
        if (!tr.access_token) return page("Sign-in failed", "Twitch didn't accept the sign-in. Try again.");
        const ur = await fetch("https://api.twitch.tv/helix/users", {
          headers: { Authorization: `Bearer ${tr.access_token}`, "Client-Id": env.TWITCH_CLIENT_ID },
        }).then((r) => r.json());
        const u = ur.data && ur.data[0];
        if (!u) return page("Sign-in failed", "Couldn't read your Twitch account. Try again.");
        return finishLogin(env, url, state, { id: u.id, login: u.login, display_name: u.display_name });
      }
      if (path === "/auth/poll") {
        const state = url.searchParams.get("state") || "";
        if (state.startsWith("adm")) return json({ error: "expired" }, 404);
        const st = await env.DB.prepare("SELECT token, user FROM auth_states WHERE state = ?").bind(state).first();
        if (!st) return json({ error: "expired" }, 404);
        if (!st.token) return json({ waiting: true });
        await env.DB.prepare("DELETE FROM auth_states WHERE state = ?").bind(state).run();   // the token is handed out once
        return json({ token: st.token, user: JSON.parse(st.user) });
      }
      if (path === "/me" || path === "/use" || path === "/signout" || path === "/ai") {
        const s = await session(env, body.token);
        if (!s) return json({ error: "signed_out" }, 401);
        if (path === "/signout") {
          await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha(body.token)).run();
          return json({ ok: true });
        }
        const c = await cfg(env);
        if (verNum(body.version) < verNum(c.MIN_VERSION || "0"))
          return json({ ok: false, reason: "update", min_version: c.MIN_VERSION, buy_url: c.BUY_URL || "", update_url: c.UPDATE_URL || "" }, 426);
        if (s.banned) return json({ ok: false, reason: "banned" }, 403);
        if (c.BETA_OPEN === "0" && !s.owner && !(s.paid_until > now()))   // kill switch: the beta is over
          return json({ ok: false, reason: "beta_closed" }, 410);
        const parts = cleanParts(body.parts);
        const pc = parts.length ? await pcId(env, parts) : s.pc_id;
        if (path === "/me") return json(await status(env, c, s, pc));
        if (path === "/ai") return aiCall(env, c, s, body);
        const item = String(body.item || "").slice(0, 200);
        if (!item) return json({ error: "no item" }, 400);
        const again = await env.DB.prepare("SELECT 1 FROM uses WHERE twitch_id = ? AND item = ?").bind(s.twitch_id, item).first();
        const st = await status(env, c, s, pc);
        if (!again && st.plan === "trial" && st.left <= 0)
          return json({ ok: false, reason: st.trials_open ? "trial_used" : "trials_closed", ...st }, 402);
        if (!again)
          await env.DB.prepare("INSERT OR IGNORE INTO uses (twitch_id, pc_id, item, at) VALUES (?, ?, ?, ?)").bind(s.twitch_id, pc, item, now()).run();
        return json({ ok: true, ...(again ? st : await status(env, c, s, pc)) });
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: "server error" }, 500);
    }
  },
};
