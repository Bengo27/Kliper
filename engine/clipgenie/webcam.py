"""Find the streamer's webcam overlay automatically.

1. Sample frames across the VOD and detect faces.
2. The webcam face sits in the same place all stream, so take the most
   consistent face location.
3. The webcam box edges are static (gameplay moves, the overlay border does
   not), so look for edges that stay put across frames around the face.
"""
import os
import numpy as np

from .util import ffmpeg, run, log, ENGINE_DIR

SAMPLE_W = 640


def _sample_frames(video_path, duration, width, height, count=36, t_range=None, sample_w=None):
    import cv2  # noqa: F401
    sw = sample_w or SAMPLE_W
    h = int(round(height * sw / width / 2) * 2)
    frames = []
    if t_range:   # 2.31: only inside one clip
        a, b = float(t_range[0]), float(t_range[1])
        times = np.linspace(a + min(0.5, (b - a) * 0.05), b - min(0.5, (b - a) * 0.05), count)
    else:
        times = np.linspace(duration * 0.04, duration * 0.96, count)
    for t in times:
        p = run([ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", video_path,
                 "-frames:v", "1", "-vf", f"scale={sw}:{h}",
                 "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=False)
        buf = p.stdout
        if len(buf) == sw * h:
            frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(h, sw))
    return frames


def _detector():
    import cv2
    yunet = os.path.join(ENGINE_DIR, "models", "face_detection_yunet_2023mar.onnx")
    if os.path.isfile(yunet) and hasattr(cv2, "FaceDetectorYN"):
        det = cv2.FaceDetectorYN.create(yunet, "", (320, 320), 0.7, 0.3, 50)

        def run_yunet(gray):
            img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            det.setInputSize((img.shape[1], img.shape[0]))
            _, faces = det.detect(img)
            if faces is None:
                return []
            return [tuple(int(v) for v in f[:4]) for f in faces]
        return run_yunet
    if not hasattr(cv2, "CascadeClassifier"):
        raise RuntimeError("Face model missing - reinstall Clip Genie")
    casc = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

    def run_haar(gray):
        f = casc.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=6, minSize=(18, 18))
        return [tuple(int(v) for v in r) for r in f]
    return run_haar


def _cluster_faces(dets, frame_count):
    """Group face detections by position; return the most frequent cluster."""
    if not dets:
        return None, 0
    clusters = []
    for (x, y, w, h) in dets:
        cx, cy = x + w / 2, y + h / 2
        for c in clusters:
            if abs(c["cx"] - cx) < max(w, c["w"]) * 0.8 and abs(c["cy"] - cy) < max(h, c["h"]) * 0.8:
                c["items"].append((x, y, w, h))
                c["cx"] = np.mean([i[0] + i[2] / 2 for i in c["items"]])
                c["cy"] = np.mean([i[1] + i[3] / 2 for i in c["items"]])
                c["w"] = np.median([i[2] for i in c["items"]])
                c["h"] = np.median([i[3] for i in c["items"]])
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "w": w, "h": h, "items": [(x, y, w, h)]})
    best = max(clusters, key=lambda c: len(c["items"]))
    items = np.array(best["items"], dtype=np.float32)
    box = tuple(float(v) for v in np.median(items, axis=0))
    return box, len(best["items"]) / max(1, frame_count)


def _second_face(dets, frame_count, face, presence):
    """A second person seen about as often and about as big as the first (duo / podcast streams), or None."""
    if not dets:
        return None
    clusters = []
    for (x, y, w, h) in dets:
        cx, cy = x + w / 2, y + h / 2
        for c in clusters:
            if abs(c["cx"] - cx) < max(w, c["w"]) * 0.8 and abs(c["cy"] - cy) < max(h, c["h"]) * 0.8:
                c["items"].append((x, y, w, h))
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "w": w, "h": h, "items": [(x, y, w, h)]})
    fx, fy, fw, fh = face
    best = None
    for c in clusters:
        b = tuple(float(v) for v in np.median(np.array(c["items"], dtype=np.float32), axis=0))
        if abs((b[0] + b[2] / 2) - (fx + fw / 2)) < max(b[2], fw):   # the same person
            continue
        pres = len(c["items"]) / max(1, frame_count)
        if pres >= 0.35 and pres >= 0.4 * presence and b[2] >= 0.5 * fw and (best is None or pres > best[1]):
            best = (b, pres)
    return best[0] if best else None


