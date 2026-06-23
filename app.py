from __future__ import annotations
# ════════════════════════════════════════════════════════════════════════
#  SenseOptics Tester — single-file build (web / Replit free-tier)
#  Всё в одном файле: классический грейдер осевой ликвации + генерик-тестер
#  дефектов + визуализация + Streamlit UI. scipy заменён numpy-эквивалентом;
#  matplotlib опционален (без него работают оверлеи, отключаются 3 графика).
#  Для запуска нужны только: streamlit, opencv-python-headless, numpy, pillow
#  (+ опционально matplotlib).
# ════════════════════════════════════════════════════════════════════════
import io
import os
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple, Dict, Any
from pathlib import Path

import numpy as np
import cv2
import streamlit as st

# --- matplotlib опционален -------------------------------------------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except Exception:
    plt = None
    HAS_MPL = False

# --- numpy-замена scipy.ndimage.uniform_filter1d ---------------------------
# Совпадает с scipy (mode="reflect", origin=0) с точностью до 1e-14.
def _uniform_filter1d(input, size):
    a = np.asarray(input, dtype=np.float64)
    n = a.shape[0]
    size = int(size)
    if size < 1:
        size = 1
    if size == 1 or n == 0:
        return a.copy()
    left = size // 2
    right = size - left - 1
    pad = np.pad(a, (left, right), mode="symmetric")  # scipy reflect == np symmetric
    csum = np.cumsum(np.insert(pad, 0, 0.0))
    return (csum[size:] - csum[:-size]) / float(size)

class _NDImageShim:
    @staticmethod
    def uniform_filter1d(input, size, *args, **kwargs):
        return _uniform_filter1d(input, size)

ndimage = _NDImageShim()  # код грейдера вызывает ndimage.uniform_filter1d(...)



# ======================================================================
# ===  centerline_core  ===
# ======================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
centerline_core.py — Standalone-порт классического грейдера осевой ликвации
(Centerline / Axial Segregation) из SenseOptics Metallograph.

Назначение
----------
Это ВЕРНЫЙ перенос классического (не-ML) конвейера ``SegregationAnalyzer``
из ``centerline_segregation_graderv5.py`` в чистую форму без PyQt5 и без
PyTorch/Ultralytics. Здесь воспроизводится дефолтный путь
``analyze() -> calculate_metrics()`` в окружении БЕЗ ML-моделей, то есть:

  • нормализация освещённости (margin-crop + вычитание локального среднего);
  • авто-детекция полосы осевой (valley по Y-профилю) либо ручной ROI/полоса;
  • маска тёмных пикселей внутри полосы + очистка мелких компонент
    (это поведение guide_centerline_classic_mask_by_ml при отсутствии ML);
  • band-метрики из маски (band_ratio / band_longest / max_segment / contrast);
  • бусты T/L/C/H/S, combined2, кусочно-линейный grade 1..5;
  • anti-inflation: weak_thin_line cap и low_mass_thin_line cap (вкл. по умолч.).

ВАЖНО (инвариант проекта): классический grade здесь — авторитетный.
Никакой ML-надстройки нет; etched-rescue по умолчанию выключен (как в проде).
Поведение совпадает с продовым в окружении без U-Net-моделей.

