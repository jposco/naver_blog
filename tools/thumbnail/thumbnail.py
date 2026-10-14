"""블로그 대표이미지 생성기: 사진 EXIF GPS → 구글 지도 위 핀 + 사진 카드 + 랜드마크 아이콘.

사용법:
    python tools/thumbnail/thumbnail.py scan   posts/.../thumbnail.yaml   # 사진별 GPS/촬영시각 확인
    python tools/thumbnail/thumbnail.py render posts/.../thumbnail.yaml   # PNG 생성

지도 배경은 환경변수 GOOGLE_MAPS_API_KEY 가 있어야 한다. 없으면 레이아웃 확인용 미리보기 배경으로 렌더링한다.
"""
import base64
import hashlib
import html
import io
import json
import math
import os
import random
import sys
from pathlib import Path

import requests
import yaml
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = ROOT / ".cache" / "thumbnail"
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

S = 1080            # 캔버스 크기(px)
MAP_SCALE = 2       # Static Maps scale=2 → 540 논리px = 1080px
TITLE_BOTTOM = 330  # 타이틀 영역 하단
TITLE_RIGHT = 660   # 타이틀 텍스트 우측 끝 (이 오른쪽은 상단까지 카드 배치 가능)
FIT_BOX = (250, 500, 830, 850)  # 핀이 들어갈 영역 (x0, y0, x1, y1). 바깥 링은 사진 카드 자리
TOWER_W, TOWER_H = 124, 310

# 차분한 웜톤 지도 스타일 (POI·대중교통·도로 라벨 제거)
MAP_STYLES = [
    "element:geometry|color:0xf3efe7",
    "element:labels.icon|visibility:off",
    "element:labels.text.fill|color:0x8f877b",
    "element:labels.text.stroke|color:0xf3efe7",
    "feature:poi|visibility:off",
    "feature:poi.park|visibility:on",
    "feature:poi.park|element:geometry|color:0xdde6d0",
    "feature:poi.park|element:labels|visibility:off",
    "feature:transit|visibility:off",
    "feature:road|element:geometry|color:0xffffff",
    "feature:road|element:labels|visibility:off",
    "feature:road.highway|element:geometry|color:0xf7e3cf",
    "feature:water|element:geometry|color:0xc6d5dc",
    "feature:administrative|element:geometry|visibility:off",
]


# ---------- EXIF ----------
def _dms(v):
    d, m, s = (float(x) for x in v)
    return d + m / 60 + s / 3600


def read_meta(path):
    img = Image.open(path)
    exif = img.getexif()
    gps = exif.get_ifd(0x8825)
    lat = lon = None
    if gps and 2 in gps and 4 in gps:
        lat, lon = _dms(gps[2]), _dms(gps[4])
        if gps.get(1) == "S":
            lat = -lat
        if gps.get(3) == "W":
            lon = -lon
    taken = exif.get_ifd(0x8769).get(36867) or exif.get(306)
    return {"lat": lat, "lon": lon, "taken": taken, "model": exif.get(272)}


def load_spots(cfg, base):
    photos_dir = base / cfg.get("photos_dir", "photos")
    overrides = cfg.get("spots") or {}
    spots = []
    for p in sorted(photos_dir.iterdir()) if photos_dir.exists() else []:
        if p.suffix.lower() not in PHOTO_EXT:
            continue
        ov = overrides.get(p.name) or {}
        if ov.get("skip"):
            continue
        meta = read_meta(p)
        if ov.get("lat") is not None:
            meta["lat"], meta["lon"] = ov["lat"], ov["lon"]
        spots.append({"file": p, "caption": ov.get("caption", ""), "card": ov.get("card"),
                      "order": ov.get("order", 999), **meta})
    spots.sort(key=lambda s: (s["order"], s["taken"] or "", s["file"].name))
    return spots


# ---------- 지도 투영 (Web Mercator) ----------
def world(lat, lon, z):
    n = 256 * 2 ** z
    siny = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n


def unworld(x, y, z):
    n = 256 * 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def fit_view(points, cfg_map):
    """모든 점이 FIT_BOX 안에 들어오는 최대 줌과 중심 계산."""
    x0, y0, x1, y1 = FIT_BOX
    if cfg_map.get("zoom"):
        zooms = [cfg_map["zoom"]]
    else:
        zooms = range(18, 9, -1)
    for z in zooms:
        ws = [world(la, lo, z) for la, lo in points]
        xs, ys = [w[0] for w in ws], [w[1] for w in ws]
        if (max(xs) - min(xs)) * MAP_SCALE <= x1 - x0 and (max(ys) - min(ys)) * MAP_SCALE <= y1 - y0 or len(zooms) == 1:
            if cfg_map.get("center"):
                return tuple(cfg_map["center"]), z
            bx, by = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            # bbox 중심이 FIT_BOX 중심에 오도록 지도 중심 이동
            cx = bx - ((x0 + x1) / 2 - S / 2) / MAP_SCALE
            cy = by - ((y0 + y1) / 2 - S / 2) / MAP_SCALE
            return unworld(cx, cy, z), z
    raise SystemExit("스팟 범위가 너무 넓음: thumbnail.yaml 의 map.zoom 을 지정하세요")


