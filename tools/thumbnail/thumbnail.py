"""블로그 대표이미지 생성기: 사진 EXIF GPS → 구글 지도 패널 위 번호 핀 + 사진 카드 + 랜드마크 아이콘.

사용법:
    python tools/thumbnail/thumbnail.py scan   posts/.../thumbnail.yaml   # 사진별 GPS + 장소 묶음(클러스터) 확인
    python tools/thumbnail/thumbnail.py render posts/.../thumbnail.yaml   # PNG 생성

지도 배경은 환경변수 GOOGLE_MAPS_API_KEY 가 있어야 한다. 없으면 레이아웃 확인용 미리보기 배경으로 렌더링한다.
"""
import base64
import hashlib
import html
import io
import itertools
import json
import math
import os
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

# ---------- 레이아웃 (px) ----------
W, H = 1360, 1200
PANEL = (40, 180, 1320, 1170)       # 지도 패널. 1280x990 = Static Maps 640x495 @scale2 (업스케일 없음)
MAP_SCALE = 2
CARD_W, CARD_H = 250, 262
CARD_X = (60, W - 60 - CARD_W)      # 좌/우 카드 열
CARD_Y = (200, 520, 840)            # 열마다 3칸 → 최대 6곳. 하단은 구글 로고 자리 비움
PIN_BOX = (350, 250, 1010, 1100)    # 핀이 들어갈 영역 (카드 열 사이)
TOWER_W, TOWER_H = 150, 330
MAX_PLACES = len(CARD_X) * len(CARD_Y)

# 크림톤 지도 스타일 (POI 아이콘·도로명 제거, 역 이름만 유지)
MAP_STYLES = [
    "element:geometry|color:0xefebe3",
    "element:labels.icon|visibility:off",
    "element:labels.text.fill|color:0x8d8578",
    "element:labels.text.stroke|color:0xefebe3|weight:3",
    "feature:poi|visibility:off",
    "feature:poi.park|visibility:on",
    "feature:poi.park|element:geometry|color:0xdbe5cf",
    "feature:poi.park|element:labels.text.fill|color:0x7f9472",
    "feature:landscape.man_made|element:geometry|color:0xe7e1d6",
    "feature:transit|visibility:off",
    "feature:transit.station.rail|element:labels.text|visibility:on",
    "feature:road|element:geometry|color:0xffffff",
    "feature:road|element:labels|visibility:off",
    "feature:road.highway|element:geometry|color:0xf6eee2",
    "feature:water|element:geometry|color:0xcad8dd",
    "feature:administrative|element:geometry|visibility:off",
    "feature:administrative.neighborhood|element:labels|visibility:off",
]


# ---------- EXIF ----------
def _dms(v):
    d, m, s = (float(x) for x in v)
    return d + m / 60 + s / 3600


def read_meta(path):
    exif = Image.open(path).getexif()
    gps = exif.get_ifd(0x8825)
    lat = lon = None
    if gps and 2 in gps and 4 in gps:
        lat, lon = _dms(gps[2]), _dms(gps[4])
        if gps.get(1) == "S":
            lat = -lat
        if gps.get(3) == "W":
            lon = -lon
    taken = exif.get_ifd(0x8769).get(36867) or exif.get(306)
    return {"file": path, "lat": lat, "lon": lon, "taken": str(taken) if taken else "", "model": exif.get(272) or ""}


def read_photos(photos_dir):
    if not photos_dir.exists():
        return []
    photos = [read_meta(p) for p in sorted(photos_dir.iterdir()) if p.suffix.lower() in PHOTO_EXT]
    return sorted(photos, key=lambda p: (p["taken"], p["file"].name))


def dist_m(a, b):
    k = 111_320
    return math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * k * math.cos(math.radians(a[0])))


def cluster(photos, radius):
    """반경 radius(m) 안의 사진을 한 장소로 묶는다 (촬영순, 묶음 중심 기준 단순 병합)."""
    groups = []
    for p in photos:
        if p["lat"] is None:
            continue
        for g in groups:
            if dist_m(g["center"], (p["lat"], p["lon"])) <= radius:
                g["photos"].append(p)
                n = len(g["photos"])
                g["center"] = (sum(x["lat"] for x in g["photos"]) / n, sum(x["lon"] for x in g["photos"]) / n)
                break
        else:
            groups.append({"center": (p["lat"], p["lon"]), "photos": [p]})
    return groups


