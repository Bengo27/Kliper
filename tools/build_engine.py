"""Compile engine/clipgenie into ONE machine-code file (clipgenie.cp311-win_amd64.pyd) with Nuitka, so the
public / tester copy ships no readable Python source. Comments (docstrings) and asserts are stripped.
Still readable inside: function names and plain text the app needs (AI prompts, messages).

Needs a FULL Python 3.11.9 (the bundled one has no C headers) with nuitka, plus Visual Studio C++:
  build Python: python.org NuGet package "python" 3.11.9 unzipped to <BUILD>/py311 (tools/python.exe),
  then  <BUILD>/py311/tools/python.exe -m pip install nuitka ordered-set zstandard
BUILD = env CLIPGENIE_BUILD or K:/ClipGenieBuild (C: is nearly full).
usage: python\\python.exe tools\\build_engine.py --licence-url=https://clipgenie-licence.<name>.workers.dev
       ->  <BUILD>/out/clipgenie.cp311-win_amd64.pyd   (the compiled engine ALWAYS has the free trial on)"""
import os, shutil, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.environ.get("CLIPGENIE_BUILD") or "K:/ClipGenieBuild"
PY = os.path.join(BUILD, "py311", "tools", "python.exe")
SRC, OUT = os.path.join(BUILD, "src"), os.path.join(BUILD, "out")
PYD = os.path.join(OUT, "clipgenie.cp311-win_amd64.pyd")


def main():
    if not os.path.exists(PY):
        sys.exit(f"No build Python at {PY} (see the notes at the top of this file).")
    shutil.rmtree(SRC, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "engine", "clipgenie"), os.path.join(SRC, "clipgenie"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # the licence server address is baked in (users can't point the compiled engine at another server)
    url = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--licence-url=")), "")
    lp = os.path.join(SRC, "clipgenie", "licence.py")
    src = open(lp, encoding="utf-8").read()
    if url:
        src = src.replace('LICENCE_URL = "https://clipgenie-licence.REPLACE.workers.dev"', f'LICENCE_URL = "{url}"')
    if "REPLACE.workers.dev" in src and "--allow-no-licence" not in sys.argv:
        sys.exit("Give the licence server: --licence-url=https://clipgenie-licence.<name>.workers.dev")
    open(lp, "w", encoding="utf-8").write(src)
    if os.path.exists(PYD):
        os.remove(PYD)
    env = dict(os.environ, NUITKA_CACHE_DIR=os.path.join(BUILD, "cache"))
    r = subprocess.run([PY, "-m", "nuitka", "--module", "clipgenie", "--include-package=clipgenie",
                        f"--output-dir={OUT}", "--remove-output", "--msvc=latest", "--assume-yes-for-downloads",
                        "--python-flag=no_docstrings", "--python-flag=no_asserts"], cwd=SRC, env=env)
    if r.returncode or not os.path.exists(PYD):
        sys.exit("Compile failed.")
    print("DONE", PYD, f"{os.path.getsize(PYD) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