Чистые зависимости: numpy, opencv-python-headless (scipy убран — заменён numpy-эквивалентом).
"""




# ════════════════════════════════════════════════════════════════════
#  Параметры и результат
# ════════════════════════════════════════════════════════════════════

@dataclass
class AnalysisSettings:
    margin: float = 0.05
    blur_size_fraction: float = 0.1
    valley_scan_top: float = 0.15
    valley_scan_bottom: float = 0.85
    roi_half_fraction: float = 0.05
    band_scan_top: float = 0.25
    band_scan_bottom: float = 0.75
    band_half_height: int = 15
    band_heal_gap: int = 15


@dataclass
class ROIRegion:
    y_top: int = 0
    y_bottom: int = 100
    x_left: int = 0
    x_right: int = 100

    @property
    def height(self) -> int:
        return self.y_bottom - self.y_top

    @property
    def width(self) -> int:
        return self.x_right - self.x_left


@dataclass
class SegregationResult:
    grade: float = 1.0
    grade_label: str = ""
    # метрики
    max_row_dark: float = 0.0
    valley_depth: float = 0.0
    band_ratio: float = 0.0
    band_contrast: float = 0.0
    linear_score: float = 0.0
    combined2: float = 0.0
    thin_line_boost: float = 0.0
    long_line_boost: float = 0.0
    contrast_boost: float = 0.0
    shallow_boost: float = 0.0
    scatter_boost: float = 0.0
    max_segment: int = 0
    band_longest: int = 0
    boost_str: str = ""
    cap_reason: str = ""
    image_kind: str = "unknown"
    # геометрия (абсолютные координаты исходного изображения)
    roi: Dict[str, int] = field(default_factory=dict)
    center_y: int = 0
    max_row_y: int = 0
    # калибровка
    px_per_mm: Optional[float] = None
    band_longest_mm: Optional[float] = None
    # массивы для визуализации (не сериализуются в JSON)
    _viz: Dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_viz", None)
        return d


# ════════════════════════════════════════════════════════════════════
#  Утилиты грейда
# ════════════════════════════════════════════════════════════════════

def grade_to_label(grade: float) -> str:
    grade = float(grade)
    names = {
        1.5: "Минимальный",
        2.0: "Слабый",
        2.5: "Незначительный",
        3.0: "Умеренный",
        3.5: "Заметный",
        4.0: "Выраженный",
        4.5: "Сильный",
        5.1: "Критический",
    }
    return next((n for t, n in names.items() if grade < t), "Критический")


def _qualitative(value: float, bins, labels) -> str:
    v = float(value)
    for b, lbl in zip(bins, labels):
        if v < b:
            return lbl
    return labels[-1]


def humanize_boosts(boost_str: str) -> str:
    """Переводит машинные T/L/C/H/S в человеческие причины."""
    import re
    if not boost_str:
        return ""
    mapping = {
        "T": "обнаружена тонкая осевая линия",
        "L": "учтена протяжённость линии",
        "C": "усилен вклад контраста",
        "H": "учтён провал профиля по центру",
        "S": "учтена рассеянность проявлений",
    }
    tokens = re.findall(r"([TLCHS])\s*:\s*([0-9.]+)", boost_str)
    seen, uniq = set(), []
    for k, _v in tokens:
        if k in mapping and mapping[k] not in seen:
            uniq.append(mapping[k])
            seen.add(mapping[k])
    return "; ".join(uniq)


def _centerline_image_kind(gray: Optional[np.ndarray]) -> str:
    """sulfur_print (белый скан) vs etched (травлёный металл) vs unknown."""
    if gray is None or getattr(gray, "size", 0) == 0:
        return "unknown"
    try:
        med = float(np.median(gray))
        std = float(np.std(gray))
        p95 = float(np.percentile(gray, 95))
    except Exception:
        return "unknown"
    if med >= 225.0 and p95 >= 248.0:
        return "sulfur_print"
    if 55.0 <= med <= 220.0 and std >= 10.0:
        return "etched"
    return "unknown"


# ════════════════════════════════════════════════════════════════════
#  1-D / mask helpers (перенос из v5)
# ════════════════════════════════════════════════════════════════════

def _remove_small_components(mask01: np.ndarray, min_area: int = 25) -> np.ndarray:
    if mask01 is None or getattr(mask01, "size", 0) == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    m = (mask01 > 0).astype(np.uint8)
    if int(m.sum()) == 0:
        return m
    try:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        out = np.zeros_like(m)
        for i in range(1, n):
            if int(stats[i, cv2.CC_STAT_AREA]) >= int(min_area):
                out[labels == i] = 1
        return out
    except Exception:
        return m


def _heal_binary_runs_1d(binary: np.ndarray, gap: int = 15) -> np.ndarray:
    b = (np.asarray(binary) > 0).astype(np.uint8).copy()
    n = len(b)
    i = 0
    while i < n:
        if b[i] == 0:
            s = i
            while i < n and b[i] == 0:
                i += 1
            if (i - s) <= int(gap) and s > 0 and i < n and b[s - 1] and b[i]:
                b[s:i] = 1
        else:
            i += 1
    return b


def _longest_run_1d(binary: np.ndarray) -> Tuple[int, int]:
    b = (np.asarray(binary) > 0).astype(np.uint8)
    longest = cur = 0
    for v in b:
        if v:
            cur += 1
        elif cur:
            longest = max(longest, cur)
            cur = 0
    if cur:
        longest = max(longest, cur)
    return int(longest), int(longest)


def _mask_band_stats_from_inner(mask_inner01: np.ndarray,
                                roi_inner: np.ndarray,
                                full_width: int,
                                bg_mean: float,
                                heal_gap: int = 15) -> dict:
    """Band-length/contrast метрики из уже очищенной inner-маски."""
    if mask_inner01 is None or getattr(mask_inner01, "size", 0) == 0:
        return {"band_ratio": 0.0, "band_longest": 0, "max_segment": 0, "band_contrast": 0.0}
    m = (mask_inner01 > 0).astype(np.uint8)
    if m.ndim != 2 or m.shape[1] <= 0:
        return {"band_ratio": 0.0, "band_longest": 0, "max_segment": 0, "band_contrast": 0.0}

    col_binary_raw = (np.sum(m, axis=0) > 0).astype(np.uint8)
    max_segment, _ = _longest_run_1d(col_binary_raw)
    col_binary = _heal_binary_runs_1d(col_binary_raw, gap=heal_gap)
    band_longest, _ = _longest_run_1d(col_binary)
    width = int(full_width) if int(full_width) > 0 else int(m.shape[1])
    band_ratio = float(band_longest) / float(max(1, width))

    vals = roi_inner[m > 0] if roi_inner is not None and roi_inner.shape == m.shape else np.array([])
    band_contrast = float(bg_mean - float(np.mean(vals))) if vals.size > 0 else 0.0
    return {
        "band_ratio": float(band_ratio),
        "band_longest": int(band_longest),
        "max_segment": int(max_segment),
        "band_contrast": float(max(0.0, band_contrast)),
    }


# ════════════════════════════════════════════════════════════════════
#  Anti-inflation caps (перенос из v5, дефолты сохранены)
# ════════════════════════════════════════════════════════════════════

def _cap_centerline_low_mass_thin_line(grade, *, thin_line_boost, long_line_boost,
                                       contrast_boost, shallow_boost, scatter_boost,
                                       max_row_dark, valley_depth, band_ratio,
                                       band_contrast, max_segment, linear_score):
    if os.environ.get("CENTERLINE_LOW_MASS_CAP_DISABLE", "0") == "1":
        return float(grade), ""
    try:
        hard_cap_max = float(os.environ.get("CENTERLINE_LOW_MASS_CAP_MAX", "2.30"))
    except ValueError:
        hard_cap_max = 2.30
    hard_cap_max = max(1.0, min(3.5, hard_cap_max))

    only_thin_boost = (thin_line_boost > 0 and long_line_boost == 0 and
                       contrast_boost == 0 and shallow_boost == 0 and scatter_boost == 0)
    low_mass_thin_line = (only_thin_boost and max_row_dark <= 9.5 and valley_depth <= 5.5
                          and linear_score <= 18.0 and band_ratio <= 0.16 and max_segment <= 150)
    if not low_mass_thin_line:
        return float(grade), ""

    cap = 1.80 + 2.70 * min(float(band_ratio), 0.16)
    if valley_depth >= 4.0:
        cap += 0.10
    if band_contrast >= 60.0:
        cap += 0.10
    cap = min(cap, hard_cap_max)
    if grade > cap:
        return float(cap), f"CAP:low_mass_thin_line<=G{cap:.2f}"
    return float(grade), ""


def _raise_centerline_etched_floor(grade, gray, *, max_row_dark, valley_depth,
                                   band_ratio, band_contrast, max_segment, linear_score):
    # По умолчанию ВЫКЛЮЧЕНО (как в проде). Включается CENTERLINE_ETCHED_RESCUE_ENABLE=1.
    if os.environ.get("CENTERLINE_ETCHED_RESCUE_ENABLE", "0") != "1":
        return float(grade), ""
    if os.environ.get("CENTERLINE_ETCHED_RESCUE_DISABLE", "0") == "1":
        return float(grade), ""
    if _centerline_image_kind(gray) != "etched":
        return float(grade), ""
    if not (max_row_dark >= 7.0 and band_ratio >= 0.050 and band_contrast >= 32.0
            and linear_score >= 13.0 and max_segment >= 10):
        return float(grade), ""
    try:
        floor_max = float(os.environ.get("CENTERLINE_ETCHED_FLOOR_MAX", "4.20"))
    except ValueError:
        floor_max = 4.20
    floor_max = max(2.0, min(5.0, floor_max))
    floor = (1.35 + 0.33 * float(valley_depth) + 0.035 * float(max_row_dark)
             + 0.070 * float(np.sqrt(max(float(band_ratio) * 100.0, 0.0)))
             + 0.003 * max(float(band_contrast) - 35.0, 0.0))
    if valley_depth >= 5.5 and band_contrast >= 38.0:
        floor += 0.12
    if valley_depth >= 6.0 and max_row_dark >= 9.0:
        floor += 0.10
    if valley_depth < 4.0:
        floor = min(floor, 3.15)
    elif valley_depth < 6.0:
        floor = min(floor, 3.65)
    else:
        floor = min(floor, floor_max)
    floor = max(1.0, min(5.0, float(floor)))
    if grade < floor:
        return floor, f"FLOOR:etched_centerline>=G{floor:.2f}"
    return float(grade), ""


# ════════════════════════════════════════════════════════════════════
#  Анализатор
# ════════════════════════════════════════════════════════════════════

class CenterlineCore:
    def __init__(self, settings: Optional[AnalysisSettings] = None):
        self.settings = settings or AnalysisSettings()
        self._image = None      # BGR
        self._gray = None
        self._normalized = None
        self._roi: Optional[ROIRegion] = None
        self._auto_roi_diag: Dict[str, Any] = {}

    # --- загрузка ---
    def set_image(self, image_bgr: np.ndarray) -> bool:
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return False
        if image_bgr.ndim == 2:
            self._image = cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
        else:
            self._image = image_bgr
        self._gray = cv2.cvtColor(self._image, cv2.COLOR_BGR2GRAY)
        self._normalized = None
        self._roi = None
        return True

    # --- нормализация освещённости ---
    def _prepare_normalized(self):
        if self._normalized is not None:
            return
        gray = self._gray.copy()
        h, w = gray.shape
        margin = self.settings.margin
        gray = gray[int(h * margin):int(h * (1 - margin)), :]
        h, w = gray.shape
        blur_size = max(51, int(min(h, w) * self.settings.blur_size_fraction) | 1)
        local_mean = cv2.GaussianBlur(gray.astype(np.float32), (blur_size, blur_size), 0)
        self._normalized = np.clip(gray.astype(np.float32) - local_mean + 128, 0, 255)

    # --- авто-ROI (полоса осевой) ---
    def auto_detect_roi(self) -> ROIRegion:
        self._prepare_normalized()
        h_norm, w_norm = self._normalized.shape
        h_orig, w_orig = self._gray.shape
        margin_y = int(h_orig * self.settings.margin)

        scan = self._normalized[int(h_norm * self.settings.valley_scan_top):
                                int(h_norm * self.settings.valley_scan_bottom), :]
        prof_y = np.mean(scan, axis=1)
        prof_s = ndimage.uniform_filter1d(prof_y, max(5, len(prof_y) // 50))
        min_idx = int(np.argmin(prof_s))
        center_y_norm = int(h_norm * self.settings.valley_scan_top) + min_idx

        # диагностика уверенности авто-ROI
        try:
            prof_med = float(np.median(prof_s))
            prof_std = float(np.std(prof_s) + 1e-6)
            valley_val = float(prof_s[min_idx])
            stability = (prof_med - valley_val) / prof_std
            excl = max(3, int(len(prof_s) * 0.03))
            masked = prof_s.copy()
            masked[max(0, min_idx - excl):min(len(prof_s), min_idx + excl + 1)] = np.inf
            second_idx = int(np.argmin(masked)) if np.isfinite(masked).any() else -1
            second_val = float(masked[second_idx]) if second_idx >= 0 else float("nan")
            margin_m = (second_val - valley_val) / prof_std if np.isfinite(second_val) else float("nan")
            conf_a = max(0.0, min(1.0, stability / 3.0))
            conf_b = max(0.0, min(1.0, margin_m / 3.0)) if np.isfinite(margin_m) else 0.5
            self._auto_roi_diag = {
                "center_y_abs": int(center_y_norm + margin_y),
                "stability": round(stability, 3),
                "second_valley_margin": round(margin_m, 3) if np.isfinite(margin_m) else None,
                "confidence": round(0.5 * conf_a + 0.5 * conf_b, 3),
            }
        except Exception:
            self._auto_roi_diag = {"confidence": None, "error": "diag_failed"}

        roi_half = int(h_norm * self.settings.roi_half_fraction)
        y_top_norm = max(0, center_y_norm - roi_half)
        y_bottom_norm = min(h_norm, center_y_norm + roi_half)
        self._roi = ROIRegion(y_top=y_top_norm + margin_y, y_bottom=y_bottom_norm + margin_y,
                              x_left=0, x_right=w_orig)
        return self._roi

    def set_roi(self, roi: ROIRegion):
        self._roi = roi

    # --- band-line из нормализованного профиля (как _compute_band_line_roi) ---
    def _compute_band_line_roi(self, roi_region):
        h, w = roi_region.shape
        scan_y1 = int(h * 0.45)
        scan_y2 = int(h * 0.65)
        if scan_y2 <= scan_y1:
            return {'band_center_y': h // 2, 'band_ratio': 0.0, 'band_contrast': 0.0,
                    'max_segment': 0, 'band_longest': 0}
        profile_5pct = np.array([np.percentile(roi_region[y, :], 5) for y in range(scan_y1, scan_y2)])
        profile_smooth = ndimage.uniform_filter1d(profile_5pct, min(15, len(profile_5pct)))
        center_y = scan_y1 + int(np.argmin(profile_smooth))
        bg_mean = (np.mean(roi_region[:int(h * 0.15), :]) + np.mean(roi_region[int(h * 0.85):, :])) / 2
        band_half = min(self.settings.band_half_height, h // 4)
        band = roi_region[max(0, center_y - band_half):min(h, center_y + band_half), :]
        col_mins = np.min(band, axis=0)
        col_median = np.median(col_mins)
        col_mad = np.median(np.abs(col_mins - col_median))
        threshold = col_median - 1.5 * max(col_mad, 1.0)
        if threshold < 10:
            threshold = col_median - 0.5 * max(col_mad, 1.0)
        binary = (col_mins < threshold).astype(np.uint8)
        max_segment_original, _ = _longest_run_1d(binary)
        result = _heal_binary_runs_1d(binary, gap=self.settings.band_heal_gap)
        longest, _ = _longest_run_1d(result)
        band_ratio = longest / w if w > 0 else 0
        dark_pixels = col_mins[result > 0]
        band_contrast = bg_mean - np.mean(dark_pixels) if len(dark_pixels) > 0 else 0
        return {'band_center_y': center_y, 'band_ratio': band_ratio,
                'band_contrast': band_contrast, 'max_segment': max_segment_original,
                'band_longest': longest, 'col_mins': col_mins, 'binary_healed': result,
                'threshold': float(threshold)}

    # --- главный расчёт (порт calculate_metrics, no-ML путь) ---
    def analyze(self, px_per_mm: Optional[float] = None) -> SegregationResult:
        if self._roi is None:
            self.auto_detect_roi()
        self._prepare_normalized()

        h_norm, w_norm = self._normalized.shape
        h_orig, w_orig = self._gray.shape
        margin_y = int(h_orig * self.settings.margin)

        roi_y1_norm = max(0, self._roi.y_top - margin_y)
        roi_y2_norm = min(h_norm, self._roi.y_bottom - margin_y)
        roi_x1_norm = max(0, self._roi.x_left)
        roi_x2_norm = min(w_norm, self._roi.x_right)
        roi_region = self._normalized[roi_y1_norm:roi_y2_norm, roi_x1_norm:roi_x2_norm]

        if roi_region.size == 0 or roi_region.shape[0] < 3:
            res = SegregationResult(grade=1.0, grade_label=grade_to_label(1.0))
            res.roi = dict(y_top=self._roi.y_top, y_bottom=self._roi.y_bottom,
                           x_left=self._roi.x_left, x_right=self._roi.x_right)
            res.cap_reason = "empty_roi"
            return res

        # профиль и valley
        scan_top_rel = max(0, int(roi_region.shape[0] * 0.15))
        scan_bottom_rel = min(roi_region.shape[0], int(roi_region.shape[0] * 0.85))
        scan = roi_region[scan_top_rel:scan_bottom_rel, :]
        prof_y = np.mean(scan, axis=1)
        prof_s = ndimage.uniform_filter1d(prof_y, max(5, len(prof_y) // 50))
        min_idx = int(np.argmin(prof_s))
        center_y_roi = scan_top_rel + min_idx
        baseline = np.percentile(prof_s, 80)
        valley_depth = float(baseline - prof_s[min_idx])

        # inner-полоса и сырая тёмная маска
        roi_half = int(roi_region.shape[0] * self.settings.roi_half_fraction)
        roi_inner_y1 = max(0, center_y_roi - roi_half)
        roi_inner_y2 = min(roi_region.shape[0], center_y_roi + roi_half)
        roi_inner = roi_region[roi_inner_y1:roi_inner_y2, :]
        roi_mean, roi_std = np.mean(roi_inner), np.std(roi_inner)
        threshold = roi_mean - 1.5 * roi_std
        dark_mask_raw = (roi_inner < threshold).astype(np.uint8)

        # no-ML guide: только очистка мелких компонент (min_area=35)
        dark_mask = _remove_small_components(dark_mask_raw, min_area=35)

        max_row_dark = float(np.max(np.mean(dark_mask, axis=1)) * 100.0) if dark_mask.shape[0] > 0 else 0.0
        max_row_idx = int(np.argmax(np.mean(dark_mask, axis=1))) if dark_mask.shape[0] > 0 else 0
        max_row_y_roi = roi_inner_y1 + max_row_idx

        # band-метрики: профильные + override из маски (GUIDED_BAND_METRICS=True по умолч.)
        band = self._compute_band_line_roi(roi_region)
        bg_mean_for_band = (np.mean(roi_region[:max(1, int(roi_region.shape[0] * 0.15)), :]) +
                            np.mean(roi_region[int(roi_region.shape[0] * 0.85):, :])) / 2.0
        guided_band = _mask_band_stats_from_inner(
            dark_mask, roi_inner, full_width=roi_region.shape[1],
            bg_mean=float(bg_mean_for_band), heal_gap=self.settings.band_heal_gap)
        band_ratio = guided_band['band_ratio']
        band_longest = guided_band['band_longest']
        max_segment = guided_band['max_segment']
        band_contrast = guided_band['band_contrast'] if guided_band['band_contrast'] > 0 else band['band_contrast']

        linear_score = max_row_dark + 2.0 * valley_depth

        # ── бусты ──
        thin_line_boost = 0.0
        if (max_row_dark < 15 and band_contrast >= 45 and band_ratio >= 0.07 and
                valley_depth < 5 and max_segment >= 40):
            thin_line_boost = min(45.0 * (band_ratio * 100 / 8) * (band_contrast / 50), 50.0)

        long_line_boost = 0.0
        if thin_line_boost == 0 and band_ratio >= 0.15 and band_contrast >= 50:
            long_line_boost = min((band_ratio * 100 - 12) * 0.5 * (band_contrast / 60), 15.0)

        contrast_boost = 0.0
        if thin_line_boost == 0 and long_line_boost == 0 and band_ratio >= 0.04:
            if linear_score < 20 and band_contrast >= 60:
                contrast_boost = min((band_contrast - 55) * 0.40 * np.sqrt(band_ratio * 100), 18.0)
            elif 24 <= linear_score < 32 and band_contrast >= 35:
                contrast_boost = min((band_contrast - 30) * 0.25 * np.sqrt(band_ratio * 100), 12.0)
            elif 32 <= linear_score < 40 and band_contrast >= 75:
                contrast_boost = min((band_contrast - 70) * 0.06 * np.sqrt(band_ratio * 100), 4.0)

        shallow_boost = 0.0
        if max_row_dark >= 18 and valley_depth < 3 and band_contrast >= 40:
            shallow_boost = min((max_row_dark - 16) * 1.2 * (band_contrast / 50), 18.0)

        scatter_boost = 0.0
        if max_row_dark >= 16:
            scatter_half = int(roi_region.shape[0] * 0.03)
            scatter_y1 = max(0, center_y_roi - scatter_half)
            scatter_y2 = min(roi_region.shape[0], center_y_roi + scatter_half)
            scatter_roi = roi_region[scatter_y1:scatter_y2, :]
            scatter_mean, scatter_std = np.mean(scatter_roi), np.std(scatter_roi)
            scatter_binary = (scatter_roi < (scatter_mean - scatter_std)).astype(np.uint8)
            dark_ratio = float(np.mean(scatter_binary))
            n_labels, _, stats, _ = cv2.connectedComponentsWithStats(scatter_binary, 8)
            comp_count = sum(1 for i in range(1, n_labels) if stats[i, cv2.CC_STAT_AREA] >= 5)
            if dark_ratio >= 0.05 and comp_count >= 10:
                base = 0.5 * dark_ratio * 100
                if linear_score < 25:
                    base *= 0.3
                elif linear_score < 30:
                    base *= 0.5
                elif max_row_dark > 22:
                    base *= 0.5
                scatter_boost = min(base, 5.0)

        combined2 = (linear_score + thin_line_boost + long_line_boost +
                     contrast_boost + shallow_boost + scatter_boost)

        # ── кусочно-линейный grade ──
        if combined2 < 16:
            grade = 0.050 * combined2 + 0.6
        elif combined2 < 26:
            grade = 0.065 * combined2 + 0.35
        elif combined2 < 36:
            grade = 0.090 * combined2 - 0.30
        elif combined2 < 46:
            grade = 0.080 * combined2 + 0.05
        elif combined2 < 56:
            grade = 0.078 * combined2 + 0.15
        else:
            grade = 0.072 * combined2 + 0.5
        grade = min(5.0, max(1.0, grade))

        # ── anti-inflation ──
        cap_reason = ""
        weak_thin_line_only = (thin_line_boost > 0 and linear_score < 18.0 and
                               max_row_dark < 9.0 and valley_depth < 4.0 and
                               band_contrast < 65.0 and max_segment < 220)
        if weak_thin_line_only:
            grade = min(grade, 1.5)
            cap_reason = "CAP:weak_thin_line_only<=G1.50"

        grade, low_mass_reason = _cap_centerline_low_mass_thin_line(
            grade, thin_line_boost=thin_line_boost, long_line_boost=long_line_boost,
            contrast_boost=contrast_boost, shallow_boost=shallow_boost, scatter_boost=scatter_boost,
            max_row_dark=max_row_dark, valley_depth=valley_depth, band_ratio=band_ratio,
            band_contrast=band_contrast, max_segment=max_segment, linear_score=linear_score)
        if low_mass_reason:
            cap_reason = low_mass_reason

        grade, etched_reason = _raise_centerline_etched_floor(
            grade, self._gray, max_row_dark=max_row_dark, valley_depth=valley_depth,
            band_ratio=band_ratio, band_contrast=band_contrast, max_segment=max_segment,
            linear_score=linear_score)
        if etched_reason:
            cap_reason = (cap_reason + " " if cap_reason else "") + etched_reason

        # ── boost_str ──
        total_boost = thin_line_boost + long_line_boost + contrast_boost + shallow_boost + scatter_boost
        boost_str = ""
        if total_boost > 0 or cap_reason:
            parts = []
            if thin_line_boost > 0: parts.append(f"T:{thin_line_boost:.1f}")
            if long_line_boost > 0: parts.append(f"L:{long_line_boost:.1f}")
            if contrast_boost > 0: parts.append(f"C:{contrast_boost:.1f}")
            if shallow_boost > 0: parts.append(f"H:{shallow_boost:.1f}")
            if scatter_boost > 0: parts.append(f"S:{scatter_boost:.1f}")
            if cap_reason: parts.append(cap_reason)
            boost_str = " [" + " ".join(parts) + "]"

        grade = round(grade, 2)
        center_y_abs = roi_y1_norm + center_y_roi + margin_y
        max_row_y_abs = roi_y1_norm + max_row_y_roi + margin_y

        band_longest_mm = None
        if px_per_mm and px_per_mm > 0:
            band_longest_mm = round(band_longest / px_per_mm, 3)

        res = SegregationResult(
            grade=grade, grade_label=grade_to_label(grade),
            max_row_dark=round(max_row_dark, 1), valley_depth=round(valley_depth, 1),
            band_ratio=round(band_ratio, 3), band_contrast=round(band_contrast, 1),
            linear_score=round(linear_score, 1), combined2=round(combined2, 1),
            thin_line_boost=round(thin_line_boost, 1), long_line_boost=round(long_line_boost, 1),
            contrast_boost=round(contrast_boost, 1), shallow_boost=round(shallow_boost, 1),
            scatter_boost=round(scatter_boost, 1), max_segment=int(max_segment),
            band_longest=int(band_longest), boost_str=boost_str, cap_reason=cap_reason.strip(),
            image_kind=_centerline_image_kind(self._gray),
            roi=dict(y_top=int(self._roi.y_top), y_bottom=int(self._roi.y_bottom),
                     x_left=int(self._roi.x_left), x_right=int(self._roi.x_right)),
            center_y=int(center_y_abs), max_row_y=int(max_row_y_abs),
            px_per_mm=(round(px_per_mm, 4) if px_per_mm else None),
            band_longest_mm=band_longest_mm,
        )

        # массивы для визуализации
        full_dark_mask = np.zeros(self._gray.shape, dtype=np.uint8)
        fy1 = roi_y1_norm + margin_y + roi_inner_y1
        fy2 = fy1 + dark_mask.shape[0]
        fx1 = roi_x1_norm
        fx2 = fx1 + dark_mask.shape[1]
        full_dark_mask[fy1:fy2, fx1:fx2] = (dark_mask * 255).astype(np.uint8)
        res._viz = {
            "profile_y": prof_s.tolist(),
            "profile_y0_abs": int(roi_y1_norm + scan_top_rel + margin_y),
            "baseline": float(baseline),
            "valley_idx": int(min_idx),
            "col_mins": band.get("col_mins"),
            "band_binary": band.get("binary_healed"),
            "band_threshold": band.get("threshold"),
            "dark_mask_full": full_dark_mask,
            "auto_roi_diag": dict(self._auto_roi_diag),
        }
        return res


def describe_segregation(res: SegregationResult) -> Tuple[str, str, List[str]]:
    """(title, base_text, bullets) для металловеда."""
    g = float(res.grade)
    label = grade_to_label(g)
    klass = max(1, min(5, int(round(g))))
    base_by_class = {
        1: "Осевая сегрегация практически не проявляется: возможны единичные пятна/точки без устойчивой полосы.",
        2: "Слабая осевая сегрегация: полоса намечается, но неоднородна и часто прерывиста.",
        3: "Умеренная осевая сегрегация: полоса в целом читается и заметно отличается от фона.",
        4: "Значительная осевая сегрегация: полоса чёткая/широкая, высокая неоднородность.",
        5: "Сильная осевая сегрегация: полоса/зона доминирует, максимальная контрастность.",
    }
    bullets = []
    if res.max_row_dark > 0:
        q = _qualitative(res.max_row_dark, [6, 10, 16, 999], ["очень низкая", "низкая", "умеренная", "высокая"])
        bullets.append(f"Интенсивность тёмных проявлений: {q} (Max Row Dark {res.max_row_dark:.1f}%).")
    if res.valley_depth > 0:
        q = _qualitative(res.valley_depth, [2, 5, 999], ["слабая", "умеренная", "выраженная"])
        bullets.append(f"Выраженность центральной зоны (профиль Y): {q} (Valley Depth {res.valley_depth:.1f}).")
    if res.band_contrast > 0:
        q = _qualitative(res.band_contrast, [15, 30, 45, 999], ["низкий", "средний", "высокий", "очень высокий"])
        bullets.append(f"Контраст полосы: {q} (Band Contrast {res.band_contrast:.1f}).")
    if res.band_ratio > 0:
        q = _qualitative(res.band_ratio, [0.05, 0.15, 0.35, 999],
                         ["прерывистая", "локально протяжённая", "устойчивая", "почти сплошная"])
        bullets.append(f"Непрерывность по длине: {q} (доля {int(res.band_ratio*100)}%, longest {res.band_longest}px).")
    if res.max_segment > 0:
        q = _qualitative(res.max_segment, [15, 35, 70, 999], ["единичные", "умеренные", "выраженные", "крупные"])
        bullets.append(f"Локальные усиления: {q} (Max Segment {res.max_segment}px).")
    h = humanize_boosts(res.boost_str)
    if h:
        bullets.append(f"Алгоритм применил поправки: {h}.")
    title = f"Класс {klass} — {label} (балл {g:.2f})"
    return title, base_by_class[klass], bullets


# ======================================================================
# ===  defect_tester  ===
# ======================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
defect_tester.py — Генерик-диагностика дефектов (аналог crack_zone_tester2)
без PyQt5. Работает для любых типов: узкие/угловые/внутренние трещины,
точечные включения и т.п.

Концепции из оригинала, перенесённые на безголовый расчёт:
  • ROI + разбиение на сетку «кирпичей»;
  • авто-классификация ячеек crack / suspect / clean по яркости
    (с ручной настройкой порогов вместо ручного клика);
  • «пипетка» — статистика яркости в точке/патче;
  • «трасса» — длина протяжённого тёмного фрагмента (авто или ручная ломаная);
  • severity-скоринг и предсказанный grade 1..5 (формулы из оригинала).

Скоринг (severity и predicted_grade) перенесён 1:1 из
``crack_zone_tester2.py`` (build_summary / _predict_grade_from_scores).
"""