def resolve_places(cfg, base):
    """thumbnail.yaml 의 places 를 좌표·대표사진으로 확정. places 가 비어 있으면 자동 클러스터 사용."""
    photos = {p["file"].name: p for p in read_photos(base / cfg.get("photos_dir", "photos"))}
    places = cfg.get("places") or []
    if not places:
        groups = cluster(list(photos.values()), cfg.get("cluster_radius_m", 40))
        places = [{"photos": [p["file"].name for p in g["photos"]]} for g in groups]
    out = []
    for i, pl in enumerate(places):
        files = pl.get("photos") or []
        missing = [f for f in files + ([pl["cover"]] if pl.get("cover") else []) if f not in photos]
        if missing:
            raise SystemExit(f"places[{i}] 사진 없음: {missing}")
        if pl.get("lat") is not None:
            lat, lon = pl["lat"], pl["lon"]
        else:
            pts = [(photos[f]["lat"], photos[f]["lon"]) for f in files if photos[f]["lat"] is not None]
            if not pts:
                raise SystemExit(f"places[{i}] 좌표 없음: GPS 있는 사진을 넣거나 lat/lon 지정")
            lat, lon = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        cover = pl.get("cover") or (files[0] if files else None)
        if not cover:
            raise SystemExit(f"places[{i}] 대표사진(cover) 없음")
        out.append({"lat": lat, "lon": lon, "cover": photos[cover]["file"], "name": pl.get("name", ""),
                    "en": pl.get("en", ""), "desc": pl.get("desc", "")})
    if len(out) > MAX_PLACES:
        raise SystemExit(f"장소 {len(out)}곳: 최대 {MAX_PLACES}곳. places 를 줄이세요")
    return out


# ---------- 지도 투영 (Web Mercator) ----------
def world(lat, lon, z):
    n = 256 * 2 ** z
    siny = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n


def unworld(x, y, z):
    n = 256 * 2 ** z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n)))), x / n * 360 - 180


def _shift(rel, bounds):
    """모든 rel[i]+t 가 bounds[i]=(lo,hi) 안에 드는 t (가능 범위의 가운데). 불가능하면 None."""
    lo = max(b[0] - r for r, b in zip(rel, bounds))
    hi = min(b[1] - r for r, b in zip(rel, bounds))
    return (lo + hi) / 2 if lo <= hi else None


def fit_view(places, landmark, cfg_map):
    """핀은 PIN_BOX 안, 타워는 꼭대기까지 패널 안에 들어오는 최대 줌과 지도 중심."""
    pts = [(p["lat"], p["lon"]) for p in places] + [(landmark["lat"], landmark["lon"])]
    x0, y0, x1, y1 = PIN_BOX
    xb = [(x0, x1)] * len(pts)
    yb = [(y0, y1)] * len(places) + [(PANEL[1] + TOWER_H + 30, y1)]
    pcx, pcy = (PANEL[0] + PANEL[2]) / 2, (PANEL[1] + PANEL[3]) / 2
    for z in [cfg_map["zoom"]] if cfg_map.get("zoom") else range(18, 9, -1):
        ws = [world(*p, z) for p in pts]
        tx = _shift([w[0] * MAP_SCALE for w in ws], xb)
        ty = _shift([w[1] * MAP_SCALE for w in ws], yb)
        if tx is None or ty is None:
            if cfg_map.get("zoom"):
                raise SystemExit("지정한 map.zoom 으로는 핀이 다 안 들어감")
            continue
        if cfg_map.get("center"):
            return tuple(cfg_map["center"]), z
        return unworld((pcx - tx) / MAP_SCALE, (pcy - ty) / MAP_SCALE, z), z
    raise SystemExit("스팟 범위가 너무 넓음")