def to_px(lat, lon, center, z):
    wx, wy = world(lat, lon, z)
    cx, cy = world(*center, z)
    return S / 2 + (wx - cx) * MAP_SCALE, S / 2 + (wy - cy) * MAP_SCALE


def fetch_map(center, z, cfg_map):
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    params = [("center", f"{center[0]:.6f},{center[1]:.6f}"), ("zoom", z), ("size", f"{S // MAP_SCALE}x{S // MAP_SCALE}"),
              ("scale", MAP_SCALE), ("language", cfg_map.get("language", "ko")), ("format", "png")]
    params += [("style", s) for s in cfg_map.get("styles", MAP_STYLES)]
    sig = hashlib.sha1(json.dumps(params).encode()).hexdigest()[:16]
    cached = CACHE / f"map_{sig}.png"
    if not cached.exists():
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params + [("key", key)], timeout=30)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            raise SystemExit(f"Static Maps 요청 실패 ({r.status_code}): {r.text[:300]}")
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
    return cached


# ---------- 카드 배치 ----------
def card_size(n):
    return 250 if n <= 4 else 214 if n <= 6 else 188 if n <= 8 else 166


def overlap(a, b, gap=0):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _leader(pin, r):
    return pin[0], pin[1], (r[0] + r[2]) / 2, (r[1] + r[3]) / 2


def crosses(a, b):
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
    p1, p2, p3, p4 = a[:2], a[2:], b[:2], b[2:]
    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def place_cards(pins, tower_px, cw, ch, manual):
    """핀·타워·지도 저작권 표기를 가리지 않는 위치에 카드를 탐욕적으로 배치."""
    blocked = [(tower_px[0] - TOWER_W / 2 - 10, tower_px[1] - TOWER_H - 10, tower_px[0] + TOWER_W / 2 + 10, tower_px[1] + 26),
               (0, S - 46, 170, S), (S - 330, S - 30, S, S)]
    blocked += [(x - 26, y - 52, x + 26, y + 10) for x, y in pins]
    slots = []
    for x in range(24, S - cw - 23, 16):
        for y in range(36 if x >= TITLE_RIGHT else TITLE_BOTTOM, S - ch - 23, 16):
            r = (x, y, x + cw, y + ch)
            if not any(overlap(r, b) for b in blocked):
                slots.append(r)
    fixed = {i: (m[0], m[1], m[0] + cw, m[1] + ch) for i, m in manual.items()}
    rng = random.Random(7)
    order = [i for i in range(len(pins)) if i not in fixed]
    best, best_cost = None, math.inf
    for _ in range(150):  # 배치 순서를 바꿔가며 지시선 길이·교차가 최소인 배치 선택
        placed, cost = dict(fixed), 0.0
        for i in order:
            px, py = pins[i]
            pick = None
            for r in slots:
                if any(overlap(r, o, 14) for o in placed.values()):
                    continue
                cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                c = math.hypot(cx - px, cy - py)
                if c < cw * 0.75:
                    c += 400
                c += 300 * sum(crosses((px, py, cx, cy), _leader(pins[j], placed[j])) for j in placed)
                if pick is None or c < pick[0]:
                    pick = (c, r)
            if pick is None:
                cost = math.inf
                break
            cost += pick[0]
            placed[i] = pick[1]
        if cost < best_cost:
            best, best_cost = placed, cost
        rng.shuffle(order)
    if best is None:
        raise SystemExit("카드 배치 실패. 사진 수를 줄이거나 spots.<파일>.card 로 위치를 직접 지정하세요")
    placed = best
    return placed