CELL_CLEAN = 0
CELL_CRACK = 1
CELL_SUSPECT = 2
CELL_STATE_NAMES = {CELL_CLEAN: "clean", CELL_CRACK: "crack", CELL_SUSPECT: "suspect"}

OPENING_SCORE = {"auto": None, "closed": 0.15, "semi_open": 0.45, "open": 0.75, "strongly_open": 1.00}
DARKNESS_SCORE = {"auto": None, "weak": 0.20, "medium": 0.45, "dark": 0.72, "black": 1.00}


@dataclass
class GridCell:
    row: int
    col: int
    x: int
    y: int
    w: int
    h: int
    state: int = CELL_CLEAN
    gray_mean: float = 0.0
    gray_std: float = 0.0
    dark_frac: float = 0.0


@dataclass
class GenericParams:
    grid_rows: int = 8
    grid_cols: int = 24
    # порог «тёмного» пикселя задаётся как (background_mean - dark_k*background_std)
    dark_k: float = 1.2
    # доля тёмных пикселей в ячейке для классов
    crack_dark_frac: float = 0.35
    suspect_dark_frac: float = 0.15
    # ручные операторские оценки
    defect_type: str = "narrow"        # narrow|corner|internal|other
    opening: str = "auto"              # auto|closed|semi_open|open|strongly_open
    darkness: str = "auto"             # auto|weak|medium|dark|black