def _fullcam(dets, frame_count, face, presence, W, H, method):
    """The whole picture is the camera: crop 9:16 around the face, or around both people when two are on camera
    (and show the whole picture, 'wide', when they are too far apart for a 9:16 crop)."""
    fx, fy, fw, fh = face
    out = {"found": True, "layout": "fullcam", "face_cx": round((fx + fw / 2) / W, 4), "box": [0, 0, 1, 1],
           "presence": round(presence, 2), "method": method}
    other = _second_face(dets, frame_count, face, presence)
    if other:
        a = min(fx, other[0]) - 0.6 * max(fw, other[2])
        b = max(fx + fw, other[0] + other[2]) + 0.6 * max(fw, other[2])
        out["face_cx"] = round(max(0.0, min(1.0, (a + b) / 2 / W)), 4)
        out["faces2"] = [[round(v / s, 4) for v, s in zip(f, (W, H, W, H))] for f in (face, other)]
        if b - a > H * 9 / 16:
            out["wide"] = True   # two people too far apart for a 9:16 crop: show the whole picture
        log.info("Two people on camera%s", " (too far apart for one crop: whole picture)" if out.get("wide") else "")
    return out


def _static_edges(frames):
    import cv2
    acc = np.zeros(frames[0].shape, dtype=np.float32)
    for f in frames:
        e = cv2.Canny(cv2.GaussianBlur(f, (3, 3), 0), 40, 120)
        # thicken so 1px jitter still counts as "same edge"
        e = cv2.dilate(e, np.ones((2, 2), np.uint8))
        acc += (e > 0)
    return acc / len(frames)


def _find_border(static, face, H, W):
    fx, fy, fw, fh = face
    thr = 0.55
    ys0, ys1 = int(max(0, fy - fh * 0.5)), int(min(H, fy + fh * 1.5))
    xs0, xs1 = int(max(0, fx - fw * 0.5)), int(min(W, fx + fw * 1.5))

    def col_score(x, y0=ys0, y1=ys1):
        return float((static[y0:y1, x] > thr).mean()) if y1 > y0 else 0.0

    def row_score(y, x0=xs0, x1=xs1):
        return float((static[y, x0:x1] > thr).mean()) if x1 > x0 else 0.0

    # 2.35.3: every still line walking out from the face, nearest first (the frame edge last). Kai Cenat sits far
    # back in his room: the first still lines were his door frame / shelves and the "camera" was a box around him.
    reach = 7
    lefts, rights, tops, bots = [], [], [], []
    for x in range(int(fx - fw * 0.15), max(-1, int(fx - fw * reach)), -1):
        if x <= 0:
            lefts.append(0); break
        if col_score(x) > 0.7 and (not lefts or lefts[-1] - x > 2):
            lefts.append(x)
    for x in range(int(fx + fw * 1.15), min(W + 1, int(fx + fw * reach))):
        if x >= W - 1:
            rights.append(W); break
        if col_score(x) > 0.7 and (not rights or x - rights[-1] > 2):
            rights.append(x)
    for y in range(int(fy - fh * 0.15), max(-1, int(fy - fh * 3)), -1):
        if y <= 0:
            tops.append(0); break
        if row_score(y) > 0.7 and (not tops or tops[-1] - y > 2):
            tops.append(y)
    for y in range(int(fy + fh * 1.15), min(H + 1, int(fy + fh * 5))):
        if y >= H - 1:
            bots.append(H); break
        if row_score(y) > 0.7 and (not bots or y - bots[-1] > 2):
            bots.append(y)
    if not (lefts and rights and tops and bots):
        return (lefts or [None])[0], (tops or [None])[0], (rights or [None])[0], (bots or [None])[0], False
    # a camera's border is a full rectangle: every line in it is still all the way along its side (lines in the
    # streamer's room only run part of the way). Of the rectangles made of the found lines whose four sides hold,
    # keep the biggest one shaped like a camera picture (at least 3:2: a cam with a sponsor panel
    # glued under it is taller than that); the frame edge always counts as a side.
    on = static > 0.3   # 2.35.3: over a long stretch (other scenes, cam hidden) the real border is still only ~half the time
    import cv2   # a found line can be a pixel or two off the true border: allow that much wobble along it
    on_r = cv2.dilate(on.astype(np.uint8), np.ones((5, 1), np.uint8)) > 0     # for rows (top / bottom)
    on_c = cv2.dilate(on.astype(np.uint8), np.ones((1, 5), np.uint8)) > 0     # for columns (left / right)

    def full(vals, lo, hi):
        return hi > lo and float(vals[lo:hi].mean()) >= 0.8

    best = None
    for l in lefts[:14]:
        for r in rights[:14]:
            for t in tops[:14]:
                for b in bots[:14]:
                    w, h = r - l, b - t
                    if w < fw * 1.4 or h < fh * 1.4:
                        continue
                    li, ri, ti, bi = int(l), min(W - 1, int(r)), int(t), min(H - 1, int(b))
                    ok = ((l <= 0 or full(on_c[ti:bi, li], 0, bi - ti)) and (r >= W or full(on_c[ti:bi, ri], 0, bi - ti)) and
                          (t <= 0 or full(on_r[ti, li:ri], 0, ri - li)) and (b >= H or full(on_r[bi, li:ri], 0, ri - li)))
                    if not ok:
                        continue
                    if not 1.5 <= w / h <= 2.1:   # his cam 1.58, cam + sponsor panel 1.37, Kai 1.94
                        continue
                    key = -w * h
                    if best is None or key < best[0]:
                        best = (key, (l, t, r, b))
    if best:
        return best[1] + (True,)
    return lefts[0], tops[0], rights[0], bots[0], False


