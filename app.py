from __future__ import annotations
# ════════════════════════════════════════════════════════════════════════
#  SenseOptics Метролог — Replit-friendly версия
#  Лёгкий геометрический метрологический инструмент:
#  линия/диаметр, окружность, площадь, ручной счётчик включений,
#  калибровка по двум кликам, экспорт PNG/JSON/XLSX/CSV.
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

st.set_page_config(page_title="SenseOptics Метролог", page_icon="📐", layout="wide")

MODES = ["Линия / диаметр", "Окружность", "Площадь", "Включения", "Калибровка (2 клика)"]

GREEN = (40, 180, 70)
BLUE = (40, 120, 230)
ORANGE = (255, 150, 0)
RED = (220, 50, 50)
BLACK = (20, 20, 20)


def _init_state() -> None:
    defaults = {
        "meas": [],
        "pending": [],
        "inclusions": [],
        "calib_pts": [],
        "px_per_mm": None,
        "last_click_id": None,
        "image_state_key": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_work(reset_calibration: bool = True) -> None:
    st.session_state.meas = []
    st.session_state.pending = []
    st.session_state.inclusions = []
    st.session_state.calib_pts = []
    st.session_state.last_click_id = None
    if reset_calibration:
        st.session_state.px_per_mm = None


_init_state()


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


def downscale(img: np.ndarray, max_w: int) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    if w <= max_w:
        return img.copy(), 1.0
    scale = max_w / float(w)
    new_size = (int(round(w * scale)), int(round(h * scale)))
    return cv2.resize(img, new_size, interpolation=cv2.INTER_AREA), scale


# ════════════════════════════════════════════════════════════════════════
#  Маска тёмных включений
# ════════════════════════════════════════════════════════════════════════
def inclusion_mask(gray, dark_k=2.0, min_area=4) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (15, 15), 0)
    diff = blur.astype(np.int16) - gray.astype(np.int16)
    thr = max(5.0, float(diff.mean()) + dark_k * float(diff.std()))
    mask = (diff >= thr).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    for label in range(1, n):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            clean[lab == label] = 255
    return clean


def mask_stats(mask, ppm) -> Dict[str, Any]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = [int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, n)]
    out = {
        "n_objects": n - 1,
        "total_dark_px": int(np.sum(mask > 0)),
        "dark_area_frac": round(float(np.mean(mask > 0)), 5),
        "max_object_area_px": int(max(areas)) if areas else 0,
        "mean_object_area_px": round(float(np.mean(areas)), 1) if areas else 0.0,
    }
    if ppm:
        out["max_object_equiv_diam_mm"] = round((out["max_object_area_px"] ** 0.5) / ppm, 3)
    return out


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


def build_export(src, W, H, ppm, meas, inclusions, scale) -> Dict[str, Any]:
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


# ════════════════════════════════════════════════════════════════════════
#  UI
# ════════════════════════════════════════════════════════════════════════
st.sidebar.title("📐 SenseOptics Метролог")
st.sidebar.caption("Лёгкий режим для Replit/free")

side_up = st.sidebar.file_uploader(
    "Изображение",
    type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
    key="side_upload",
)

st.title("📐 SenseOptics Метролог")
st.caption("Загрузите фото, выберите режим и кликайте прямо по изображению.")

main_up = None
if side_up is None:
    main_up = st.file_uploader(
        "Загрузка в основной области — используйте её, если кнопка в боковой панели не открывается",
        type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
        key="main_upload",
    )

up = side_up or main_up
sample = Path(__file__).parent / "sample" / "demo_template.png"
img_bgr = None
src_name = None
file_id = None
scale = 1.0

if up is not None:
    data = up.getvalue()
    src_name = up.name
    file_id = file_fingerprint(data, src_name)
    img_bgr, err = decode_image(data, src_name)
    if err:
        st.error(err)
        st.stop()
elif sample.exists() and st.sidebar.checkbox("Использовать пример", value=True):
    data = sample.read_bytes()
    src_name = sample.name
    file_id = file_fingerprint(data, src_name)
    img_bgr, err = decode_image(data, src_name)
    if err:
        st.error(err)
        st.stop()

if img_bgr is None:
    st.info("Загрузите изображение. На Replit часто надёжнее просто перетащить файл в область загрузки.")
    st.stop()