# ════════════════════════════════════════════════════════════════════
#  helpers (перенос)
# ════════════════════════════════════════════════════════════════════

def polyline_length(points: List[List[float]]) -> float:
    if not points or len(points) < 2:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
        total += math.hypot(float(x2) - float(x1), float(y2) - float(y1))
    return total


def _cell_zone(c: GridCell, roi: Tuple[int, int, int, int]) -> str:
    x0, y0, rw, rh = roi
    cx = c.x + c.w / 2.0
    cy = c.y + c.h / 2.0
    dx = min(cx - x0, x0 + rw - cx) / max(1.0, rw)
    dy = min(cy - y0, y0 + rh - cy) / max(1.0, rh)
    d = min(dx, dy)
    if d <= 0.12:
        return "edge"
    if d <= 0.28:
        return "near_edge"
    return "central"


def _cluster_cells(cells: List[GridCell]) -> Dict[str, Any]:
    selected = {(c.row, c.col): c for c in cells}
    visited, clusters = set(), []
    for key in selected:
        if key in visited:
            continue
        stack, cur = [key], []
        visited.add(key)
        while stack:
            k = stack.pop()
            cur.append(selected[k])
            r, col = k
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nk = (r + dr, col + dc)
                    if nk in selected and nk not in visited:
                        visited.add(nk)
                        stack.append(nk)
        clusters.append(cur)
    sizes = [len(cl) for cl in clusters]
    return {"n_clusters": len(clusters), "max_cluster_cells": max(sizes) if sizes else 0,
            "cluster_sizes": sizes}


