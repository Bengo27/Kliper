"""Klipon launcher: starts the engine (if it isn't running) and opens the app window."""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
# 2.35.5: a second copy (e.g. a tester copy on another drive) can run beside the main one on its own port
PORT = int(os.environ.get("KLIPON_PORT") or 47860)
TITLE = os.environ.get("KLIPON_TITLE") or "Klipon"
URL = f"http://127.0.0.1:{PORT}/app/"


def alive():
    return running_version() is not None


def running_version():
    try:
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("version", "?")
    except Exception:
        return None


def installed_version():
    import re
    try:
        with open(os.path.join(HERE, "clipgenie", "server.py"), "r", encoding="utf-8") as f:
            m = re.search(r'VERSION = "([^"]+)"', f.read())
            return m.group(1) if m else None
    except Exception:
        return None


def stop_old_engine():
    """An older engine is still running after an update: stop it so the new code loads."""
    if os.name != "nt":
        return
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*" + os.path.join(HERE, "run_engine") + "*' } | "   # only THIS copy's engine
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.25)
        if not alive():
            break


def start_engine():
    py = sys.executable
    if py.lower().endswith("python.exe"):
        py = py[:-10] + "pythonw.exe"
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([py, os.path.join(HERE, "run_engine.py")], creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def find_edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(p):
                return p
    return None


APP_ID = "BengoGames.Klipon" + ("" if PORT == 47860 else f".p{PORT}")
DATA = os.path.join(os.environ.get("APPDATA", HERE), "BengoGames", "Klipon")
LOCAL = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "BengoGames", "Klipon")
ICON = os.path.normpath(os.path.join(HERE, "..", "app", "icon.ico"))
VENDOR = os.path.join(HERE, "vendor")


def log(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "window.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def unblock(folder):
    """Files from a downloaded zip carry a 'from the internet' mark that stops .NET loading them."""
    for root, _, files in os.walk(folder):
        for n in files:
            if n.lower().endswith((".dll", ".pyd", ".exe")):
                try:
                    os.remove(os.path.join(root, n) + ":Zone.Identifier")
                except OSError:
                    pass


SHORTCUT_CS = r'''
using System; using System.Runtime.InteropServices;
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CgShellLink {}
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010b-0000-0000-C000-000000000046")]
interface CgPersistFile { void GetClassID(out Guid c); [PreserveSig] int IsDirty();
  void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint m); void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool r);
  void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f); void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f); }
[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
interface CgPropertyStore { void GetCount(out uint c); void GetAt(uint i, out CgPropKey k); void GetValue(ref CgPropKey k, out CgPropVariant v);
  void SetValue(ref CgPropKey k, ref CgPropVariant v); void Commit(); }
[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct CgPropKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)] public struct CgPropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
public static class CgAumid {
  public static void Set(string lnk, string id) {
    object link = new CgShellLink(); CgPersistFile pf = (CgPersistFile)link; pf.Load(lnk, 2);
    CgPropertyStore ps = (CgPropertyStore)link;
    CgPropKey k = new CgPropKey(); k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); k.pid = 5;
    CgPropVariant v = new CgPropVariant(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(id);
    ps.SetValue(ref k, ref v); ps.Commit(); pf.Save(lnk, true); Marshal.FreeCoTaskMem(v.p);
  }
}
'''


def tag_shortcuts():
    """Give the Desktop / Start menu shortcuts Klipon's own taskbar id, so a pinned
    taskbar button opens Klipon (not Edge or Python). Runs once."""
    mark = os.path.join(DATA, "shortcuts_tagged_v1")
    if os.name != "nt" or os.path.exists(mark):
        return
    links = [os.path.join(os.path.expanduser("~"), "Desktop", "Klipon.lnk"),
             os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Klipon.lnk")]
    try:
        import ctypes.wintypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # real Desktop (OneDrive-aware)
            links.insert(0, os.path.join(buf.value, "Klipon.lnk"))
    except Exception:
        pass
    links = [l for l in dict.fromkeys(links) if os.path.isfile(l)]
    if not links:
        return
    script = "Add-Type -TypeDefinition @'\n" + SHORTCUT_CS + "\n'@\n" + "".join(
        "try {{ [CgAumid]::Set('{0}', '{1}') }} catch {{ Write-Output $_ }}\n".format(l.replace("'", "''"), APP_ID) for l in links)
    try:
        os.makedirs(DATA, exist_ok=True)
        ps1 = os.path.join(DATA, "tag_shortcuts.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write(script)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                           creationflags=0x08000000, capture_output=True, text=True, timeout=90)
        out = (r.stdout + r.stderr).strip()
        if out:
            log("shortcut tag: " + out[:800])
        else:
            open(mark, "w").close()
    except Exception as e:
        log("shortcut tag failed: %r" % e)


def set_window_icon(window):
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = 0
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            pass
        if not hwnd:
            hwnd = u.FindWindowW(None, "Klipon")
        if not hwnd or not os.path.isfile(ICON):
            return
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        big = u.LoadImageW(None, ICON, 1, 256, 256, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE
        small = u.LoadImageW(None, ICON, 1, 32, 32, 0x10)
        if big:
            u.SendMessageW(hwnd, 0x80, 1, big)     # WM_SETICON, ICON_BIG
        if small:
            u.SendMessageW(hwnd, 0x80, 0, small)   # ICON_SMALL
    except Exception as e:
        log("icon: %r" % e)


def open_native():
    """Klipon's own window (Windows' built-in WebView2), with its own taskbar button and icon."""
    if os.name != "nt" or not os.path.isdir(VENDOR):
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    try:
        unblock(VENDOR)
        sys.path.insert(0, VENDOR)
        import threading
        if PORT == 47860:
            threading.Thread(target=tag_shortcuts, daemon=True).start()
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        store = os.path.join(LOCAL, "window2")
        os.makedirs(store, exist_ok=True)
        win = webview.create_window(TITLE, URL, width=1340, height=880, min_size=(380, 520),
                                    background_color="#161618", text_select=True)
        win.events.shown += lambda: set_window_icon(win)
        webview.start(gui="edgechromium", private_mode=False, storage_path=store)
        return True
    except Exception as e:
        import traceback
        log("native window failed, using Edge instead:\n" + traceback.format_exc())
        return False


def open_window():
    if open_native():
        return
    edge = find_edge()
    if edge:
        profile = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "BengoGames", "Klipon", "window")
        os.makedirs(profile, exist_ok=True)
        subprocess.Popen([edge, f"--app={URL}", f"--user-data-dir={profile}", "--window-size=1340,880",
                          "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"])
    else:
        webbrowser.open(URL)


if __name__ == "__main__":
    v = running_version()
    if v is not None and installed_version() and v != installed_version():
        stop_old_engine()
    if not alive():
        start_engine()
        for _ in range(80):
            time.sleep(0.25)
            if alive():
                break
    open_window()
