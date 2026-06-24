from __future__ import annotations
# ════════════════════════════════════════════════════════════════════════
#  SenseOptics Метролог — геометрические замеры на макрошлифах
#  Замер: линия/диаметр, окружность, площадь (полигон), счётчик включений.
#  Калибровка: прямой ввод px/mm или по двум кликам с известным расстоянием.
#  Экспорт: аннотированное изображение PNG, маска включений PNG, JSON, Excel.
#  Лёгкий стек (без matplotlib/scipy) — быстро на free-плане Replit.
# ════════════════════════════════════════════════════════════════════════
import io
import json
import math
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import cv2
import streamlit as st

# клики по изображению (обязательны для интерактива; есть ручной фолбэк)
try:
    from streamlit_image_coordinates import streamlit_image_coordinates as st_imgcoords
    HAS_CLICK = True
except Exception:
    st_imgcoords = None
    HAS_CLICK = False

# Excel-экспорт (опционален; без него — CSV)
try:
    from openpyxl import Workbook
    HAS_XLSX = True
except Exception:
    HAS_XLSX = False

from PIL import Image as PILImage

st.set_page_config(page_title="SenseOptics Метролог", page_icon="📐", layout="wide")

MODES = ["Линия / диаметр", "Окружность", "Площадь", "Включения", "Калибровка (2 клика)"]

# ── палитра (RGB) ──
GREEN  = (40, 180, 70)
BLUE   = (40, 120, 230)
ORANGE = (255, 150, 0)
RED    = (220, 50, 50)
BLACK  = (20, 20, 20)