# ---------- 렌더 ----------
def photo_uri(path, px):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img = ImageOps.fit(img, (px, px), Image.LANCZOS, centering=(0.5, 0.5))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def file_uri(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def build_html(cfg, spots, center, z, map_path):
    lm = cfg["landmark"]
    tx, ty = to_px(lm["lat"], lm["lon"], center, z)
    pins = [to_px(s["lat"], s["lon"], center, z) for s in spots]
    cw = card_size(len(spots))
    ch = cw + 34 if any(s["caption"] for s in spots) else cw + 16
    manual = {i: s["card"] for i, s in enumerate(spots) if s["card"]}
    cards = place_cards(pins, (tx, ty), cw, ch, manual)
    css = (HERE / "style.css").read_text(encoding="utf-8")
    tower = (HERE / "tokyo_tower.svg").read_text(encoding="utf-8")
    bg = f'<img class="map" src="{file_uri(map_path)}">' if map_path else '<div class="map preview"></div>'

    lines, items = [], []
    for i, s in enumerate(spots):
        px, py = pins[i]
        x0, y0, x1, y1 = cards[i]
        lines.append(f'<line x1="{px:.1f}" y1="{py - 20:.1f}" x2="{(x0 + x1) / 2:.1f}" y2="{(y0 + y1) / 2:.1f}"/>')
        rot = (-2.6, 1.8, -1.2, 2.4, -2.0, 1.4, -1.6, 2.2)[i % 8]
        cap = f'<figcaption>{html.escape(s["caption"])}</figcaption>' if s["caption"] else ""
        items.append(
            f'<figure class="card" style="left:{x0}px;top:{y0}px;width:{cw}px;height:{ch}px;transform:rotate({rot}deg)">'
            f'<img src="{photo_uri(s["file"], (cw - 16) * 2)}"><span class="no">{i + 1}</span>{cap}</figure>'
            f'<div class="pin" style="left:{px:.1f}px;top:{py:.1f}px"><span>{i + 1}</span></div>')

    t = cfg.get("title") or ["", ""]
    sub = cfg.get("subtitle") or f"{len(spots)} SPOTS"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;800&family=Noto+Sans+KR:wght@500;900&display=block" rel="stylesheet">
<style>{css}</style></head><body><div class="canvas">
{bg}
<div class="title-fade"></div>
<svg class="leaders" viewBox="0 0 {S} {S}">{''.join(lines)}</svg>
<div class="halo" style="left:{tx:.1f}px;top:{ty:.1f}px"></div>
<div class="tower" style="left:{tx - TOWER_W / 2:.1f}px;top:{ty - TOWER_H:.1f}px;width:{TOWER_W}px;height:{TOWER_H}px">{tower}</div>
<div class="tower-label" style="left:{tx:.1f}px;top:{ty + 8:.1f}px">{html.escape(lm.get("label", ""))}</div>
{''.join(items)}
<header>
  <div class="eyebrow">{html.escape(cfg.get("eyebrow", ""))}</div>
  <h1>{html.escape(t[0])}<br><em>{html.escape(t[1])}</em></h1>
  <div class="sub">{html.escape(sub)}</div>
</header>
</div></body></html>"""


def render(cfg_path):
    from playwright.sync_api import sync_playwright

    cfg_path = Path(cfg_path).resolve()
    base = cfg_path.parent
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    spots = [s for s in load_spots(cfg, base) if s["lat"] is not None]
    if not spots:
        raise SystemExit("GPS 있는 사진이 없음. scan 으로 확인하세요")
    spots = spots[: cfg.get("max_spots", 8)]
    lm = cfg["landmark"]
    center, z = fit_view([(s["lat"], s["lon"]) for s in spots] + [(lm["lat"], lm["lon"])], cfg.get("map") or {})
    map_path = fetch_map(center, z, cfg.get("map") or {})
    if not map_path:
        print("! GOOGLE_MAPS_API_KEY 없음 → 미리보기 배경으로 렌더링")
    doc = build_html(cfg, spots, center, z, map_path)
    CACHE.mkdir(parents=True, exist_ok=True)
    page_file = CACHE / "page.html"
    page_file.write_text(doc, encoding="utf-8")
    out = base / cfg.get("output", "out/thumbnail.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        if not exe and Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").exists():
            exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": S, "height": S})
        page.goto(page_file.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(out))
        browser.close()
    print(f"zoom={z} center={center[0]:.5f},{center[1]:.5f} spots={len(spots)} → {out}")


def scan(cfg_path):
    cfg_path = Path(cfg_path).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    spots = load_spots(cfg, cfg_path.parent)
    if not spots:
        print("사진 없음:", cfg_path.parent / cfg.get("photos_dir", "photos"))
    for s in spots:
        loc = f'{s["lat"]:.6f},{s["lon"]:.6f}' if s["lat"] is not None else "GPS 없음"
        print(f'{s["file"].name:32} {loc:24} {s["taken"] or "-":20} {s["model"] or ""}')
    no_gps = [s["file"].name for s in spots if s["lat"] is None]
    if no_gps:
        print(f"\n! GPS 없는 사진 {len(no_gps)}장: thumbnail.yaml spots.<파일명>.lat/lon 으로 직접 입력하거나 skip 처리")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("scan", "render"):
        raise SystemExit(__doc__)
    {"scan": scan, "render": render}[sys.argv[1]](sys.argv[2])