# ════════════════════════════════════════════════════════════════════
#  Анализ
# ════════════════════════════════════════════════════════════════════

class GenericDefectTester:
    def __init__(self, gray: np.ndarray, roi: Tuple[int, int, int, int],
                 params: Optional[GenericParams] = None, px_per_mm: Optional[float] = None):
        """gray: одноканальное изображение; roi=(x,y,w,h) в пикселях исходника."""
        self.gray = gray
        self.roi = tuple(int(v) for v in roi)
        self.p = params or GenericParams()
        self.px_per_mm = px_per_mm
        self.cells: List[GridCell] = []
        self.traces: List[List[List[float]]] = []
        self._dark_threshold = 0.0
        self._bg_mean = 0.0
        self._bg_std = 0.0

    # --- авто-сетка + классификация по яркости ---
    def build_grid(self):
        x0, y0, rw, rh = self.roi
        roi_img = self.gray[y0:y0 + rh, x0:x0 + rw]
        if roi_img.size == 0:
            self.cells = []
            return
        self._bg_mean = float(np.mean(roi_img))
        self._bg_std = float(np.std(roi_img))
        self._dark_threshold = self._bg_mean - self.p.dark_k * max(1.0, self._bg_std)

        rows = max(1, int(self.p.grid_rows))
        cols = max(1, int(self.p.grid_cols))
        cw = rw / cols
        ch = rh / rows
        self.cells = []
        for r in range(rows):
            for c in range(cols):
                cx = int(round(x0 + c * cw))
                cy = int(round(y0 + r * ch))
                w = int(round(cw)) if c < cols - 1 else (x0 + rw - cx)
                h = int(round(ch)) if r < rows - 1 else (y0 + rh - cy)
                patch = self.gray[cy:cy + h, cx:cx + w]
                if patch.size == 0:
                    continue
                gm = float(np.mean(patch))
                gs = float(np.std(patch))
                dark_frac = float(np.mean(patch < self._dark_threshold))
                state = CELL_CLEAN
                if dark_frac >= self.p.crack_dark_frac:
                    state = CELL_CRACK
                elif dark_frac >= self.p.suspect_dark_frac:
                    state = CELL_SUSPECT
                self.cells.append(GridCell(r, c, cx, cy, w, h, state, gm, gs, dark_frac))

    # --- авто-трасса: длиннейший тёмный связный фрагмент в ROI ---
    def auto_trace(self) -> Dict[str, Any]:
        x0, y0, rw, rh = self.roi
        roi_img = self.gray[y0:y0 + rh, x0:x0 + rw]
        if roi_img.size == 0:
            return {"length_px": 0.0, "length_mm": None, "bbox": None}
        thr = self._dark_threshold if self._dark_threshold else (np.mean(roi_img) - self.p.dark_k * np.std(roi_img))
        binm = (roi_img < thr).astype(np.uint8)
        binm = cv2.morphologyEx(binm, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(binm, 8)
        if n <= 1:
            return {"length_px": 0.0, "length_mm": None, "bbox": None}
        best_i = int(np.argmax([stats[i, cv2.CC_STAT_AREA] for i in range(1, n)])) + 1
        comp = (labels == best_i).astype(np.uint8)
        # длина = диагональ bbox компоненты (грубая оценка протяжённости)
        cx, cy, cw, ch, area = stats[best_i]
        length_px = float(math.hypot(cw, ch))
        length_mm = round(length_px / self.px_per_mm, 3) if (self.px_per_mm and self.px_per_mm > 0) else None
        return {"length_px": round(length_px, 1), "length_mm": length_mm,
                "bbox": [int(x0 + cx), int(y0 + cy), int(cw), int(ch)], "area_px": int(area)}

    # --- пипетка ---
    def pipette(self, x: int, y: int, radius: int = 6) -> Dict[str, Any]:
        h, w = self.gray.shape
        x = int(np.clip(x, 0, w - 1))
        y = int(np.clip(y, 0, h - 1))
        x1, x2 = max(0, x - radius), min(w, x + radius + 1)
        y1, y2 = max(0, y - radius), min(h, y + radius + 1)
        patch = self.gray[y1:y2, x1:x2]
        thr = self._dark_threshold if self._dark_threshold else float(np.mean(patch))
        return {
            "x": x, "y": y, "radius": radius,
            "gray": int(self.gray[y, x]),
            "gray_mean": round(float(np.mean(patch)), 1),
            "gray_std": round(float(np.std(patch)), 1),
            "gray_min": int(np.min(patch)), "gray_max": int(np.max(patch)),
            "dark_frac": round(float(np.mean(patch < thr)), 3),
        }

    # --- сводка + предсказанный grade (перенос build_summary/_predict) ---
    def _opening_score(self) -> float:
        manual = OPENING_SCORE.get(self.p.opening)
        if manual is not None:
            return float(manual)
        marked = [c for c in self.cells if c.state in (CELL_CRACK, CELL_SUSPECT)]
        if not marked:
            return 0.0
        dark = float(np.mean([c.dark_frac for c in marked]))
        std = float(np.mean([c.gray_std for c in marked]))
        return float(np.clip(0.65 * dark + 0.35 * (std / 35.0), 0.0, 1.0))

    def _darkness_score(self, crack_mean, background_mean, dark_frac) -> float:
        manual = DARKNESS_SCORE.get(self.p.darkness)
        if manual is not None:
            return float(manual)
        if crack_mean is None or background_mean is None or background_mean <= 1e-6:
            return float(np.clip(dark_frac, 0.0, 1.0))
        delta = max(0.0, background_mean - crack_mean)
        contrast_ratio = delta / max(1.0, background_mean)
        return float(np.clip(0.55 * (delta / 65.0) + 0.45 * (contrast_ratio / 0.35) + 0.20 * dark_frac, 0.0, 1.0))

    def build_summary(self) -> Dict[str, Any]:
        x0, y0, rw, rh = self.roi
        n_cells = len(self.cells)
        crack_cells = [c for c in self.cells if c.state == CELL_CRACK]
        suspect_cells = [c for c in self.cells if c.state == CELL_SUSPECT]
        selected = crack_cells + suspect_cells
        clean_cells = [c for c in self.cells if c.state == CELL_CLEAN]

        zones = {"central": 0.0, "near_edge": 0.0, "edge": 0.0, "unknown": 0.0}
        for c in selected:
            z = _cell_zone(c, self.roi)
            zones[z] = zones.get(z, 0.0) + (1.0 if c.state == CELL_CRACK else 0.5)

        cluster_stats = _cluster_cells(selected)
        rows_occupied = len({c.row for c in selected})
        cols_occupied = len({c.col for c in selected})

        # трассы
        lengths_px = [polyline_length(t) for t in self.traces]
        longest_px = max(lengths_px) if lengths_px else 0.0
        total_px = float(sum(lengths_px))
        ref_len = max(float(rw), float(rh), 1.0)

        crack_mean = float(np.mean([c.gray_mean for c in selected])) if selected else None
        dark_frac_marked = float(np.mean([c.dark_frac for c in selected])) if selected else 0.0
        background_mean = float(np.mean([c.gray_mean for c in clean_cells])) if clean_cells else self._bg_mean
        delta_gray = None if (crack_mean is None or background_mean is None) else float(background_mean - crack_mean)
        contrast_ratio = None if (delta_gray is None or not background_mean) else float(delta_gray / max(1.0, background_mean))

        weighted_cells = len(crack_cells) + 0.5 * len(suspect_cells)
        weighted_cell_frac = weighted_cells / max(1, n_cells)
        amount_score = float(np.clip(weighted_cell_frac * 8.0, 0.0, 1.0))
        length_score = float(np.clip(max((longest_px / ref_len) * 2.3, (total_px / ref_len) * 0.80), 0.0, 1.0))
        opening_score = self._opening_score()
        darkness_score = self._darkness_score(crack_mean, background_mean, dark_frac_marked)
        edge_weighted = zones.get("edge", 0.0) + 0.55 * zones.get("near_edge", 0.0)
        edge_score = float(np.clip(edge_weighted / max(1.0, weighted_cells), 0.0, 1.0)) if weighted_cells else 0.0
        multiplicity_score = float(np.clip((cluster_stats["n_clusters"] - 1) / 4.0 + len(self.traces) / 8.0, 0.0, 1.0))

        severity = float(np.clip(
            0.22 * length_score + 0.24 * opening_score + 0.24 * darkness_score +
            0.14 * amount_score + 0.10 * multiplicity_score + 0.06 * edge_score, 0.0, 1.0))

        out = {
            "defect_type": self.p.defect_type,
            "operator_opening": self.p.opening, "operator_darkness": self.p.darkness,
            "n_cells": n_cells, "n_crack_cells": len(crack_cells), "n_suspect_cells": len(suspect_cells),
            "weighted_cell_count": round(weighted_cells, 2),
            "rows_occupied": rows_occupied, "cols_occupied": cols_occupied,
            "n_cell_clusters": cluster_stats["n_clusters"],
            "max_cluster_cells": cluster_stats["max_cluster_cells"],
            "central_weighted_cells": round(zones.get("central", 0.0), 2),
            "near_edge_weighted_cells": round(zones.get("near_edge", 0.0), 2),
            "edge_weighted_cells": round(zones.get("edge", 0.0), 2),
            "trace_count": len(self.traces),
            "trace_longest_length_px": round(float(longest_px), 1),
            "trace_total_length_px": round(total_px, 1),
            "crack_gray_mean": round(crack_mean, 1) if crack_mean is not None else None,
            "background_gray_mean": round(background_mean, 1) if background_mean is not None else None,
            "delta_gray": round(delta_gray, 1) if delta_gray is not None else None,
            "contrast_ratio": round(contrast_ratio, 3) if contrast_ratio is not None else None,
            "marked_dark_frac": round(dark_frac_marked, 3),
            "amount_score": round(amount_score, 3), "length_score": round(length_score, 3),
            "opening_score": round(opening_score, 3), "darkness_score": round(darkness_score, 3),
            "edge_score": round(edge_score, 3), "multiplicity_score": round(multiplicity_score, 3),
            "severity_score": round(severity, 3),
            "px_per_mm": round(self.px_per_mm, 4) if self.px_per_mm else None,
            "dark_threshold": round(self._dark_threshold, 1),
        }
        out["predicted_grade"] = self._predict_grade_from_scores(out)
        out["predicted_label"] = _grade_label(out["predicted_grade"])
        return out

    def _predict_grade_from_scores(self, summary: Dict[str, Any]) -> float:
        selected = int(summary.get("n_crack_cells", 0)) + int(summary.get("n_suspect_cells", 0))
        if selected == 0 and int(summary.get("trace_count", 0)) == 0:
            return 1.0
        severity = float(summary.get("severity_score", 0.0))
        opening = float(summary.get("opening_score", 0.0))
        darkness = float(summary.get("darkness_score", 0.0))
        edge = float(summary.get("edge_score", 0.0))
        mult = float(summary.get("multiplicity_score", 0.0))
        if severity < 0.13:
            g = 1.5
        elif severity < 0.30:
            g = 2.0
        elif severity < 0.52:
            g = 3.0
        elif severity < 0.76:
            g = 4.0
        else:
            g = 5.0
        if opening >= 0.75 and darkness >= 0.72 and (edge >= 0.35 or mult >= 0.45):
            g = max(g, 4.0)
        if opening >= 0.95 and darkness >= 0.90 and edge >= 0.45:
            g = max(g, 5.0)
        if g >= 4.0 and opening < 0.45 and darkness < 0.55 and mult < 0.35:
            g = 3.0
        return float(g)


def _grade_label(grade: float) -> str:
    # Дискретный предиктор возвращает {1.0,1.5,2.0,3.0,4.0,5.0}; пороги подобраны
    # так, чтобы граничные целые значения попадали в правильную категорию.
    g = float(grade)
    names = {1.75: "Минимальный", 2.5: "Слабый", 3.5: "Умеренный", 4.5: "Выраженный", 99.0: "Критический"}
    return next((n for t, n in names.items() if g < t), "Критический")


# ======================================================================
# ===  viz (overlays + optional matplotlib figures)  ===
# ======================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""viz.py — оверлеи и графики для тестера (numpy + cv2 + matplotlib Agg)."""





def to_rgb(img_bgr: np.ndarray) -> np.ndarray:
    if img_bgr.ndim == 2:
        return cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2RGB)
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)