# ════════════════════════════════════════════════════════════════════════
#  Session state
# ════════════════════════════════════════════════════════════════════════
def _init_state():
    defaults = {
        "meas":        [],      # завершённые замеры [{type, ...points}]
        "pending":     [],      # точки текущего незавершённого замера
        "inclusions":  [],      # [(x,y), ...]
        "calib_pts":   [],      # [[x,y],[x,y]] для калибровки
        "px_per_mm":   None,
        "last_click":  None,    # дедуп повторного клика
        "src":         None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init_state()


# ════════════════════════════════════════════════════════════════════════
#  Геометрия
# ════════════════════════════════════════════════════════════════════════
def dist(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def poly_area(pts) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def poly_perimeter(pts) -> float:
    n = len(pts)
    if n < 2:
        return 0.0
    return sum(dist(pts[i], pts[(i + 1) % n]) for i in range(n))


def measure_metrics(m: Dict[str, Any], ppm: Optional[float]) -> Dict[str, Any]:
    """Геометрические величины одного замера в px и (если задано) в мм."""
    t = m["type"]
    out: Dict[str, Any] = {"type": t}
    if t == "line":
        d = dist(m["p1"], m["p2"])
        out["length_px"] = round(d, 2)
        out["length_mm"] = round(d / ppm, 3) if ppm else None
    elif t == "circle":
        r = dist(m["center"], m["edge"])
        out["radius_px"]        = round(r, 2)
        out["diameter_px"]      = round(2 * r, 2)
        out["circumference_px"] = round(2 * math.pi * r, 2)
        out["area_px2"]         = round(math.pi * r * r, 1)
        if ppm:
            out["radius_mm"]        = round(r / ppm, 3)
            out["diameter_mm"]      = round(2 * r / ppm, 3)
            out["circumference_mm"] = round(2 * math.pi * r / ppm, 3)
            out["area_mm2"]         = round(math.pi * r * r / (ppm * ppm), 4)
    elif t == "polygon":
        a = poly_area(m["pts"])
        p = poly_perimeter(m["pts"])
        out["area_px2"]      = round(a, 1)
        out["perimeter_px"]  = round(p, 2)
        out["n_vertices"]    = len(m["pts"])
        if ppm:
            out["area_mm2"]     = round(a / (ppm * ppm), 4)
            out["perimeter_mm"] = round(p / ppm, 3)
    return out


def short_label(m: Dict[str, Any], ppm: Optional[float]) -> str:
    mm = measure_metrics(m, ppm)
    if m["type"] == "line":
        return f"{mm['length_mm']}mm" if ppm else f"{mm['length_px']}px"
    if m["type"] == "circle":
        return f"D{mm['diameter_mm']}mm" if ppm else f"D{mm['diameter_px']}px"
    if m["type"] == "polygon":
        return f"{mm['area_mm2']}mm2" if ppm else f"{mm['area_px2']}px2"
    return ""


# ════════════════════════════════════════════════════════════════════════
#  Отрисовка оверлея (для экрана и для экспорта)
# ════════════════════════════════════════════════════════════════════════
def _put(img, text, org, color):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def draw_overlay(work_bgr, meas, pending, inclusions, calib_pts, ppm, mode) -> np.ndarray:
    rgb = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB).copy()

    for m in meas:
        t = m["type"]
        if t == "line":
            p1, p2 = tuple(m["p1"]), tuple(m["p2"])
            cv2.line(rgb, p1, p2, GREEN, 2)
            cv2.circle(rgb, p1, 3, GREEN, -1)
            cv2.circle(rgb, p2, 3, GREEN, -1)
            mx, my = (p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2
            _put(rgb, short_label(m, ppm), (mx + 4, my - 6), GREEN)
        elif t == "circle":
            c = tuple(m["center"])
            r = int(round(dist(m["center"], m["edge"])))
            cv2.circle(rgb, c, r, BLUE, 2)
            cv2.circle(rgb, c, 3, BLUE, -1)
            _put(rgb, short_label(m, ppm), (c[0] + 4, c[1] - r - 6), BLUE)
        elif t == "polygon":
            pts = np.array(m["pts"], np.int32)
            cv2.polylines(rgb, [pts], True, ORANGE, 2)
            for p in m["pts"]:
                cv2.circle(rgb, tuple(p), 3, ORANGE, -1)
            cx = int(np.mean([p[0] for p in m["pts"]]))
            cy = int(np.mean([p[1] for p in m["pts"]]))
            _put(rgb, short_label(m, ppm), (cx, cy), ORANGE)

    # включения (с номерами)
    for i, (x, y) in enumerate(inclusions):
        cv2.circle(rgb, (x, y), 7, RED, 2)
        _put(rgb, str(i + 1), (x + 9, y - 6), RED)

    # незавершённые точки
    for p in pending:
        cv2.drawMarker(rgb, tuple(p), BLACK, cv2.MARKER_CROSS, 12, 2)
    if mode == "Площадь" and len(pending) >= 2:
        cv2.polylines(rgb, [np.array(pending, np.int32)], False, ORANGE, 1)

    # калибровочные точки
    for i, (x, y) in enumerate(calib_pts):
        cv2.circle(rgb, (x, y), 8, ORANGE, 2)
        _put(rgb, f"C{i+1}", (x + 9, y - 6), ORANGE)
    if len(calib_pts) == 2:
        cv2.line(rgb, tuple(calib_pts[0]), tuple(calib_pts[1]), ORANGE, 1)

    return rgb


# ════════════════════════════════════════════════════════════════════════
#  Маска тёмных включений
# ════════════════════════════════════════════════════════════════════════
def inclusion_mask(gray, dark_k=2.0, min_area=4) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (15, 15), 0)
    diff = blur.astype(np.int16) - gray.astype(np.int16)
    thr  = max(5.0, float(diff.mean()) + dark_k * float(diff.std()))
    mask = (diff >= thr).astype(np.uint8) * 255
    k    = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    for l in range(1, n):
        if stats[l, cv2.CC_STAT_AREA] >= min_area:
            clean[lab == l] = 255
    return clean


def mask_stats(mask, ppm) -> Dict[str, Any]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = [int(stats[l, cv2.CC_STAT_AREA]) for l in range(1, n)]
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
#  Экспорт: JSON / Excel / PNG
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


def build_export(src, W, H, ppm, meas, inclusions) -> Dict[str, Any]:
    return {
        "source": src,
        "working_size_px": [W, H],
        "px_per_mm": ppm,
        "measurements": [
            {**measure_metrics(m, ppm), "raw": m} for m in meas
        ],
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
    ws.append(["Тип", "Длина_px", "Длина_mm", "Диаметр_px", "Диаметр_mm",
               "Окружность_px", "Окружность_mm", "Периметр_px", "Периметр_mm",
               "Площадь_px2", "Площадь_mm2"])
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
    ws_info.append(["source", export["source"]])
    ws_info.append(["working_w_px", export["working_size_px"][0]])
    ws_info.append(["working_h_px", export["working_size_px"][1]])
    ws_info.append(["px_per_mm", export["px_per_mm"]])
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
    lines = ["type,length_px,length_mm,diameter_px,diameter_mm,circumference_px,"
             "circumference_mm,perimeter_px,perimeter_mm,area_px2,area_mm2"]
    for m in export["measurements"]:
        lines.append(",".join(str(m.get(k, "")) for k in
                     ["type", "length_px", "length_mm", "diameter_px", "diameter_mm",
                      "circumference_px", "circumference_mm", "perimeter_px",
                      "perimeter_mm", "area_px2", "area_mm2"]))
    lines.append("")
    lines.append(f"inclusions_count,{export['inclusions']['count']}")
    lines.append(f"px_per_mm,{export['px_per_mm']}")
    return "\n".join(lines)


def png_bytes(rgb) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return buf.tobytes() if ok else b""


# ════════════════════════════════════════════════════════════════════════
#  Клик-обработчик
# ════════════════════════════════════════════════════════════════════════
def handle_click(x: int, y: int, mode: str):
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
        st.session_state.pending.append([x, y])      # замыкается кнопкой
    elif mode == "Включения":
        st.session_state.inclusions.append((x, y))
    elif mode == "Калибровка (2 клика)":
        cp = st.session_state.calib_pts
        if len(cp) >= 2:
            st.session_state.calib_pts = [[x, y]]
        else:
            cp.append([x, y])


# ════════════════════════════════════════════════════════════════════════
#  Загрузка изображения
# ════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def decode_image(data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


@st.cache_data(show_spinner=False)
def downscale(img: np.ndarray, max_w: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= max_w:
        return img
    s = max_w / float(w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


# ════════════════════════════════════════════════════════════════════════
#  Sidebar
# ════════════════════════════════════════════════════════════════════════
st.sidebar.title("📐 SenseOptics Метролог")
st.sidebar.caption("Геометрические замеры на макрошлифах")

up = st.sidebar.file_uploader("Изображение", type=["jpg", "jpeg", "png", "bmp", "tif", "tiff"])

sample = Path(__file__).parent / "sample" / "demo_template.png"
img_bgr = None
src_name = None
if up is not None:
    img_bgr = decode_image(up.getvalue())
    src_name = up.name
elif sample.exists() and st.sidebar.checkbox("Использовать пример", value=True):
    img_bgr = decode_image(sample.read_bytes())
    src_name = sample.name

if img_bgr is None:
    st.title("📐 SenseOptics Метролог")
    st.info("⬅️ Загрузите изображение, чтобы начать замеры.")
    st.stop()

max_w = st.sidebar.slider("Рабочая ширина, px", 500, 2400, 1000, step=100,
                          help="Меньше = быстрее и легче для free-плана. "
                               "Если метки клика смещены — уменьшите ширину.")
work = downscale(img_bgr, max_w)
H, W = work.shape[:2]
gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
st.sidebar.caption(f"{src_name} · {W}×{H}px")

# сброс при смене файла
if st.session_state.src != src_name:
    st.session_state.meas = []
    st.session_state.pending = []
    st.session_state.inclusions = []
    st.session_state.calib_pts = []
    st.session_state.last_click = None
    st.session_state.src = src_name

# ── калибровка: прямой ввод ──
st.sidebar.subheader("Калибровка")
cur = st.session_state.px_per_mm
st.sidebar.caption(f"Текущее: **{cur:.4f} px/mm**" if cur else "Текущее: не задана")
ppm_in = st.sidebar.number_input("Задать px/mm вручную", min_value=0.0,
                                 value=0.0, step=0.5, format="%.4f")
cset1, cset2 = st.sidebar.columns(2)
if cset1.button("Применить px/mm") and ppm_in > 0:
    st.session_state.px_per_mm = ppm_in
    st.rerun()
if cset2.button("Сбросить"):
    st.session_state.px_per_mm = None
    st.session_state.calib_pts = []
    st.rerun()

ppm = st.session_state.px_per_mm


# ════════════════════════════════════════════════════════════════════════
#  Основная область — режим + холст кликов
# ════════════════════════════════════════════════════════════════════════
mode = st.radio("Режим", MODES, horizontal=True)

hints = {
    "Линия / диаметр": "👆 Два клика: начало и конец отрезка.",
    "Окружность": "👆 Клик 1 — центр, клик 2 — точка на окружности.",
    "Площадь": "👆 Кликайте вершины контура, затем «Замкнуть площадь».",
    "Включения": "👆 Клик по включению — добавляется в счётчик и на изображение.",
    "Калибровка (2 клика)": "👆 Два клика по концам эталона, затем введите его длину.",
}
st.caption(hints[mode])

if not HAS_CLICK:
    st.warning("📦 Для кликов установите пакет: в Shell → "
               "`pip install streamlit-image-coordinates` и добавьте его в requirements.txt. "
               "Пока доступен ручной ввод координат ниже.")

overlay = draw_overlay(work, st.session_state.meas, st.session_state.pending,
                       st.session_state.inclusions, st.session_state.calib_pts, ppm, mode)

if HAS_CLICK:
    val = st_imgcoords(PILImage.fromarray(overlay), key=f"canvas_{mode}")
    if val is not None:
        click = (int(val["x"]), int(val["y"]))
        if click != st.session_state.last_click:
            st.session_state.last_click = click
            handle_click(click[0], click[1], mode)
            st.rerun()
else:
    st.image(overlay, use_container_width=True)
    mc1, mc2, mc3 = st.columns(3)
    mx = mc1.number_input("x", 0, W - 1, W // 2)
    my = mc2.number_input("y", 0, H - 1, H // 2)
    if mc3.button("Добавить точку"):
        handle_click(int(mx), int(my), mode)
        st.rerun()

# ── управление режимом ──
if mode == "Площадь":
    pc1, pc2 = st.columns(2)
    if pc1.button("✅ Замкнуть площадь"):
        if len(st.session_state.pending) >= 3:
            st.session_state.meas.append({"type": "polygon", "pts": list(st.session_state.pending)})
            st.session_state.pending = []
            st.rerun()
        else:
            st.warning("Нужно минимум 3 точки.")
    if pc2.button("Сбросить точки"):
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
                txt = f"{i+1}. Линия: {mm['length_px']} px" + (f" = {mm['length_mm']} мм" if ppm else "")
            elif m["type"] == "circle":
                txt = (f"{i+1}. Окружность: D={mm['diameter_px']} px, S={mm['area_px2']} px²"
                       + (f" · D={mm['diameter_mm']} мм, S={mm['area_mm2']} мм²" if ppm else ""))
            else:
                txt = (f"{i+1}. Площадь: {mm['area_px2']} px² ({mm['n_vertices']} верш.)"
                       + (f" = {mm['area_mm2']} мм²" if ppm else ""))
            dc1, dc2 = st.columns([5, 1])
            dc1.write(txt)
            if dc2.button("✕", key=f"del_{i}"):
                st.session_state.meas.pop(i)
                st.rerun()
    else:
        st.caption("Пока нет замеров.")
    if st.session_state.meas and st.button("🗑 Очистить все замеры"):
        st.session_state.meas = []
        st.rerun()

with res_r:
    st.subheader(f"Включения: {len(st.session_state.inclusions)}")
    if st.session_state.inclusions and st.button("🗑 Очистить включения"):
        st.session_state.inclusions = []
        st.rerun()

    if st.checkbox("Маска тёмных включений (авто)"):
        mk1, mk2 = st.columns(2)
        dark_k = mk1.slider("Чувствит. (k)", 0.5, 5.0, 2.0, 0.1)
        min_a  = mk2.slider("Мин. площадь px²", 1, 50, 4, 1)
        mask = inclusion_mask(gray, dark_k, min_a)
        mov = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        mov[mask > 0] = RED
        st.image(mov, caption="Маска (красный)", use_container_width=True)
        ms = mask_stats(mask, ppm)
        st.json(ms)
        ok, mbuf = cv2.imencode(".png", mask)
        if ok:
            st.download_button("⬇️ Маска PNG", mbuf.tobytes(),
                               file_name=f"mask_{Path(src_name).stem}.png", mime="image/png")


# ════════════════════════════════════════════════════════════════════════
#  Экспорт
# ════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("Экспорт")
export = build_export(src_name, W, H, ppm, st.session_state.meas, st.session_state.inclusions)

e1, e2, e3 = st.columns(3)
# 1) аннотированное изображение со всеми замерами
annotated = draw_overlay(work, st.session_state.meas, [], st.session_state.inclusions,
                         st.session_state.calib_pts, ppm, mode)
e1.download_button("🖼 Изображение с замерами PNG", png_bytes(annotated),
                   file_name=f"annotated_{Path(src_name).stem}.png", mime="image/png")
# 2) JSON
e2.download_button("⬇️ JSON", safe_json(export),
                   file_name=f"measure_{Path(src_name).stem}.json", mime="application/json")
# 3) Excel или CSV
if HAS_XLSX:
    e3.download_button("⬇️ Excel (.xlsx)", build_xlsx(export),
                       file_name=f"measure_{Path(src_name).stem}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    e3.download_button("⬇️ CSV (Excel недоступен)", build_csv(export),
                       file_name=f"measure_{Path(src_name).stem}.csv", mime="text/csv")