def to_px(lat, lon, center, z):
    wx, wy = world(lat, lon, z)
    cx, cy = world(*center, z)
    return (PANEL[0] + PANEL[2]) / 2 + (wx - cx) * MAP_SCALE, (PANEL[1] + PANEL[3]) / 2 + (wy - cy) * MAP_SCALE


def fetch_map(center, z, cfg_map):
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    pw, ph = (PANEL[2] - PANEL[0]) // MAP_SCALE, (PANEL[3] - PANEL[1]) // MAP_SCALE
    params = [("center", f"{center[0]:.6f},{center[1]:.6f}"), ("zoom", z), ("size", f"{pw}x{ph}"),
              ("scale", MAP_SCALE), ("language", cfg_map.get("language", "ko")), ("format", "png")]
    params += [("style", s) for s in cfg_map.get("styles", MAP_STYLES)]
    cached = CACHE / f"map_{hashlib.sha1(json.dumps(params).encode()).hexdigest()[:16]}.png"
    if not cached.exists():
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params + [("key", key)], timeout=30)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            raise SystemExit(f"Static Maps 요청 실패 ({r.status_code}): {r.text[:300]}")
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
    return cached


# ---------- 카드 배치 ----------
def _attach(slot):
    x, y = slot
    return (x + CARD_W if x < W / 2 else x), y + CARD_H * 0.36


def _crosses(a, b):
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
    p1, p2, p3, p4 = a[:2], a[2:], b[:2], b[2:]
    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def assign_cards(pins, tower):
    """좌우 6칸 중 지시선 길이·교차가 최소인 칸 조합을 전수 탐색."""
    slots = [(x, y) for x in CARD_X for y in CARD_Y]
    best, best_cost = None, math.inf
    for perm in itertools.permutations(slots, len(pins)):
        leaders = [(*pins[i], *_attach(s)) for i, s in enumerate(perm)]
        cost = sum(math.hypot(l[2] - l[0], l[3] - l[1]) for l in leaders)
        cost += 1500 * sum(_crosses(a, b) for a, b in itertools.combinations(leaders, 2))
        cost += 200 * sum(_crosses(l, (*tower, *p)) for l in leaders for p in pins if (l[0], l[1]) != p)
        if cost < best_cost:
            best, best_cost = perm, cost
    return best


