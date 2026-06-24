from __future__ import annotations
# ════════════════════════════════════════════════════════════════════════
#  SenseOptics Метролог — Replit/mobile friendly версия
#  Лёгкий геометрический метрологический инструмент:
#  линия/диаметр, окружность, площадь, ручной счётчик включений,
#  калибровка по двум кликам, маска включений, экспорт PNG/JSON/XLSX/CSV.
# ════════════════════════════════════════════════════════════════════════
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image as PILImage, ImageOps

try:
    from streamlit_image_coordinates import streamlit_image_coordinates as st_imgcoords
    HAS_CLICK = True
except Exception:
    st_imgcoords = None
    HAS_CLICK = False

try:
    from openpyxl import Workbook
    HAS_XLSX = True
except Exception:
    HAS_XLSX = False

st.set_page_config(
    page_title="SenseOptics Метролог",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

MODES = ["Линия / диаметр", "Окружность", "Площадь", "Включения", "Калибровка (2 клика)"]

GREEN = (40, 180, 70)
BLUE = (40, 120, 230)
ORANGE = (255, 150, 0)
RED = (220, 50, 50)
BLACK = (20, 20, 20)


# ════════════════════════════════════════════════════════════════════════
#  Состояние
# ════════════════════════════════════════════════════════════════════════
def _init_state() -> None:
    defaults = {
        "meas": [],
        "pending": [],
        "inclusions": [],
        "calib_pts": [],
        "px_per_mm": None,
        "last_click_id": None,
        "image_state_key": None,
        "fullscreen_mode": False,
        "upload_bytes": None,
        "upload_name": None,
        "mask": None,
        "mask_overlay": None,
        "mask_stats": None,
        "mask_settings": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_mask() -> None:
    st.session_state.mask = None
    st.session_state.mask_overlay = None
    st.session_state.mask_stats = None
    st.session_state.mask_settings = None


def reset_work(reset_calibration: bool = True, reset_mask_data: bool = True) -> None:
    st.session_state.meas = []
    st.session_state.pending = []
    st.session_state.inclusions = []
    st.session_state.calib_pts = []
    st.session_state.last_click_id = None
    if reset_calibration:
        st.session_state.px_per_mm = None
    if reset_mask_data:
        reset_mask()


_init_state()


# ════════════════════════════════════════════════════════════════════════
#  CSS / мобильный вид
# ════════════════════════════════════════════════════════════════════════
st.markdown(
    """
<style>
    section[data-testid="stSidebar"] {display: none !important;}
    [data-testid="collapsedControl"] {display: none !important;}
    .block-container {
        max-width: 1260px;
        padding-top: 1.0rem;
        padding-left: clamp(0.45rem, 2vw, 2rem);
        padding-right: clamp(0.45rem, 2vw, 2rem);
    }
    div[data-testid="stFileUploader"] section {
        border-radius: 16px;
        min-height: 86px;
    }
    div.stButton > button, div[data-testid="stDownloadButton"] > button {
        min-height: 42px;
        border-radius: 10px;
        width: 100%;
    }
    div[role="radiogroup"] label {
        padding: 0.25rem 0.25rem;
    }
    .so-hint {
        padding: 0.65rem 0.8rem;
        border-radius: 12px;
        background: rgba(128, 128, 128, 0.08);
        margin: 0.4rem 0 0.7rem 0;
        font-size: 0.92rem;
    }
    .so-small {
        opacity: 0.78;
        font-size: 0.86rem;
    }
    @media (max-width: 760px) {
        .block-container {
            padding-left: 0.35rem;
            padding-right: 0.35rem;
            padding-top: 0.55rem;
        }
        h1 {font-size: 1.35rem !important;}
        h2 {font-size: 1.12rem !important;}
        h3 {font-size: 1.02rem !important;}
        div.stButton > button, div[data-testid="stDownloadButton"] > button {
            min-height: 46px;
            font-size: 0.88rem;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)


def inject_mobile_fullscreen_css(fullscreen: bool) -> None:
    """Полноэкранный мобильный режим.

    В Replit/Chrome нельзя гарантировать настоящий fullscreen и lock ориентации,
    поэтому делаем два слоя:
    1) JS best-effort для requestFullscreen + landscape;
    2) CSS fallback: если телефон в portrait, поворачиваем рабочую область как landscape.
    """
    if not fullscreen:
        return

    st.markdown(
        """
<style>
    header, footer { visibility: hidden !important; height: 0 !important; }
    section[data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none !important;}

    .block-container {
        max-width: 100vw !important;
        padding: 0.22rem 0.28rem 0.28rem 0.28rem !important;
    }
    .so-fs-hint {
        padding: 0.28rem 0.5rem;
        border-radius: 0.55rem;
        background: rgba(80, 140, 255, 0.10);
        font-size: 0.82rem;
        margin: 0.12rem 0 0.28rem 0;
    }
    .so-fs-status {
        opacity: 0.72;
        font-size: 0.78rem;
        margin: 0.10rem 0 0.25rem 0;
    }
    div[data-testid="stHorizontalBlock"] { gap: 0.28rem !important; }
    div.stButton > button, div[data-testid="stDownloadButton"] > button {
        min-height: 34px !important;
        padding: 0.22rem 0.35rem !important;
        font-size: 0.78rem !important;
        border-radius: 8px !important;
    }
    div[role="radiogroup"] label {
        padding: 0.10rem 0.12rem !important;
        font-size: 0.78rem !important;
    }
    iframe[title="streamlit_image_coordinates.streamlit_image_coordinates"] {
        display: block !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }

    /* Если телефон физически остался вертикальным — имитируем landscape. */
    @media screen and (max-width: 900px) and (orientation: portrait) {
        html, body { overflow: hidden !important; }
        .block-container {
            position: fixed !important;
            left: 0 !important;
            top: 0 !important;
            width: 100vh !important;
            max-width: 100vh !important;
            height: 100vw !important;
            overflow: auto !important;
            transform: rotate(90deg) translateY(-100vw);
            transform-origin: top left;
            z-index: 999999 !important;
            padding: 0.18rem 0.25rem !important;
        }
    }
</style>
        """,
        unsafe_allow_html=True,
    )


def request_browser_landscape(fullscreen: bool) -> None:
    """Best-effort: настоящий fullscreen/landscape может быть заблокирован браузером или iframe Replit."""
    if not fullscreen:
        return
    components.html(
        """
<button id="soFsBtn" style="
  width: 100%; height: 30px; border-radius: 9px; border: 1px solid rgba(0,0,0,.15);
  background: rgba(255,255,255,.92); font-size: 12px;">⛶ native fullscreen / landscape</button>
<script>
const btn = document.getElementById('soFsBtn');
async function goFs(){
  try {
    const doc = window.parent.document;
    const el = doc.documentElement;
    if (!doc.fullscreenElement && el.requestFullscreen) {
      await el.requestFullscreen().catch(() => {});
    }
    const so = window.parent.screen && window.parent.screen.orientation;
    if (so && so.lock) {
      await so.lock('landscape').catch(() => {});
    }
  } catch(e) {}
}
btn.addEventListener('click', goFs);
setTimeout(goFs, 250);
</script>
        """,
        height=34,
        width=260,
    )


# ════════════════════════════════════════════════════════════════════════
#  Геометрия
# ════════════════════════════════════════════════════════════════════════
def dist(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def poly_area(pts) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def poly_perimeter(pts) -> float:
    if len(pts) < 2:
        return 0.0
    return sum(dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def measure_metrics(m: Dict[str, Any], ppm: Optional[float]) -> Dict[str, Any]:
    t = m["type"]
    out: Dict[str, Any] = {"type": t}
    if t == "line":
        d = dist(m["p1"], m["p2"])
        out["length_px"] = round(d, 2)
        out["length_mm"] = round(d / ppm, 3) if ppm else None
    elif t == "circle":
        r = dist(m["center"], m["edge"])
        out["radius_px"] = round(r, 2)
        out["diameter_px"] = round(2 * r, 2)
        out["circumference_px"] = round(2 * math.pi * r, 2)
        out["area_px2"] = round(math.pi * r * r, 1)
        if ppm:
            out["radius_mm"] = round(r / ppm, 3)
            out["diameter_mm"] = round(2 * r / ppm, 3)
            out["circumference_mm"] = round(2 * math.pi * r / ppm, 3)
            out["area_mm2"] = round(math.pi * r * r / (ppm * ppm), 4)
    elif t == "polygon":
        a = poly_area(m["pts"])
        p = poly_perimeter(m["pts"])
        out["area_px2"] = round(a, 1)
        out["perimeter_px"] = round(p, 2)
        out["n_vertices"] = len(m["pts"])
        if ppm:
            out["area_mm2"] = round(a / (ppm * ppm), 4)
            out["perimeter_mm"] = round(p / ppm, 3)
    return out


def short_label(m: Dict[str, Any], ppm: Optional[float]) -> str:
    mm = measure_metrics(m, ppm)
    if m["type"] == "line":
        return f"{mm['length_mm']} мм" if ppm else f"{mm['length_px']} px"
    if m["type"] == "circle":
        return f"D {mm['diameter_mm']} мм" if ppm else f"D {mm['diameter_px']} px"
    if m["type"] == "polygon":
        return f"{mm['area_mm2']} мм²" if ppm else f"{mm['area_px2']} px²"
    return ""


# ════════════════════════════════════════════════════════════════════════
#  Отрисовка
# ════════════════════════════════════════════════════════════════════════
def _put(img, text, org, color) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)


def draw_overlay(work_bgr, meas, pending, inclusions, calib_pts, ppm, mode) -> np.ndarray:
    rgb = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB).copy()

    for m in meas:
        if m["type"] == "line":
            p1, p2 = tuple(m["p1"]), tuple(m["p2"])
            cv2.line(rgb, p1, p2, GREEN, 2)
            cv2.circle(rgb, p1, 4, GREEN, -1)
            cv2.circle(rgb, p2, 4, GREEN, -1)
            mx, my = (p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2
            _put(rgb, short_label(m, ppm), (mx + 5, my - 7), GREEN)
        elif m["type"] == "circle":
            c = tuple(m["center"])
            r = int(round(dist(m["center"], m["edge"])))
            cv2.circle(rgb, c, r, BLUE, 2)
            cv2.circle(rgb, c, 4, BLUE, -1)
            cv2.line(rgb, c, tuple(m["edge"]), BLUE, 1)
            _put(rgb, short_label(m, ppm), (c[0] + 5, max(15, c[1] - r - 7)), BLUE)
        elif m["type"] == "polygon":
            pts = np.array(m["pts"], np.int32)
            cv2.polylines(rgb, [pts], True, ORANGE, 2)
            for p in m["pts"]:
                cv2.circle(rgb, tuple(p), 4, ORANGE, -1)
            cx = int(np.mean([p[0] for p in m["pts"]]))
            cy = int(np.mean([p[1] for p in m["pts"]]))
            _put(rgb, short_label(m, ppm), (cx + 5, cy), ORANGE)

    for i, (x, y) in enumerate(inclusions):
        cv2.circle(rgb, (x, y), 8, RED, 2)
        _put(rgb, str(i + 1), (x + 10, y - 6), RED)

    for p in pending:
        cv2.drawMarker(rgb, tuple(p), BLACK, cv2.MARKER_CROSS, 14, 2)
    if mode == "Площадь" and len(pending) >= 2:
        cv2.polylines(rgb, [np.array(pending, np.int32)], False, ORANGE, 1)

    for i, (x, y) in enumerate(calib_pts):
        cv2.circle(rgb, (x, y), 9, ORANGE, 2)
        _put(rgb, f"C{i + 1}", (x + 10, y - 6), ORANGE)
    if len(calib_pts) == 2:
        cv2.line(rgb, tuple(calib_pts[0]), tuple(calib_pts[1]), ORANGE, 1)

    return rgb


# ════════════════════════════════════════════════════════════════════════
#  Загрузка / декодирование
# ════════════════════════════════════════════════════════════════════════
def file_fingerprint(data: bytes, name: str) -> str:
    h = hashlib.sha1()
    h.update(name.encode("utf-8", errors="ignore"))
    h.update(str(len(data)).encode())
    h.update(data[:1024 * 1024])
    return h.hexdigest()[:14]


@st.cache_data(show_spinner=False)
def decode_image(data: bytes, name: str = "") -> tuple[Optional[np.ndarray], Optional[str]]:
    """Возвращает BGR OpenCV. Сначала OpenCV, затем Pillow fallback для проблемных TIFF/PNG."""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is not None:
        return img, None

    try:
        pil = PILImage.open(io.BytesIO(data))
        pil = ImageOps.exif_transpose(pil)
        pil = pil.convert("RGB")
        rgb = np.array(pil)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), None
    except Exception as e:
        return None, f"Не удалось прочитать изображение {name!r}: {e}"


@st.cache_data(show_spinner=False)
def cached_downscale(img: np.ndarray, max_w: int) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    if w <= max_w:
        return img.copy(), 1.0
    scale = max_w / float(w)
    new_size = (int(round(w * scale)), int(round(h * scale)))
    return cv2.resize(img, new_size, interpolation=cv2.INTER_AREA), scale


# ════════════════════════════════════════════════════════════════════════
#  Маска тёмных/светлых включений
# ════════════════════════════════════════════════════════════════════════
def _odd(v: int) -> int:
    v = int(v)
    return v if v % 2 == 1 else v + 1


def build_inclusion_mask(
    work_bgr: np.ndarray,
    threshold_mode: str = "Otsu",
    manual_threshold: int = 90,
    min_area: int = 4,
    max_area: int = 0,
    detect_bright: bool = False,
    blur_size: int = 3,
    close_iter: int = 1,
) -> np.ndarray:
    gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
    if blur_size > 1:
        gray = cv2.GaussianBlur(gray, (_odd(blur_size), _odd(blur_size)), 0)

    if threshold_mode == "Otsu":
        flag = cv2.THRESH_BINARY if detect_bright else cv2.THRESH_BINARY_INV
        _, mask = cv2.threshold(gray, 0, 255, flag + cv2.THRESH_OTSU)
    else:
        if detect_bright:
            mask = (gray >= int(manual_threshold)).astype(np.uint8) * 255
        else:
            mask = (gray <= int(manual_threshold)).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    if close_iter > 0:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=int(close_iter))

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    for label in range(1, n):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < int(min_area):
            continue
        if max_area and area > int(max_area):
            continue
        clean[lab == label] = 255
    return clean


def make_mask_overlay(work_bgr: np.ndarray, mask: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    rgb = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB).copy()
    color = np.zeros_like(rgb)
    color[:, :] = RED
    m = mask > 0
    rgb[m] = cv2.addWeighted(rgb, 1.0 - alpha, color, alpha, 0)[m]
    return rgb


def mask_stats(mask: np.ndarray, ppm: Optional[float]) -> Dict[str, Any]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = [int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, n)]
    total_px = int(np.sum(mask > 0))
    out: Dict[str, Any] = {
        "n_objects": n - 1,
        "total_mask_px": total_px,
        "area_fraction": round(float(np.mean(mask > 0)), 6),
        "max_object_area_px": int(max(areas)) if areas else 0,
        "mean_object_area_px": round(float(np.mean(areas)), 2) if areas else 0.0,
    }
    if ppm:
        out["total_area_mm2"] = round(total_px / (ppm * ppm), 5)
        out["max_object_area_mm2"] = round(out["max_object_area_px"] / (ppm * ppm), 5)
        out["mean_object_area_mm2"] = round(out["mean_object_area_px"] / (ppm * ppm), 5)
    return out


def gray_png_bytes(gray: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", gray)
    return buf.tobytes() if ok else b""


# ════════════════════════════════════════════════════════════════════════
#  Экспорт
# ════════════════════════════════════════════════════════════════════════
class _NpEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def safe_stem(name: str) -> str:
    return Path(name).stem.replace(" ", "_") or "image"


def build_export(src, W, H, ppm, meas, inclusions, scale, mask_info=None) -> Dict[str, Any]:
    return {
        "source": src,
        "working_size_px": [W, H],
        "display_scale_from_original": scale,
        "px_per_mm_working_image": ppm,
        "measurements": [{**measure_metrics(m, ppm), "raw": m} for m in meas],
        "inclusions": {
            "count": len(inclusions),
            "points": [{"n": i + 1, "x": int(x), "y": int(y)} for i, (x, y) in enumerate(inclusions)],
        },
        "mask": mask_info,
    }


def safe_json(d) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2, cls=_NpEncoder)


def build_xlsx(export) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Замеры"
    ws.append([
        "Тип", "Длина_px", "Длина_mm", "Диаметр_px", "Диаметр_mm",
        "Окружность_px", "Окружность_mm", "Периметр_px", "Периметр_mm",
        "Площадь_px2", "Площадь_mm2",
    ])
    for m in export["measurements"]:
        ws.append([
            m.get("type"),
            m.get("length_px"), m.get("length_mm"),
            m.get("diameter_px"), m.get("diameter_mm"),
            m.get("circumference_px"), m.get("circumference_mm"),
            m.get("perimeter_px"), m.get("perimeter_mm"),
            m.get("area_px2"), m.get("area_mm2"),
        ])
    ws_info = wb.create_sheet("Инфо")
    for k, v in [
        ("source", export["source"]),
        ("working_w_px", export["working_size_px"][0]),
        ("working_h_px", export["working_size_px"][1]),
        ("display_scale_from_original", export["display_scale_from_original"]),
        ("px_per_mm_working_image", export["px_per_mm_working_image"]),
    ]:
        ws_info.append([k, v])

    ws_inc = wb.create_sheet("Включения")
    ws_inc.append(["№", "x", "y"])
    for p in export["inclusions"]["points"]:
        ws_inc.append([p["n"], p["x"], p["y"]])
    ws_inc.append([])
    ws_inc.append(["Всего", export["inclusions"]["count"]])

    ws_mask = wb.create_sheet("Маска")
    mask_info = export.get("mask") or {}
    if mask_info:
        for k, v in mask_info.items():
            ws_mask.append([k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v])
    else:
        ws_mask.append(["mask", "not built"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv(export) -> str:
    lines = [
        "type,length_px,length_mm,diameter_px,diameter_mm,circumference_px,"
        "circumference_mm,perimeter_px,perimeter_mm,area_px2,area_mm2"
    ]
    keys = [
        "type", "length_px", "length_mm", "diameter_px", "diameter_mm",
        "circumference_px", "circumference_mm", "perimeter_px", "perimeter_mm",
        "area_px2", "area_mm2",
    ]
    for m in export["measurements"]:
        lines.append(",".join(str(m.get(k, "")) for k in keys))
    lines.append("")
    lines.append(f"inclusions_count,{export['inclusions']['count']}")
    lines.append(f"px_per_mm_working_image,{export['px_per_mm_working_image']}")
    if export.get("mask"):
        lines.append("")
        for k, v in export["mask"].items():
            lines.append(f"mask_{k},{v}")
    return "\n".join(lines)


def png_bytes(rgb) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return buf.tobytes() if ok else b""


# ════════════════════════════════════════════════════════════════════════
#  Клики
# ════════════════════════════════════════════════════════════════════════
def clamp_click(x: int, y: int, W: int, H: int) -> tuple[int, int]:
    return max(0, min(W - 1, x)), max(0, min(H - 1, y))


def handle_click(x: int, y: int, mode: str) -> None:
    if mode == "Линия / диаметр":
        st.session_state.pending.append([x, y])
        if len(st.session_state.pending) == 2:
            p1, p2 = st.session_state.pending
            st.session_state.meas.append({"type": "line", "p1": p1, "p2": p2})
            st.session_state.pending = []
    elif mode == "Окружность":
        st.session_state.pending.append([x, y])
        if len(st.session_state.pending) == 2:
            c, e = st.session_state.pending
            st.session_state.meas.append({"type": "circle", "center": c, "edge": e})
            st.session_state.pending = []
    elif mode == "Площадь":
        st.session_state.pending.append([x, y])
    elif mode == "Включения":
        st.session_state.inclusions.append((x, y))
    elif mode == "Калибровка (2 клика)":
        if len(st.session_state.calib_pts) >= 2:
            st.session_state.calib_pts = [[x, y]]
        else:
            st.session_state.calib_pts.append([x, y])


def undo_last_point(mode: str) -> bool:
    if mode == "Калибровка (2 клика)" and st.session_state.calib_pts:
        st.session_state.calib_pts.pop()
        return True
    if st.session_state.pending:
        st.session_state.pending.pop()
        return True
    if mode == "Включения" and st.session_state.inclusions:
        st.session_state.inclusions.pop()
        return True
    return False


# ════════════════════════════════════════════════════════════════════════
#  UI: шапка и загрузка
# ════════════════════════════════════════════════════════════════════════
fullscreen = bool(st.session_state.fullscreen_mode)
inject_mobile_fullscreen_css(fullscreen)
request_browser_landscape(fullscreen)

if not fullscreen:
    st.title("📐 SenseOptics Метролог")
    st.caption("Лёгкий режим для Replit/free: замеры, калибровка, включения, маска и экспорт.")

    with st.container(border=True):
        st.markdown("**1. Загрузка изображения**")
        main_up = st.file_uploader(
            "Перетащите файл сюда или нажмите Browse files",
            type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
            key="main_upload",
            help="Загрузка находится в основной области. Боковая панель не используется.",
        )
        sample = Path(__file__).parent / "sample" / "demo_template.png"
        use_sample = False
        if main_up is None and sample.exists() and st.session_state.upload_bytes is None:
            use_sample = st.checkbox("Использовать demo_template.png", value=True)

        if main_up is not None:
            st.session_state.upload_bytes = main_up.getvalue()
            st.session_state.upload_name = main_up.name
        elif use_sample:
            st.session_state.upload_bytes = sample.read_bytes()
            st.session_state.upload_name = sample.name
else:
    st.markdown(
        "<div class='so-fs-hint'>📱 Полноэкранный landscape-режим: оставлен только холст замеров. "
        "Если браузер не повернулся сам — поверните телефон горизонтально или нажмите маленькую кнопку fullscreen.</div>",
        unsafe_allow_html=True,
    )

img_bgr = None
src_name = st.session_state.upload_name
file_id = None
scale = 1.0

data = st.session_state.upload_bytes
if data is not None and src_name:
    file_id = file_fingerprint(data, src_name)
    img_bgr, err = decode_image(data, src_name)
    if err:
        st.error(err)
        st.stop()

if img_bgr is None:
    if fullscreen:
        st.warning("Изображение не найдено в состоянии сессии. Вернитесь в обычный режим и загрузите файл заново.")
        if st.button("↩️ Обычный режим"):
            st.session_state.fullscreen_mode = False
            st.rerun()
    else:
        st.info("Загрузите изображение. На телефоне и в Replit надёжнее пользоваться центральной областью загрузки.")
    st.stop()

orig_h, orig_w = img_bgr.shape[:2]

# ════════════════════════════════════════════════════════════════════════
#  UI: настройки работы
# ════════════════════════════════════════════════════════════════════════
if fullscreen:
    w_max = max(500, min(2400, int(orig_w)))
    default_w = min(w_max, max(1200, min(int(orig_w), 1600)))
    t1, t2, t3, t4 = st.columns([0.95, 1.45, 2.1, 0.95])
    if t1.button("↩️ Обычный", use_container_width=True):
        st.session_state.fullscreen_mode = False
        st.rerun()
    max_w = t2.slider(
        "Ширина, px",
        min_value=500,
        max_value=w_max,
        value=default_w,
        step=50,
        help="В fullscreen можно ставить 1200–1600 px, чтобы фото занимало почти весь экран телефона.",
        key="work_width_fullscreen",
    )
    with t3:
        mode = st.radio("Режим", MODES, horizontal=True, label_visibility="collapsed", key="mode")
    if t4.button("🗑 Очистить", use_container_width=True):
        reset_work(reset_calibration=False, reset_mask_data=False)
        st.rerun()
else:
    with st.container(border=True):
        st.markdown("**2. Настройки и режим**")
        fs_now = st.toggle("📱 Полноэкранный landscape-режим замеров", key="fullscreen_mode")
        if fs_now:
            st.rerun()

        w_max = max(300, min(2400, int(orig_w)))
        default_w = min(900, w_max)
        c_width, c_ppm, c_ppm_btn, c_reset_ppm = st.columns([1.3, 1.0, 0.7, 0.7])
        max_w = c_width.slider(
            "Рабочая ширина, px",
            min_value=300,
            max_value=w_max,
            value=default_w,
            step=50,
            help="При изменении ширины замеры сбрасываются, чтобы масштаб не сломался.",
            key="work_width_normal",
        )
        ppm_value = float(st.session_state.px_per_mm or 0.0)
        ppm_in = c_ppm.number_input(
            "px/mm для рабочей картинки",
            min_value=0.0,
            value=ppm_value,
            step=0.5,
            format="%.4f",
            help="Можно ввести вручную или откалибровать двумя кликами.",
        )
        if c_ppm_btn.button("Применить px/mm") and ppm_in > 0:
            st.session_state.px_per_mm = float(ppm_in)
            st.rerun()
        if c_reset_ppm.button("Сброс px/mm"):
            st.session_state.px_per_mm = None
            st.session_state.calib_pts = []
            st.rerun()

        mode = st.radio("Режим", MODES, horizontal=True, key="mode")

work, scale = cached_downscale(img_bgr, int(max_w))
H, W = work.shape[:2]
gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

image_state_key = f"{file_id}:{W}x{H}"
if st.session_state.image_state_key != image_state_key:
    reset_work(reset_calibration=True, reset_mask_data=True)
    st.session_state.image_state_key = image_state_key

ppm = st.session_state.px_per_mm

hints = {
    "Линия / диаметр": "Два клика: начало и конец отрезка.",
    "Окружность": "Клик 1 — центр, клик 2 — точка на окружности.",
    "Площадь": "Кликайте вершины контура, затем нажмите «Замкнуть площадь».",
    "Включения": "Клик по включению добавляет точку в ручной счётчик.",
    "Калибровка (2 клика)": "Два клика по концам эталона, затем введите известную длину.",
}
st.markdown(f"<div class='so-hint'>👆 {hints[mode]}</div>", unsafe_allow_html=True)

status_text = (
    f"Файл: **{src_name}** · оригинал: **{orig_w}×{orig_h}px** · "
    f"рабочее: **{W}×{H}px** · scale={scale:.4f} · "
    + (f"калибровка: **{ppm:.4f} px/mm**" if ppm else "калибровка: **не задана**")
)
st.markdown(f"<div class='so-small'>{status_text}</div>", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════
#  UI: центральные кнопки
# ════════════════════════════════════════════════════════════════════════
if fullscreen:
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("↩️ Отменить точку"):
        if undo_last_point(mode):
            st.rerun()
    if b2.button("↩️ Удалить замер") and st.session_state.meas:
        st.session_state.meas.pop()
        st.rerun()
    if b3.button("🗑 Очистить"):
        reset_work(reset_calibration=False, reset_mask_data=False)
        st.rerun()
    debug = b4.checkbox("debug")
else:
    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("↩️ Отменить точку"):
        if undo_last_point(mode):
            st.rerun()
    if b2.button("Сбросить текущие точки"):
        st.session_state.pending = []
        if mode == "Калибровка (2 клика)":
            st.session_state.calib_pts = []
        st.rerun()
    if b3.button("↩️ Удалить последний замер") and st.session_state.meas:
        st.session_state.meas.pop()
        st.rerun()
    if b4.button("🗑 Очистить всё"):
        reset_work(reset_calibration=False, reset_mask_data=False)
        st.rerun()
    debug = b5.checkbox("debug")

# ════════════════════════════════════════════════════════════════════════
#  Холст / клики
# ════════════════════════════════════════════════════════════════════════
overlay = draw_overlay(
    work,
    st.session_state.meas,
    st.session_state.pending,
    st.session_state.inclusions,
    st.session_state.calib_pts,
    ppm,
    mode,
)

if HAS_CLICK:
    # Важно: width=W. Так координаты компонента совпадают с рабочими пикселями изображения.
    val = st_imgcoords(
        PILImage.fromarray(overlay),
        width=W,
        key=f"canvas_{image_state_key}_{mode}_{'fs' if fullscreen else 'normal'}",
    )
    if debug:
        st.write({
            "component_value": val,
            "pending": st.session_state.pending,
            "calib_pts": st.session_state.calib_pts,
            "last_click_id": st.session_state.last_click_id,
        })
    if val is not None and "x" in val and "y" in val:
        x, y = clamp_click(int(round(val["x"])), int(round(val["y"])), W, H)
        event_time = val.get("timestamp", val.get("time", val.get("event_time", "")))
        click_id = f"{image_state_key}:{mode}:{x}:{y}:{event_time}"
        if click_id != st.session_state.last_click_id:
            st.session_state.last_click_id = click_id
            handle_click(x, y, mode)
            st.rerun()
else:
    st.warning(
        "Пакет streamlit-image-coordinates не установлен или не загрузился. "
        "Проверьте requirements.txt и перезапустите Replit. Пока можно вводить координаты вручную."
    )
    st.image(overlay, use_container_width=False)
    mc1, mc2, mc3 = st.columns(3)
    mx = mc1.number_input("x", 0, W - 1, W // 2)
    my = mc2.number_input("y", 0, H - 1, H // 2)
    if mc3.button("Добавить точку"):
        handle_click(int(mx), int(my), mode)
        st.rerun()

st.caption(
    f"Текущие точки: {len(st.session_state.pending)} · "
    f"калибровочные точки: {len(st.session_state.calib_pts)} · "
    f"замеры: {len(st.session_state.meas)} · включения: {len(st.session_state.inclusions)}"
)

# ════════════════════════════════════════════════════════════════════════
#  Действия для площади и калибровки
# ════════════════════════════════════════════════════════════════════════
if mode == "Площадь":
    pc1, pc2 = st.columns(2)
    if pc1.button("✅ Замкнуть площадь"):
        if len(st.session_state.pending) >= 3:
            st.session_state.meas.append({"type": "polygon", "pts": list(st.session_state.pending)})
            st.session_state.pending = []
            st.rerun()
        else:
            st.warning("Нужно минимум 3 точки.")
    if pc2.button("Сбросить точки площади"):
        st.session_state.pending = []
        st.rerun()

if mode == "Калибровка (2 клика)" and len(st.session_state.calib_pts) == 2:
    d_px = dist(st.session_state.calib_pts[0], st.session_state.calib_pts[1])
    st.info(f"Расстояние между точками: **{d_px:.1f} px**")
    kc1, kc2 = st.columns([1, 1])
    known = kc1.number_input("Известная длина эталона, мм", min_value=0.001, value=10.0, step=1.0)
    if kc2.button("Применить калибровку") and known > 0:
        st.session_state.px_per_mm = d_px / known
        st.rerun()

# ════════════════════════════════════════════════════════════════════════
#  Результаты и маска
# ════════════════════════════════════════════════════════════════════════
if not fullscreen:
    res_l, res_r = st.columns([1, 1])

    with res_l:
        with st.expander(f"📏 Замеры ({len(st.session_state.meas)})", expanded=True):
            if st.session_state.meas:
                for i, m in enumerate(st.session_state.meas):
                    mm = measure_metrics(m, ppm)
                    if m["type"] == "line":
                        txt = f"{i + 1}. Линия: {mm['length_px']} px" + (f" = {mm['length_mm']} мм" if ppm else "")
                    elif m["type"] == "circle":
                        txt = f"{i + 1}. Окружность: D={mm['diameter_px']} px, S={mm['area_px2']} px²" + (
                            f" · D={mm['diameter_mm']} мм, S={mm['area_mm2']} мм²" if ppm else ""
                        )
                    else:
                        txt = f"{i + 1}. Площадь: {mm['area_px2']} px² ({mm['n_vertices']} верш.)" + (
                            f" = {mm['area_mm2']} мм²" if ppm else ""
                        )
                    dc1, dc2 = st.columns([5, 1])
                    dc1.write(txt)
                    if dc2.button("✕", key=f"del_meas_{i}"):
                        st.session_state.meas.pop(i)
                        st.rerun()
            else:
                st.caption("Пока нет замеров.")

    with res_r:
        with st.expander(f"🔴 Включения: {len(st.session_state.inclusions)}", expanded=True):
            if st.session_state.inclusions:
                if st.button("🗑 Очистить ручные включения"):
                    st.session_state.inclusions = []
                    st.rerun()
            else:
                st.caption("Ручной счётчик пуст.")

    with st.expander("🧩 Маска включений / отдача маски", expanded=(mode == "Включения")):
        st.caption("Маска строится только по кнопке — она не пересчитывается после каждого клика по изображению.")
        m1, m2, m3 = st.columns(3)
        threshold_mode = m1.radio("Порог", ["Otsu", "Manual"], horizontal=True)
        manual_threshold = m2.slider("Manual threshold", 0, 255, 90, 1)
        detect_bright = m3.checkbox("Искать светлые, не тёмные", value=False)

        m4, m5, m6, m7 = st.columns(4)
        min_area = m4.number_input("min area, px²", min_value=1, max_value=1000000, value=4, step=1)
        max_area = m5.number_input("max area, px² (0 = без лимита)", min_value=0, max_value=10000000, value=0, step=10)
        blur_size = m6.slider("blur", 1, 15, 3, 2)
        close_iter = m7.slider("close", 0, 5, 1, 1)

        mb1, mb2 = st.columns([1, 1])
        if mb1.button("🧩 Построить / обновить маску"):
            mask = build_inclusion_mask(
                work,
                threshold_mode=threshold_mode,
                manual_threshold=manual_threshold,
                min_area=int(min_area),
                max_area=int(max_area),
                detect_bright=detect_bright,
                blur_size=int(blur_size),
                close_iter=int(close_iter),
            )
            overlay_mask = make_mask_overlay(work, mask)
            stats = mask_stats(mask, ppm)
            st.session_state.mask = mask
            st.session_state.mask_overlay = overlay_mask
            st.session_state.mask_stats = stats
            st.session_state.mask_settings = {
                "threshold_mode": threshold_mode,
                "manual_threshold": int(manual_threshold),
                "detect_bright": bool(detect_bright),
                "min_area_px": int(min_area),
                "max_area_px": int(max_area),
                "blur_size": int(blur_size),
                "close_iter": int(close_iter),
            }
            st.rerun()
        if mb2.button("🗑 Сбросить маску"):
            reset_mask()
            st.rerun()

        if st.session_state.mask is not None:
            st.image(st.session_state.mask_overlay, caption="Overlay маски", use_container_width=False)
            st.json(st.session_state.mask_stats)
            md1, md2 = st.columns(2)
            md1.download_button(
                "⬇️ Скачать mask.png",
                gray_png_bytes(st.session_state.mask),
                file_name=f"mask_{safe_stem(src_name)}.png",
                mime="image/png",
            )
            md2.download_button(
                "⬇️ Скачать mask_overlay.png",
                png_bytes(st.session_state.mask_overlay),
                file_name=f"mask_overlay_{safe_stem(src_name)}.png",
                mime="image/png",
            )
        else:
            st.caption("Маска ещё не построена.")
else:
    st.caption("Полноразмерный режим: результаты и экспорт скрыты, чтобы не занимать экран. Выключите режим, чтобы увидеть таблицы, маску и экспорт.")

# ════════════════════════════════════════════════════════════════════════
#  Экспорт
# ════════════════════════════════════════════════════════════════════════
if not fullscreen:
    st.divider()
    with st.expander("⬇️ Экспорт", expanded=True):
        mask_info = None
        if st.session_state.mask_stats is not None:
            mask_info = {
                "stats": st.session_state.mask_stats,
                "settings": st.session_state.mask_settings,
                "files": ["mask.png", "mask_overlay.png"],
            }
        export = build_export(src_name, W, H, ppm, st.session_state.meas, st.session_state.inclusions, scale, mask_info)
        annotated = draw_overlay(
            work,
            st.session_state.meas,
            [],
            st.session_state.inclusions,
            st.session_state.calib_pts,
            ppm,
            mode,
        )

        e1, e2, e3 = st.columns(3)
        e1.download_button(
            "🖼 Изображение с замерами PNG",
            png_bytes(annotated),
            file_name=f"annotated_{safe_stem(src_name)}.png",
            mime="image/png",
        )
        e2.download_button(
            "⬇️ JSON",
            safe_json(export),
            file_name=f"measure_{safe_stem(src_name)}.json",
            mime="application/json",
        )
        if HAS_XLSX:
            e3.download_button(
                "⬇️ Excel (.xlsx)",
                build_xlsx(export),
                file_name=f"measure_{safe_stem(src_name)}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            e3.download_button(
                "⬇️ CSV",
                build_csv(export),
                file_name=f"measure_{safe_stem(src_name)}.csv",
                mime="text/csv",
            )