def _trim_still_panels(frames, l, t, r, b, face):
    """2.35: cut stream overlays that sit inside the found box (sponsor banners, alert panels, chat glued to the
    cam) off its edges, for any streamer's layout. The real camera always moves (the streamer, camera noise);
    an overlay panel is a still graphic. Walking out from the face, the box ends where the picture goes still
    for a solid band. Rows are cut on motion alone; columns also need the band to hold static edges (text /
    logos), so a plain wall beside the head is never cut off."""
    import cv2
    H, W = frames[0].shape
    st = np.stack(frames).astype(np.float32)
    mot = np.abs(st - np.median(st, axis=0)).mean(axis=0)
    edges = _static_edges(frames) > 0.55
    fx, fy, fw, fh = face
    l, t, r, b = int(l), int(t), int(r), int(b)
    if r - l < 8 or b - t < 8:
        return l, t, r, b
    face_mot = float(np.median(mot[int(fy):int(fy + fh), int(fx):int(fx + fw)]))
    # a still graphic measures ~0.2-0.9, a live camera (even a plain wall) ~2+: a fixed bar, a bit higher for very
    # busy cams. (Relative to the face it cut real background off calm cams.)
    thr = min(2.0, max(1.5, 0.05 * face_mot))
    band_r, band_c = max(4, int(H * 0.035)), max(4, int(W * 0.03))
    rows = mot[:, l:r].mean(axis=1)
    cols = mot[t:b, :].mean(axis=0)
    cole = edges[t:b, :].mean(axis=0)

    def still_rows(y0, y1):
        return y1 > y0 and float(rows[y0:y1].mean()) < thr

    nb = b
    for y in range(int(fy + fh) + 2, b - band_r // 2):
        if still_rows(y, min(b, y + band_r)):
            while y < b and rows[y] >= thr:   # the band can start on the last live rows
                y += 1
            nb = y; break
    nt = t
    for y in range(int(fy) - 2, t + band_r // 2, -1):
        if still_rows(max(t, y - band_r), y):
            while y > t and rows[y - 1] >= thr:
                y -= 1
            nt = y; break

    def still_cols(x0, x1):
        return x1 > x0 and float(cols[x0:x1].mean()) < thr and float(cole[x0:x1].mean()) > 0.2

    nr = r
    for x in range(int(fx + fw) + 2, r - band_c // 2):
        if still_cols(x, min(r, x + band_c)):
            while x < r and cols[x] >= thr:
                x += 1
            nr = x; break
    nl = l
    for x in range(int(fx) - 2, l + band_c // 2, -1):
        if still_cols(max(l, x - band_c), x):
            while x > l and cols[x - 1] >= thr:
                x -= 1
            nl = x; break
    # never trim into a postage stamp: keep at least ~2 face widths / heights around the face
    if nr - nl < fw * 2.0:
        nl, nr = l, r
    if nb - nt < fh * 1.3:
        nt, nb = t, b
    if (nl, nt, nr, nb) != (l, t, r, b):
        log.info("Webcam box trimmed past still overlay panels: %s -> %s", (l, t, r, b), (nl, nt, nr, nb))
    return nl, nt, nr, nb


def detect_webcam(video_path, info, cache_dir=None, count=36, t_range=None):
    W0, H0 = info["width"], info["height"]
    frames = _sample_frames(video_path, info["duration"], W0, H0, count=count, t_range=t_range)
    if len(frames) < 6:
        return {"found": False, "reason": "Could not read frames"}
    H, W = frames[0].shape
    det = _detector()
    dets = []
    for f in frames:
        dets.extend(det(f))
    face, presence = _cluster_faces(dets, len(frames))
    if face is None or presence < 0.5:
        # 2.35: small webcams (other streamers' layouts: a face ~2 % of the width) are missed at 640 px wide;
        # look again at 1280 and keep that if it sees the face more often
        try:
            big = _sample_frames(video_path, info["duration"], W0, H0, count=min(count, 16), t_range=t_range,
                                 sample_w=SAMPLE_W * 2)
            bd = []
            for f in big:
                bd.extend(det(f))
            bface, bpres = _cluster_faces(bd, len(big))
            if bface is not None and bpres > (presence or 0) + 0.15:
                face, presence = tuple(v / 2 for v in bface), bpres
                log.info("Small webcam found at full size (presence %.2f)", bpres)
        except Exception as e:
            log.warning("Sharper face check failed: %s", e)
    if face is None or presence < 0.2:
        return {"found": False, "layout": "gameplay", "reason": "No webcam face found", "presence": round(presence or 0, 2)}
    fx, fy, fw, fh = face
    # Full-screen cam (Just Chatting): face is big and central. 2.30: a TALL face counts too: a streamer looking
    # down / sideways has a narrow face box (the snacks stream: 14 % wide but 32 % tall made a fake split TikTok
    # with the same face top and bottom). Corner webcams have faces ~13-14 % of the height.
    if fw > W * 0.18 or fh > H * 0.22:
        return _fullcam(dets, len(frames), face, presence, W, H, "fullscreen")
    static = _static_edges(frames)
    l, t, r, b, framed = _find_border(static, face, H, W)
    method = "edges"
    # fallback: estimate a 4:3 box around the face
    est_w = fw * 3.4
    est_h = est_w * 0.75
    if l is None or r is None or (r - l) < fw * 1.4 or (r - l) > fw * 10:
        l = fx + fw / 2 - est_w / 2
        r = l + est_w
        method = "estimate"
    if t is None or b is None or (b - t) < fh * 1.4 or (b - t) > fh * 6:
        t = fy + fh * 0.45 - est_h * 0.45
        b = t + est_h
        method = "estimate"
    l, t = max(0.0, l), max(0.0, t)
    r, b = min(float(W), r), min(float(H), b)
    # 0.1.2: no camera frame around a face in the middle of the picture, or a "webcam" a third of the screen or
    # bigger: that's not a webcam corner over gameplay, the whole picture is the camera (IRL, a couch stream, a
    # VTuber's room). As a split TikTok it showed the same room top and bottom, or a far-away face blown up.
    fcx, fcy = (fx + fw / 2) / W, (fy + fh / 2) / H
    if (method == "estimate" and 0.2 < fcx < 0.8 and 0.15 < fcy < 0.85) or (r - l) > W * 0.35 or (b - t) > H * 0.6:
        log.info("The whole picture is the camera (face at %.2f, %.2f; %s)", fcx, fcy, method)
        return _fullcam(dets, len(frames), face, presence, W, H, "scene")
    try:   # 2.35.3: the camera's whole border was found: nothing glued inside it to trim (Kai Cenat's still room)
        if not (framed and method == "edges"):
            l, t, r, b = _trim_still_panels(frames, l, t, r, b, face)
    except Exception as e:
        log.warning("Overlay trim skipped: %s", e)
    # 2.35: a stream webcam is landscape (16:9 .. 4:3). A box taller than ~1.1x its width has picked up a panel
    # glued above / below the cam (a chat box that scrolls, so the still-panel trim can't see it): keep a 4:3
    # slice of it around the face.
    if (b - t) > (r - l) * 1.15:   # dima's squarish cam is ~1.04: leave those
        nh = (r - l) * 0.75
        nt = min(max(t, fy + fh * 0.5 - nh * 0.45), b - nh)
        log.info("Webcam box too tall for a camera (%.0fx%.0f): kept a 4:3 slice around the face", r - l, b - t)
        t, b = nt, nt + nh
    box = [round(l / W, 4), round(t / H, 4), round((r - l) / W, 4), round((b - t) / H, 4)]
    log.info("Webcam box %s via %s (presence %.2f)", box, method, presence)
    return {"found": True, "layout": "split", "box": box, "presence": round(presence, 2),
            "method": method, "trimmed": 1,
            "face": [round(fx / W, 4), round(fy / H, 4), round(fw / W, 4), round(fh / H, 4)]}