def overlay_centerline(img_bgr: np.ndarray, res) -> np.ndarray:
    """ROI-полоса (жёлтая рамка) + центр (зелёная линия) + маска (красная)."""
    rgb = to_rgb(img_bgr).copy()
    roi = res.roi
    x1, x2 = int(roi["x_left"]), int(roi["x_right"])
    y1, y2 = int(roi["y_top"]), int(roi["y_bottom"])
    # маска
    mask = res._viz.get("dark_mask_full")
    if mask is not None and mask.shape[:2] == rgb.shape[:2]:
        red = np.zeros_like(rgb)
        red[..., 0] = 255
        m = mask > 0
        rgb[m] = (0.45 * rgb[m] + 0.55 * red[m]).astype(np.uint8)
    # ROI рамка
    cv2.rectangle(rgb, (x1, y1), (x2 - 1, y2 - 1), (255, 210, 0), 2)
    # центр и max_row
    cv2.line(rgb, (x1, int(res.center_y)), (x2, int(res.center_y)), (0, 220, 0), 1)
    cv2.line(rgb, (x1, int(res.max_row_y)), (x2, int(res.max_row_y)), (0, 150, 255), 1)
    return rgb


def overlay_grid(img_bgr: np.ndarray, cells, roi_xywh, trace_bbox=None) -> np.ndarray:
    """Сетка ячеек, окрашенная по состоянию; bbox авто-трассы."""
    rgb = to_rgb(img_bgr).copy()
    colors = {0: None, 1: (0, 200, 0), 2: (255, 190, 0)}  # clean/crack/suspect
    overlay = rgb.copy()
    for c in cells:
        col = colors.get(c.state)
        if col is None:
            cv2.rectangle(overlay, (c.x, c.y), (c.x + c.w - 1, c.y + c.h - 1), (90, 90, 90), 1)
            continue
        cv2.rectangle(overlay, (c.x, c.y), (c.x + c.w - 1, c.y + c.h - 1), col, -1)
        cv2.rectangle(rgb, (c.x, c.y), (c.x + c.w - 1, c.y + c.h - 1), col, 1)
    rgb = cv2.addWeighted(overlay, 0.35, rgb, 0.65, 0)
    x0, y0, rw, rh = [int(v) for v in roi_xywh]
    cv2.rectangle(rgb, (x0, y0), (x0 + rw - 1, y0 + rh - 1), (255, 210, 0), 2)
    if trace_bbox:
        bx, by, bw, bh = [int(v) for v in trace_bbox]
        cv2.rectangle(rgb, (bx, by), (bx + bw, by + bh), (255, 0, 0), 2)
    return rgb


def fig_profile(res):
    """Y-профиль яркости ROI с baseline и valley."""
    if plt is None:
        return None
    prof = res._viz.get("profile_y") or []
    fig, ax = plt.subplots(figsize=(6, 2.4))
    if prof:
        y = np.arange(len(prof))
        ax.plot(prof, y, color="#1f77b4", lw=1.3)
        ax.axvline(res._viz.get("baseline", 0), color="#888", ls="--", lw=1, label="baseline (80%)")
        vi = res._viz.get("valley_idx", 0)
        ax.scatter([prof[vi]], [vi], color="red", zorder=5, label="valley (центр)")
        ax.invert_yaxis()
        ax.set_xlabel("яркость (normalized)")
        ax.set_ylabel("строка ROI")
        ax.legend(fontsize=7, loc="lower right")
    ax.set_title(f"Y-профиль  •  valley_depth={res.valley_depth:.1f}", fontsize=9)
    fig.tight_layout()
    return fig


