"""Build the tester zip: prototypes/tester/ClipGenie_tester_<version>.zip (git-ignored).
No user data: settings, keys, library, cache, models and the WebView profile live elsewhere and are never added.
app/ comes from this repo (newest), engine/python/ffmpeg from the live install (vendor runtimes included).
usage: python\python.exe tools\build_tester.py [--compiled] [--stage=K:\ClipGenieBuild\stage]
--compiled: ship the compiled engine (tools/build_engine.py first) instead of the readable clipgenie/ source."""
import os, sys, re, zipfile, time
COMPILED = "--compiled" in sys.argv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(os.environ["LOCALAPPDATA"], "GenieStudioFX", "ClipGenie")
ver = re.search(r'VERSION\s*=\s*"([^"]+)"', open(os.path.join(ROOT, "engine", "clipgenie", "server.py"), encoding="utf-8").read()).group(1)
out_dir = os.path.join(ROOT, "prototypes", "tester"); os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, f"ClipGenie_tester_{ver}.zip")
TOP = f"Clip Genie Tester {ver}"
SKIP_DIRS = {"__pycache__"}
BAD = re.compile(r"(settings\.json|library\.json|taste\.json|tiktok_token\.json|engine_token|twitch_cookies\.txt|\.log)$", re.I)
parts = [(os.path.join(ROOT, "app"), "ClipGenie/app"),
         (os.path.join(LIVE, "engine"), "ClipGenie/engine"),          # vendor runtimes etc. (clipgenie/ replaced below)
         (os.path.join(LIVE, "python"), "ClipGenie/python"),
         (os.path.join(LIVE, "ffmpeg"), "ClipGenie/ffmpeg")]
files = [(os.path.join(ROOT, "README.md"), "ClipGenie/README.md"),
         (os.path.join(ROOT, "uninstall.ps1"), "ClipGenie/uninstall.ps1")]
if COMPILED:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_engine import PYD
    if not os.path.exists(PYD):
        sys.exit("No compiled engine: run tools\\build_engine.py first.")
    files.append((PYD, "ClipGenie/engine/" + os.path.basename(PYD)))
    out = out.replace(".zip", "_compiled.zip")
else:
    parts.insert(2, (os.path.join(ROOT, "engine", "clipgenie"), "ClipGenie/engine/clipgenie"))
for f in ("install.ps1", "Install Clip Genie.cmd", "READ ME FIRST.txt"):
    files.append((os.path.join(ROOT, "tools", "tester", f), f))
for src, arc in parts:
    for d, dirs, fs in os.walk(src):
        if src == os.path.join(LIVE, "engine") and d == src:
            dirs[:] = [x for x in dirs if x != "clipgenie"]   # the repo's clipgenie/ is newest
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in fs:
            if f.endswith(".pyc") or BAD.search(f):
                continue
            p = os.path.join(d, f)
            files.append((p, arc + "/" + os.path.relpath(p, src).replace(os.sep, "/")))
# the launcher files come from the repo too (newest)
for f in ("run_app.pyw", "run_engine.py"):
    files = [(os.path.join(ROOT, "engine", f), a) if a == "ClipGenie/engine/" + f else (p, a) for p, a in files]
t0 = time.time(); total = 0
STAGE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--stage=")), "")
if STAGE:   # --stage=DIR: copy the ClipGenie folder to DIR/ClipGenie (for tools/installer/clipgenie.iss), no zip
    import shutil
    dst_root = os.path.join(STAGE, "ClipGenie")
    if os.path.exists(dst_root):
        shutil.rmtree(dst_root)
    for p, arc in files:
        if not arc.startswith("ClipGenie/") or arc.endswith("/uninstall.ps1"):
            continue
        d = os.path.join(STAGE, arc.replace("/", os.sep)); os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(p, d); total += os.path.getsize(p)
    print("STAGED", dst_root, f"{total/1e9:.2f} GB, {time.time()-t0:.0f}s"); sys.exit(0)
with zipfile.ZipFile(out + ".part", "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
    for i, (p, arc) in enumerate(files):
        z.write(p, TOP + "/" + arc); total += os.path.getsize(p)
        if i % 2000 == 0:
            print(f"{i}/{len(files)} files, {total/1e9:.2f} GB, {time.time()-t0:.0f}s", flush=True)
os.replace(out + ".part", out)
print("DONE", out, f"{len(files)} files, {total/1e9:.2f} GB in, {os.path.getsize(out)/1e9:.2f} GB zip, {time.time()-t0:.0f}s")
