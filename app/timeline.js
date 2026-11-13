/* Klipon timeline editor: cut the video, edit/move/retime subtitles, resize the webcam.
   Times are in "clip time": 0 = the TikTok's current start in the stream. */
window.CGTimeline = (function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  var T = null; // state
  var MIN = 0.25;

  function fmt(t) {
    t = Math.max(0, t); var m = Math.floor(t / 60), s = t - m * 60;
    return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function open(data, mediaUrl, onChange) {
    T = {
      d: data, onChange: onChange,
      segs: data.segs.map(function (s) { return [s[0], s[1], s[2] == null ? 0 : s[2]]; }),
      music: data.music_url || "", mvol: data.music_volume == null ? 0.16 : data.music_volume,
      vol: data.volume_db || 0,
      caps: data.captions.map(function (c) { return { s: c.s, e: c.e, text: c.text, other: !!c.other }; }),
      cam: data.cam_box ? data.cam_box.slice() : null,
      game: data.game_box ? data.game_box.slice() : null, gshape: data.game_shape || null,   // 2.28 gameplay box
      gameMoved: false, camMoved: false,   // 2.31: boxes are only saved when YOU moved them
      sfx: (data.sfx || []).map(function (m) { return { t: m.t, name: m.name, vol: m.vol == null ? 0.5 : m.vol }; }),
      orig: JSON.stringify([data.segs, data.captions, data.cam_box, data.music_volume, data.volume_db || 0, data.sfx || [], data.game_box]),
      w0: data.window[0], w1: data.window[1], pps: Number($("tlZoom").value), t: 0, sel: null, pick: [], raf: 0, changed: false
    };
    var v = $("tlVideo");
    $("tlLoading").classList.remove("hidden");
    v.src = mediaUrl;
    v.onloadeddata = function () { $("tlLoading").classList.add("hidden"); seek(T.segs.length ? T.segs[0][0] : 0); };
    v.onseeked = function () { if (T) T.phoneDirty = true; };
    v.onerror = function () { var sp = $("tlLoading").querySelector(".spinner"); if (sp) sp.remove(); $("tlLoading").querySelector("span").textContent = "לא הצלחתי לנגן את התצוגה המקדימה, אבל אפשר עדיין לערוך."; };
    T.aspect = (data.width || 16) / (data.height || 9);
    fitBox();
    var ma = $("tlMusicAudio");
    if (T.music) { ma.src = T.music; } else { ma.removeAttribute("src"); }
    $("tlMusicBox").classList.toggle("hidden", !T.music);
    var snd = window.CGSounds ? window.CGSounds() : [];
    $("tlSfxPick").innerHTML = snd.length ? snd.map(function (s) { return '<option value="' + esc(s.name) + '">♪ ' + esc(s.name) + "</option>"; }).join("")
      : '<option value="">עדיין אין סאונדים (בהגדרות)</option>';
    $("tlAddSfx").disabled = !snd.length;
    $("tlVol").value = T.vol; $("tlVolVal").textContent = (T.vol > 0 ? "+" : "") + T.vol + " dB";
    $("tlMusic").value = Math.round(T.mvol * 100); $("tlMusicVal").textContent = Math.round(T.mvol * 100) + "%";
    layoutCam(); layoutGame(); render(); loop(); setTimeout(fitBox, 60);
    // open with the TikTok itself in view (the 30 s of stream before it is off to the left)
    var first = T.segs.length ? T.segs[0][0] : 0;
    T.t = first; $("tlScroll").scrollLeft = Math.max(0, x(first) - 60); drawHead();
  }
  // size the video preview to the space left (keeps the tracks on screen in any window size)
  function fitBox() {
    if (!T) return;
    var st = document.querySelector(".tl-stage"), box = $("tlBox");
    var w = st.clientWidth - 24, h = st.clientHeight - 24;
    if (w <= 0 || h <= 0) return;
    // 2.29: the 16:9 stream on the left, the finished 9:16 TikTok (live preview) on the right
    var ph = Math.max(120, h - 22), pw = Math.floor(ph * 9 / 16);
    $("tlPhone").style.height = ph + "px"; $("tlPhone").style.width = pw + "px";
    var bw = Math.min(w - pw - 20, h * T.aspect), bh = bw / T.aspect;
    box.style.width = Math.floor(bw) + "px"; box.style.height = Math.floor(bh) + "px";
    T.phoneDirty = true;
  }
  window.addEventListener("resize", function () { fitBox(); });
  function close() {
    if (!T) return;
    cancelAnimationFrame(T.raf); var v = $("tlVideo"); v.pause(); v.removeAttribute("src"); v.load();
    var ma = $("tlMusicAudio"); ma.pause(); ma.removeAttribute("src"); T = null;
  }
  function changed() { T.changed = true; if (T.onChange) T.onChange(); }

  /* ---------- time / playback ---------- */
  function vt(t) { return t + T.d.clip_start - T.d.video_offset; }
  function seek(t) { T.t = clamp(t, T.w0, T.w1); try { $("tlVideo").currentTime = Math.max(0, vt(T.t)); } catch (e) {} drawHead(); }
  function inSeg(t) { for (var i = 0; i < T.segs.length; i++) if (t >= T.segs[i][0] && t < T.segs[i][1]) return i; return -1; }
  function loop() {
    if (!T) return;
    var v = $("tlVideo");
    if (!v.paused) {
      var t = v.currentTime - T.d.clip_start + T.d.video_offset;
      if (inSeg(t) < 0) { // skip the parts that are cut out
        var nxt = T.segs.filter(function (s) { return s[0] > t - 0.01; }).sort(function (a, b) { return a[0] - b[0]; })[0];
        if (nxt) { v.currentTime = vt(nxt[0]); t = nxt[0]; } else { v.pause(); }
      }
      if (T.lastT != null && t > T.lastT && t - T.lastT < 0.5 && window.CGPlaySound)   // play placed sounds as the playhead passes
        T.sfx.forEach(function (m) { if (m.t > T.lastT && m.t <= t) window.CGPlaySound(m.name); });
      T.lastT = t;
      T.t = t; drawHead();
      applyVol();
      T.phoneDirty = true;
    }
    if (T.phoneDirty) { T.phoneDirty = false; drawPhone(); }
    T.raf = requestAnimationFrame(loop);
  }
  function gainOf(g) { return g === "mute" ? 0 : Math.pow(10, (Number(g) || 0) / 20); }
  function applyVol() {
    var i = inSeg(T.t), v = $("tlVideo");
    var g = (i >= 0 ? gainOf(T.segs[i][2]) : 1) * Math.pow(10, T.vol / 20);
    v.volume = clamp(g, 0, 1);
    $("tlMusicAudio").volume = clamp(T.mvol * 2.2, 0, 1);  // the preview player is quieter than the final mix
  }
  function togglePlay() {
    var v = $("tlVideo");
    if (v.paused) { if (inSeg(T.t) < 0) { var n = T.segs.filter(function (s) { return s[0] >= T.t; })[0] || T.segs[0]; if (n) seek(n[0]); } v.muted = false; applyVol(); v.play(); $("tlPlay").textContent = "❚❚";
      var ma = $("tlMusicAudio"); if (T.music && T.mvol > 0) { try { ma.play(); } catch (e) {} } }
    else { v.pause(); $("tlMusicAudio").pause(); $("tlPlay").textContent = "▶"; }
  }

  /* ---------- selection ----------
     T.sel  = the one selected block ({type, i}) shown in the inspector
     T.pick = several blocks selected together (drag a box, or Ctrl/Shift+click); holds the blocks themselves */
  function picked(item) { return T.pick.indexOf(item) >= 0; }
  function itemOf(type, i) { return type === "seg" ? T.segs[i] : T.caps[i]; }
  function typeOf(item) { return Array.isArray(item) ? "seg" : "cap"; }
  function setPick(items) {
    items = items.filter(function (it, k) { return it && items.indexOf(it) === k; });
    if (items.length === 1) { var it = items[0], ty = typeOf(it); T.pick = []; T.sel = { type: ty, i: (ty === "seg" ? T.segs : T.caps).indexOf(it) }; }
    else { T.pick = items; T.sel = null; }
  }
  function prunePick() { T.pick = T.pick.filter(function (it) { return T.segs.indexOf(it) >= 0 || T.caps.indexOf(it) >= 0; }); if (T.pick.length === 1) setPick(T.pick); }

  /* ---------- drawing ---------- */
  function x(t) { return (t - T.w0) * T.pps; }
  function render() {
    fitBox();
    var width = Math.max(200, (T.w1 - T.w0) * T.pps);
    ["trkVideo", "trkSubs", "trkAudio"].forEach(function (id) { $(id).style.width = width + "px"; });
    $("tlRuler").style.width = width + "px";
    // ruler
    var step = T.pps > 70 ? 1 : T.pps > 30 ? 2 : 5, html = "";
    for (var t = Math.ceil(T.w0 / step) * step; t <= T.w1; t += step) {
      var lab = (t < 0 ? "-" : "") + fmt(Math.abs(t)).replace(/\.0$/, "");
      html += '<i style="left:' + x(t) + 'px"></i><span style="left:' + x(t) + 'px">' + lab + "</span>";
    }
    if (T.d.peak != null) html += '<b class="tl-peak" style="left:' + x(T.d.peak) + 'px" title="הרגע הכי טוב"></b>';
    $("tlRuler").innerHTML = html;
    // segments
    var selItem = T.sel && T.sel.type !== "sfx" ? itemOf(T.sel.type, T.sel.i) : null;   // sorting moves blocks: keep the same one selected
    T.segs.sort(function (a, b) { return a[0] - b[0]; });
    T.caps.sort(function (a, b) { return a.s - b.s; });
    $("trkSfx").style.width = width + "px";
    $("trkSfx").innerHTML = T.sfx.map(function (m, i) {
      var sel = T.sel && T.sel.type === "sfx" && T.sel.i === i;
      return '<div class="tl-sfxb' + (sel ? " sel" : "") + '" data-sfx="' + i + '" title="' + esc(m.name) + ': drag to move, Delete removes" style="left:' + x(m.t) + 'px">♪ ' + esc(m.name) + "</div>";
    }).join("");
    if (selItem) { var si = (T.sel.type === "seg" ? T.segs : T.caps).indexOf(selItem); T.sel = si >= 0 ? { type: T.sel.type, i: si } : null; }
    prunePick();
    $("trkVideo").innerHTML = T.segs.map(function (s, i) {
      var sel = (T.sel && T.sel.type === "seg" && T.sel.i === i) || picked(s);
      return '<div class="tl-seg' + (sel ? " sel" : "") + '" data-type="seg" data-i="' + i + '" style="left:' + x(s[0]) + "px;width:" + Math.max(4, (s[1] - s[0]) * T.pps) + 'px">' +
        '<span class="tl-edge l" data-edge="l"></span><span class="tl-edge r" data-edge="r"></span></div>';
    }).join("");
    drawWave(width);
    T.caps.sort(function (a, b) { return a.s - b.s; });
    $("trkSubs").innerHTML = T.caps.map(function (c, i) {
      var sel = (T.sel && T.sel.type === "cap" && T.sel.i === i) || picked(c);
      return '<div class="tl-capb' + (sel ? " sel" : "") + (c.other ? " other" : "") + '" data-type="cap" data-i="' + i + '" title="' + esc(c.text) + '" style="left:' + x(c.s) + "px;width:" + Math.max(6, (c.e - c.s) * T.pps) + 'px">' +
        esc(c.text) + '<span class="tl-edge l" data-edge="l"></span><span class="tl-edge r" data-edge="r"></span></div>';
    }).join("");
    $("tlInner").style.width = (width + 84) + "px";
    drawHead(); drawInspector();
  }
  function drawWave(width) {
    var cv = $("tlWave"), h = 48, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(width * dpr); cv.height = h * dpr; cv.style.width = width + "px";
    var g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, width, h);
    var wv = T.d.wave || {}, pk = wv.peaks || [], ps = wv.per_sec || 40;
    for (var px = 0; px < width; px++) {
      var t = T.w0 + px / T.pps, k = Math.floor((t - T.w0) * ps), k2 = Math.max(k + 1, Math.floor((t + 1 / T.pps - T.w0) * ps));
      var m = 0; for (var j = k; j < k2 && j < pk.length; j++) m = Math.max(m, pk[j] || 0);
      var i = inSeg(t), gain = i >= 0 ? gainOf(T.segs[i][2]) : 1;
      var amp = Math.min(1, m * Math.sqrt(gain)) * (h / 2 - 3);
      g.fillStyle = i < 0 ? "#3a3a44" : (T.segs[i][2] === "mute" ? "#5a2a35" : "#3ddc84");
      g.fillRect(px, h / 2 - Math.max(0.5, amp), 1, Math.max(1, amp * 2));
    }
    $("tlGains").innerHTML = T.segs.map(function (s) {
      if (!s[2]) return "";
      var lab = s[2] === "mute" ? "muted" : (s[2] > 0 ? "+" : "") + s[2] + " dB";
      return '<span class="tl-gain' + (s[2] === "mute" ? " mute" : "") + '" style="left:' + (x(s[0]) + 4) + 'px">' + lab + "</span>";
    }).join("");
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function drawHead() {
    if (!T) return;
    $("tlHead").style.left = (84 + x(T.t)) + "px";
    $("tlTime").textContent = fmt(T.t - (T.segs[0] ? T.segs[0][0] : 0));
    var cur = T.caps.filter(function (c) { return T.t >= c.s && T.t < c.e; })[0];
    $("tlCap").textContent = cur && inSeg(T.t) >= 0 ? cur.text : "";
    $("tlCap").classList.toggle("other", !!(cur && cur.other));
    // keep the playhead in view while playing
    var sc = $("tlScroll"), hx = 84 + x(T.t);
    if (!$("tlVideo").paused && (hx < sc.scrollLeft + 90 || hx > sc.scrollLeft + sc.clientWidth - 40)) sc.scrollLeft = hx - 120;
  }
  function drawInspector() {
    var c = T.sel && T.sel.type === "cap" ? T.caps[T.sel.i] : null;
    var sg = T.sel && T.sel.type === "seg" ? T.segs[T.sel.i] : null;
    var many = T.pick.length > 1, mSegs = T.pick.filter(function (it) { return typeOf(it) === "seg"; });
    if (many && mSegs.length) sg = mSegs[0];
    $("tlCapEdit").classList.toggle("hidden", !c);
    $("tlSegEdit").classList.toggle("hidden", !sg);
    $("tlHint").classList.toggle("hidden", !many && (!!c || !!sg));
    var sx = T.sel && T.sel.type === "sfx" ? T.sfx[T.sel.i] : null;
    $("tlHint").textContent = sx ? "סאונד \"" + sx.name + "\" ב-" + fmt(sx.t) + ". גררו כדי להזיז, Delete מוחק."
      : many
      ? T.pick.length + " נבחרו (" + mSegs.length + " וידאו, " + (T.pick.length - mSegs.length) + " כתוביות). גררו אחד כדי להזיז את כולם, Delete מוחק, Esc מנקה בחירה."
      : "לחצו על קטע כדי לבחור. גררו על שטח ריק לבחירת כמה (או Ctrl+קליק). גררו קצוות לקיצור. חלקים אפורים נחתכים.";
    if (sg) {
      var muted = sg[2] === "mute", db = muted ? 0 : Number(sg[2]) || 0;
      $("tlSegTimes").textContent = many ? mSegs.length + " קטעי וידאו" : fmt(sg[0]) + " → " + fmt(sg[1]);
      if (document.activeElement !== $("tlSegVol")) $("tlSegVol").value = db;
      $("tlSegVolVal").textContent = muted ? "muted" : (db > 0 ? "+" : "") + db + " dB";
      $("tlSegVol").disabled = muted;
      $("tlSegMute").setAttribute("aria-pressed", muted ? "true" : "false");
      $("tlSegMute").innerHTML = muted ? '<i class="gly i-volume"></i> Unmute' : '<i class="gly i-mute"></i> Mute';
    }
    if (c) {
      if (document.activeElement !== $("tlCapText")) $("tlCapText").value = c.text;
      $("tlCapTimes").textContent = fmt(c.s) + " → " + fmt(c.e);
      $("tlCapOther").setAttribute("aria-pressed", c.other ? "true" : "false");
    }
  }

  /* ---------- webcam box ---------- */
  function layoutCam() {
    var el = $("tlCam");
    if (!T.cam) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    el.style.left = (T.cam[0] * 100) + "%"; el.style.top = (T.cam[1] * 100) + "%";
    el.style.width = (T.cam[2] * 100) + "%"; el.style.height = (T.cam[3] * 100) + "%";
  }
  function camDrag(ev) {
    if (!T || !T.cam) return;
    ev.preventDefault();
    var box = $("tlBox").getBoundingClientRect(), h = ev.target.dataset.h, start = T.cam.slice();
    var sx = ev.clientX, sy = ev.clientY;
    function mv(e) {
      var dx = (e.clientX - sx) / box.width, dy = (e.clientY - sy) / box.height, c = start.slice();
      if (!h) { c[0] = clamp(start[0] + dx, 0, 1 - c[2]); c[1] = clamp(start[1] + dy, 0, 1 - c[3]); }
      else {
        if (h.indexOf("w") >= 0) { c[0] = clamp(start[0] + dx, 0, start[0] + start[2] - 0.05); c[2] = start[2] + (start[0] - c[0]); }
        if (h.indexOf("e") >= 0) { c[2] = clamp(start[2] + dx, 0.05, 1 - c[0]); }
        if (h.indexOf("n") >= 0) { c[1] = clamp(start[1] + dy, 0, start[1] + start[3] - 0.05); c[3] = start[3] + (start[1] - c[1]); }
        if (h.indexOf("s") >= 0) { c[3] = clamp(start[3] + dy, 0.05, 1 - c[1]); }
      }
      T.cam = c.map(function (v) { return Math.round(v * 10000) / 10000; }); T.camMoved = true; layoutCam(); boxesMoved();
    }
    function up() { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); changed(); }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }

  /* ---------- 2.29: live 9:16 preview of the finished TikTok ---------- */
  function boxesMoved() { if (window.CGLivePreview) window.CGLivePreview(); }
  function wrapLines(ctx, text, maxW) {
    var words = String(text).split(/\s+/), lines = [], cur = "";
    words.forEach(function (w) {
      var t = cur ? cur + " " + w : w;
      if (cur && ctx.measureText(t.replace(/\*/g, "")).width > maxW) { lines.push(cur); cur = w; } else cur = t;
    });
    if (cur) lines.push(cur);
    return lines;
  }
  function strokeFill(ctx, text, x, y, outline, fill) {
    if (outline > 0) { ctx.lineJoin = "round"; ctx.lineWidth = outline; ctx.strokeStyle = "#000"; ctx.strokeText(text, x, y); }
    ctx.fillStyle = fill; ctx.fillText(text, x, y);
  }
  // a line with *starred* words in the highlight colour, centred on cx
  function starLine(ctx, line, cx, y, outline, hl, base) {
    var parts = line.split(/(\*[^*]+\*)/).filter(Boolean), total = 0;
    parts.forEach(function (p) { total += ctx.measureText(p.replace(/\*/g, "")).width; });
    var x = cx - total / 2;
    ctx.textAlign = "left";
    parts.forEach(function (p) {
      var star = /^\*.+\*$/.test(p), t = p.replace(/\*/g, "");
      strokeFill(ctx, t, x, y, outline, star ? hl : base); x += ctx.measureText(t).width;
    });
    ctx.textAlign = "center";
  }
  var LOGO = { outer: [[14, 0], [90, 0], [90, 56], [66, 80], [48, 80], [32, 96], [32, 80], [0, 80], [0, 14]],
    inner: [[10, 8], [82, 8], [82, 52], [62, 72], [44, 72], [32, 84], [32, 72], [10, 72]],
    e1: [[36, 22], [44, 22], [44, 46], [36, 46]], e2: [[58, 22], [66, 22], [66, 46], [58, 46]] };
  function poly(ctx, pts, x, y, k, col) {
    ctx.beginPath(); pts.forEach(function (p, i) { (i ? ctx.lineTo : ctx.moveTo).call(ctx, x + p[0] * k, y + p[1] * k); });
    ctx.closePath(); ctx.fillStyle = col; ctx.fill();
  }
  function drawPhone() {
    var cv = $("tlPhone"), ctx = cv.getContext("2d"), v = $("tlVideo"), g = T && T.geo;
    var K = cv.width / 1080;
    ctx.save(); ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cv.width, cv.height);
    if (!g) { ctx.fillStyle = "#888"; ctx.font = "13px sans-serif"; ctx.textAlign = "center"; ctx.fillText("טוען תצוגה מקדימה…", cv.width / 2, cv.height / 2); ctx.restore(); return; }
    ctx.scale(K, K);
    var vw = v.videoWidth, vh = v.videoHeight;
    if (vw && vh) g.layers.forEach(function (l) {
      var s = l.src, d = l.dst;
      ctx.save();
      if (l.border) { ctx.fillStyle = "#fff"; ctx.fillRect(d[0] - l.border, d[1] - l.border, d[2] + 2 * l.border, d[3] + 2 * l.border); }
      if (l.blur) ctx.filter = "blur(" + Math.round(l.blur * 0.9) + "px) brightness(" + (1 - (l.dark || 0)) + ")";
      try { ctx.drawImage(v, s[0] * vw, s[1] * vh, s[2] * vw, s[3] * vh, d[0], d[1], d[2], d[3]); } catch (e) {}
      ctx.restore();
    });
    ctx.textAlign = "center";
    var t = g.title;
    if (t) {
      var txt = t.upper ? t.text.toUpperCase() : t.text;
      if (t.quote && !/^[“"]/.test(txt)) txt = "“" + txt.replace(/"/g, "") + "”";
      ctx.font = "800 " + t.size + "px Montserrat, 'Segoe UI', sans-serif"; ctx.textBaseline = "top";
      var lines = wrapLines(ctx, txt, 1080 - 140), lh = t.size * 1.12;
      lines.forEach(function (ln, i) {
        var y = t.y + i * lh;
        if (t.boxed) {
          var w = ctx.measureText(ln.replace(/\*/g, "")).width + 28;
          ctx.fillStyle = "#fff"; ctx.fillRect(540 - w / 2, y - 14, w, lh + 4);
          starLine(ctx, ln, 540, y, 0, t.hl, "#111");
        } else starLine(ctx, ln, 540, y, 12, t.hl, "#fff");
      });
    }
    var n = g.name;
    if (n) {
      ctx.save(); ctx.globalAlpha = n.alpha == null ? 1 : n.alpha;   // 0.1: logo + name opacity
      if (n.logo) {
        var k = n.logo[2] / 104, lx = n.logo[0], ly = n.logo[1];
        poly(ctx, LOGO.outer, lx, ly, k, "#9141ff"); poly(ctx, LOGO.inner, lx, ly, k, "#fff");
        poly(ctx, LOGO.e1, lx, ly, k, "#9141ff"); poly(ctx, LOGO.e2, lx, ly, k, "#9141ff");
      }
      ctx.font = "800 " + n.fs + "px Montserrat, 'Segoe UI', sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      strokeFill(ctx, n.text, n.x, n.y, 6, "#fff"); ctx.textAlign = "center";
      ctx.restore();
    }
    var sb = g.subs;
    if (sb) {
      var cap = T.caps.filter(function (c) { return T.t >= c.s && T.t < c.e; })[0];
      if (cap && cap.text.trim()) {
        var st = sb.upper ? cap.text.toUpperCase() : cap.text.toLowerCase();
        var fam = sb.font === "Montserrat ExtraBold" ? "800 " + sb.size + "px Montserrat" : sb.size + "px '" + sb.font + "', Impact, sans-serif";
        ctx.font = fam + ", sans-serif"; ctx.textBaseline = "middle";
        var sl = wrapLines(ctx, st, 1080 - 160), slh = sb.size * 1.05;
        sl.forEach(function (ln, i) { strokeFill(ctx, ln, 540, sb.y + (i - (sl.length - 1) / 2) * slh, 12, cap.other ? sb.other : "#fff"); });
      }
    }
    ctx.restore();
  }
  function setGeo(g) { if (!T) return; T.geo = g; T.phoneDirty = true; }
  function boxes() { return T ? { cam_box: T.camMoved ? T.cam : null, game_box: T.gameMoved ? T.game : null } : {}; }

  /* ---------- gameplay box (2.28): which part of the stream the gameplay is cut from ---------- */
  function layoutGame() {
    var el = $("tlGame");
    if (!T.game || !T.gshape) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    el.style.left = (T.game[0] * 100) + "%"; el.style.top = (T.game[1] * 100) + "%";
    el.style.width = (T.game[2] * 100) + "%"; el.style.height = (T.game[3] * 100) + "%";
  }
  function gameDrag(ev) {
    if (!T || !T.game) return;
    ev.preventDefault(); ev.stopPropagation();
    var box = $("tlBox").getBoundingClientRect(), h = ev.target.dataset.h, start = T.game.slice(), sh = T.gshape;
    var sx = ev.clientX, sy = ev.clientY;
    var maxW = Math.min(1, sh);                          // the box keeps the TikTok's gameplay shape
    function mv(e) {
      var dx = (e.clientX - sx) / box.width, dy = (e.clientY - sy) / box.height, g = start.slice();
      if (!h) { g[0] = clamp(start[0] + dx, 0, 1 - g[2]); g[1] = clamp(start[1] + dy, 0, 1 - g[3]); }
      else {
        var grow = (h.indexOf("e") >= 0 ? dx : -dx);       // drag a corner out = see more, in = zoom in
        var w = clamp(start[2] + grow, maxW * 0.3, maxW), hh = w / sh;
        g[2] = w; g[3] = hh;
        g[0] = h.indexOf("w") >= 0 ? start[0] + start[2] - w : start[0];
        g[1] = h.indexOf("n") >= 0 ? start[1] + start[3] - hh : start[1];
        g[0] = clamp(g[0], 0, 1 - w); g[1] = clamp(g[1], 0, 1 - hh);
      }
      T.game = g.map(function (v) { return Math.round(v * 10000) / 10000; }); T.gameMoved = true; layoutGame(); boxesMoved();
    }
    function up() { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); changed(); }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }

  /* ---------- track interaction ---------- */
  function trackDown(ev) {
    var blk = ev.target.closest("[data-type]");
    var track = ev.currentTarget, rect = track.getBoundingClientRect();
    var tAt = function (cx) { return T.w0 + (cx - rect.left) / T.pps; };
    if (!blk) { marquee(ev, tAt); return; }
    ev.preventDefault();
    var type = blk.dataset.type, i = Number(blk.dataset.i), edge = ev.target.dataset.edge;
    var item = itemOf(type, i);
    if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {       // add to / remove from the selection
      var cur = T.pick.length ? T.pick.slice() : (T.sel ? [itemOf(T.sel.type, T.sel.i)] : []);
      var k = cur.indexOf(item); if (k >= 0) cur.splice(k, 1); else cur.push(item);
      setPick(cur); render(); return;
    }
    if (blk.offsetWidth < 26) edge = null;               // tiny block: the whole thing drags, no trim handles
    if (picked(item) && T.pick.length > 1) { groupDrag(ev, item, tAt); return; }
    T.pick = []; T.sel = { type: type, i: i };
    var a0 = type === "seg" ? item[0] : item.s, b0 = type === "seg" ? item[1] : item.e, sx = ev.clientX, moved = false;
    function set(a, b) { if (type === "seg") { item[0] = a; item[1] = b; } else { item.s = a; item.e = b; } }
    function mv(e) {
      var dt = (e.clientX - sx) / T.pps; if (Math.abs(e.clientX - sx) > 2) moved = true;
      if (!moved) return;
      if (edge === "l") set(clamp(a0 + dt, T.w0, b0 - MIN), b0);
      else if (edge === "r") set(a0, clamp(b0 + dt, a0 + MIN, T.w1));
      else { var len = b0 - a0, a = clamp(a0 + dt, T.w0, T.w1 - len); set(a, a + len); }
      render();
    }
    function up(e) {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      if (!moved) seek(type === "cap" ? item.s + 0.02 : tAt(e.clientX)); else { if (type === "seg") { mergeSegs(); fillCaps(); } changed(); }
      render();
      if (type === "cap" && !moved) setTimeout(function () { $("tlCapText").focus(); $("tlCapText").select(); }, 0);
    }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
    render();
  }
  /* Drag any selected block: everything selected moves together. */
  function groupDrag(ev, item, tAt) {
    var items = T.pick.slice(), sx = ev.clientX, moved = false;
    var o = items.map(function (it) { return typeOf(it) === "seg" ? [it[0], it[1]] : [it.s, it.e]; });
    var lo = Math.max.apply(null, o.map(function (r) { return T.w0 - r[0]; })), hi = Math.min.apply(null, o.map(function (r) { return T.w1 - r[1]; }));
    function mv(e) {
      if (Math.abs(e.clientX - sx) > 2) moved = true;
      if (!moved) return;
      var dt = clamp((e.clientX - sx) / T.pps, lo, hi);
      items.forEach(function (it, k) { if (typeOf(it) === "seg") { it[0] = o[k][0] + dt; it[1] = o[k][1] + dt; } else { it.s = o[k][0] + dt; it.e = o[k][1] + dt; } });
      render();
    }
    function up(e) {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      if (!moved) { setPick([item]); seek(tAt(e.clientX)); }      // a plain click on one of them selects just that one
      else {
        if (items.some(function (it) { return typeOf(it) === "seg"; })) { mergeSegs(); T.pick = items; fillCaps(); }
        changed();
      }
      render();
    }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  /* Drag across empty space: a box that selects every block it touches (like Premiere). */
  function marquee(ev, tAt) {
    ev.preventDefault();
    var inner = $("tlInner"), box = $("tlMarquee"), sc = $("tlScroll");
    var sx = ev.clientX, sy = ev.clientY, moved = false, add = ev.ctrlKey || ev.metaKey || ev.shiftKey;
    var base = add ? (T.pick.length ? T.pick.slice() : (T.sel ? [itemOf(T.sel.type, T.sel.i)] : [])) : [];
    var sLeft0 = sc.scrollLeft;
    function mv(e) {
      if (Math.abs(e.clientX - sx) > 4 || Math.abs(e.clientY - sy) > 4) moved = true;
      if (!moved) return;
      var ir = inner.getBoundingClientRect();
      // near the edge of the timeline: scroll along
      var sr = sc.getBoundingClientRect();
      if (e.clientX > sr.right - 30) sc.scrollLeft += 18; else if (e.clientX < sr.left + 100) sc.scrollLeft -= 18;
      var startX = sx - (sc.scrollLeft - sLeft0);            // where the drag began, in today's screen position
      var x0 = Math.min(startX, e.clientX), x1 = Math.max(startX, e.clientX), y0 = Math.min(sy, e.clientY), y1 = Math.max(sy, e.clientY);
      box.style.display = "block";
      box.style.left = (x0 - ir.left) + "px"; box.style.top = (y0 - ir.top) + "px";
      box.style.width = (x1 - x0) + "px"; box.style.height = (y1 - y0) + "px";
      var hit = base.slice();
      [["trkVideo", T.segs], ["trkAudio", T.segs], ["trkSubs", T.caps]].forEach(function (row) {
        var r = $(row[0]).getBoundingClientRect();
        if (y1 < r.top || y0 > r.bottom) return;
        var t0 = T.w0 + (x0 - r.left) / T.pps, t1 = T.w0 + (x1 - r.left) / T.pps;
        row[1].forEach(function (it) {
          var a = typeOf(it) === "seg" ? it[0] : it.s, b = typeOf(it) === "seg" ? it[1] : it.e;
          if (b > t0 && a < t1 && hit.indexOf(it) < 0) hit.push(it);
        });
      });
      setPick(hit); render();
    }
    function up(e) {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      box.style.display = "none";
      if (!moved) { if (!add) { T.sel = null; T.pick = []; } seek(tAt(e.clientX)); render(); }
    }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  /* Drag the playhead: grab the red handle, or press anywhere on the time ruler and drag. */
  function scrub(ev) {
    ev.preventDefault();
    var sc = $("tlScroll"), v = $("tlVideo"), wasPlaying = !v.paused;
    if (wasPlaying) v.pause();
    document.body.classList.add("tl-scrubbing");
    function at(e) { var r = $("tlRuler").getBoundingClientRect(); seek(T.w0 + (e.clientX - r.left) / T.pps); }
    function mv(e) {
      var sr = sc.getBoundingClientRect();
      if (e.clientX > sr.right - 30) sc.scrollLeft += 16; else if (e.clientX < sr.left + 100) sc.scrollLeft -= 16;
      at(e);
    }
    function up() {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      document.body.classList.remove("tl-scrubbing");
      if (wasPlaying) togglePlay();
    }
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
    if (ev.currentTarget.id === "tlRuler") at(ev);
  }
  // subtitles made by Klipon itself (same as the automatic ones) for the kept parts
  function serverCaps() {
    return fetch("/library/timeline_captions", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Klipon-Token": window.CG_TOKEN },
      body: JSON.stringify({ key: T.d.key, clip_id: T.d.clip_id, clip_start: T.d.clip_start,
        segs: T.segs.map(function (s) { return [s[0], s[1]]; }) })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) throw new Error(d.error);
      return d.captions.map(function (c) { return { s: c.s, e: c.e, text: c.text, other: !!c.other }; });
    });
  }
  // after extending: add subtitles only where there are none yet
  function fillCaps() {
    if (!T.d.key) { if (autoCaps()) render(); return; }
    var mine = T;
    serverCaps().then(function (caps) {
      if (T !== mine) return;
      var add = caps.filter(function (c) {
        var m = (c.s + c.e) / 2;
        return !T.caps.some(function (x) { return m >= x.s - 0.05 && m <= x.e + 0.05 || (c.s < x.e - 0.05 && c.e > x.s + 0.05); });
      });
      if (add.length) { T.caps = T.caps.concat(add).sort(function (a, b) { return a.s - b.s; }); changed(); render(); }
    }).catch(function () { if (autoCaps()) render(); });
  }
  // subtitles for speech that isn't covered yet (fallback without the engine)
  function autoCaps() {
    var ws = (T.d.words || []).filter(function (w) {
      var m = (w.s + w.e) / 2;
      return inSeg(m) >= 0 && !T.caps.some(function (c) { return m >= c.s - 0.05 && m <= c.e + 0.05; });
    }).sort(function (a, b) { return a.s - b.s; });
    if (!ws.length) return 0;
    var added = 0, cur = [];
    function flush() {
      if (!cur.length) return;
      T.caps.push({ s: cur[0].s, e: cur[cur.length - 1].e + 0.1, text: cur.map(function (w) { return w.t; }).join(" "), other: !!cur[0].o });
      added++; cur = [];
    }
    ws.forEach(function (w) {
      var last = cur[cur.length - 1];
      if (last && (cur.length >= 4 || w.s - last.e > 0.45 || /[.!?,]$/.test(last.t) || !!w.o !== !!last.o)) flush();
      cur.push(w);
    });
    flush();
    T.caps.sort(function (a, b) { return a.s - b.s; });
    return added;
  }
  function mergeSegs() {
    T.segs.sort(function (a, b) { return a[0] - b[0]; });
    var out = [];
    T.segs.forEach(function (s) { if (out.length && s[0] <= out[out.length - 1][1] && String(s[2] || 0) === String(out[out.length - 1][2] || 0)) out[out.length - 1][1] = Math.max(out[out.length - 1][1], s[1]); else out.push(s); });
    T.segs = out.map(function (s, i, arr) {  // pieces with different volumes may touch but not overlap
      if (i > 0 && s[0] < arr[i - 1][1]) s[0] = arr[i - 1][1];
      return s;
    }).filter(function (s) { return s[1] - s[0] >= MIN / 2; });
    T.sel = null; prunePick();
  }
  function split() {
    var i = inSeg(T.t);
    if (i < 0) return;
    var s = T.segs[i];
    if (T.t - s[0] < MIN || s[1] - T.t < MIN) return;
    T.segs.splice(i, 1, [s[0], T.t, s[2] || 0], [T.t, s[1], s[2] || 0]);
    T.sel = { type: "seg", i: i + 1 }; changed(); render();
  }
  function del() {
    if (T.sel && T.sel.type === "sfx") { T.sfx.splice(T.sel.i, 1); T.sel = null; changed(); render(); return; }
    var items = T.pick.length ? T.pick.slice() : (T.sel ? [itemOf(T.sel.type, T.sel.i)] : []);
    if (!items.length) return;
    var segsGone = items.filter(function (it) { return typeOf(it) === "seg"; });
    if (segsGone.length >= T.segs.length) segsGone = segsGone.slice(0, T.segs.length - 1);   // always keep one piece of video
    T.segs = T.segs.filter(function (sg) { return segsGone.indexOf(sg) < 0; });
    T.caps = T.caps.filter(function (c) { return items.indexOf(c) < 0; });
    T.sel = null; T.pick = []; changed(); render();
  }
  function addCap() {
    var s = T.t, e = Math.min(T.w1, s + 1.2);
    var nxt = T.caps.filter(function (c) { return c.s > s; })[0];
    if (nxt) e = Math.min(e, nxt.s);
    if (e - s < MIN) e = s + MIN;
    T.caps.push({ s: s, e: e, text: "כתובית חדשה", other: false });
    T.caps.sort(function (a, b) { return a.s - b.s; });
    T.sel = { type: "cap", i: T.caps.findIndex(function (c) { return c.s === s; }) };
    changed(); render(); setTimeout(function () { $("tlCapText").focus(); $("tlCapText").select(); }, 0);
  }
  function reset() {
    var o = JSON.parse(T.orig);
    T.segs = o[0].map(function (s) { return [s[0], s[1], s[2] == null ? 0 : s[2]]; });
    T.vol = o[4] || 0; $("tlVol").value = T.vol; $("tlVolVal").textContent = (T.vol > 0 ? "+" : "") + T.vol + " dB";
    T.mvol = o[3] == null ? 0.16 : o[3]; $("tlMusic").value = Math.round(T.mvol * 100); $("tlMusicVal").textContent = Math.round(T.mvol * 100) + "%"; T.caps = o[1].map(function (c) { return { s: c.s, e: c.e, text: c.text, other: !!c.other }; });
    T.sfx = (o[5] || []).map(function (m) { return { t: m.t, name: m.name, vol: m.vol == null ? 0.5 : m.vol }; });
    T.cam = o[2] ? o[2].slice() : null; T.game = o[6] ? o[6].slice() : null; T.gameMoved = false; T.camMoved = false;
    T.sel = null; T.pick = []; layoutCam(); layoutGame(); changed(); render(); boxesMoved();
  }

  /* ---------- result ---------- */
  function result() {
    if (!T) return null;
    var segs = T.segs.slice().sort(function (a, b) { return a[0] - b[0]; });
    var a0 = segs[0][0], b1 = segs[segs.length - 1][1];
    var r = function (v) { return Math.round(v * 1000) / 1000; };
    return {
      start: r(T.d.clip_start + a0), end: r(T.d.clip_start + b1),
      segs: segs.map(function (s) { return [r(s[0] - a0), r(s[1] - a0)]; }),
      seg_gain: segs.map(function (s) { return s[2] === "mute" ? "mute" : Number(s[2]) || 0; }),
      music_volume: T.music ? Math.round(T.mvol * 1000) / 1000 : null,
      volume_db: T.vol,
      captions: T.caps.filter(function (c) { return c.e > a0 && c.s < b1 && c.text.trim(); })
        .map(function (c) { return { s: r(Math.max(0, c.s - a0)), e: r(Math.min(b1, c.e) - a0), text: c.text.trim(), other: !!c.other }; }),
      cam_box: T.camMoved ? T.cam : null,
      game_box: T.gameMoved ? T.game : null,
      // placed sounds in STREAM time, so they stay on their moment whatever the trim
      sfx: T.sfx.filter(function (m) { return m.t >= a0 - 0.01 && m.t <= b1; })
        .map(function (m) { return { at: r(T.d.clip_start + m.t), name: m.name, vol: m.vol }; })
    };
  }

  function wire() {
    $("tlZoom").addEventListener("input", function () { if (!T) return; T.pps = Number(this.value); render(); });
    $("tlPlay").addEventListener("click", function () { if (T) togglePlay(); });
    $("tlSplit").addEventListener("click", function () { if (T) split(); });
    $("tlDelete").addEventListener("click", function () { if (T) del(); });
    $("tlAddCap").addEventListener("click", function () { if (T) addCap(); });
    $("tlReset").addEventListener("click", function () { if (T) reset(); });
    $("tlRegen").addEventListener("click", function () {
      if (!T) return;
      if (T.caps.length && !confirm("ליצור את כל הכתוביות מחדש ממה שנאמר? העריכות שלכם לכתוביות בקליפ הזה יוחלפו.")) return;
      var btn = $("tlRegen"); btn.disabled = true;
      var done = function () { btn.disabled = false; changed(); render(); };
      if (!T.d.key) { T.caps = []; T.sel = null; autoCaps(); done(); return; }
      serverCaps().then(function (caps) { T.caps = caps; T.sel = null; T.pick = []; done(); })
        .catch(function () { T.caps = []; T.sel = null; T.pick = []; autoCaps(); done(); });
    });
    ["trkVideo", "trkSubs"].forEach(function (id) { $(id).addEventListener("pointerdown", function (e) { if (T) trackDown(e); }); });
    $("tlInner").addEventListener("pointerdown", function (e) {   // the gaps between / below the rows start a box too
      if (!T || !(e.target === this || e.target.classList.contains("tl-row"))) return;
      var r = $("trkVideo").getBoundingClientRect();
      marquee(e, function (cx) { return T.w0 + (cx - r.left) / T.pps; });
    });
    $("tlAddSfx").addEventListener("click", function () {
      if (!T) return; var name = $("tlSfxPick").value; if (!name) return;
      T.sfx.push({ t: Math.round(T.t * 100) / 100, name: name, vol: 0.5 });
      T.sel = { type: "sfx", i: T.sfx.length - 1 }; T.pick = [];
      if (window.CGPlaySound) window.CGPlaySound(name);
      changed(); render();
    });
    $("trkSfx").addEventListener("pointerdown", function (e) {
      if (!T) return;
      var b = e.target.closest("[data-sfx]"), r = this.getBoundingClientRect();
      if (!b) { T.sel = null; T.pick = []; seek(T.w0 + (e.clientX - r.left) / T.pps); render(); return; }
      e.preventDefault();
      var i = Number(b.dataset.sfx), m = T.sfx[i], t0 = m.t, sx = e.clientX, moved = false;
      T.sel = { type: "sfx", i: i }; T.pick = []; render();
      function mv(ev) {
        if (Math.abs(ev.clientX - sx) > 2) moved = true;
        if (!moved) return;
        m.t = Math.round(clamp(t0 + (ev.clientX - sx) / T.pps, T.w0, T.w1) * 100) / 100; render();
      }
      function up() {
        window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
        if (moved) changed(); else { seek(m.t); if (window.CGPlaySound) window.CGPlaySound(m.name); }
        T.sel = { type: "sfx", i: T.sfx.indexOf(m) }; render();
      }
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
    });
    $("tlRuler").addEventListener("pointerdown", function (e) { if (T) scrub(e); });
    $("tlHeadGrip").addEventListener("pointerdown", function (e) { if (T) scrub(e); });
    $("tlCam").addEventListener("pointerdown", camDrag);
    $("tlGame").addEventListener("pointerdown", gameDrag);
    $("trkAudio").addEventListener("pointerdown", function (e) {  // click the audio row = select that piece
      if (!T) return; var r = this.getBoundingClientRect(), t = T.w0 + (e.clientX - r.left) / T.pps, i = inSeg(t);
      if (i < 0) { marquee(e, function (cx) { return T.w0 + (cx - r.left) / T.pps; }); return; }
      if (e.ctrlKey || e.metaKey || e.shiftKey) {
        var cur = T.pick.length ? T.pick.slice() : (T.sel ? [itemOf(T.sel.type, T.sel.i)] : []), k = cur.indexOf(T.segs[i]);
        if (k >= 0) cur.splice(k, 1); else cur.push(T.segs[i]); setPick(cur); render(); return;
      }
      T.pick = []; T.sel = { type: "seg", i: i }; seek(t); render();
    });
    function selSegs() {
      if (!T) return [];
      if (T.pick.length > 1) return T.pick.filter(function (it) { return typeOf(it) === "seg"; });
      return T.sel && T.sel.type === "seg" ? [T.segs[T.sel.i]] : [];
    }
    $("tlSegVol").addEventListener("input", function () {
      var ss = selSegs(), v = Number(this.value); if (!ss.length) return;
      ss.forEach(function (sg) { sg[2] = v; }); changed(); render(); applyVol();
    });
    $("tlSegMute").addEventListener("click", function () {
      var ss = selSegs(); if (!ss.length) return;
      var to = ss[0][2] === "mute" ? 0 : "mute";
      ss.forEach(function (sg) { sg[2] = to; }); changed(); render(); applyVol();
    });
    $("tlSegVolReset").addEventListener("click", function () {
      var ss = selSegs(); if (!ss.length) return;
      ss.forEach(function (sg) { sg[2] = 0; }); changed(); render(); applyVol();
    });
    $("tlVol").addEventListener("input", function () {
      if (!T) return; T.vol = Number(this.value); $("tlVolVal").textContent = (T.vol > 0 ? "+" : "") + T.vol + " dB"; changed(); applyVol();
    });
    $("tlMusic").addEventListener("input", function () {
      if (!T) return; T.mvol = Number(this.value) / 100; $("tlMusicVal").textContent = this.value + "%"; changed(); applyVol();
      if (T.mvol === 0) $("tlMusicAudio").pause();
    });
    $("tlCapText").addEventListener("input", function () {
      if (!T || !T.sel || T.sel.type !== "cap") return;
      T.caps[T.sel.i].text = this.value; changed();
      var b = document.querySelector('.tl-capb[data-i="' + T.sel.i + '"]'); if (b) b.firstChild.textContent = this.value;
      drawHead();
    });
    $("tlCapOther").addEventListener("click", function () {
      if (!T || !T.sel || T.sel.type !== "cap") return;
      var c = T.caps[T.sel.i]; c.other = !c.other; changed(); render();
    });
    $("tlVideo").addEventListener("pause", function () { $("tlPlay").textContent = "▶"; });
    document.addEventListener("keydown", function (e) {
      if (!T || $("tlWrap").classList.contains("hidden")) return;
      var tag = (document.activeElement || {}).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if (e.key === "s" || e.key === "S") split();
      else if (e.key === "Delete" || e.key === "Backspace") del();
      else if ((e.key === "a" || e.key === "A") && (e.ctrlKey || e.metaKey)) { e.preventDefault(); setPick(T.segs.concat(T.caps)); render(); }
      else if (e.key === "Escape" && (T.pick.length || T.sel)) { e.preventDefault(); e.stopImmediatePropagation(); T.pick = []; T.sel = null; render(); }
      else if (e.key === "ArrowLeft") seek(T.t - (e.shiftKey ? 1 : 0.1));
      else if (e.key === "ArrowRight") seek(T.t + (e.shiftKey ? 1 : 0.1));
    });
  }
  wire();
  return { open: open, close: close, result: result, isChanged: function () { return !!(T && T.changed); },
    setGeo: setGeo, boxes: boxes, isOpen: function () { return !!T; } };
})();