orig_h, orig_w = img_bgr.shape[:2]
max_w = st.sidebar.slider(
    "Рабочая ширина, px",
    min_value=500,
    max_value=1800,
    value=900,
    step=100,
    help="Для Replit/free лучше 700–1100 px. При изменении ширины замеры сбрасываются, чтобы не сломать масштаб.",
)
work, scale = downscale(img_bgr, max_w)
H, W = work.shape[:2]
gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

image_state_key = f"{file_id}:{W}x{H}"
if st.session_state.image_state_key != image_state_key:
    reset_work(reset_calibration=True)
    st.session_state.image_state_key = image_state_key

st.sidebar.caption(f"Файл: {src_name}")
st.sidebar.caption(f"Оригинал: {orig_w}×{orig_h}px · работа: {W}×{H}px · scale={scale:.4f}")

st.sidebar.subheader("Калибровка")
cur = st.session_state.px_per_mm
st.sidebar.caption(f"Текущее: **{cur:.4f} px/mm**" if cur else "Текущее: не задана")
ppm_in = st.sidebar.number_input(
    "px/mm для рабочей картинки",
    min_value=0.0,
    value=0.0,
    step=0.5,
    format="%.4f",
    help="Надёжнее калибровать по двум кликам на текущем изображении. Если меняете рабочую ширину — калибровка сбросится.",
)
cs1, cs2 = st.sidebar.columns(2)
if cs1.button("Применить") and ppm_in > 0:
    st.session_state.px_per_mm = ppm_in
    st.rerun()
if cs2.button("Сброс"):
    st.session_state.px_per_mm = None
    st.session_state.calib_pts = []
    st.rerun()

ppm = st.session_state.px_per_mm

mode = st.radio("Режим", MODES, horizontal=True)
hints = {
    "Линия / диаметр": "Два клика: начало и конец отрезка.",
    "Окружность": "Клик 1 — центр, клик 2 — точка на окружности.",
    "Площадь": "Кликайте вершины контура, затем нажмите «Замкнуть площадь».",
    "Включения": "Клик по включению добавляет точку в счётчик.",
    "Калибровка (2 клика)": "Два клика по концам эталона, затем введите известную длину.",
}
st.caption("👆 " + hints[mode])

# кнопки управления рядом с холстом
c1, c2, c3, c4 = st.columns(4)
if c1.button("↩️ Сбросить текущие точки"):
    st.session_state.pending = []
    st.session_state.calib_pts = [] if mode == "Калибровка (2 клика)" else st.session_state.calib_pts
    st.rerun()
if c2.button("↩️ Удалить последний замер") and st.session_state.meas:
    st.session_state.meas.pop()
    st.rerun()
if c3.button("🗑 Очистить всё"):
    reset_work(reset_calibration=False)
    st.rerun()
debug = c4.checkbox("debug")

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
        key=f"canvas_{image_state_key}_{mode}",
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
    known = st.number_input("Известная длина эталона, мм", min_value=0.001, value=10.0, step=1.0)
    if st.button("Применить калибровку") and known > 0:
        st.session_state.px_per_mm = d_px / known
        st.rerun()

# ════════════════════════════════════════════════════════════════════════
#  Результаты
# ════════════════════════════════════════════════════════════════════════
res_l, res_r = st.columns([1, 1])

with res_l:
    st.subheader(f"Замеры ({len(st.session_state.meas)})")
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
    st.subheader(f"Включения: {len(st.session_state.inclusions)}")
    if st.session_state.inclusions:
        if st.button("🗑 Очистить включения"):
            st.session_state.inclusions = []
            st.rerun()
    if st.checkbox("Маска тёмных включений (авто)"):
        mk1, mk2 = st.columns(2)
        dark_k = mk1.slider("Чувствит. (k)", 0.5, 5.0, 2.0, 0.1)
        min_a = mk2.slider("Мин. площадь px²", 1, 50, 4, 1)
        mask = inclusion_mask(gray, dark_k, min_a)
        mov = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        mov[mask > 0] = RED
        st.image(mov, caption="Маска (красный)", use_container_width=False)
        st.json(mask_stats(mask, ppm))
        ok, mbuf = cv2.imencode(".png", mask)
        if ok:
            st.download_button(
                "⬇️ Маска PNG",
                mbuf.tobytes(),
                file_name=f"mask_{safe_stem(src_name)}.png",
                mime="image/png",
            )

# ════════════════════════════════════════════════════════════════════════
#  Экспорт
# ════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("Экспорт")
export = build_export(src_name, W, H, ppm, st.session_state.meas, st.session_state.inclusions, scale)
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