# ---------- 렌더 ----------
def img_uri(path, size):
    img = ImageOps.fit(ImageOps.exif_transpose(Image.open(path)).convert("RGB"), size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def build_html(cfg, places, center, z, map_path):
    e = html.escape
    lm = cfg["landmark"]
    tx, ty = to_px(lm["lat"], lm["lon"], center, z)
    pins = [to_px(p["lat"], p["lon"], center, z) for p in places]
    slots = assign_cards(pins, (tx, ty))
    css = (HERE / "style.css").read_text(encoding="utf-8")
    tower = (HERE / "tokyo_tower.svg").read_text(encoding="utf-8")
    if map_path:
        bg = f'<img class="map" src="data:image/png;base64,{base64.b64encode(map_path.read_bytes()).decode()}">'
    else:
        bg = '<div class="map preview"></div>'

    radials, leaders, items = [], [], []
    for i, (p, (px, py), slot) in enumerate(zip(places, pins, slots)):
        ax, ay = _attach(slot)
        d = math.hypot(px - tx, py - ty) or 1  # 타워 다리 바깥에서 출발 (아치 사이로 점선이 비치지 않게)
        r = min(TOWER_W * 0.45, d * 0.5)
        radials.append(f'<line x1="{tx + (px - tx) / d * r:.1f}" y1="{ty + (py - ty) / d * r:.1f}" x2="{px:.1f}" y2="{py:.1f}"/>')
        leaders.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{ax:.1f}" y2="{ay:.1f}"/>')
        items.append(
            f'<figure class="card" style="left:{slot[0]}px;top:{slot[1]}px;width:{CARD_W}px;height:{CARD_H}px">'
            f'<img src="{img_uri(p["cover"], (CARD_W * 2 - 40, 320))}">'
            f'<figcaption><b><i>{i + 1:02d}</i>{e(p["name"])}</b><small>{e(p["en"])}</small><span>{e(p["desc"])}</span></figcaption>'
            f'</figure><div class="pin" style="left:{px:.1f}px;top:{py:.1f}px">{i + 1}</div>')

    badge = cfg.get("badge") or ["PHOTO SPOTS", "{n:02d} places"]
    lx, ly = tx + 64, ty - TOWER_H + 44
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=block" rel="stylesheet">
<style>{css}</style></head><body><div class="canvas">
<header>
  <div class="eyebrow">{e(cfg.get("eyebrow", ""))}</div>
  <h1>{e(cfg.get("title", ""))}</h1>
  <p>{e(cfg.get("subtitle", ""))}</p>
</header>
<aside class="badge"><b>{e(badge[0])}</b><span>{e(badge[1].format(n=len(places)))}</span></aside>
<div class="panel" style="left:{PANEL[0]}px;top:{PANEL[1]}px;width:{PANEL[2] - PANEL[0]}px;height:{PANEL[3] - PANEL[1]}px">{bg}<div class="north">N ↑</div></div>
<svg class="lines" viewBox="0 0 {W} {H}"><g class="radial">{''.join(radials)}</g><g class="leader">{''.join(leaders)}</g></svg>
<div class="halo" style="left:{tx:.1f}px;top:{ty:.1f}px"></div>
<div class="tower" style="left:{tx - TOWER_W / 2:.1f}px;top:{ty - TOWER_H:.1f}px;width:{TOWER_W}px;height:{TOWER_H}px">{tower}</div>
<div class="tower-label" style="left:{lx:.1f}px;top:{ly:.1f}px"><b>{e(lm.get("name", ""))}</b><small>{e(lm.get("sub", ""))}</small></div>
{''.join(items)}
</div></body></html>"""


def render(cfg_path):
    from playwright.sync_api import sync_playwright

    cfg_path = Path(cfg_path).resolve()
    base = cfg_path.parent
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    places = resolve_places(cfg, base)
    if not places:
        raise SystemExit("장소 없음. scan 으로 사진 GPS를 확인하세요")
    center, z = fit_view(places, cfg["landmark"], cfg.get("map") or {})
    map_path = fetch_map(center, z, cfg.get("map") or {})
    if not map_path:
        print("! GOOGLE_MAPS_API_KEY 없음 → 미리보기 배경으로 렌더링")
    CACHE.mkdir(parents=True, exist_ok=True)
    page_file = CACHE / "page.html"
    page_file.write_text(build_html(cfg, places, center, z, map_path), encoding="utf-8")
    out = base / cfg.get("output", "out/thumbnail.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        if not exe and Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").exists():
            exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.goto(page_file.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(out))
        browser.close()
    print(f"zoom={z} center={center[0]:.5f},{center[1]:.5f} places={len(places)} → {out}")


def scan(cfg_path):
    cfg_path = Path(cfg_path).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    photos = read_photos(cfg_path.parent / cfg.get("photos_dir", "photos"))
    if not photos:
        print("사진 없음:", cfg_path.parent / cfg.get("photos_dir", "photos"))
        return
    for p in photos:
        loc = f'{p["lat"]:.6f},{p["lon"]:.6f}' if p["lat"] is not None else "GPS 없음"
        print(f'{p["file"].name:28} {loc:24} {p["taken"]:20} {p["model"]}')
    radius = cfg.get("cluster_radius_m", 40)
    lm = cfg.get("landmark")
    print(f"\n장소 묶음 (반경 {radius}m):")
    for i, g in enumerate(cluster(photos, radius), 1):
        far = f'  타워까지 {dist_m(g["center"], (lm["lat"], lm["lon"])):.0f}m' if lm else ""
        print(f'  {i}. {g["center"][0]:.6f},{g["center"][1]:.6f}{far}  ← {", ".join(p["file"].name for p in g["photos"])}')
    no_gps = [p["file"].name for p in photos if p["lat"] is None]
    if no_gps:
        print(f"\n! GPS 없는 사진 {len(no_gps)}장: {', '.join(no_gps)}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("scan", "render"):
        raise SystemExit(__doc__)
    {"scan": scan, "render": render}[sys.argv[1]](sys.argv[2])