def fig_band(res):
    """col_mins вдоль полосы с порогом и бинарной длиной."""
    if plt is None:
        return None
    col_mins = res._viz.get("col_mins")
    binm = res._viz.get("band_binary")
    thr = res._viz.get("band_threshold")
    fig, ax = plt.subplots(figsize=(6, 2.2))
    if col_mins is not None:
        x = np.arange(len(col_mins))
        ax.plot(col_mins, color="#444", lw=0.8, label="col_min (тёмность)")
        if thr is not None:
            ax.axhline(thr, color="red", ls="--", lw=1, label=f"порог={thr:.0f}")
        if binm is not None:
            ymax = float(np.max(col_mins)) if len(col_mins) else 1.0
            ax.fill_between(x, 0, ymax, where=(np.asarray(binm) > 0), color="#2ca02c", alpha=0.18,
                            step="mid", label="полоса")
        ax.set_xlabel("столбец ROI (длина →)")
        ax.legend(fontsize=7, loc="upper right")
    ax.set_title(f"Полоса осевой  •  band_ratio={res.band_ratio:.2f}  longest={res.band_longest}px",
                 fontsize=9)
    fig.tight_layout()
    return fig


def fig_scores(summary: Dict[str, Any]):
    """Горизонтальные бары шести score-компонент генерик-скоринга."""
    if plt is None:
        return None
    keys = [("amount_score", "масса"), ("length_score", "длина"), ("opening_score", "раскрытие"),
            ("darkness_score", "темнота"), ("edge_score", "край"), ("multiplicity_score", "множеств.")]
    vals = [float(summary.get(k, 0.0)) for k, _ in keys]
    labels = [lbl for _, lbl in keys]
    fig, ax = plt.subplots(figsize=(5, 2.4))
    bars = ax.barh(labels, vals, color="#1f77b4")
    ax.set_xlim(0, 1)
    ax.invert_yaxis()
    for b, v in zip(bars, vals):
        ax.text(min(v + 0.02, 0.92), b.get_y() + b.get_height() / 2, f"{v:.2f}",
                va="center", fontsize=8)
    ax.set_title(f"severity={summary.get('severity_score', 0):.2f}", fontsize=9)
    fig.tight_layout()
    return fig


import sys as _sys
viz = _sys.modules[__name__]



# ======================================================================
# ===  Streamlit application  ===
# ======================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — SenseOptics Defect Tester (web / Replit free-tier)

Веб-аналог crack_zone_tester2 для тестирования осевой ликвации и других
дефектов макрошлифов. Лёгкий стек (Streamlit + OpenCV-headless + SciPy),
без PyQt5 и без ML-моделей — помещается в бесплатный план Replit.

