/* Klipon app — BengoGames */
(function () {
  "use strict";
  var TOKEN = window.CG_TOKEN;
  var STYLES = [
    { id: "editor", name: "My editor", css: "color:#fff;font-family:'Montserrat',sans-serif;font-weight:800;font-size:17px;text-transform:lowercase;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000" },
    { id: "pop", name: "Pop", css: "color:#00e5ff;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;transform:scale(1.1)" },
    { id: "bounce", name: "Bounce", css: "color:#00e5ff;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;transform:translateY(-3px) scale(1.15)" },
    { id: "karaoke", name: "Karaoke", css: "color:#fff;background:#7c4dff;padding:0 6px;border-radius:5px" },
    { id: "shake", name: "Shake", css: "color:#ff5c8a;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;transform:rotate(-6deg)" },
    { id: "glow", name: "Glow", css: "color:#fff;text-shadow:0 0 8px #a37bff,0 0 16px #a37bff" },
    { id: "clean", name: "Clean", css: "color:#fff;font-family:'Segoe UI',sans-serif;font-weight:700;font-size:16px;text-decoration:underline" }
  ];
  var SWATCHES = ["#3dff4a", "#00e5ff", "#ffd23f", "#ff5c8a", "#a37bff", "#ff8a3d", "#ffffff"];

  var state = { lib: null, view: "__new", ed: null, polling: {}, account: null };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function media(p, bust) { return "/media?t=" + encodeURIComponent(TOKEN) + "&p=" + encodeURIComponent(p) + (bust ? "&v=" + bust : ""); }
  function fmt(sec) { sec = Math.max(0, Math.round(sec || 0)); var m = Math.floor(sec / 60), s = sec % 60; return m + ":" + (s < 10 ? "0" : "") + s; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  var toastT;
  function toast(msg, err) {
    if (err && /^(SIGNIN|TRIAL_USED|UPDATE|PRO):/.test(msg || "") && acctError(msg)) return;   // trial limits open the account box
    var t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
    clearTimeout(toastT); toastT = setTimeout(function () { t.classList.add("hidden"); }, err ? 7000 : 3500);
  }

  function api(method, url, body) {
    return fetch(url, { method: method, headers: { "Content-Type": "application/json", "X-Klipon-Token": TOKEN },
      body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok || d.error) throw new Error(d.error || "HTTP " + r.status); return d; }); });
  }
  async function pollJob(id, onp) {
    var misses = 0;
    for (;;) {
      var j;
      // a check can fail for a moment while the PC is busy rendering: the job is still running, so keep waiting
      try { j = await api("GET", "/job?id=" + id); misses = 0; }
      catch (e) { if (++misses > 30) throw new Error("Lost contact with Klipon's engine. Close Klipon and open it again."); await sleep(2000); continue; }
      if (onp) onp(j.progress, j.message);
      if (j.status === "done") return j.result;
      if (j.status === "error") throw new Error(j.message);
      await sleep(1000);
    }
  }

  /* ---------------- 0.1.0: update available / message from the admin dashboard ---------------- */
  function verNum(v) {
    var p = String(v || "0").split(".").map(function (x) { return parseInt(x, 10) || 0; });
    while (p.length < 3) p.push(0);
    return p[0] * 1e6 + p[1] * 1e3 + p[2];
  }
  function newsKey(n) { return (n.latest || "") + "|" + (n.notice || ""); }
  function renderNews() {
    var n = (state.lib && state.lib.news) || {}, bar = $("newsBar");
    var newer = !!(n.latest && verNum(n.latest) > verNum(state.lib.version));
    var text = (newer ? "Klipon " + n.latest + " is out. " : "") + (n.notice || "");
    var hidden = "";
    try { hidden = localStorage.getItem("cg_news_hidden") || ""; } catch (e) {}
    bar.classList.toggle("hidden", !text.trim() || hidden === newsKey(n));
    $("newsText").textContent = text.trim();
    $("newsGet").classList.toggle("hidden", !(newer && n.url) || IS_PHONE);
  }
  function openUpdate() { api("POST", "/open_update", {}).catch(function (e) { toast(e.message, true); }); }

  /* ---------------- library ---------------- */
  async function refresh() {
    try { state.lib = await api("GET", "/library"); } catch (e) { toast(e.message, true); return; }
    // public builds: AI only when the server's built-in AI is on; never an API key box
    document.body.classList.toggle("no-ai", !!state.lib.public && !state.lib.server_ai);
    document.body.classList.toggle("no-key", !!state.lib.public);
    document.body.classList.toggle("pro-locked", !!state.lib.public && !state.lib.pro);
    if (state.lib.version) { $("appVersion").textContent = "v" + state.lib.version; $("setTitle").textContent = "Settings · Klipon v" + state.lib.version + (state.lib.public ? " Beta" : ""); }
    renderNews();
    renderSide(); renderMain(); renderAddLooks();
    var busy = state.lib.streams.some(function (s) { return s.status === "processing" || s.status === "queued"; });
    clearTimeout(state.refreshT);
    if (busy) state.refreshT = setTimeout(refresh, 2500);
  }
  function streams() { return (state.lib && state.lib.streams) || []; }
  function streamByKey(k) { return streams().filter(function (s) { return s.key === k; })[0]; }
  function allClips(filterNew) {
    var out = [];
    streams().forEach(function (s) { (s.clips || []).forEach(function (c) {
      if (c.status === "discarded") return;
      if (filterNew && (c.status === "posted")) return;
      out.push({ s: s, c: c });
    }); });
    return out;
  }

  function renderSide() {
    $("newCount").textContent = allClips(true).filter(function (x) { return x.c.status === "new"; }).length;
    document.querySelector('.stream-item.all').classList.toggle("on", state.view === "__new");
    $("streamList").innerHTML = streams().map(function (s) {
      var n = (s.clips || []).filter(function (c) { return c.status !== "discarded"; }).length;
      var sub = s.status === "ready" ? n + " TikToks" : (s.status === "error" ? "Failed" : (s.message || "Waiting"));
      var bar = (s.status === "processing") ? '<span class="si-bar"><div style="width:' + Math.round((s.progress || 0) * 100) + '%"></div></span>' : "";
      return '<button class="stream-item' + (state.view === s.key ? " on" : "") + '" data-key="' + esc(s.key) + '">' +
        '<span class="si-title">' + esc(s.title || "Twitch stream") + '</span>' +
        '<span class="si-sub"><span class="dot ' + esc(s.status) + '"></span>' + esc((s.date ? s.date + " · " : "") + sub) + '</span>' + bar + '</button>';
    }).join("") || '<p class="muted small" style="padding:6px 10px">No streams yet.</p>';
  }

  function card(x) {
    var c = x.c, chip = c.status === "posted" ? '<span class="chip posted">Posted</span>' : c.status === "edited" ? '<span class="chip edited">Edited</span>' : c.status === "new" ? '<span class="chip">New</span>' : "";
    return '<button class="card" data-key="' + esc(x.s.key) + '" data-id="' + esc(c.id) + '">' +
      '<div class="thumb">' + (c.thumb ? '<img alt="" loading="lazy" src="' + media(c.thumb, Math.round(c.rendered_at || 0)) + '">' : "") + chip +
      '<span class="dur">' + fmt(c.duration) + '</span>' + ((c.checks || []).length ? '<span class="warn" title="Self-check: something to look at">⚠ check</span>' : "") + '</div>' +
      '<div class="body"><span class="t">' + esc(c.title) + '</span><span class="m">Score ' + esc(c.score) + ' · ' + esc(c.start_label || "") + (c.views != null ? ' · 👁 ' + nice(c.views) : "") + '</span></div></button>';
  }

  function renderMain() {
    var s = state.view === "__new" ? null : streamByKey(state.view);
    $("btnFolder").classList.toggle("hidden", !s);
    $("btnRemoveStream").classList.toggle("hidden", !s);
    var pb = $("progressBox");
    if (s && (s.status === "processing" || s.status === "queued")) {
      pb.classList.remove("hidden");
      $("pbTitle").textContent = s.status === "queued" ? "Waiting to start" : (s.kind === "clip" ? "Making a TikTok from your clip" : "Making TikToks");
      // 0.1.0: while the speech model downloads (first run) the bar shows the download itself
      var dl = /^Downloading speech model.*?\((\d+)%\)/.exec(s.message || "");
      var shown = dl ? Number(dl[1]) : Math.round((s.progress || 0) * 100);
      $("pbPct").textContent = shown + "%";
      $("pbBar").style.width = shown + "%";
      $("pbMsg").textContent = s.message || "";
      $("pbEta").textContent = s.status === "processing" ? etaText(s) : "";
      funStart();
    } else { pb.classList.add("hidden"); funStop(); }
    var items, html = "";
    if (!s) {
      $("viewTitle").textContent = "New TikToks";
      items = allClips(true);
      $("viewSub").textContent = items.length ? "Ready to post. Click one to watch, tweak and upload." : "";
      var groups = {};
      items.forEach(function (x) { (groups[x.s.key] = groups[x.s.key] || []).push(x); });
      Object.keys(groups).forEach(function (k) {
        var st = streamByKey(k);
        html += '<div class="group-title">' + esc((st.date ? st.date + " · " : "") + (st.title || "")) + '</div>' + groups[k].map(card).join("");
      });
      $("candidates").classList.add("hidden");
      $("findBox").classList.add("hidden");
    } else {
      $("viewTitle").textContent = s.title || "Twitch stream";
      $("viewSub").textContent = s.status === "error" ? "Failed: " + (s.message || "") : ((s.date || "") + (s.duration ? " · " + fmt(s.duration) + (s.kind === "clip" ? " clip" : " stream") : "") + (s.folder ? " · saved in " + s.folder : ""));
      items = (s.clips || []).filter(function (c) { return c.status !== "discarded"; }).map(function (c) { return { s: s, c: c }; });
      html = items.map(card).join("");
      var cands = s.extra_candidates || [];
      $("findBox").classList.toggle("hidden", s.status !== "ready");
      if ($("findBox").dataset.key !== s.key) { $("findBox").dataset.key = s.key; $("findText").value = ""; $("findMsg").classList.add("hidden"); }
      $("candidates").classList.toggle("hidden", !cands.length || s.status !== "ready");
      var sigs = s.signals || [];
      var aiOn = sigs.indexOf("ai") >= 0;
      $("candSub").textContent = "Ranked by " + sigLabel(sigs) + ". Preview, tick the ones you want, then make them." +
        (aiOn || state.lib.public ? "" : state.lib.ai_trial ? " The AI read isn't part of the free trial." : state.lib.ai_ready ? " The AI read wasn't run on this stream yet." : " The AI read (best at funny moments) is off: add your Claude API key to turn it on for every stream.");
      var ab = $("btnAiRank");
      ab.classList.toggle("hidden", aiOn || !!state.lib.ai_trial);
      ab.textContent = state.lib.ai_ready ? "✨ Re-rank with AI" : "✨ Turn on AI read";
      ab.dataset.mode = state.lib.ai_ready ? "rerank" : "settings";
      var sigKey = s.key + "|" + cands.map(function (c) { return c.id + ":" + c.score; }).join(",");
      if ($("candList").dataset.sig !== sigKey) {   // don't wipe ticks / open previews on a refresh
        $("candList").innerHTML = cands.map(function (c, i) { return candRow(c, i); }).join("");
        $("candList").dataset.sig = sigKey;
      }
      updateMakeSel();
    }
    $("grid").innerHTML = html;
    var nothing = !items.length && !(s && s.status !== "ready") && !(s && (s.extra_candidates || []).length);
    $("empty").classList.toggle("hidden", !nothing || (s && s.status === "error"));
    $("hero").classList.toggle("compact", !!s);
    updateSteps();
  }

  /* ---------------- steps bar ---------------- */
  function updateSteps() {
    var step = 1;
    if (state.ed) step = state.sec === "post" ? 4 : 3;
    else {
      var s = state.view === "__new" ? null : streamByKey(state.view);
      if (s && (s.status === "processing" || s.status === "queued")) step = 1;
      else if (s && s.status === "ready" && (s.extra_candidates || []).length) step = 2;
    }
    document.querySelectorAll("#steps .step").forEach(function (b) {
      var n = Number(b.dataset.step);
      b.classList.toggle("on", n === step); b.classList.toggle("done", n < step);
      b.querySelector("i").textContent = n < step ? "✓" : String(n);
    });
    document.body.classList.toggle("has-editor", !!state.ed);
  }
  /* 2.27.2: time left while a stream is being worked on. Blends the pace of the last few minutes (stages run at
     different speeds: the chat replay of a long stream is slow) with the average since it started. */
  var etaSamples = {};
  function fmtMins(sec) {
    var m = Math.round(sec / 60);
    if (m < 1) return "under a minute";
    if (m < 60) return m + " min";
    return Math.floor(m / 60) + " h " + (m % 60 ? (m % 60) + " min" : "");
  }
  var etaFrom = {};   // 0.1.0: time + progress where the estimate starts (after a speech model download)
  function etaText(s) {
    var now = Date.now() / 1000, p = Number(s.progress) || 0, key = s.key;
    if (/^Downloading speech model/.test(s.message || "")) {   // one-off download: don't let it skew the estimate
      etaSamples[key] = []; etaFrom[key] = { t: now, p: p };
      return "Downloading the speech model once. It won't need this again";
    }
    var list = etaSamples[key] = etaSamples[key] || [];
    if (!list.length || list[list.length - 1].p !== p) list.push({ t: now, p: p });
    while (list.length > 2 && now - list[1].t > 300) list.shift();   // keep about the last 5 minutes
    var started = Number(s.started) || (list.length ? list[0].t : now);
    var ran = Math.max(0, now - started);
    var out = ran >= 60 ? "Running " + fmtMins(ran) : "";
    var f = etaFrom[key] || { t: started, p: 0 }, fr = Math.max(0, now - f.t), fp = p - f.p;
    if (p < 0.03 || fr < 45 || fp <= 0.005) return out + (out ? " · " : "") + "Working out how long this takes…";
    var est = fr * (1 - p) / fp, old = list[0];
    if (old && now - old.t >= 90 && p > old.p) {
      var recent = (1 - p) * (now - old.t) / (p - old.p);
      est = 0.6 * recent + 0.4 * est;
    }
    if (p >= 0.97) return out + (out ? " · " : "") + "Almost done";
    return out + (out ? " · " : "") + "About " + fmtMins(Math.max(30, est)) + " left";
  }

  /* rotating messages while Klipon is hunting for TikToks */
  var FUN = ["Finding TikToks…", "Listening for the loud bits…", "Reading chat for hype…", "Spotting the funny moments…",
    "Hunting for clutches…", "Counting laughs and screams…", "Checking the gameplay…", "Lining up the best clips…",
    "Trimming the boring parts…", "Picking your top moments…"];
  var funT = null, funI = 0;
  function funStart() {
    if (funT) return;
    funT = setInterval(function () { funI = (funI + 1) % FUN.length; var e = $("pbFun"); if (e) e.textContent = FUN[funI]; }, 2600);
  }
  function funStop() { clearInterval(funT); funT = null; }

  /* ---------------- ranked moments ---------------- */
  var SIG = [["chat", "Chat"], ["audio", "Audio"], ["visual", "Gameplay"], ["funny", "Funny"], ["text", "Hype words"], ["clips", "Viewer clips"], ["ai", "AI read"]];
  var CAT = { funny: "Funny", banter: "Banter", hype: "Hype", story: "Story", found: "You asked for this" };
  function sigLabel(sigs) {
    var names = SIG.filter(function (x) { return sigs.indexOf(x[0]) >= 0; }).map(function (x) { return x[1].toLowerCase(); });
    return names.length ? names.join(", ") : "chat, audio and transcript";
  }
  function candRow(c, i) {
    var sig = c.signals || {};
    var bars = SIG.filter(function (x) { return sig[x[0]] !== undefined; }).map(function (x) {
      return '<span class="sig" title="' + x[1] + ': ' + sig[x[0]] + '/100">' + x[1] + '<i><b style="width:' + sig[x[0]] + '%"></b></i></span>';
    }).join("");
    var vc = (c.viewer_clips || []).length ? ' · <strong>' + c.viewer_clips.length + ' viewer clip' + (c.viewer_clips.length > 1 ? "s" : "") + '</strong>' : "";
    return '<div class="cand' + (c.category === "found" ? " found" : "") + '" data-id="' + esc(c.id) + '">' +
      '<input type="checkbox" class="pick" aria-label="Select ' + esc(c.title) + '">' +
      '<span class="rank">#' + (i + 1) + '</span><span class="score">' + c.score + '</span>' +
      '<span class="ct"><strong>' + esc(c.title) + (c.category ? '<span class="cat cat-' + esc(c.category) + '">' + esc(CAT[c.category] || c.category) + '</span>' : "") + '</strong>' +
      '<span class="small muted">' + esc(c.start_label) + ' · ' + Math.round(c.end - c.start) + 's' + (c.game ? ' · ' + esc(c.game) : '') + vc + ' · ' + esc(c.ai_why || c.why) + '</span>' +
      (c.on_screen || c.lead_up ? '<span class="small onscreen">🎮 ' + esc(c.on_screen || "Starts on the play, then your reaction") + '</span>' : "") +
      (c.quote ? '<span class="small muted quote">“' + esc(c.quote) + '…”</span>' : "") +
      '<span class="bars">' + bars + '</span></span>' +
      '<span class="acts"><span class="vote">' +
        '<button class="btn ghost sm" data-vote="up" aria-pressed="' + (c.vote === "up") + '" aria-label="Good moment" title="Good moment: find more like this">👍</button>' +
        '<button class="btn ghost sm" data-vote="down" aria-pressed="' + (c.vote === "down") + '" aria-label="Bad moment" title="Not funny: find fewer like this">👎</button></span>' +
        '<button class="btn ghost sm" data-preview="' + esc(c.id) + '">▶ Preview</button>' +
      '<button class="btn ghost sm" data-make="' + esc(c.id) + '">Make TikTok</button></span>' +
      '<span class="why-row' + (c.vote === "down" ? "" : " hidden") + '">' + (c.vote === "down" ? whyRow(c.vote_reasons) : "") + '</span></div>';
  }
  function selectedMoments() {
    return Array.prototype.map.call(document.querySelectorAll("#candList .pick:checked"), function (b) { return b.closest(".cand").dataset.id; });
  }
  function updateMakeSel() {
    var n = selectedMoments().length;
    $("btnMakeSel").disabled = !n; $("btnMakeSel").textContent = "Make TikToks (" + n + ")";
  }
  var WHY = [["too_long", "Too long"], ["slow_start", "Slow start"], ["no_leadup", "Missing the lead-up"],
    ["ends_early", "Ends too early"], ["not_funny", "Not funny"], ["wrong_moment", "Wrong moment"]];
  function whyRow(reasons) {
    reasons = reasons || [];
    return '<span class="why-label small muted">What was wrong? (helps the AI fix it)</span>' + WHY.map(function (w) {
      return '<button class="chip-btn" data-why="' + w[0] + '" aria-pressed="' + (reasons.indexOf(w[0]) >= 0) + '">' + w[1] + '</button>';
    }).join("");
  }
  /* host: the element the "what was wrong?" chips live in (a .why-row). */
  function showWhy(host, on, reasons) {
    if (!host) return;
    host.classList.toggle("hidden", !on);
    host.innerHTML = on ? whyRow(reasons) : "";
  }
  function whyHost(btn) {
    var cand = btn.closest(".cand");
    return cand ? cand.querySelector(".why-row") : $("edWhy");
  }
  async function sendVote(key, id, btn) {
    var box = btn.parentNode, on = btn.getAttribute("aria-pressed") === "true", v = on ? null : btn.dataset.vote;
    box.querySelectorAll("[data-vote]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.vote === v)); });
    showWhy(whyHost(btn), v === "down", []);
    try {
      var r = await api("POST", "/library/feedback", { key: key, clip_id: id, vote: v });
      if (v) toast((v === "up" ? "Got it: more like this. " : "Got it: fewer like this. Tap what was wrong so it knows why. ") + "Klipon has learned from " + (r.likes + r.dislikes) + " ratings.");
    } catch (err) { toast(err.message, true); }
  }
  async function sendWhy(key, id, chip) {
    chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
    var reasons = Array.prototype.filter.call(chip.parentNode.querySelectorAll("[data-why]"), function (b) { return b.getAttribute("aria-pressed") === "true"; })
      .map(function (b) { return b.dataset.why; });
    try { await api("POST", "/library/feedback", { key: key, clip_id: id, vote: "down", reasons: reasons }); return reasons; }
    catch (err) { toast(err.message, true); return null; }
  }
  /* Find a moment from a description: the AI searches everything said in the stream. */
  async function findMoment() {
    var key = state.view, text = $("findText").value.trim(), msg = $("findMsg"), btn = $("btnFind");
    if (!text) { $("findText").focus(); return; }
    if (state.lib.ai_trial) { acctError("TRIAL_USED: The AI isn't part of the free trial. Upgrade to use it."); return; }
    if (!state.lib.ai_ready) { toast("Add your Claude API key in Settings to use this.", true); openSettings(); return; }
    btn.disabled = true; btn.textContent = "Searching…"; msg.classList.remove("hidden"); msg.textContent = "The AI is reading through the stream…";
    try {
      var r = await api("POST", "/library/find", { key: key, text: text });
      var res = await pollJob(r.job, function (p, m) { msg.textContent = (m || "Searching") + "…"; });
      await refresh();
      if (state.view !== key) return;
      if (res.found.length) {
        msg.textContent = "Found " + res.found.length + (res.found.length > 1 ? " possible matches" : " match") + ". " + (res.found.length > 1 ? "They're" : "It's") + " at the top of the list below: preview, then make it.";
        var first = document.querySelector('#candList .cand[data-id="' + res.found[0].id + '"]');
        if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
      } else {
        msg.textContent = "Couldn't find that in " + res.searched_minutes + " minutes of stream. Try saying what was said, or who was talking.";
      }
    } catch (e) { msg.textContent = e.message; toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = "Find it"; }
  }
  async function makeMoments(ids) {
    var key = state.view;
    for (var k = 0; k < ids.length; k++) {
      $("btnMakeSel").disabled = true; $("btnMakeSel").textContent = "Making " + (k + 1) + " of " + ids.length + "…";
      try { var r = await api("POST", "/library/rerender", { key: key, clip_id: ids[k], edits: {}, style: {} }); await pollJob(r.job); }
      catch (err) { toast(err.message, true); }
    }
    toast(ids.length > 1 ? ids.length + " TikToks made." : "TikTok made.");
    await refresh();
  }
  async function previewMoment(row, id, btn) {
    var box = row.querySelector(".pv");
    if (box) { box.remove(); btn.textContent = "▶ Preview"; return; }
    btn.disabled = true; btn.textContent = "Loading…";
    try {
      var d = await api("GET", "/library/moment_preview?key=" + encodeURIComponent(state.view) + "&clip_id=" + encodeURIComponent(id));
      row.insertAdjacentHTML("beforeend", '<div class="pv"><video controls autoplay playsinline src="' + media(d.path) + '"></video></div>');
      btn.textContent = "✕ Close";
    } catch (err) { toast(err.message, true); btn.textContent = "▶ Preview"; }
    btn.disabled = false;
  }

  /* ---------------- add stream ---------------- */
  /* 2.23: pick the editing style before pasting the link; new TikToks from that stream use it */
  function addLook() { try { return localStorage.getItem("cg_add_look") || ""; } catch (e) { return ""; } }
  function renderAddLooks() {
    var box = $("addLook"); if (!box || !state.lib) return;
    var cur = addLook(), list = [{ id: "", name: "Auto", about: "Your default style. CS2 streams get the CS2 + kill feed style." }].concat(lookList());
    if (!list.some(function (l) { return l.id === cur; })) cur = "";
    box.innerHTML = list.map(function (l) {
      return '<button class="look-chip' + (l.id === cur ? " on" : "") + (l.mine ? " mine" : "") + (l.id === "cs2" ? ' pro-only" data-pro="killfeed' : "") + '" role="radio" aria-checked="' + (l.id === cur) +
        '" data-addlook="' + esc(l.id) + '" title="' + esc(l.about || "") + '">' + esc(l.name) + "</button>";
    }).join("") + '<button class="look-chip" data-addother title="Make your own style">＋ Other</button>';
  }
  /* 2.35.3: a VOD link first asks which part: the whole VOD, or From / To times. */
  var VOD_RX = /twitch\.tv\/(?:[\w-]+\/)?(?:videos?|v)\/\d+/i;
  function hmsToSec(v) {
    v = String(v || "").trim(); if (!v) return null;
    var p = v.split(":").map(Number);
    if (p.some(function (x) { return isNaN(x) || x < 0; }) || p.length > 3) return NaN;
    return p.reduce(function (a, x) { return a * 60 + x; }, 0);
  }
  function addStream(src) {
    src = (src || $("addSrc").value).trim();
    if (!src) { toast("Paste a Twitch, YouTube or Kick link first.", true); return; }
    if (VOD_RX.test(src)) {
      state.rangeSrc = src;
      $("addRange").classList.remove("hidden");
      $("rangeAll").focus();
      return;
    }
    doAdd(src, null);
  }
  function rangeGo(whole) {
    var src = state.rangeSrc; if (!src) return;
    var rg = null;
    if (!whole) {
      var a = hmsToSec($("rangeFrom").value), b = hmsToSec($("rangeTo").value);
      if (a == null) a = 0;
      if (b == null || isNaN(a) || isNaN(b)) { toast("Type the times like 1:30:00 (hours:minutes:seconds).", true); return; }
      if (b - a < 60) { toast("\"To\" must be at least 1 minute after \"From\".", true); return; }
      rg = [a, b];
    }
    rangeClose();
    doAdd(src, rg);
  }
  function rangeClose() { state.rangeSrc = null; $("addRange").classList.add("hidden"); $("rangeFrom").value = ""; $("rangeTo").value = ""; }
  async function doAdd(src, rg) {
    try {
      var body = { source: src, look: addLook() }; if (rg) body.range = rg;
      var r = await api("POST", "/library/add", body);
      $("addSrc").value = "";
      state.view = r.key;
      await refresh();
      if (window.CG_acctError) loadAccount(true);   // the trial count changed
      pollJob(r.job).then(function () { toast(r.kind === "clip" ? "TikTok made from your clip. Click it to watch and tweak." : "Moments ranked. Preview and pick the ones to make."); refresh(); })
        .catch(function (e) { toast(e.message, true); refresh(); });
    } catch (e) { if (!acctError(e.message)) toast(e.message, true); }
  }
  async function pickFile() {
    try {
      var r = await api("POST", "/pick_file", {});
      if (r.path) { $("addSrc").value = r.path; addStream(r.path); }
    } catch (e) { toast(e.message, true); }
  }

  /* ---------------- editor ---------------- */
  var ED_OPTS = ["color_others", "style", "font", "words_per_line", "highlight", "size", "caption_seam", "position", "uppercase", "captions",
    "layout", "zoom", "jump_cuts", "cut_boring", "hook_teaser", "cutaways", "sfx", "cam_tight", "watermark",
    "look", "title_bar", "title_pos", "title_highlight", "title_text", "frame", "watermark_pos", "overlays", "react_sfx",
    "title_style", "chat_popups", "cam_ratio", "chat_opacity", "chat_name_opacity", "logo_size", "logo_dx", "logo_dy", "watermark_logo", "watermark_opacity",
    "subs_mode", "title_size", "punch_more", "end_bw", "end_word", "kf_names", "subs_lang"];
  var LOOK_KEYS = ["layout", "title_bar", "title_pos", "title_highlight", "title_style", "frame", "watermark_pos", "overlays",
    "title_size", "subs_mode", "punch_more", "end_bw", "sfx", "chat_style", "chat_pos"];

  /* ---------------- looks (2.18): ready-made styles + your own ---------------- */
  function looks() { return (state.lib && state.lib.looks) || { presets: [], mine: {}, hud_parts: [] }; }
  function lookList(keep) {   // 2.26: hidden looks (Clean, Zelda, Fortnite) only show when a TikTok already uses one
    var L = looks(), out = L.presets.filter(function (p) { return !p.hidden || p.id === keep; })
      .map(function (p) { return { id: p.id, name: p.name, about: p.about, style: p.style }; });
    Object.keys(L.mine || {}).sort().forEach(function (n) { out.push({ id: "mine:" + n, name: n, about: "Your own style", style: L.mine[n], mine: true }); });
    return out;
  }
  function renderLooks() {
    var o = state.ed.style, cur = o.look || "editor", list = lookList(cur), on = list.filter(function (l) { return l.id === cur; })[0];
    $("lookGrid").innerHTML = list.map(function (l) {
      return '<button class="look-card' + (l.id === cur ? " on" : "") + (l.mine ? " mine" : "") + (l.id === "cs2" ? ' pro-only" data-pro="killfeed' : "") + '" data-look="' + esc(l.id) + '" aria-pressed="' + (l.id === cur) + '">' +
        "<strong>" + esc(l.name) + "</strong><span>" + esc(l.about || "") + "</span></button>";
    }).join("") + '<button class="look-card' + (on ? "" : " on") + '" data-look="__other" aria-pressed="' + !on + '"><strong>＋ Other</strong><span>Make your own style: change anything below, then save it.</span></button>';
    $("lookAbout").textContent = on ? "" : "Your own style: change the title, layout and the rest below, then press \"Save as my style\" to use it again.";
    $("btnDelLook").classList.toggle("hidden", !(on && on.mine));
    var ov = o.overlays || [];
    $("hudParts").innerHTML = (looks().hud_parts || []).map(function (h) {
      var p = ov.indexOf(h.id) >= 0;
      return '<div class="toggle"><span>' + esc(h.name) + '</span><button class="switch' + (p ? " on" : "") + '" data-hud="' + esc(h.id) + '" aria-pressed="' + p + '" aria-label="' + esc(h.name) + '"><span></span></button></div>';
    }).join("");
    renderKfNames();
    $("sTitlePos").value = o.title_pos || "top";
    $("sTitleStyle").value = o.title_style || "plain";
    $("sChatPop").value = o.chat_popups || "auto";
    [["sChatOpacity", "chat_opacity"], ["sChatNameOpacity", "chat_name_opacity"]].forEach(function (p) {
      var v = o[p[1]] == null || o[p[1]] === "" ? 100 : Number(o[p[1]]);
      $(p[0]).value = v; $(p[0] + "Val").textContent = v + "%";
    });
    var ls = o.watermark_logo === false ? 0 : Math.round((o.logo_size == null || o.logo_size === "" ? 1 : Number(o.logo_size)) * 100);
    [["sLogoSize", ls, "%"], ["sLogoX", Math.round(Number(o.logo_dx || 0) * 100), ""], ["sLogoY", Math.round(Number(o.logo_dy || 0) * 100), ""],
     ["sLogoOpacity", o.watermark_opacity == null || o.watermark_opacity === "" ? 100 : Math.round(Number(o.watermark_opacity)), "%"]].forEach(function (p) {
      $(p[0]).value = p[1]; $(p[0] + "Val").textContent = p[1] + p[2];
    });
    $("chatOpacityOpts").classList.toggle("hidden", o.chat_popups === "off");
    $("titleStyleField").classList.toggle("hidden", !o.title_bar);
    $("titleSizeField").classList.toggle("hidden", !o.title_bar);
    var tsz = Math.round(Number(o.title_size == null || o.title_size === "" ? 1 : o.title_size) * 100);
    $("sTitleSize").value = tsz; $("sTitleSizeVal").textContent = tsz + "%";
    var lg = Number(o.end_linger == null || o.end_linger === "" ? 1.8 : o.end_linger);
    $("sLinger").value = lg; $("sLingerVal").textContent = lg ? lg + " s after the last word" : "cut right away";
    $("sSubsMode").value = o.subs_mode === "key" ? "key" : "all";
    $("sSubsLang").value = o.subs_lang || (state.ed ? (streamByKey(state.ed.key) || {}).language : "") || "";
    if ($("sSubsLang").selectedIndex < 0) $("sSubsLang").value = "";
    $("sEndWord").value = o.end_word || "";
    $("endWordField").classList.toggle("hidden", !o.end_bw);   // 2.24.1: the effect only runs with a typed word
    $("sLayout").value = o.layout || "auto";
    $("sTitleColor").value = o.title_highlight || "#ffd23f";
    $("sTitleText").value = o.title_text || "";
    $("sWmPos").value = o.watermark_pos || "bottom";
    var snd = looks().sounds || [], rs = o.react_sfx || "";
    $("sReactSfx").innerHTML = '<option value="">Built-in bass hit</option><option value="none">No sound</option>' +
      snd.map(function (s) { return '<option value="' + esc(s.name) + '">♪ ' + esc(s.name) + "</option>"; }).join("") +
      (rs && rs !== "none" && !snd.some(function (s) { return s.name === rs; }) ? '<option value="' + esc(rs) + '">' + esc(rs) + " (deleted)</option>" : "");
    $("sReactSfx").value = rs;
    $("reactSfxField").classList.toggle("hidden", !o.sfx);
    $("titleOpts").classList.toggle("hidden", !o.title_bar);
    $("sTitleText").closest(".field").classList.toggle("hidden", !o.title_bar);
  }
  function applyLook(id) {
    var l = lookList().filter(function (x) { return x.id === id; })[0]; if (!l || !state.ed) return;
    Object.keys(l.style).forEach(function (k) { if (k !== "look") state.ed.style[k] = Array.isArray(l.style[k]) ? l.style[k].slice() : l.style[k]; });
    state.ed.style.look = id; syncEdControls(); setDirty();
  }
  /* ---------------- sound effects (2.19) ---------------- */
  var sfxAudio = null;
  function soundPath(name) { var s = (looks().sounds || []).filter(function (x) { return x.name === name; })[0]; return s ? s.path : null; }
  function playSound(path) {
    try { if (sfxAudio) sfxAudio.pause(); sfxAudio = new Audio(media(path)); sfxAudio.volume = 0.8; sfxAudio.play(); } catch (e) {}
  }
  function renderSfx() {
    var snd = looks().sounds || [];
    $("sfxList").innerHTML = snd.length ? snd.map(function (s) {
      return '<div class="sfx-row"><button class="btn ghost sm" data-play="' + esc(s.path) + '" aria-label="Play ' + esc(s.name) + '">▶</button><span>' + esc(s.name) +
        '</span><button class="btn ghost sm" data-del="' + esc(s.name) + '" aria-label="Remove ' + esc(s.name) + '">✕</button></div>';
    }).join("") : '<span class="tiny muted">No sounds yet.</span>';
  }
  window.CGSounds = function () { return looks().sounds || []; };
  window.CGPlaySound = function (name) { var p = soundPath(name); if (p) playSound(p); };
  function lookChanged(key) { if (state.ed && LOOK_KEYS.indexOf(key) >= 0) state.ed.style.look = "custom"; }

  async function openEditor(key, id) {
    var s = streamByKey(key), c = (s.clips || []).filter(function (x) { return x.id === id; })[0];
    if (!c) return;
    var base = Object.assign({}, state.lib.style, s.channel ? { watermark: s.channel } : {}, c.style || {});   // 2.35: the stream's own streamer
    state.ed = { key: key, id: id, clip: c, style: base, edits: Object.assign({}, c.edits || {}), dirty: false, original: "" };
    $("exportQR").classList.add("hidden");
    $("edTitle").textContent = c.title;
    document.querySelectorAll("#edVote [data-vote]").forEach(function (b) { b.setAttribute("aria-pressed", String(c.vote === b.dataset.vote)); });
    $("edMeta").textContent = (s.title || "") + " · " + c.start_label;
    $("askText").value = "";
    $("askReply").textContent = "Type it like you'd tell an editor: longer, shorter, add a moment, cut a part, subtitles, volume, title…";
    showWhy($("edWhy"), c.vote === "down", c.vote_reasons);
    showViews(); showUndo(); showChecks(c);
    $("editor").classList.remove("hidden");
    loadVideo();
    $("pTitle").value = c.title || "";
    $("pCaption").value = c.caption || "";
    $("subText").value = (c.edits && c.edits.text) || (c.words || []).join(" ");
    state.ed.original = (c.words || []).join(" ");
    syncEdControls();
    trimInfo();
    switchTab("look");
    try {
      var w = await api("GET", "/library/words?key=" + encodeURIComponent(key) + "&clip_id=" + encodeURIComponent(id));
      if (w.words && w.words.length) {
        state.ed.original = w.words.join(" ");
        if (!(c.edits && c.edits.text)) $("subText").value = state.ed.original;
      }
    } catch (e) {}
  }
  /* Let go of the video file before it's re-made (Windows can't replace a file that's being played). */
  function unloadVideo() {
    var v = $("edVideo"); try { v.pause(); } catch (e) {}
    v.removeAttribute("src"); v.load();
  }
  function loadVideo() {
    var v = $("edVideo"), c = state.ed.clip;
    v.src = media(c.path, Math.round(c.rendered_at || 0));
    v.load();
    $("btnSave").href = media(c.path, Math.round(c.rendered_at || 0)) + "&dl=1";
    $("btnSave").setAttribute("download", (c.title || "tiktok").replace(/[^\w\- ]+/g, "").trim() + ".mp4");
  }
  async function toggleTimeline(force) {
    var on = force != null ? force : $("tlWrap").classList.contains("hidden");
    $("edTimeline").setAttribute("aria-pressed", on);
    $("edTimeline").classList.toggle("on", on);
    $("editor").classList.toggle("tl-mode", on);
    $("tlWrap").classList.toggle("hidden", !on);
    if (!on) { window.CGTimeline.close(); return; }
    $("edVideo").pause();
    try {
      var ed = state.ed;
      var d = await api("GET", "/library/timeline?key=" + encodeURIComponent(ed.key) + "&clip_id=" + encodeURIComponent(ed.id));
      if (state.ed !== ed) return;
      d.music_url = d.music ? media(d.music) : "";
      window.CGTimeline.open(d, media(d.video), function () { setDirty(); });
      livePreview(false);
    } catch (e) { toast(e.message, true); toggleTimeline(false); }
  }

  function nice(n) { return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k" : String(n); }
  function showViews() {
    var c = state.ed.clip, has = c.views != null;
    $("pViews").value = has ? c.views : "";
    $("edViews").classList.toggle("hidden", !has);
    $("edViews").textContent = has ? "👁 " + nice(c.views) + " views" : "";
  }
  async function saveViews() {
    var ed = state.ed; if (!ed) return;
    var raw = $("pViews").value.trim();
    try {
      ed.clip = await api("POST", "/library/views", { key: ed.key, clip_id: ed.id, views: raw === "" ? null : Math.max(0, Math.round(Number(raw))) });
      showViews(); toast(raw === "" ? "Views removed." : "Saved. The AI now knows how this one did.");
    } catch (e) { toast(e.message, true); }
  }
  async function syncViews() {
    var b = $("btnSyncViews"); b.disabled = true; b.textContent = "Checking TikTok…";
    try {
      var r = await api("POST", "/tiktok/sync_views", {});
      toast(r.matched ? "Got views for " + r.matched + " TikTok" + (r.matched > 1 ? "s" : "") + "." : "Looked at " + r.videos + " TikTok posts but couldn't match any by title. Type the views in instead.");
      await refresh();
      if (state.ed) { var s = streamByKey(state.ed.key); var c = (s.clips || []).filter(function (x) { return x.id === state.ed.id; })[0]; if (c) { state.ed.clip = c; showViews(); } }
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "↻ Get views from TikTok"; }
  }
  /* ---------------- themes ---------------- */
  var THEMES = [
    ["midnight", "Midnight", "#161618", "#1c1c1f", "#7c4dff"],
    ["genie", "Genie Gold", "#0e1120", "#12162a", "#f5b942"],
    ["neon", "Neon", "#050507", "#0a0a0e", "#00e5ff"],
    ["ember", "Ember", "#17120f", "#1d1612", "#ff5a36"],
    ["mint", "Mint", "#0f1615", "#131c1b", "#2dd4a3"],
    ["daylight", "Daylight", "#f4f4f7", "#ffffff", "#5b3df5"]
  ];
  function setTheme(id, save) {
    document.documentElement.dataset.theme = id;
    try { localStorage.setItem("cg_theme", id); } catch (e) {}
    document.querySelectorAll(".theme-card").forEach(function (b) { b.classList.toggle("on", b.dataset.theme === id); });
    if (save) api("POST", "/settings", { theme: id }).catch(function () {});
  }
  function buildThemes() {
    var box = $("themes"); if (!box || box.childElementCount) return;
    THEMES.forEach(function (t) {
      var b = document.createElement("button"); b.type = "button"; b.className = "theme-card"; b.dataset.theme = t[0];
      b.innerHTML = '<div class="pv"><i style="background:' + t[3] + '"></i><i style="background:' + t[2] + '"></i><b style="background:' + t[4] + '"></b></div><span>' + t[1] + '</span>';
      b.addEventListener("click", function () { setTheme(t[0], true); });
      box.appendChild(b);
    });
    setTheme(document.documentElement.dataset.theme || "midnight", false);
  }

  function showLearned(t) {
    var n = t.likes + t.dislikes + t.edits + t.views;
    $("learnedStats").textContent = n ? "From " + t.likes + " 👍, " + t.dislikes + " 👎, " + t.edits + " edits and " + t.views + " TikToks with views." : "Nothing yet. Rate moments with 👍 / 👎, edit TikToks and add their views: it learns from all of it.";
    $("learnedSummary").textContent = t.summary || "";
    $("learnedRules").innerHTML = (t.rules || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
    $("btnRules").classList.toggle("hidden", !t.ai_ready || n < 3);
  }

  function closeEditor() {
    if (state.ed && state.ed.dirty && !confirm("Leave without applying your changes?")) return;
    toggleTimeline(false);
    $("edVideo").pause(); $("edVideo").removeAttribute("src"); $("edVideo").load();
    $("editor").classList.add("hidden"); state.ed = null; updateSteps(); refresh();
  }
  function setDirty() { state.ed.dirty = true; $("edApply").disabled = false; livePreview(true); }
  /* 2.29: live 9:16 preview in the Timeline. Changing the look (Twitch logo, name, title, layout...) redraws it
     straight away; in the Look section a change opens the live view if it isn't open yet. */
  var liveT = null, liveSeq = 0;
  function livePreview(fromUser) {
    var ed = state.ed; if (!ed) return;
    if (!window.CGTimeline.isOpen()) {
      if (fromUser && state.sec === "look" && $("tlWrap").classList.contains("hidden") && !ed.liveAsked) {
        ed.liveAsked = true; toast("Opening the live preview: your changes show on the right as you make them.");
        toggleTimeline(true);
      }
      return;
    }
    clearTimeout(liveT);
    liveT = setTimeout(function () {
      var seq = ++liveSeq, b = window.CGTimeline.boxes(), style = {};
      ED_OPTS.forEach(function (k) { if (ed.style[k] !== undefined) style[k] = ed.style[k]; });
      api("POST", "/library/preview", { key: ed.key, clip_id: ed.id, style: style, cam_box: b.cam_box, game_box: b.game_box })
        .then(function (g) { if (seq === liveSeq && state.ed === ed) window.CGTimeline.setGeo(g); })
        .catch(function () {});
    }, 90);
  }
  window.CGLivePreview = function () { livePreview(false); };
  var SECS = {
    look: ["Look", "Layout, title, channel name and the Twitch logo", "pane-edit"],
    subs: ["Subtitles", "Words on the video", "pane-subs"],
    chat: ["Chat pop-up", "A real chat message from the moment", "pane-edit"],
    kill: ["Kill feed", "Game parts and kills on screen", "pane-edit"],
    sound: ["Sound", "Reaction sounds", "pane-edit"],
    cut: ["Cutting", "Trim, jump cuts, zooms, cutaways", "pane-edit"],
    ask: ["Ask the AI", "Type what you want changed", null],
    post: ["Export", "Title, caption and saving the video", "pane-post"]   // 2.36.1: export only (the creator)
  };
  function switchTab(t) {
    if (!SECS[t]) t = "look";
    state.sec = t;
    document.querySelectorAll(".tab[data-tab]").forEach(function (b) { var on = b.dataset.tab === t; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    var pane = SECS[t][2];
    ["pane-subs", "pane-edit", "pane-post"].forEach(function (p) { $(p).classList.toggle("hidden", p !== pane); });
    document.querySelectorAll("#pane-edit .sec").forEach(function (x) { x.classList.toggle("hidden", x.dataset.sec !== t); });
    $("askBox").classList.toggle("hidden", t !== "ask");
    $("secTitle").textContent = SECS[t][0]; $("secSub").textContent = SECS[t][1];
    if (t === "post") refreshAccount();
    updateSteps();
  }
  function syncEdControls() {
    var o = state.ed.style;
    $("styleGrid").innerHTML = STYLES.map(function (s) {
      return '<button class="style-card' + (o.style === s.id ? " on" : "") + '" data-style="' + s.id + '" aria-pressed="' + (o.style === s.id) + '"><img class="prev" src="subs/' + s.id + '.jpg" alt="" loading="lazy"><span class="name">' + s.name + '</span></button>';
    }).join("");
    $("swatches").innerHTML = SWATCHES.map(function (c) {
      return '<button class="swatch' + (String(o.highlight).toLowerCase() === c ? " on" : "") + '" data-color="' + c + '" style="background:' + c + '" aria-label="Highlight ' + c + '"></button>';
    }).join("");
    $("sFont").value = o.font || "Rubik Black";
    $("sWatermark").value = o.watermark || "";
    $("sWords").value = String(o.words_per_line || "3");
    $("sSize").value = o.size || 1;
    $("sPos").value = o.position || 0.62;
    $("sLayout").value = o.layout || "auto";
    document.querySelectorAll("#editor [data-opt]").forEach(function (b) { var on = !!o[b.dataset.opt]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    $("posField").classList.toggle("hidden", !!o.caption_seam);
    $("tlSubsOff").classList.toggle("hidden", !!o.captions);
    $("noSubs").classList.toggle("hidden", !!o.captions);
    $("subsOn").classList.toggle("hidden", !o.captions);
    renderLooks();
  }
  function trimInfo() {
    var c = state.ed.clip, e = state.ed.edits;
    var st = e.start != null ? e.start : c.start, en = e.end != null ? e.end : c.end;
    $("trimInfo").textContent = "Clip runs " + fmt(st) + " → " + fmt(en) + " of the stream (" + Math.round(en - st) + "s before cuts)";
  }
  /* 2.29: the loading screen shows the moment Apply is pressed, over the preview AND the timeline */
  var BUSY_FUN = { apply: ["Cutting your clip…", "Placing your webcam…", "Framing the gameplay…", "Adding your Twitch name…",
      "Writing the subtitles…", "Mixing the sound…", "Rendering your TikTok…", "Almost there…"],
    ask: ["Reading what you asked…", "Working out the change…", "Re-cutting the clip…", "Rendering your TikTok…"] };
  var busyT = null;
  function busyStart(kind) {
    var list = BUSY_FUN[kind] || BUSY_FUN.apply, i = 0;
    $("edBusyFun").textContent = list[0]; $("edBusyMsg").textContent = "Starting…"; $("edBusyBar").style.width = "2%";
    $("edBusy").classList.remove("hidden");
    clearInterval(busyT); busyT = setInterval(function () { i = Math.min(list.length - 1, i + 1); $("edBusyFun").textContent = list[i]; }, 2600);
  }
  function busyStop() { clearInterval(busyT); $("edBusy").classList.add("hidden"); $("edBusyMsg").textContent = "Re-building…"; $("edBusyBar").style.width = "0"; }
  async function applyEdits() {
    var ed = state.ed; if (!ed) return;
    var txt = $("subText").value.trim();
    if (txt && txt !== ed.original.trim()) ed.edits.text = txt; else delete ed.edits.text;
    var tl = !$("tlWrap").classList.contains("hidden") && window.CGTimeline.isChanged() ? window.CGTimeline.result() : null;
    if (tl) {
      delete ed.edits.text;            // the timeline's subtitles win
      ed.edits.start = tl.start; ed.edits.end = tl.end;
      ed.edits.segs = tl.segs; ed.edits.captions = tl.captions;
      if (tl.cam_box) ed.edits.cam_box = tl.cam_box;
      if (tl.game_box) ed.edits.game_box = tl.game_box;
      ed.edits.seg_gain = tl.seg_gain;
      if (tl.music_volume != null) ed.edits.music_volume = tl.music_volume;
      ed.edits.volume_db = tl.volume_db;
      ed.edits.sfx = tl.sfx;
    }
    var style = {}; ED_OPTS.forEach(function (k) { if (ed.style[k] !== undefined) style[k] = ed.style[k]; });
    busyStart("apply"); $("edApply").disabled = true;
    unloadVideo();
    try {
      var r = await api("POST", "/library/rerender", { key: ed.key, clip_id: ed.id, edits: ed.edits, style: style });
      var c = await pollJob(r.job, function (p, m) { $("edBusyMsg").textContent = (m || "Re-building") + " · " + Math.round(p * 100) + "%"; $("edBusyBar").style.width = Math.round(p * 100) + "%"; });
      ed.clip = c; ed.dirty = false; loadVideo(); showUndo(); showChecks(c); toast("TikTok updated.");
      loadWords();
      if (!$("tlWrap").classList.contains("hidden")) { toggleTimeline(false); toggleTimeline(true); }
    } catch (e) { toast(e.message, true); $("edApply").disabled = false; loadVideo(); }
    finally { busyStop(); }
  }
  /* 0.1.2: the subtitle text box shows the words the subtitles are made from (in the picked language) */
  async function loadWords() {
    var ed = state.ed; if (!ed) return;
    try {
      var w = await api("GET", "/library/words?key=" + encodeURIComponent(ed.key) + "&clip_id=" + encodeURIComponent(ed.id));
      if (state.ed !== ed || !w.words) return;
      ed.original = w.words.join(" ");
      if (!(ed.edits && ed.edits.text)) $("subText").value = ed.original;
    } catch (e) {}
  }
  /* The TikTok changed on the PC (AI edit, undo): show the new version in the editor. */
  function adoptClip(c) {
    var ed = state.ed, wasTl = !$("tlWrap").classList.contains("hidden");
    ed.clip = c; ed.edits = Object.assign({}, c.edits || {}); ed.style = Object.assign({}, state.lib.style, (streamByKey(ed.key) || {}).channel ? { watermark: streamByKey(ed.key).channel } : {}, c.style || {});
    ed.dirty = false; $("edApply").disabled = true;
    $("edTitle").textContent = c.title; $("pTitle").value = c.title || ""; $("pCaption").value = c.caption || "";
    $("subText").value = (c.edits && c.edits.text) || (c.words || []).join(" ");
    loadVideo(); syncEdControls(); trimInfo(); showUndo(); showChecks(c);
    if (wasTl) { toggleTimeline(false); toggleTimeline(true); }
  }
  /* 2.35.3: names the kill feed read (Windows OCR can get them wrong): type the right name, Apply re-makes it. */
  function renderKfNames() {
    var ed = state.ed, names = (ed && ed.clip && ed.clip.kill_names) || [], fix = (ed && ed.style.kf_names) || {};
    $("kfNamesWrap").classList.toggle("hidden", !names.length);
    $("kfNames").innerHTML = names.map(function (n) {
      return '<label class="field kf-name"><span class="tiny muted">Read as: ' + esc(n) + '</span>' +
        '<input type="text" maxlength="40" data-kfname="' + esc(n) + '" value="' + esc(fix[n] != null ? fix[n] : n) + '"></label>';
    }).join("");
  }
  /* 2.35.1: the self-check after a TikTok is made (too much cut, shots cut out, mid-word ends, black / silent). */
  function showChecks(c) {
    var el = $("edChecks"), w = (c && c.checks) || [];
    el.classList.toggle("hidden", !w.length);
    el.innerHTML = w.length ? '<strong>⚠ Check this TikTok</strong><ul>' + w.map(function (x) { return '<li>' + esc(x.text) + '</li>'; }).join("") + '</ul>' : "";
  }
  function showUndo() {
    var h = (state.ed && state.ed.clip.history) || [], b = $("edUndo");
    $("edOriginal").classList.toggle("hidden", !Object.keys((state.ed && state.ed.clip.edits) || {}).length);
    b.classList.toggle("hidden", !h.length);
    if (h.length) b.title = "Undo: " + (h[h.length - 1].note || "last change") + (h.length > 1 ? " (" + h.length + " steps back available)" : "");
  }
  async function undoLast() {
    var ed = state.ed; if (!ed || !(ed.clip.history || []).length) return;
    if (ed.dirty && !confirm("You have changes you haven't applied. Undo the last applied change anyway?")) return;
    var b = $("edUndo"); b.disabled = true;
    unloadVideo();
    try {
      var c = await api("POST", "/library/undo", { key: ed.key, clip_id: ed.id });
      if (state.ed !== ed) return;
      adoptClip(c);
      $("askReply").textContent = "Undone. This TikTok is back to how it was before: " + (c.undone || "the last change") + ".";
      toast("Undone: " + (c.undone || "last change"));
      refresh();
    } catch (e) { toast(e.message, true); loadVideo(); }
    finally { b.disabled = false; }
  }

  /* "Tell the AI what to change": type it, the AI edits this TikTok. */
  async function askAI() {
    var ed = state.ed; if (!ed) return;
    var text = $("askText").value.trim();
    if (!text) { $("askText").focus(); return; }
    if (state.lib.ai_trial) { acctError("TRIAL_USED: The AI isn't part of the free trial. Upgrade to use it."); return; }
    if (!state.lib.ai_ready) { toast("Add your Claude API key in Settings to use this.", true); openSettings(); return; }
    var txt = $("subText").value.trim();
    if (txt && txt !== ed.original.trim()) ed.edits.text = txt;
    var style = {}; ED_OPTS.forEach(function (k) { if (ed.style[k] !== undefined) style[k] = ed.style[k]; });
    var btn = $("btnAsk"); btn.disabled = true; btn.textContent = "Working…";
    busyStart("ask"); $("edBusyMsg").textContent = "AI is reading your request…";
    unloadVideo();
    var reloaded = false;
    try {
      var r = await api("POST", "/library/ask", { key: ed.key, clip_id: ed.id, text: text, edits: ed.edits, style: style });
      var c = await pollJob(r.job, function (p, m) { $("edBusyMsg").textContent = (m || "Working") + " · " + Math.round(p * 100) + "%"; $("edBusyBar").style.width = Math.round(p * 100) + "%"; });
      if (state.ed !== ed) return;
      $("askReply").innerHTML = '<span class="said">' + esc(c.ai_reply || "") + '</span>' + (c.ai_cannot ? '<span class="cant">' + esc(c.ai_cannot) + '</span>' : "");
      if (c.ai_changed) {
        $("askReply").innerHTML += ' <button class="link-btn" id="askUndo">Undo this</button>';
        $("askText").value = "";
        adoptClip(c); reloaded = true;
        toast("Done. Not right? Press Undo, or tell it what to fix.");
      }
    } catch (e) { toast(e.message, true); }
    finally { if (!reloaded && state.ed === ed) loadVideo(); btn.disabled = false; btn.textContent = "Do it"; busyStop(); }
  }
  async function saveField(fields) {
    var ed = state.ed; if (!ed) return;
    try { ed.clip = await api("POST", "/library/clip", { key: ed.key, clip_id: ed.id, fields: fields }); }
    catch (e) { toast(e.message, true); }
  }

  /* ---------------- TikTok ---------------- */
  // 2.35.4: no TikTok keys (testers / public) = export only: hide "Connect", "Send to TikTok drafts" and "Get views"
  function ttMode(st) { document.body.classList.toggle("no-tiktok", !(st && st.tiktok_client_key)); }
  async function refreshAccount() {
    try {
      var a = await api("GET", "/tiktok/account"); state.account = a;
      $("accName").textContent = a.connected ? (a.display_name || "TikTok connected") : "TikTok not connected";
      $("accSub").textContent = a.connected ? "Connected" : (a.error || "Connect @hotgamerdude_");
      $("btnConnect").textContent = a.connected ? "Disconnect" : "Connect";
      if (a.connected && a.avatar) document.querySelector("#account .avatar").innerHTML = '<img alt="" src="' + esc(a.avatar) + '">';
    } catch (e) {}
  }
  async function connect() {
    try {
      if (state.account && state.account.connected) { await api("POST", "/tiktok/logout"); return refreshAccount(); }
      var r = await api("GET", "/tiktok/login");
      window.open(r.url, "_blank");
      $("accSub").textContent = "Finish logging in, in the window that opened…";
      for (var i = 0; i < 60; i++) { await sleep(3000); await refreshAccount(); if (state.account && state.account.connected) return; }
    } catch (e) { toast(e.message, true); if (/client key/i.test(e.message)) openSettings(); }
  }
  async function upload() {
    var ed = state.ed; if (!ed) return;
    if (ed.dirty) { toast("Press Apply changes first, so the upload has your edits.", true); return; }
    if (!(state.account && state.account.connected)) { toast("Connect your TikTok account first.", true); return; }
    var btn = $("btnUpload"); btn.disabled = true; btn.textContent = "Uploading…";
    try {
      await saveField({ title: $("pTitle").value, caption: $("pCaption").value });
      var r = await api("POST", "/tiktok/upload", { items: [{ path: ed.clip.path, caption: ($("pTitle").value + " " + $("pCaption").value).trim() }], mode: "draft" });
      await pollJob(r.job);
      await saveField({ status: "posted" });
      toast("Sent to your TikTok drafts. Open TikTok to post it.");
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = "Send to TikTok drafts"; }
  }

  /* ---------------- settings ---------------- */
  var IS_PHONE = !/^(127\.0\.0\.1|localhost)$/.test(location.hostname);   // 2.36.1: phones always come in over the Wi-Fi address
  if (IS_PHONE) document.body.classList.add("is-phone");
  function showPhone(st) {
    $("phoneSwitch").classList.toggle("on", !!st.on);
    $("phoneSwitch").setAttribute("aria-pressed", String(!!st.on));
    $("phonePanel").classList.toggle("hidden", !st.on || !st.url);
    if (st.on && st.url) {
      $("phoneUrl").textContent = st.url;
      try { var qr = qrcode(0, "M"); qr.addData(st.url); qr.make(); $("phoneQR").innerHTML = qr.createImgTag(5, 0); }
      catch (e) { $("phoneQR").textContent = ""; }
    }
    if (st.on && !st.url) toast("Couldn't find this PC's Wi-Fi address. Is it connected to your router?", true);
  }
  async function setPhone(on, newKey) {
    try { showPhone(await api("POST", "/phone", { on: on, new_key: !!newKey })); }
    catch (e) { toast(e.message, true); }
  }
  async function openSettings() {
    $("settings").classList.remove("hidden");
    if (!IS_PHONE) api("GET", "/phone").then(showPhone).catch(function () {});
    try {
      var s = await api("GET", "/settings"), st = s.style || {};
      var vd = st.volume_db != null ? Number(st.volume_db) : 0;
      $("setVolume").value = String([-6, -3, 0, 3, 6, 10].indexOf(vd) >= 0 ? vd : 0);
      var ac = st.auto_count != null ? st.auto_count : (state.lib.style.auto_count || 0);
      $("setCount").value = String([0, 3, 6, 10].indexOf(Number(ac)) >= 0 ? ac : 6);
      // show the nearest choice in the list (the built-in default, 22 s, isn't one of them: an unmatched
      // value left the box empty and Save then stored a length of 0)
      var tl = Number(st.target_len || state.lib.style.target_len || 30), lens = [20, 30, 40, 55];
      $("setLen").value = String(lens.reduce(function (a, b) { return Math.abs(b - tl) < Math.abs(a - tl) ? b : a; }));
      $("setOut").value = s.out_dir || "";
      var mus = (st.bg_music || state.lib.style.bg_music || "");
      $("musicName").textContent = mus ? mus.split(/[\\/]/).pop() : "None";
      $("setMusicVol").value = st.bg_music_volume || state.lib.style.bg_music_volume || 0.16;
      $("setApiKey").value = s.api_key || "";
      $("setSubs").value = s.subs_default ? "1" : "0";
      $("setGameLooks").value = s.game_looks ? "1" : "0";
      $("setNotes").value = s.ai_notes || "";
      buildThemes();
      renderSfx();
      if (s.theme && s.theme !== document.documentElement.dataset.theme) setTheme(s.theme, false);
      api("GET", "/taste").then(showLearned).catch(function () {});
      $("setWhisper").value = s.whisper_model || "ivrit-turbo";
      $("setDevice").value = s.device || "auto";
      $("setLang").value = s.language || "he";
      $("setTtKey").value = s.tiktok_client_key || "";
      $("setTtSecret").value = s.tiktok_client_secret || "";
    } catch (e) { toast(e.message, true); }
  }
  async function saveSettings() {
    try {
      var cur = await api("GET", "/settings");
      var t = Number($("setLen").value) || Number((cur.style || {}).target_len) || 30;   // never save a length of 0
      var style = Object.assign({}, cur.style || {}, { volume_db: Number($("setVolume").value), auto_count: Number($("setCount").value), target_len: t, min_len: Math.max(10, t * 0.5), max_len: t * 1.5 });
      await api("POST", "/settings", { out_dir: $("setOut").value.trim(), api_key: $("setApiKey").value.trim(),
        subs_default: $("setSubs").value === "1", game_looks: $("setGameLooks").value === "1", ai_notes: $("setNotes").value.trim(), whisper_model: $("setWhisper").value, device: $("setDevice").value, language: $("setLang").value,
        tiktok_client_key: $("setTtKey").value.trim(), tiktok_client_secret: $("setTtSecret").value.trim() });
      await api("POST", "/library/style", { style: style });
      await api("POST", "/music", { volume: Number($("setMusicVol").value) });
      ttMode({ tiktok_client_key: $("setTtKey").value.trim() });
      $("settings").classList.add("hidden"); toast("Settings saved."); refresh();
    } catch (e) { toast(e.message, true); }
  }

  /* ---------------- 2.25: first-time welcome ---------------- */
  var wc = null, WC_LAST = 2;   // 3 steps: channel, music, sound effects (kept simple on purpose)
  function channelName(v) {
    v = (v || "").trim().replace(/^https?:\/\//i, "").replace(/^(www\.|m\.)?twitch\.tv\//i, "").replace(/^@/, "");
    return v.split(/[\/?#\s]/)[0].slice(0, 25);
  }
  async function openWelcome() {
    var s = {}; try { s = await api("GET", "/settings"); } catch (e) {}
    var st = s.style || {};
    wc = { step: 0, music: st.bg_music || "", sfxOn: st.sfx !== false, react: st.react_sfx || "" };
    $("wcChannel").value = s.twitch_channel || (s.setup_done ? (st.watermark || "") : "")
      || (acct && acct.user && acct.user.guest && acct.user.name !== "Guest" ? acct.user.name : "");
    $("settings").classList.add("hidden");
    $("welcome").classList.remove("hidden");
    showWcStep();
  }
  function showWcStep() {
    var n = wc.step;
    document.querySelectorAll("#welcome .wc-step").forEach(function (x) { x.classList.toggle("hidden", Number(x.dataset.step) !== n); });
    $("wcDots").innerHTML = [0, 1, 2].map(function (i) { return '<span class="' + (i === n ? "on" : i < n ? "done" : "") + '"></span>'; }).join("");
    $("wcBack").style.visibility = n ? "visible" : "hidden";
    $("wcNext").textContent = n === WC_LAST ? "Done" : "Next";
    renderWc();
  }
  function renderWc() {
    var name = channelName($("wcChannel").value);
    $("wcTagPreview").innerHTML = name ? '<svg class="tw" viewBox="0 0 90 96" width="20" height="21" aria-hidden="true"><path fill="#9146ff" d="M14 0H90V56L66 80H48L32 96V80H0V14Z"/>' +
      '<path fill="#fff" d="M10 8H82V52L62 72H44L32 84V72H10Z"/><path fill="#9146ff" d="M36 22h8v24h-8zM58 22h8v24h-8z"/></svg>' + esc(name) : "";
    document.querySelectorAll("[data-music]").forEach(function (b) {
      b.classList.toggle("on", b.dataset.music === (wc.music ? "file" : "none"));
    });
    $("wcMusicName").textContent = wc.music ? wc.music.split(/[\\/]/).pop() : "mp3, wav, m4a…";
    $("wcSfxOn").classList.toggle("on", wc.sfxOn); $("wcSfxOn").setAttribute("aria-pressed", String(wc.sfxOn));
    $("wcSfxBox").classList.toggle("dim", !wc.sfxOn);
    var snd = looks().sounds || [];
    $("wcSfxList").innerHTML = '<button class="wc-snd' + (!wc.react ? " on" : "") + '" data-react=""><strong>💥 Built-in</strong><span>Bass hit</span></button>' +
      snd.map(function (s) {
        return '<button class="wc-snd' + (wc.react === s.name ? " on" : "") + '" data-react="' + esc(s.name) + '" data-path="' + esc(s.path) + '"><strong>' +
          esc(s.name) + "</strong><span>Your sound</span></button>";
      }).join("");
  }
  async function finishWelcome(skip) {
    try {
      var cur = await api("GET", "/settings"), name = channelName($("wcChannel").value);
      var style = Object.assign({}, cur.style || {});
      if (!skip) {
        if (name) style.watermark = name;
        style.sfx = wc.sfxOn; style.react_sfx = wc.react;
        if (!wc.music) style.bg_music = "";
      }
      await api("POST", "/library/style", { style: style });
      var put = { setup_done: true };
      if (!skip && name) put.twitch_channel = name;
      await api("POST", "/settings", put);
      $("welcome").classList.add("hidden");
      if (!skip) toast(name ? "All set, " + name + "! Paste a stream link to make your first TikToks." : "All set! Paste a stream link to start.");
      refresh();
    } catch (e) { toast(e.message, true); }
  }
  function wireWelcome() {
    $("btnWelcome").addEventListener("click", openWelcome);
    $("wcChannel").addEventListener("input", renderWc);
    $("wcBack").addEventListener("click", function () { if (wc.step) { wc.step--; showWcStep(); } });
    $("wcSkip").addEventListener("click", function () { finishWelcome(true); });
    $("wcNext").addEventListener("click", function () {
      if (wc.step === 0 && !channelName($("wcChannel").value)) { toast("Type your Twitch channel (or press Skip for now).", true); return; }
      if (wc.step < WC_LAST) { wc.step++; showWcStep(); } else finishWelcome(false);
    });
    $("welcome").addEventListener("click", async function (e) {
      var m = e.target.closest("[data-music]");
      if (m) {
        if (m.dataset.music === "none") { wc.music = ""; renderWc(); return; }
        try {
          var r = await api("POST", "/pick_file", { kind: "audio" }); if (!r.path) return;
          var d = await api("POST", "/music", { path: r.path }); wc.music = d.bg_music; renderWc();
        } catch (err) { toast(err.message, true); }
        return;
      }
      if (e.target.closest("#wcSfxOn")) { wc.sfxOn = !wc.sfxOn; renderWc(); return; }
      if (e.target.closest("#wcSfxAdd")) {
        try {
          var r2 = await api("POST", "/pick_file", { kind: "sfx" }); if (!r2.path) return;
          var d2 = await api("POST", "/sfx", { path: r2.path });
          state.lib.looks.sounds = d2.sounds; wc.react = d2.added;   // the sound you add is the one used
          var p = soundPath(d2.added); if (p) playSound(p);
          renderWc();
        } catch (err) { toast(err.message, true); }
        return;
      }
      var rb = e.target.closest("[data-react]");
      if (rb) { wc.react = rb.dataset.react; if (rb.dataset.path) playSound(rb.dataset.path); renderWc(); }
    });
  }

  /* ---------------- wiring ---------------- */
  function wire() {
    $("btnAdd").addEventListener("click", function () { addStream(); });
    $("rangeAll").addEventListener("click", function () { rangeGo(true); });
    $("rangeGo").addEventListener("click", function () { rangeGo(false); });
    $("rangeCancel").addEventListener("click", rangeClose);
    ["rangeFrom", "rangeTo"].forEach(function (id) { $(id).addEventListener("keydown", function (e) { if (e.key === "Enter") rangeGo(false); }); });
    $("addSrc").addEventListener("keydown", function (e) { if (e.key === "Enter") addStream(); });
    $("btnFile").addEventListener("click", pickFile);
    $("addLook").addEventListener("click", function (e) {
      if (e.target.closest("[data-addother]")) {   // 2.26: own styles are made in the editor, then show up here
        toast("To make your own style: open any TikTok → Look → Other, change it how you like, then \"Save as my style\". It shows up here with a ★.");
        return;
      }
      var b = e.target.closest("[data-addlook]"); if (!b) return;
      try { localStorage.setItem("cg_add_look", b.dataset.addlook); } catch (err) {}
      renderAddLooks();
    });
    document.querySelector(".streams").addEventListener("click", function (e) {
      var b = e.target.closest(".stream-item"); if (!b) return;
      state.view = b.dataset.key; renderSide(); renderMain(); $("grid").scrollIntoView({ block: "start" });
    });
    $("grid").addEventListener("click", function (e) { var c = e.target.closest(".card"); if (c) openEditor(c.dataset.key, c.dataset.id); });
    $("candList").addEventListener("click", async function (e) {
      var vb = e.target.closest("[data-vote]");
      if (vb) { sendVote(state.view, vb.closest(".cand").dataset.id, vb); return; }
      var wb = e.target.closest("[data-why]");
      if (wb) { sendWhy(state.view, wb.closest(".cand").dataset.id, wb); return; }
      var p = e.target.closest("[data-preview]");
      if (p) { previewMoment(p.closest(".cand"), p.dataset.preview, p); return; }
      var b = e.target.closest("[data-make]"); if (!b) return;
      b.disabled = true; b.textContent = "Making…";
      try { var r = await api("POST", "/library/rerender", { key: state.view, clip_id: b.dataset.make, edits: {}, style: {} }); await pollJob(r.job); toast("TikTok made."); refresh(); }
      catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Make TikTok"; }
    });
    $("candList").addEventListener("change", function (e) { if (e.target.classList.contains("pick")) updateMakeSel(); });
    $("phoneSwitch").addEventListener("click", function () { setPhone(this.getAttribute("aria-pressed") !== "true"); });
    $("phoneNewKey").addEventListener("click", function () { setPhone(true, true); });
    $("btnAiRank").addEventListener("click", async function () {
      if (this.dataset.mode === "settings") { openSettings(); return; }
      var st = streamByKey(state.view); if (!st) return;
      try {
        var r = await api("POST", "/library/add", { source: st.source });
        await refresh();
        pollJob(r.job).then(function () { toast("Re-ranked with the AI read."); refresh(); })
          .catch(function (e) { toast(e.message, true); refresh(); });
      } catch (e) { toast(e.message, true); }
    });
    $("btnMakeSel").addEventListener("click", function () { var ids = selectedMoments(); if (ids.length) makeMoments(ids); });
    $("btnFolder").addEventListener("click", function () { api("POST", "/library/open_folder", { key: state.view }).catch(function (e) { toast(e.message, true); }); });
    $("btnRemoveStream").addEventListener("click", async function () {
      if (!confirm("Remove this stream from the list? The video files stay in your folder.")) return;
      await api("POST", "/library/remove_stream", { key: state.view }); state.view = "__new"; refresh();
    });
    $("btnSettings").addEventListener("click", openSettings);
    $("btnSaveSettings").addEventListener("click", saveSettings);
    $("btnMusic").addEventListener("click", async function () {
      try {
        var r = await api("POST", "/pick_file", { kind: "audio" });
        if (!r.path) return;
        var m = await api("POST", "/music", { path: r.path });
        $("musicName").textContent = m.bg_music.split(/[\\/]/).pop();
        toast("Background music set. It plays under every new TikTok.");
      } catch (e) { toast(e.message, true); }
    });
    $("btnMusicClear").addEventListener("click", async function () {
      await api("POST", "/music", { clear: true }); $("musicName").textContent = "None";
    });
    document.querySelectorAll("[data-close]").forEach(function (b) { b.addEventListener("click", function () { b.closest(".modal").classList.add("hidden"); }); });

    $("edBack").addEventListener("click", closeEditor);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !e.defaultPrevented && !$("editor").classList.contains("hidden") && document.activeElement.tagName !== "TEXTAREA") closeEditor(); });
    $("edApply").addEventListener("click", applyEdits);
    $("edTimeline").addEventListener("click", function () { toggleTimeline(); });
    $("edVote").addEventListener("click", function (e) {
      var b = e.target.closest("[data-vote]"); if (!b || !state.ed) return;
      sendVote(state.ed.key, state.ed.id, b);
      state.ed.clip.vote = b.getAttribute("aria-pressed") === "true" ? b.dataset.vote : null;
      state.ed.clip.vote_reasons = [];
    });
    $("edWhy").addEventListener("click", async function (e) {
      var b = e.target.closest("[data-why]"); if (!b || !state.ed) return;
      var r = await sendWhy(state.ed.key, state.ed.id, b);
      if (r) state.ed.clip.vote_reasons = r;
    });
    $("tlSubsOff").addEventListener("click", function () {
      if (!state.ed) return;
      state.ed.style.captions = true; syncEdControls(); setDirty(); toast("Subtitles will be added when you apply your changes.");
    });
    $("btnAddSubs").addEventListener("click", function () {
      if (!state.ed) return;
      state.ed.style.captions = true; syncEdControls(); applyEdits();
    });
    $("edUndo").addEventListener("click", undoLast);
    $("edOriginal").addEventListener("click", function () {
      var ed = state.ed; if (!ed) return;
      if (!confirm("Re-make this TikTok the way it was first made? Every trim, cut and AI change on it is removed. (You can undo this.)")) return;
      if (!$("tlWrap").classList.contains("hidden")) toggleTimeline(false);
      ed.edits = {}; $("subText").value = ed.original = (ed.clip.words || []).join(" ");
      applyEdits().then(function () { if (state.ed === ed) adoptClip(ed.clip); });
    });
    $("askReply").addEventListener("click", function (e) { if (e.target.id === "askUndo") undoLast(); });
    $("btnFind").addEventListener("click", findMoment);
    $("findText").addEventListener("keydown", function (e) { if (e.key === "Enter") findMoment(); });
    $("btnAsk").addEventListener("click", askAI);
    $("askText").addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) askAI(); });
    $("btnSaveViews").addEventListener("click", saveViews);
    $("pViews").addEventListener("keydown", function (e) { if (e.key === "Enter") saveViews(); });
    $("btnSyncViews").addEventListener("click", syncViews);
    $("btnRules").addEventListener("click", async function () {
      var b = this; b.disabled = true; b.textContent = "Thinking…";
      try { showLearned(await api("POST", "/taste/rules", {})); toast("Updated what Klipon knows about your taste."); }
      catch (e) { toast(e.message, true); }
      finally { b.disabled = false; b.textContent = "Update now"; }
    });
    $("edDiscard").addEventListener("click", async function () {
      if (!confirm("Discard this TikTok? The file stays in your folder.")) return;
      await saveField({ status: "discarded" }); state.ed.dirty = false; closeEditor();
    });
    document.querySelectorAll(".tab[data-tab]").forEach(function (b) { b.addEventListener("click", function () { switchTab(b.dataset.tab); }); });
    $("steps").addEventListener("click", function (e) {
      var b = e.target.closest("[data-step]"); if (!b) return;
      var n = b.dataset.step;
      if (n === "1") { if (state.ed) closeEditor(); if (state.ed) return; state.view = "__new"; renderSide(); renderMain(); $("addSrc").focus(); }
      else if (n === "2") {
        if (state.ed) closeEditor(); if (state.ed) return;
        var s2 = streams().filter(function (s) { return (s.extra_candidates || []).length && s.status === "ready"; })[0] || streamByKey(state.view);
        if (s2) { state.view = s2.key; renderSide(); renderMain(); }
        $("candidates").scrollIntoView({ block: "start" });
      } else if (n === "3" && state.ed) switchTab("look");
      else if (n === "4" && state.ed) switchTab("post");
    });
    $("subText").addEventListener("input", setDirty);
    $("styleGrid").addEventListener("click", function (e) { var b = e.target.closest("[data-style]"); if (!b) return; state.ed.style.style = b.dataset.style; syncEdControls(); setDirty(); });
    $("swatches").addEventListener("click", function (e) { var b = e.target.closest("[data-color]"); if (!b) return; state.ed.style.highlight = b.dataset.color; syncEdControls(); setDirty(); });
    [["sFont", "font", String], ["sWatermark", "watermark", String], ["sWords", "words_per_line", String], ["sSize", "size", Number], ["sPos", "position", Number], ["sLayout", "layout", String]].forEach(function (p) {
      $(p[0]).addEventListener("change", function () { state.ed.style[p[1]] = p[2](this.value); lookChanged(p[1]); if (p[1] === "layout") renderLooks(); setDirty(); });
      if (p[0] === "sWatermark") $(p[0]).addEventListener("input", function () { state.ed.style.watermark = this.value; livePreview(true); });
    });
    document.querySelectorAll("#editor [data-opt]").forEach(function (b) {
      b.addEventListener("click", function () { state.ed.style[b.dataset.opt] = !state.ed.style[b.dataset.opt]; lookChanged(b.dataset.opt); syncEdControls(); setDirty(); });
    });
    $("lookGrid").addEventListener("click", function (e) {
      var b = e.target.closest("[data-look]"); if (!b) return;
      if (b.dataset.look === "__other") {   // 2.26: "Other" = make your own style from what's on screen now
        state.ed.style.look = "custom"; renderLooks(); setDirty();
        $("btnSaveLook").scrollIntoView({ block: "center", behavior: "smooth" });
        return;
      }
      applyLook(b.dataset.look);
    });
    $("kfNames").addEventListener("input", function (e) {
      var i = e.target.closest("[data-kfname]"); if (!i || !state.ed) return;
      var fix = Object.assign({}, state.ed.style.kf_names || {}), n = i.dataset.kfname, v = i.value.trim();
      if (!v || v === n) delete fix[n]; else fix[n] = v;
      state.ed.style.kf_names = fix; setDirty();
    });
    $("hudParts").addEventListener("click", function (e) {
      var b = e.target.closest("[data-hud]"); if (!b || !state.ed) return;
      var ov = (state.ed.style.overlays || []).slice(), k = ov.indexOf(b.dataset.hud);
      if (k >= 0) ov.splice(k, 1); else ov.push(b.dataset.hud);
      state.ed.style.overlays = ov; lookChanged("overlays"); syncEdControls(); setDirty();
    });
    [["sTitlePos", "title_pos"], ["sTitleColor", "title_highlight"], ["sWmPos", "watermark_pos"], ["sTitleStyle", "title_style"], ["sChatPop", "chat_popups"]].forEach(function (p) {
      $(p[0]).addEventListener("change", function () { state.ed.style[p[1]] = this.value; lookChanged(p[1]); syncEdControls(); setDirty(); });
    });
    [["sChatOpacity", "chat_opacity"], ["sChatNameOpacity", "chat_name_opacity"]].forEach(function (p) {
      $(p[0]).addEventListener("input", function () { $(p[0] + "Val").textContent = this.value + "%"; });
      $(p[0]).addEventListener("change", function () { state.ed.style[p[1]] = Number(this.value); setDirty(); });
    });
    [["sLogoSize", "%"], ["sLogoX", ""], ["sLogoY", ""], ["sLogoOpacity", "%"]].forEach(function (p) {
      $(p[0]).addEventListener("input", function () {
        $(p[0] + "Val").textContent = this.value + p[1];
        var v = Number(this.value), st = state.ed && state.ed.style; if (!st) return;   // 2.29: live while dragging
        if (p[0] === "sLogoSize") { st.watermark_logo = v > 0; st.logo_size = v > 0 ? v / 100 : 1; }
        else if (p[0] === "sLogoOpacity") st.watermark_opacity = v;
        else st[p[0] === "sLogoX" ? "logo_dx" : "logo_dy"] = v / 100;
        livePreview(true);
      });
      $(p[0]).addEventListener("change", function () {
        var v = Number(this.value), st = state.ed.style;
        if (p[0] === "sLogoSize") { st.watermark_logo = v > 0; st.logo_size = v > 0 ? v / 100 : 1; }
        else if (p[0] === "sLogoOpacity") st.watermark_opacity = v;
        else st[p[0] === "sLogoX" ? "logo_dx" : "logo_dy"] = v / 100;
        setDirty();
      });
    });
    $("sReactSfx").addEventListener("change", function () {
      state.ed.style.react_sfx = this.value; setDirty();
      var p = soundPath(this.value); if (p) playSound(p);
    });
    $("btnSfxAdd").addEventListener("click", async function () {
      try {
        var r = await api("POST", "/pick_file", { kind: "sfx" }); if (!r.path) return;
        var d = await api("POST", "/sfx", { path: r.path });
        state.lib.looks.sounds = d.sounds; renderSfx();
        if (state.ed) syncEdControls();
        toast("Added \"" + d.added + "\". Use it on big reactions (Edit tab) or place it in the Timeline.");
      } catch (e) { toast(e.message, true); }
    });
    $("sfxList").addEventListener("click", async function (e) {
      var pb = e.target.closest("[data-play]"); if (pb) { playSound(pb.dataset.play); return; }
      var db = e.target.closest("[data-del]"); if (!db) return;
      if (!confirm("Remove the sound \"" + db.dataset.del + "\"? TikToks already made keep it; new ones won't use it.")) return;
      try { var d = await api("POST", "/sfx", { name: db.dataset.del, delete: true }); state.lib.looks.sounds = d.sounds; renderSfx(); }
      catch (err) { toast(err.message, true); }
    });
    $("sTitleText").addEventListener("input", function () { state.ed.style.title_text = this.value.trim(); setDirty(); });
    $("sLinger").addEventListener("input", function () { var v = Number(this.value); $("sLingerVal").textContent = v ? v + " s after the last word" : "cut right away"; });
    $("sLinger").addEventListener("change", function () { state.ed.style.end_linger = Number(this.value); setDirty(); });
    $("sTitleSize").addEventListener("input", function () { $("sTitleSizeVal").textContent = this.value + "%"; if (state.ed) { state.ed.style.title_size = Number(this.value) / 100; livePreview(true); } });
    $("sTitleSize").addEventListener("change", function () { state.ed.style.title_size = Number(this.value) / 100; lookChanged("title_size"); setDirty(); });
    $("sSubsMode").addEventListener("change", function () { state.ed.style.subs_mode = this.value; lookChanged("subs_mode"); setDirty(); });
    /* 0.1.2: the subtitle language comes first; the subtitles are then written in that language only */
    $("sSubsLang").addEventListener("change", function () {
      var ed = state.ed; if (!ed) return;
      ed.style.subs_lang = this.value;
      delete ed.edits.text; delete ed.edits.captions;   // fixed words were in the old language
      setDirty();
      if (ed.style.captions) toast("Press Apply to re-make the subtitles in this language.");
    });
    $("sEndWord").addEventListener("input", function () { state.ed.style.end_word = this.value.trim(); setDirty(); });
    $("btnSaveLook").addEventListener("click", async function () {
      if (!state.ed) return;
      var cur = lookList().filter(function (l) { return l.id === state.ed.style.look && l.mine; })[0];
      var name = prompt("Name your style (it'll be in the Look list for every TikTok):", cur ? cur.name : "");
      if (!name || !name.trim()) return;
      var style = {}; ED_OPTS.forEach(function (k) { if (k !== "captions" && k !== "title_text" && k !== "look" && k !== "kf_names" && k !== "subs_lang" && state.ed.style[k] !== undefined) style[k] = state.ed.style[k]; });
      try {
        state.lib.looks = await api("POST", "/styles", { name: name.trim(), style: style });
        state.ed.style.look = "mine:" + name.trim().slice(0, 40); syncEdControls();
        toast("Saved \"" + name.trim() + "\". Pick it for any TikTok. Use \"Use these settings for all new TikToks\" to make it your default.");
      } catch (e) { toast(e.message, true); }
    });
    $("btnDelLook").addEventListener("click", async function () {
      var id = state.ed && state.ed.style.look; if (!id || id.indexOf("mine:") !== 0) return;
      var name = id.slice(5);
      if (!confirm("Delete your style \"" + name + "\"? TikToks already made with it don't change.")) return;
      try { state.lib.looks = await api("POST", "/styles", { name: name, delete: true }); state.ed.style.look = "custom"; syncEdControls(); toast("Style deleted."); }
      catch (e) { toast(e.message, true); }
    });
    document.querySelectorAll("[data-trim]").forEach(function (b) {
      b.addEventListener("click", function () {
        var p = b.dataset.trim.split(","), k = p[0], d = Number(p[1]), c = state.ed.clip, e = state.ed.edits;
        var cur = e[k] != null ? e[k] : c[k];
        e[k] = Math.max(0, cur + d);
        delete e.segs; delete e.captions;
        var st = e.start != null ? e.start : c.start, en = e.end != null ? e.end : c.end;
        if (en - st < 6) { e[k] = cur; toast("A TikTok needs at least 6 seconds.", true); return; }
        trimInfo(); setDirty();
      });
    });
    $("btnSaveDefault").addEventListener("click", async function () {
      var cur = await api("GET", "/settings");
      var style = Object.assign({}, cur.style || {});
      ED_OPTS.forEach(function (k) { if (k !== "captions" && k !== "title_text" && k !== "kf_names" && k !== "subs_lang" && state.ed.style[k] !== undefined) style[k] = state.ed.style[k]; });
      await api("POST", "/library/style", { style: style }); toast("New TikToks will use these settings.");
      state.lib.style = Object.assign({}, state.lib.style, style);
    });
    $("btnConnect").addEventListener("click", connect);
    $("btnUpload").addEventListener("click", upload);
    var plats = document.querySelectorAll(".postall-pick input");
    try { var savedP = JSON.parse(localStorage.getItem("cg_post_plats") || "null"); if (savedP) plats.forEach(function (i) { i.checked = savedP.indexOf(i.dataset.plat) >= 0; }); } catch (e) {}
    $("btnPostAll").addEventListener("click", async function () {
      var ed = state.ed; if (!ed) return;
      var pick = Array.prototype.filter.call(plats, function (i) { return i.checked; }).map(function (i) { return i.dataset.plat; });
      if (!pick.length) { toast("Tick at least one app.", true); return; }
      try { localStorage.setItem("cg_post_plats", JSON.stringify(pick)); } catch (e) {}
      await saveField({ title: $("pTitle").value, caption: $("pCaption").value });
      var cap = $("pCaption").value.trim() || $("pTitle").value.trim(), copied = false;
      try { await navigator.clipboard.writeText(cap); copied = true; }
      catch (e) { try { $("pCaption").select(); copied = document.execCommand("copy"); } catch (e2) {} }
      try {
        await api("POST", "/library/post_open", { key: ed.key, clip_id: ed.id, platforms: pick });
        toast((copied ? "Caption copied. " : "") + "Drag the highlighted clip into each upload page" + (copied ? ", paste the caption (Ctrl+V)" : "") + " and post. Then press Mark as posted.");
      } catch (e) { toast(e.message, true); }
    });
    // 2.36.1: Export to phone = a QR code that pairs the phone AND opens this TikTok there (the creator)
    $("btnPhoneExport").addEventListener("click", async function () {
      var ed = state.ed; if (!ed) return;
      try {
        var st = await api("GET", "/phone"), turned = false;
        if (!st.on) { st = await api("POST", "/phone", { on: true }); turned = true; showPhone(st); }
        if (!st.url) { toast("Couldn't start phone access. Is this PC on your home Wi-Fi?", true); return; }
        var url = st.url + "?open=" + encodeURIComponent(ed.key + "|" + ed.id);
        var qr = qrcode(0, "M"); qr.addData(url); qr.make(); $("exportQRImg").innerHTML = qr.createImgTag(5, 0);
        $("exportQR").classList.remove("hidden");
        if (turned) toast("Phone access is on (Settings → Use Klipon on your phone).");
      } catch (e) { toast(e.message, true); }
    });
    $("btnMarkPosted").addEventListener("click", async function () { await saveField({ status: "posted" }); toast("Marked as posted."); });
    $("pTitle").addEventListener("change", function () { saveField({ title: this.value }); $("edTitle").textContent = this.value; });
    $("pCaption").addEventListener("change", function () { saveField({ caption: this.value }); });
  }

  wire();
  wireWelcome();
  api("GET", "/settings").then(function (st) { ttMode(st); if (st.theme && st.theme !== document.documentElement.dataset.theme) setTheme(st.theme, false); }).catch(function () {});
  refresh().then(function () {
    // 2.36.1: opened from an "Export to phone" QR code: go straight to that TikTok's Export panel
    var m = /^#open=(.+)$/.exec(location.hash || "");
    if (m) {
      var parts = decodeURIComponent(m[1]).split("|");
      history.replaceState(null, "", location.pathname);
      if (parts.length === 2 && streamByKey(parts[0])) openEditor(parts[0], parts[1]).then(function () { switchTab("post"); }).catch(function () {});
    }
    // 2.25: new streamers get the welcome once (anyone who already has streams is treated as set up)
    if (IS_PHONE) { loadAccount(); return; }
    function welcomeOnce() {
      api("GET", "/settings").then(function (st) {
        var has = state.lib && Object.keys(state.lib.streams || {}).length;
        if (!st.setup_done && !has) openWelcome();
      }).catch(function () {});
    }
    // public builds: sign in with Twitch first, then the welcome
    loadAccount().then(function (a) {
      // free (no login, the creator 12 Nov): never show the sign-in box; the engine makes this PC's account itself
      welcomeOnce();   // no login screen, ever
    });
  });

  /* ---------------- Sign in with Twitch + free trial (public builds; licence.py) ---------------- */
  var acct = null, acctAfter = null, acctPolling = false;
  function acctLabel(a) {
    if (!a || !a.enforced) return "";
    if (!a.signed_in) return a.free ? "Get started" : "Start free trial";
    if (a.banned) return "Account blocked";
    if (a.closed) return "Beta ended";
    if (a.free) return "Free · " + ((a.user && a.user.name) || "Account");
    if (a.plan === "trial") return "Free trial · " + a.left + " of " + a.limit + " left";
    return (a.user && a.user.name) || "Account";
  }
  function showAccount(a) {
    acct = a;
    var b = $("btnAccount");
    b.classList.add("hidden");   // no login (the creator, 12 Nov): no account button at all
    b.textContent = acctLabel(a);
    b.classList.toggle("primary", !!(a && a.enforced && (!a.signed_in || (a.plan === "trial" && a.left <= 0))));
    b.classList.toggle("ghost", !(a && a.enforced && (!a.signed_in || (a.plan === "trial" && a.left <= 0))));
  }
  function loadAccount(fresh) {
    return api("POST", "/account", { fresh: !!fresh }).then(function (a) { showAccount(a); return a; })
      .catch(function () { return null; });
  }
  function openAcct(usedUp) {
    var a = acct || {}, inn = !!a.signed_in, plan = $("acctPlan");
    if (a.free && !a.banned && !a.closed) usedUp = false;
    var tr = a.plan === "trial" && !a.free;   // a counted trial (not the free version)
    $("acctTitle").textContent = a.free && !inn ? "Welcome to Klipon"
      : a.free ? "Klipon is free"
      : !inn ? "Start your free trial"
      : usedUp || (a.plan === "trial" && a.left <= 0) ? "Your free trial is used up"
      : a.plan === "trial" ? "Free trial" : "Your account";
    if (inn && a.banned) $("acctTitle").textContent = "Account blocked";
    if (inn && a.closed) $("acctTitle").textContent = "The beta has ended";
    $("acctText").textContent = inn && a.banned ? "This account is blocked from Klipon."
      : inn && a.closed ? "Thanks for testing Klipon! The beta is over. Your videos are still in your Videos folder."
      : a.free && !inn ? "Klipon is free. No limits, every feature. Sign in with Twitch, or carry on without it."
      : a.free ? "Unlimited streams and TikToks, every feature. Enjoy!"
      : !inn
      ? "Your first " + (a.limit || 3) + " streams or clips are free. Sign in with Twitch, or carry on without it (the trial is then kept for this PC)."
      : tr && a.left <= 0
        ? "You've used your " + a.limit + " free streams. Upgrade to keep making TikToks. Everything you already made stays here."
        : tr ? "Each new stream or clip you add uses one. Making and re-making TikToks from it is free."
        : "Unlimited streams. Thanks for supporting Klipon!";
    plan.classList.toggle("hidden", !inn);
    plan.textContent = inn ? (a.user && a.user.guest ? "No Twitch login" + (a.user.name && a.user.name !== "Guest" ? " · " + a.user.name : "") : "Signed in as " + ((a.user && a.user.name) || "")) + (tr ? " · " + a.left + " of " + a.limit + " free left" : "") : "";
    $("acctSignIn").classList.toggle("hidden", inn);
    $("acctGuest").classList.toggle("hidden", inn || IS_PHONE || acctPolling);
    $("acctGuestBox").classList.add("hidden");
    $("acctGuestGo").textContent = a.free ? "Continue" : "Start free trial";
    $("acctWait").classList.toggle("hidden", !acctPolling || inn);
    $("acctSignOut").classList.toggle("hidden", !inn || IS_PHONE);
    var buy = $("acctBuy"), canBuy = inn && tr && a.buy_url;
    buy.classList.toggle("hidden", !canBuy); if (canBuy) buy.href = a.buy_url;
    if (inn && tr && a.left <= 0 && !a.buy_url) $("acctText").textContent += " Upgrades are coming soon.";
    if (IS_PHONE) $("acctSignIn").classList.add("hidden");
    $("acctUpdate").classList.add("hidden");
    $("acctModal").classList.remove("hidden");
  }
  function closeAcct() {
    $("acctModal").classList.add("hidden");
    var f = acctAfter; acctAfter = null;
    if (f && acct && acct.signed_in) f();
  }
  async function signIn() {
    try {
      var r = await api("POST", "/account/signin", {});
      acctPolling = true; openAcct();
      for (var i = 0; i < 200 && acctPolling; i++) {   // ~10 minutes
        await sleep(3000);
        var p = await api("POST", "/account/poll", { state: r.state }).catch(function () { return { waiting: true }; });
        if (p.expired) { toast("Sign-in timed out. Press Sign in with Twitch again.", true); break; }
        if (p.signed_in) { showAccount(p); toast("Signed in as " + ((p.user && p.user.name) || "you") + "."); acctPolling = false; openAcct(); return; }
      }
    } catch (e) { toast(e.message, true); }
    acctPolling = false; if (!$("acctModal").classList.contains("hidden")) openAcct();
  }
  async function guestGo() {
    var b = $("acctGuestGo"); b.disabled = true;
    try {
      var a = await api("POST", "/account/guest", { name: $("acctGuestName").value.trim() });
      showAccount(a); toast(a.free ? "You're in. Enjoy Klipon!" : "Free trial started."); openAcct();
      if (a.signed_in) closeAcct();
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  }
  function wireAccount() {
    $("btnAccount").addEventListener("click", function () { openAcct(); });
    $("acctGuest").addEventListener("click", function () {
      $("acctGuest").classList.add("hidden"); $("acctGuestBox").classList.remove("hidden"); $("acctGuestName").focus();
    });
    $("acctGuestGo").addEventListener("click", guestGo);
    $("acctGuestName").addEventListener("keydown", function (e) { if (e.key === "Enter") guestGo(); });
    $("acctSignIn").addEventListener("click", signIn);
    $("acctUpdate").addEventListener("click", openUpdate);
    $("newsGet").addEventListener("click", openUpdate);
    $("newsClose").addEventListener("click", function () {
      try { localStorage.setItem("cg_news_hidden", newsKey((state.lib && state.lib.news) || {})); } catch (e) {}
      $("newsBar").classList.add("hidden");
    });
    $("acctClose").addEventListener("click", closeAcct);
    $("acctSignOut").addEventListener("click", async function () {
      if (!confirm("Sign out of Klipon?")) return;
      showAccount(await api("POST", "/account/signout", {}).catch(function () { return acct; })); openAcct();
    });
  }
  // errors from the engine that start with SIGNIN: / TRIAL_USED: open the account box
  function acctError(msg) {
    var m = /^(SIGNIN|TRIAL_USED|UPDATE|PRO):\s*(.*)$/.exec(msg || "");
    if (!m) return false;
    if (m[1] === "PRO") { proBox(m[2]); return true; }
    if (m[1] === "SIGNIN") { toast("Couldn't reach the Klipon server. Check your internet and try again."); return true; }
    loadAccount(true).then(function () {
      if (m[1] === "SIGNIN") openAcct(); else openAcct(true);
      if (m[1] !== "SIGNIN") { $("acctTitle").textContent = m[1] === "UPDATE" ? "Update Klipon" : $("acctTitle").textContent; $("acctText").textContent = m[2]; }
      var n = (state.lib && state.lib.news) || {};
      $("acctUpdate").classList.toggle("hidden", !(m[1] === "UPDATE" && n.url) || IS_PHONE);
    });
    return true;
  }
  window.CG_acctError = acctError;
  /* Free trial: Pro features show as previews with a PRO tag; using one explains it instead. */
  var PRO_NAMES = { killfeed: "The CS2 kill feed", styles: "Saving your own styles", music: "Background music",
    sfx: "Your own sound effects", range: "Picking part of a VOD", phone: "Export to phone" };
  function proBox(text) {
    $("proText").textContent = text;
    $("proModal").classList.remove("hidden");
  }
  document.addEventListener("click", function (e) {
    if (!document.body.classList.contains("pro-locked")) return;
    var el = e.target.closest && e.target.closest(".pro-only"); if (!el) return;
    e.preventDefault(); e.stopImmediatePropagation();
    proBox((PRO_NAMES[el.dataset.pro] || "This") + " is part of Klipon Pro, coming soon. Your free trial had it unlocked.");
  }, true);
  $("proClose").addEventListener("click", function () { $("proModal").classList.add("hidden"); });
  $("proModal").addEventListener("click", function (e) { if (e.target === $("proModal")) $("proModal").classList.add("hidden"); });
  wireAccount();
})();