Запуск локально:  streamlit run app.py
"""




st.set_page_config(page_title="SenseOptics Defect Tester", page_icon="🔬", layout="wide")

MAX_DISPLAY_W = 1100  # ширина показа в UI (исходник анализируется в рабочем разрешении)


# ════════════════════════════════════════════════════════════════════
#  Загрузка изображения
# ════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def decode_image(file_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


@st.cache_data(show_spinner=False)
def downscale(img: np.ndarray, max_w: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= max_w:
        return img
    s = max_w / float(w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def img_to_png_bytes(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return buf.tobytes() if ok else b""


def metrics_csv(d: dict) -> str:
    lines = ["key,value"]
    for k, v in d.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k},{v}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
#  Sidebar: изображение + калибровка + рабочее разрешение
# ════════════════════════════════════════════════════════════════════

st.sidebar.title("🔬 SenseOptics Tester")
st.sidebar.caption("Тестер дефектов макрошлифов · web / free-tier")

up = st.sidebar.file_uploader("Темплет (изображение)", type=["jpg", "jpeg", "png", "bmp", "tif", "tiff"])

# опциональный встроенный пример
sample_dir = Path(__file__).parent / "sample"
samples = sorted([p for p in sample_dir.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")]) if sample_dir.exists() else []
use_sample = None
if not up and samples:
    if st.sidebar.checkbox("Использовать встроенный пример", value=True):
        use_sample = st.sidebar.selectbox("Пример", samples, format_func=lambda p: p.name)

img_bgr = None
src_name = None
if up is not None:
    img_bgr = decode_image(up.getvalue())
    src_name = up.name
elif use_sample is not None:
    img_bgr = decode_image(use_sample.read_bytes())
    src_name = use_sample.name

if img_bgr is None:
    st.title("SenseOptics Defect Tester")
    st.info("⬅️ Загрузите изображение темплета в боковой панели, чтобы начать.\n\n"
            "Инструмент тестирует **осевую ликвацию** (классический грейдер 1..5) и **другие дефекты** "
            "(сетка / пипетка / трасса со скорингом), не требуя PyQt5 и ML-моделей.")
    st.stop()

max_w = st.sidebar.slider("Рабочая ширина, px", 600, 4000, 1600, step=100,
                          help="Большие изображения масштабируются для экономии памяти free-плана.")
work = downscale(img_bgr, max_w)
H, W = work.shape[:2]
gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
st.sidebar.caption(f"Файл: {src_name}  ·  рабочее: {W}×{H}px")

# ── Калибровка px/mm ──
st.sidebar.subheader("Калибровка")
cal_mode = st.sidebar.radio("Способ", ["Не задана", "Прямой px/mm", "Две точки"], horizontal=False)
px_per_mm = None
if cal_mode == "Прямой px/mm":
    px_per_mm = st.sidebar.number_input("px на 1 мм", min_value=0.0, value=10.0, step=0.5)
    px_per_mm = px_per_mm if px_per_mm > 0 else None
elif cal_mode == "Две точки":
    c1, c2 = st.sidebar.columns(2)
    x1 = c1.number_input("x1", 0, W, 0); y1 = c2.number_input("y1", 0, H, 0)
    x2 = c1.number_input("x2", 0, W, min(100, W)); y2 = c2.number_input("y2", 0, H, 0)
    real_mm = st.sidebar.number_input("реальное расстояние, мм", min_value=0.0, value=10.0, step=0.5)
    d = float(np.hypot(x2 - x1, y2 - y1))
    if real_mm > 0 and d > 1e-6:
        px_per_mm = d / real_mm
        st.sidebar.caption(f"→ {px_per_mm:.3f} px/mm")

st.sidebar.divider()
st.sidebar.caption("Классический грейд — авторитетный. ML-надстройки нет.")


# ════════════════════════════════════════════════════════════════════
#  Tabs
# ════════════════════════════════════════════════════════════════════

tab_seg, tab_gen, tab_help = st.tabs(["🩻 Осевая сегрегация", "🧩 Другие дефекты", "ℹ️ Справка"])


# ─────────────────────────────────────────────────────────────────────
#  TAB 1 — Centerline / Axial segregation
# ─────────────────────────────────────────────────────────────────────
with tab_seg:
    st.subheader("Осевая ликвация (Centerline Segregation)")
    left, right = st.columns([2, 1])

    with right:
        roi_mode = st.radio("ROI", ["Авто", "Ручная полоса", "Полный numeric"], horizontal=True)
        with st.expander("Параметры анализа", expanded=False):
            margin = st.slider("margin (обрезка краёв)", 0.0, 0.15, 0.05, 0.01)
            band_half = st.slider("band_half_height", 5, 40, 15, 1)
            heal_gap = st.slider("band_heal_gap", 0, 40, 15, 1)
            roi_half = st.slider("roi_half_fraction", 0.01, 0.15, 0.05, 0.01)
            vtop = st.slider("valley_scan_top", 0.0, 0.4, 0.15, 0.05)
            vbot = st.slider("valley_scan_bottom", 0.6, 1.0, 0.85, 0.05)

        settings = AnalysisSettings(margin=margin, roi_half_fraction=roi_half,
                                    band_half_height=band_half, band_heal_gap=heal_gap,
                                    valley_scan_top=vtop, valley_scan_bottom=vbot)
        core = CenterlineCore(settings)
        core.set_image(work)

        roi = None
        if roi_mode == "Ручная полоса":
            yt, yb = st.slider("полоса по Y (доля высоты)", 0.0, 1.0, (0.42, 0.58), 0.01)
            roi = ROIRegion(y_top=int(yt * H), y_bottom=int(yb * H), x_left=0, x_right=W)
        elif roi_mode == "Полный numeric":
            cc1, cc2 = st.columns(2)
            rx1 = cc1.number_input("x_left", 0, W, 0)
            rx2 = cc2.number_input("x_right", 0, W, W)
            ry1 = cc1.number_input("y_top", 0, H, int(0.42 * H))
            ry2 = cc2.number_input("y_bottom", 0, H, int(0.58 * H))
            roi = ROIRegion(y_top=ry1, y_bottom=ry2, x_left=rx1, x_right=rx2)
        if roi is not None:
            core.set_roi(roi)

        try:
            res = core.analyze(px_per_mm=px_per_mm)
        except Exception as e:
            st.error(f"Ошибка анализа: {e}")
            st.stop()

        # грейд-карточка
        st.metric("Класс осевой ликвации", f"G{res.grade:.2f}", res.grade_label)
        if res.boost_str:
            st.caption(f"Поправки: `{res.boost_str.strip()}`")
        diag = res._viz.get("auto_roi_diag", {})
        if roi_mode == "Авто" and diag.get("confidence") is not None:
            conf = diag["confidence"]
            st.caption(f"Уверенность авто-ROI: {conf:.2f} "
                       + ("✅" if conf >= 0.5 else "⚠️ проверьте ROI вручную"))

    with left:
        st.image(viz.overlay_centerline(work, res), use_container_width=True,
                 caption="Жёлтый — ROI · зелёный — центр · синий — max-row · красный — маска")
        if HAS_MPL:
            cpa, cpb = st.columns(2)
            cpa.pyplot(viz.fig_profile(res), use_container_width=True)
            cpb.pyplot(viz.fig_band(res), use_container_width=True)
        else:
            st.caption("📉 Графики профиля/полосы отключены (matplotlib не установлен). Оверлей выше работает.")

    # описание + метрики
    title, base, bullets = describe_segregation(res)
    st.markdown(f"**{title}** — {base}")
    if bullets:
        st.markdown("\n".join(f"- {b}" for b in bullets))

    with st.expander("Все метрики (диагностика)", expanded=False):
        jd = res.to_json_dict()
        show = {k: v for k, v in jd.items() if not isinstance(v, dict)}
        st.json(show)

    dl1, dl2 = st.columns(2)
    payload = res.to_json_dict()
    payload["source"] = src_name
    payload["working_size"] = [W, H]
    dl1.download_button("⬇️ JSON", json.dumps(payload, ensure_ascii=False, indent=2),
                        file_name=f"centerline_{Path(src_name).stem}.json", mime="application/json")
    dl2.download_button("⬇️ CSV", metrics_csv(payload),
                        file_name=f"centerline_{Path(src_name).stem}.csv", mime="text/csv")
    ov = viz.overlay_centerline(work, res)
    st.download_button("⬇️ Оверлей PNG", img_to_png_bytes(ov),
                       file_name=f"centerline_{Path(src_name).stem}_overlay.png", mime="image/png")


# ─────────────────────────────────────────────────────────────────────
#  TAB 2 — Generic defect tester
# ─────────────────────────────────────────────────────────────────────
with tab_gen:
    st.subheader("Генерик-тестер дефектов (трещины, точки и др.)")
    gleft, gright = st.columns([2, 1])

    with gright:
        st.markdown("**ROI** (доля изображения)")
        gx = st.slider("X", 0.0, 1.0, (0.0, 1.0), 0.01)
        gy = st.slider("Y", 0.0, 1.0, (0.0, 1.0), 0.01)
        rx0 = int(gx[0] * W); rw = max(4, int((gx[1] - gx[0]) * W))
        ry0 = int(gy[0] * H); rh = max(4, int((gy[1] - gy[0]) * H))
        roi_xywh = (rx0, ry0, rw, rh)

        with st.expander("Сетка и пороги", expanded=True):
            rows = st.slider("строк сетки", 2, 24, 8)
            cols = st.slider("столбцов сетки", 2, 60, 24)
            dark_k = st.slider("dark_k (порог тёмного = mean − k·std)", 0.2, 3.0, 1.2, 0.1)
            crack_df = st.slider("crack: доля тёмных ≥", 0.05, 0.9, 0.35, 0.05)
            suspect_df = st.slider("suspect: доля тёмных ≥", 0.02, 0.6, 0.15, 0.05)

        with st.expander("Операторские оценки", expanded=False):
            dtype = st.selectbox("тип дефекта", ["narrow", "corner", "internal", "other"])
            opening = st.selectbox("раскрытие", ["auto", "closed", "semi_open", "open", "strongly_open"])
            darkness = st.selectbox("темнота", ["auto", "weak", "medium", "dark", "black"])

        params = GenericParams(grid_rows=rows, grid_cols=cols, dark_k=dark_k,
                               crack_dark_frac=crack_df, suspect_dark_frac=suspect_df,
                               defect_type=dtype, opening=opening, darkness=darkness)
        tester = GenericDefectTester(gray, roi_xywh, params, px_per_mm)
        tester.build_grid()
        trace = tester.auto_trace()
        if trace.get("length_px", 0) > 0 and trace.get("bbox"):
            bx, by, bw, bh = trace["bbox"]
            tester.traces.append([[bx, by], [bx + bw, by + bh]])
        summ = tester.build_summary()

        st.metric("Предсказанный класс", f"G{summ['predicted_grade']:.1f}", summ["predicted_label"])
        st.caption(f"crack-ячеек: {summ['n_crack_cells']} · suspect: {summ['n_suspect_cells']} · "
                   f"кластеров: {summ['n_cell_clusters']}")
        if trace.get("length_px", 0) > 0:
            mm = f" / {trace['length_mm']} мм" if trace.get("length_mm") else ""
            st.caption(f"Авто-трасса (длиннейший фрагмент): {trace['length_px']} px{mm}")

    with gleft:
        st.image(viz.overlay_grid(work, tester.cells, roi_xywh, trace.get("bbox")),
                 use_container_width=True,
                 caption="Зелёный — crack · жёлтый — suspect · красный — bbox трассы")
        if HAS_MPL:
            st.pyplot(viz.fig_scores(summ), use_container_width=True)
        else:
            st.caption("📊 Бар-график score-компонент отключён (matplotlib не установлен). Значения см. ниже в метриках.")

    # пипетка
    with st.expander("🔍 Пипетка (статистика яркости в точке)", expanded=False):
        pc1, pc2, pc3 = st.columns(3)
        px = pc1.number_input("x", 0, W - 1, W // 2)
        py = pc2.number_input("y", 0, H - 1, H // 2)
        pr = pc3.number_input("радиус", 1, 50, 6)
        pip = tester.pipette(px, py, pr)
        st.json(pip)

    with st.expander("Сводка (диагностика)", expanded=False):
        st.json(summ)

    gp = dict(summ)
    gp["source"] = src_name
    gp["roi_xywh"] = list(roi_xywh)
    gp["auto_trace"] = trace
    gd1, gd2 = st.columns(2)
    gd1.download_button("⬇️ JSON", json.dumps(gp, ensure_ascii=False, indent=2),
                        file_name=f"defect_{Path(src_name).stem}.json", mime="application/json")
    gd2.download_button("⬇️ CSV", metrics_csv(gp),
                        file_name=f"defect_{Path(src_name).stem}.csv", mime="text/csv")


# ─────────────────────────────────────────────────────────────────────
#  TAB 3 — Help
# ─────────────────────────────────────────────────────────────────────
with tab_help:
    st.subheader("Как пользоваться")
    st.markdown(
        """
**Осевая сегрегация** — это перенос классического грейдера `centerline_segregation_graderv5`
в безголовый вид (без PyQt5/ML). Метрики и кусочно-линейный grade 1..5, бусты T/L/C/H/S
и anti-inflation cap'ы воспроизведены 1:1 с продовым путём `analyze() → calculate_metrics()`
в окружении без U-Net-моделей.

- **ROI = Авто** — алгоритм сам находит полосу осевой по провалу Y-профиля.
- **Ручная полоса** — задайте границы полосы по доле высоты (для смещённой/двойной оси).
- Графики: слева Y-профиль (`valley_depth`), справа разрез полосы (`band_ratio`, `longest`).

**Другие дефекты** — генерик-аналог `crack_zone_tester2`: ROI разбивается на сетку,
ячейки авто-классифицируются по доле тёмных пикселей (`crack`/`suspect`/`clean`),
строится severity-скоринг и предсказанный класс по формулам оригинала.
«Пипетка» даёт статистику яркости в точке, «авто-трасса» — длину длиннейшего тёмного фрагмента.

**Калибровка px/mm** в боковой панели включает мм-длины (`band_longest_mm`, длину трассы).

**Важно:** значения метрик считаются в *рабочем разрешении* (после масштабирования по ширине).
Для абсолютных мм задавайте калибровку на том же рабочем изображении.
        """
    )
    st.caption("Переменные окружения для калибровки центра наследуются из проекта: "
               "CENTERLINE_LOW_MASS_CAP_MAX, CENTERLINE_LOW_MASS_CAP_DISABLE, "
               "CENTERLINE_ETCHED_RESCUE_ENABLE и т.д.")
