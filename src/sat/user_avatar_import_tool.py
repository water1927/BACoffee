from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import MSFluentWindow

from avatar_import_core import (
    AvatarImportError,
    FINAL_SIZE,
    Inspection,
    default_crop_box,
    inspect_avatar,
    invitation_recognition_precheck,
    read_rgba_file,
    write_import_atomic,
)
from user_tool_core import (
    PathSelectionError,
    CARD_OPACITY_DEFAULT,
    CARD_OPACITY_MAX,
    CARD_OPACITY_MIN,
    OVERLAY_TRANSPARENCY_DEFAULT,
    OVERLAY_TRANSPARENCY_MAX,
    OVERLAY_TRANSPARENCY_MIN,
    BUILTIN_LIBRARY_GUIDANCE,
    StudentLibraryContext,
    ToolSettings,
    card_opacity_default,
    check_student_library_duplicate,
    discover_student_libraries,
    overlay_transparency_default,
    resolve_student_library,
    student_library_context_or_error,
)


TOOL_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1]
)
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ASSET_DIR = BUNDLE_ROOT / "assets"
PRODUCT_NAME = "BACoffee Student Add Tool"
PRODUCT_SHORT_NAME = "SAT"
IMPORT_SUCCESS_NOTICE = (
    "导入成功。重新打开 BACoffee 的学生选择窗口后即可选择该学生。"
)
SELF_TEST_TIMEOUT_SECONDS = 30
_SELF_TEST_LOG_FILE: Path | None = None


def _begin_self_test_evidence(arguments: list[str]) -> None:
    """Open a file-backed trace that remains available in a windowed EXE."""
    global _SELF_TEST_LOG_FILE
    candidates = (
        TOOL_ROOT / "data" / "self_test.log",
        Path(os.environ.get("TEMP", str(TOOL_ROOT)))
        / "BACoffee_SAT_self_test.log",
    )
    header = (
        f"pid={os.getpid()} args={arguments!r}\n"
        "stage=1 module entry received complete command line\n"
    )
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(header, encoding="utf-8")
            _SELF_TEST_LOG_FILE = candidate
            return
        except OSError:
            continue


def _write_self_test_log(line: str) -> None:
    if _SELF_TEST_LOG_FILE is None:
        return
    try:
        with _SELF_TEST_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")
            handle.flush()
    except OSError:
        pass


def _write_self_test_stage(stage: str) -> None:
    _write_self_test_log(f"stage={stage}")


def _safe_diagnostic_output(message: str, *, error: bool = False) -> None:
    """Write diagnostics without triggering a windowed-EXE error dialog."""
    stream = sys.stderr if error else sys.stdout
    wrote_stream = False
    if stream is not None and not getattr(stream, "closed", False):
        try:
            stream.write(message + "\n")
            stream.flush()
            wrote_stream = True
        except (OSError, ValueError, AttributeError):
            pass
    if not wrote_stream:
        _write_self_test_log(f"output={message}")


def _flush_diagnostic_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        flush = getattr(stream, "flush", None)
        if flush is None:
            continue
        try:
            flush()
        except (OSError, ValueError):
            pass


def _self_test_timeout() -> None:
    _write_self_test_stage(
        f"TIMEOUT after {SELF_TEST_TIMEOUT_SECONDS}s; forcing nonzero process exit"
    )
    _flush_diagnostic_streams()
    os._exit(124)


def _start_self_test_watchdog() -> threading.Timer:
    watchdog = threading.Timer(SELF_TEST_TIMEOUT_SECONDS, _self_test_timeout)
    watchdog.daemon = True
    watchdog.start()
    return watchdog


PALETTES = {
    "dark": {
        "window": "#0b0f1b",
        "text": "#f4f6fb",
        "muted": "#bdc6d8",
        "card": "rgba(38,40,47,255)",
        "card_alt": "rgba(30,34,46,255)",
        "border": "#3d4761",
        "input": "rgba(10,13,24,235)",
        "button": "#343d59",
        "button_hover": "#46516f",
        "accent": "#ff79a8",
        "accent_hover": "#ff9bc0",
        "accent_text": "#241321",
        "success": "#79e2a5",
        "warning": "#ffc27a",
        "danger": "#ff8c9f",
        "titlebar": "#141a2b",
        "titlebar_text": "#f4f6fb",
        "overlay": QColor(24, 19, 26),
        "preview": "rgba(12,15,27,205)",
        "preview_panel": "rgba(8,12,22,92)",
    },
    "light": {
        "window": "#eef3f7",
        "text": "#1f2937",
        "muted": "#4b5563",
        "card": "rgba(255,255,255,255)",
        "card_alt": "rgba(247,250,252,255)",
        "border": "#b8c8d6",
        "input": "rgba(255,255,255,242)",
        "button": "#e5edf3",
        "button_hover": "#d5e6f0",
        "accent": "#1976a8",
        "accent_hover": "#115f88",
        "accent_text": "#ffffff",
        "success": "#177548",
        "warning": "#9a5a00",
        "danger": "#b42338",
        "titlebar": "#f4f8fb",
        "titlebar_text": "#1f2937",
        "overlay": QColor(245, 250, 253),
        "preview": "rgba(255,255,255,235)",
        "preview_panel": "rgba(255,255,255,76)",
    },
}


def _material_rgba(color: str, transparency: int, alpha_ceiling: int = 255) -> str:
    """Apply user-facing transparency while keeping the endpoints exact."""
    match = re.fullmatch(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*\)",
        color,
    )
    if match is None:
        hex_match = re.fullmatch(r"#([0-9a-fA-F]{6})", color)
        if hex_match is None:
            return color
        raw = hex_match.group(1)
        match = re.fullmatch(r"(..)(..)(..)", raw)
        assert match is not None
        red, green, blue = (int(part, 16) for part in match.groups())
    else:
        red, green, blue = (int(part) for part in match.groups())
    normalized = max(0, min(100, int(transparency)))
    if normalized == 0:
        alpha = 255
    elif normalized == 100:
        alpha = 0
    else:
        alpha = round(max(0, min(255, int(alpha_ceiling))) * (100 - normalized) / 100)
    return f"rgba({red},{green},{blue},{alpha})"


def _qcolor_from_css(color: str) -> QColor:
    """Convert the stylesheet rgba form into a real QColor for custom painting."""
    match = re.fullmatch(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
        color,
    )
    if match is not None:
        red, green, blue, alpha = (int(part) for part in match.groups())
        return QColor(red, green, blue, alpha)
    result = QColor(color)
    return result if result.isValid() else QColor(0, 0, 0, 0)


def _transparency_alpha(transparency: int) -> int:
    """Convert user-facing transparency to a real 0..255 alpha value."""
    normalized = max(0, min(100, int(transparency)))
    return round(255 * (100 - normalized) / 100)


def _homepage_card_rgba(color: str, transparency: int, theme: str) -> str:
    """Build homepage card RGBA, including the specified light default alpha."""
    base = _material_rgba(color, 0)
    match = re.fullmatch(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*\)",
        base,
    )
    if match is None:
        return base
    normalized = max(0, min(100, int(transparency)))
    alpha = (
        60
        if theme == "light" and normalized == 76
        else _transparency_alpha(normalized)
    )
    red, green, blue = (int(part) for part in match.groups())
    return f"rgba({red},{green},{blue},{alpha})"


def _overlay_color(color: QColor, transparency: int) -> QColor:
    result = QColor(color)
    result.setAlpha(_transparency_alpha(transparency))
    return result


def _rgba_to_display(rgba: np.ndarray, tile: int = 16) -> np.ndarray:
    height, width = rgba.shape[:2]
    yy, xx = np.indices((height, width))
    light = ((xx // tile + yy // tile) % 2 == 0)
    checker = np.where(light[..., None], 236, 204).astype(np.float32)
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgba[:, :, :3].astype(np.float32)
    return np.clip(rgb * alpha + checker * (1.0 - alpha), 0, 255).astype(np.uint8)


def _bgr_to_display(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _pixmap_from_rgb(image: np.ndarray) -> QPixmap:
    height, width = image.shape[:2]
    contiguous = np.ascontiguousarray(image)
    qimage = QImage(
        contiguous.data, width, height, contiguous.strides[0], QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class Win11ScrollBarAnimator(QWidget):
    """BAC-compatible thin scrollbar thumb with visual-only hover animation."""

    def __init__(self, bar, accent: str, dark: bool):
        super().__init__(bar)
        self.bar = bar
        self.accent = QColor(accent)
        self.dark = dark
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        bar.setMouseTracking(True)
        bar.installEventFilter(self)
        self._apply_style()

    def set_theme(self, accent: str, dark: bool) -> None:
        self.accent = QColor(accent)
        self.dark = dark
        self._apply_style()

    def eventFilter(self, watched, event):
        if watched is self.bar and event.type() in (QEvent.Enter, QEvent.Leave):
            target = 1.0 if event.type() == QEvent.Enter else 0.0
            self.animation.stop()
            self.animation.setStartValue(self.progress)
            self.animation.setEndValue(target)
            self.animation.start()
        return False

    def _animate(self, value) -> None:
        self.progress = float(value)
        self._apply_style()

    def _color(self) -> QColor:
        idle = QColor(220, 224, 232, 54) if self.dark else QColor(70, 78, 88, 46)
        accent = QColor(self.accent)
        accent.setAlpha(220)
        p = self.progress
        return QColor(
            round(idle.red() + (accent.red() - idle.red()) * p),
            round(idle.green() + (accent.green() - idle.green()) * p),
            round(idle.blue() + (accent.blue() - idle.blue()) * p),
            round(idle.alpha() + (accent.alpha() - idle.alpha()) * p),
        )

    def _apply_style(self) -> None:
        # Keep an 8px layout footprint; only the painted thumb changes width.
        thickness = 2 + round(4 * self.progress)
        inset = max(1, (8 - thickness) // 2)
        radius = max(1, thickness // 2)
        color = self._color()
        rgba = f"rgba({color.red()},{color.green()},{color.blue()},{color.alpha()})"
        if self.bar.orientation() == Qt.Vertical:
            style = (
                "QScrollBar:vertical { background: transparent; width: 8px; "
                "margin: 6px 3px 6px 0; border: none; }"
                f"QScrollBar::handle:vertical {{ background: {rgba}; min-height: 34px; "
                f"border: none; border-radius: {radius}px; margin: 0 {inset}px; }}"
                "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }"
                "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            )
        else:
            style = (
                "QScrollBar:horizontal { background: transparent; height: 8px; "
                "margin: 0 6px 3px 6px; border: none; }"
                f"QScrollBar::handle:horizontal {{ background: {rgba}; min-width: 34px; "
                f"border: none; border-radius: {radius}px; margin: {inset}px 0; }}"
                "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; }"
                "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
            )
        self.bar.setStyleSheet(style)


def _install_win11_scrollbars(owner, widget, accent: str) -> None:
    if not hasattr(owner, "_win11_scrollbars"):
        owner._win11_scrollbars = []
    for bar in (widget.verticalScrollBar(), widget.horizontalScrollBar()):
        owner._win11_scrollbars.append(
            Win11ScrollBarAnimator(bar, accent, owner.current_theme == "dark"))


class PreviewLabel(QLabel):
    def __init__(self, title: str, minimum=(180, 150)):
        super().__init__()
        self.placeholder = title
        self._image: np.ndarray | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(*minimum)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setText(title)

    def set_theme(self, palette: dict, card_opacity: int = CARD_OPACITY_DEFAULT) -> None:
        self.setStyleSheet(
            f"QLabel {{ background: transparent; border: none; color: {palette['muted']}; }}")

    def set_image(self, image: np.ndarray | None) -> None:
        self._image = image
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        image = self._image
        if image is None:
            self.setPixmap(QPixmap())
            self.setText(self.placeholder if self.placeholder else "暂无预览")
            return
        self.setText("")
        pixmap = _pixmap_from_rgb(image)
        self.setPixmap(pixmap.scaled(
            self.contentsRect().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        self._refresh_pixmap()
        super().resizeEvent(event)


class NameLineEdit(QLineEdit):
    focusChanged = pyqtSignal(bool)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focusChanged.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focusChanged.emit(False)


class PureBackgroundButton(QPushButton):
    """Compact SAT-owned toggle that keeps its exit affordance visible."""

    def __init__(self, icon_path: Path):
        super().__init__()
        self._label = "纯净背景"
        self.icon_path = icon_path
        self._icon_loaded = False
        icon = QIcon(str(icon_path))
        if icon_path.is_file() and not icon.isNull():
            self.setIcon(icon)
            self.setIconSize(QSize(18, 18))
            self._icon_loaded = True
        self.setText(self._label if not self._icon_loaded else "")
        self.setToolTip(self._label)
        self.setAccessibleName(self._label)
        self.setCheckable(True)

    def set_theme(self, accent: str) -> None:
        """Tint the bundled coffee bean with a restrained theme alpha."""
        if not self._icon_loaded:
            return
        source = QImage(str(self.icon_path)).convertToFormat(QImage.Format_ARGB32)
        tinted = QImage(source.size(), QImage.Format_ARGB32)
        tinted.fill(QColor(0, 0, 0, 0))
        color = QColor(accent)
        color.setAlpha(156)
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(tinted.rect(), color)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, source)
        painter.end()
        self.setIcon(QIcon(QPixmap.fromImage(tinted)))


class PersistentThemeButtonSlot(QWidget):
    """Keep the theme-button layout cell while the button is hidden in pure mode."""

    def __init__(self, button: QPushButton):
        super().__init__()
        self._button = button
        self.setSizePolicy(button.sizePolicy())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(button)

    def sizeHint(self) -> QSize:
        return self._button.sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self._button.minimumSizeHint()


class CropPreview(QWidget):
    """Small inline crop editor for the ordinary import flow."""

    cropChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.source_image: np.ndarray | None = None
        self.crop_box: tuple[int, int, int, int] | None = None
        self._drag_start: QPoint | None = None
        self._drag_box: tuple[int, int, int, int] | None = None
        self.setMinimumSize(180, 180)
        self.setMaximumHeight(220)
        self.setMouseTracking(True)

    def set_source(self, image: np.ndarray | None, crop_box=None) -> None:
        self.source_image = image
        self.crop_box = crop_box
        self._drag_start = None
        self._drag_box = None
        self.update()

    def set_crop_box(self, crop_box) -> None:
        self.crop_box = crop_box
        self.update()

    def _image_rect(self):
        if self.source_image is None:
            return None, 1.0
        height, width = self.source_image.shape[:2]
        area = self.rect().adjusted(8, 8, -8, -8)
        scale = min(area.width() / max(1, width), area.height() / max(1, height))
        draw_width = max(1, round(width * scale))
        draw_height = max(1, round(height * scale))
        left = area.left() + (area.width() - draw_width) // 2
        top = area.top() + (area.height() - draw_height) // 2
        return (left, top, draw_width, draw_height), scale

    def _box_from_point(self, point: QPoint, box=None):
        if self.source_image is None:
            return None
        image_rect, scale = self._image_rect()
        if image_rect is None or scale <= 0:
            return None
        left, top, _, _ = image_rect
        height, width = self.source_image.shape[:2]
        current = box or self.crop_box or default_crop_box(self.source_image)
        side = current[2] - current[0]
        cx = (current[0] + current[2]) / 2
        cy = (current[1] + current[3]) / 2
        px = (point.x() - left) / scale
        py = (point.y() - top) / scale
        margin = side / 2
        x = max(margin, min(width - margin, px))
        y = max(margin, min(height - margin, py))
        return int(round(x)), int(round(y)), side

    @staticmethod
    def _clamped_box(cx, cy, side, width, height):
        side = max(1, min(int(round(side)), width, height))
        x = max(0, min(int(round(cx - side / 2)), width - side))
        y = max(0, min(int(round(cy - side / 2)), height - side))
        return x, y, x + side, y + side

    def _crop_rect(self):
        image_rect, scale = self._image_rect()
        if image_rect is None or self.crop_box is None:
            return None
        left, top, _, _ = image_rect
        x1, y1, x2, y2 = self.crop_box
        return (
            left + round(x1 * scale),
            top + round(y1 * scale),
            max(1, round((x2 - x1) * scale)),
            max(1, round((y2 - y1) * scale)),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(15, 18, 30, 210))
        if self.source_image is None:
            painter.setPen(QColor(190, 198, 216))
            painter.drawText(self.rect(), Qt.AlignCenter, "拖动图片调整裁剪")
            painter.end()
            return
        image = self.source_image
        if image.ndim == 3 and image.shape[2] == 4:
            display = _rgba_to_display(cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA))
        else:
            display = _bgr_to_display(image)
        image_rect, scale = self._image_rect()
        left, top, draw_width, draw_height = image_rect
        pixmap = _pixmap_from_rgb(display).scaled(
            draw_width, draw_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(left, top, pixmap)
        crop_rect = self._crop_rect()
        if crop_rect is not None:
            x, y, width, height = crop_rect
            overlay = QColor(5, 8, 15, 135)
            painter.fillRect(left, top, draw_width, max(0, y - top), overlay)
            painter.fillRect(left, y + height, draw_width,
                             max(0, top + draw_height - y - height), overlay)
            painter.fillRect(left, y, max(0, x - left), height, overlay)
            painter.fillRect(x + width, y, max(0, left + draw_width - x - width), height, overlay)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawEllipse(x, y, width, height)
            painter.setPen(QColor(255, 160, 205, 220))
            painter.drawRect(x, y, width, height)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.crop_box is None:
            return
        crop_rect = self._crop_rect()
        if crop_rect is None:
            return
        x, y, width, height = crop_rect
        if x <= event.pos().x() <= x + width and y <= event.pos().y() <= y + height:
            self._drag_start = event.pos()
            self._drag_box = self.crop_box

    def mouseMoveEvent(self, event):
        if self._drag_start is None or self._drag_box is None or self.source_image is None:
            return
        image_rect, scale = self._image_rect()
        if image_rect is None or scale <= 0:
            return
        dx = (event.pos().x() - self._drag_start.x()) / scale
        dy = (event.pos().y() - self._drag_start.y()) / scale
        x1, y1, x2, y2 = self._drag_box
        height, width = self.source_image.shape[:2]
        side = x2 - x1
        box = self._clamped_box((x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy,
                                side, width, height)
        self.crop_box = box
        self.cropChanged.emit(box)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._drag_box = None

    def wheelEvent(self, event):
        if self.crop_box is None or self.source_image is None:
            return
        x1, y1, x2, y2 = self.crop_box
        side = x2 - x1
        factor = 0.9 if event.angleDelta().y() > 0 else 1.1
        height, width = self.source_image.shape[:2]
        box = self._clamped_box((x1 + x2) / 2, (y1 + y2) / 2,
                                side * factor, width, height)
        self.crop_box = box
        self.cropChanged.emit(box)
        self.update()


class BackgroundWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.background_path: Path | None = None
        self.theme = "dark"
        self.overlay_transparency = OVERLAY_TRANSPARENCY_DEFAULT
        self._paint_enabled = True
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set_background(self, path: Path, theme: str) -> None:
        self.background_path = path
        self.theme = theme
        self.update()

    def set_overlay_transparency(self, value: int) -> None:
        self.overlay_transparency = max(
            OVERLAY_TRANSPARENCY_MIN,
            min(OVERLAY_TRANSPARENCY_MAX, int(value)),
        )
        self.update()

    def set_paint_enabled(self, enabled: bool) -> None:
        """Allow the top-level window to paint the same background under its title bar."""
        self._paint_enabled = bool(enabled)
        self.update()

    def paint_to(self, painter: QPainter, target: QRect) -> None:
        palette = PALETTES.get(self.theme, PALETTES["dark"])
        painter.save()
        painter.fillRect(target, QColor(palette["window"]))
        if self.background_path and self.background_path.is_file():
            pixmap = QPixmap(str(self.background_path))
            scaled = pixmap.scaled(
                target.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (scaled.width() - target.width()) // 2
            y = (scaled.height() - target.height()) // 2
            painter.drawPixmap(target.left() - x, target.top() - y, scaled)
        painter.fillRect(
            target, _overlay_color(palette["overlay"], self.overlay_transparency))
        painter.restore()

    def paintEvent(self, event):
        if not self._paint_enabled:
            return
        painter = QPainter(self)
        self.paint_to(painter, self.rect())
        painter.end()


class OpacityCard(QFrame):
    """Homepage card whose fill is painted independently of Fluent styles."""

    def __init__(self, radius: float = 12.0):
        super().__init__()
        self._radius = radius
        self._fill = QColor(0, 0, 0, 0)
        self._border = QColor(255, 255, 255, 0)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def set_card_visual(self, fill: QColor, border: QColor) -> None:
        self._fill = QColor(fill)
        self._border = QColor(border)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(self._fill)
        painter.setPen(QPen(self._border, 1.0))
        painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.end()


class BackgroundSettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("背景设置")
        self.setMinimumWidth(600)
        self.rows: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        title = QLabel("分别设置深色与浅色模式的背景")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        for theme, label in (("dark", "深色背景"), ("light", "浅色背景")):
            card = QFrame()
            card.setObjectName("card")
            row = QHBoxLayout(card)
            name = QLabel(label)
            name.setMinimumWidth(90)
            path = QLabel()
            path.setWordWrap(True)
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path.setToolTip("")
            self.rows[theme] = path
            choose = QPushButton("选择图片")
            choose.clicked.connect(lambda _checked=False, value=theme: self.choose(value))
            restore = QPushButton("恢复默认")
            restore.clicked.connect(lambda _checked=False, value=theme: self.restore(value))
            row.addWidget(name)
            row.addWidget(path, 1)
            row.addWidget(choose)
            row.addWidget(restore)
            layout.addWidget(card)
        opacity_card = QFrame()
        opacity_card.setObjectName("card")
        opacity_layout = QVBoxLayout(opacity_card)
        opacity_layout.setContentsMargins(12, 10, 12, 10)
        opacity_layout.setSpacing(6)
        opacity_title = QLabel("卡片透明度")
        opacity_title.setObjectName("dialogTitle")
        opacity_layout.addWidget(opacity_title)
        opacity_row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(CARD_OPACITY_MIN, CARD_OPACITY_MAX)
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        self.opacity_value_label = QLabel()
        self.opacity_value_label.setMinimumWidth(68)
        self.opacity_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_value_label)
        opacity_layout.addLayout(opacity_row)
        opacity_hint = QLabel("0% 不透明，100% 透明。")
        opacity_hint.setObjectName("mutedLabel")
        opacity_hint.setWordWrap(True)
        opacity_layout.addWidget(opacity_hint)
        opacity_actions = QHBoxLayout()
        opacity_actions.addStretch(1)
        self.restore_opacity_button = QPushButton()
        self.restore_opacity_button.clicked.connect(self.restore_opacity)
        opacity_actions.addWidget(self.restore_opacity_button)
        opacity_layout.addLayout(opacity_actions)
        layout.addWidget(opacity_card)

        overlay_card = QFrame()
        overlay_card.setObjectName("card")
        overlay_layout = QVBoxLayout(overlay_card)
        overlay_layout.setContentsMargins(12, 10, 12, 10)
        overlay_layout.setSpacing(6)
        overlay_title = QLabel("背景遮罩透明度")
        overlay_title.setObjectName("dialogTitle")
        overlay_layout.addWidget(overlay_title)
        overlay_row = QHBoxLayout()
        self.overlay_slider = QSlider(Qt.Horizontal)
        self.overlay_slider.setRange(
            OVERLAY_TRANSPARENCY_MIN, OVERLAY_TRANSPARENCY_MAX)
        self.overlay_slider.valueChanged.connect(self.change_overlay_transparency)
        self.overlay_value_label = QLabel()
        self.overlay_value_label.setMinimumWidth(68)
        self.overlay_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        overlay_row.addWidget(self.overlay_slider, 1)
        overlay_row.addWidget(self.overlay_value_label)
        overlay_layout.addLayout(overlay_row)
        overlay_hint = QLabel("0% 完全遮罩，100% 无遮罩。")
        overlay_hint.setObjectName("mutedLabel")
        overlay_hint.setWordWrap(True)
        overlay_layout.addWidget(overlay_hint)
        overlay_actions = QHBoxLayout()
        overlay_actions.addStretch(1)
        self.restore_overlay_button = QPushButton()
        self.restore_overlay_button.clicked.connect(self.restore_overlay_transparency)
        overlay_actions.addWidget(self.restore_overlay_button)
        overlay_layout.addLayout(overlay_actions)
        layout.addWidget(overlay_card)
        close = QPushButton("完成")
        close.clicked.connect(self.accept)
        close_layout = QHBoxLayout()
        close_layout.addStretch(1)
        close_layout.addWidget(close)
        layout.addLayout(close_layout)
        self.refresh()

    def refresh(self) -> None:
        for theme, label in self.rows.items():
            path = self.owner.settings.background_path(theme)
            label.setText(str(path))
            label.setToolTip(str(path))
        if hasattr(self, "opacity_slider"):
            value = self.owner.settings.card_opacity(self.owner.current_theme)
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(value)
            self.opacity_slider.blockSignals(False)
            self.opacity_value_label.setText(f"{value}%")
            self.restore_opacity_button.setText(
                f"恢复默认 {card_opacity_default(self.owner.current_theme)}%")
        if hasattr(self, "overlay_slider"):
            value = self.owner.settings.overlay_transparency(self.owner.current_theme)
            self.overlay_slider.blockSignals(True)
            self.overlay_slider.setValue(value)
            self.overlay_slider.blockSignals(False)
            self.overlay_value_label.setText(f"{value}%")
            self.restore_overlay_button.setText(
                f"恢复默认 {overlay_transparency_default(self.owner.current_theme)}%")

    def change_opacity(self, value: int) -> None:
        self.owner.set_card_opacity(value)
        self.refresh()

    def restore_opacity(self) -> None:
        self.owner.restore_card_opacity()
        self.refresh()

    def change_overlay_transparency(self, value: int) -> None:
        self.owner.set_overlay_transparency(value)
        self.refresh()

    def restore_overlay_transparency(self) -> None:
        self.owner.restore_overlay_transparency()
        self.refresh()
        self.refresh()

    def choose(self, theme: str) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, f"选择{('深色' if theme == 'dark' else '浅色')}背景", "",
            "图片 (*.png *.jpg *.jpeg *.webp)")
        if not selected:
            return
        try:
            self.owner.settings.set_background(Path(selected), theme)
            self.owner.refresh_background()
            self.refresh()
        except PathSelectionError as exc:
            QMessageBox.warning(self, "背景不可用", str(exc))

    def restore(self, theme: str) -> None:
        try:
            self.owner.settings.restore_default_background(theme)
            self.owner.refresh_background()
            self.refresh()
        except PathSelectionError as exc:
            QMessageBox.warning(self, "背景不可用", str(exc))

    def showEvent(self, event):
        super().showEvent(event)
        self.owner.apply_theme_to(self)
        self.owner.apply_native_title_bar(self)


class UserAvatarImportWindow(MSFluentWindow):
    def __init__(self):
        super().__init__()
        self._initial_visual_refresh_scheduled = False
        self._initial_visual_refresh_done = False
        self._content_layout_locked = False
        # SAT is a single-page tool: keep BAC's Fluent title bar and frameless
        # window behavior, but remove the navigation bar entirely.
        self.navigationInterface.hide()
        self.navigationInterface.setFixedWidth(0)
        self.setMicaEffectEnabled(False)
        self.stackedWidget.setAttribute(Qt.WA_TranslucentBackground, True)
        self.stackedWidget.setStyleSheet(
            "QStackedWidget { background: transparent; border: none; }"
        )
        self.titleBar.setObjectName("satTitleBar")
        self.titleBar.setAttribute(Qt.WA_TranslucentBackground, True)
        self.titleBar.setAutoFillBackground(False)
        self.setWindowTitle(PRODUCT_NAME)
        self.setWindowIcon(QIcon(str(ASSET_DIR / "BACoffee_Student_Add_Tool.ico")))
        self._fit_initial_window_to_available_geometry()
        self.settings = ToolSettings(
            TOOL_ROOT,
            default_background=ASSET_DIR / "深色背景.png",
            light_background=ASSET_DIR / "浅色背景.jpg",
        )
        self.current_theme = self.settings.theme()
        self.pure_background = self.settings.pure_background()
        self.card_opacity = self.settings.card_opacity(self.current_theme)
        self.overlay_transparency = self.settings.overlay_transparency(self.current_theme)
        discovered = discover_student_libraries(TOOL_ROOT)
        self.student_dir: Path | None = discovered[0] if len(discovered) == 1 else None
        if not discovered:
            # A previously repaired location is still valid as a silent fallback;
            # ambiguous automatic candidates must remain a hard stop.
            self.student_dir = self.settings.student_library()
        self.automatic_student_library = bool(self.student_dir and len(discovered) == 1)
        self.library_context: StudentLibraryContext = (
            student_library_context_or_error(self.student_dir, TOOL_ROOT)
            if self.student_dir is not None
            else StudentLibraryContext(None, None, None, BUILTIN_LIBRARY_GUIDANCE)
        )
        if self.library_context.external_dir is not None:
            self.student_dir = self.library_context.external_dir
        self.library_conflict = None
        self.source_path: Path | None = None
        self.inspection: Inspection | None = None
        self.auto_crop_box: tuple[int, int, int, int] | None = None
        self.crop_box: tuple[int, int, int, int] | None = None
        self.crop_adjusting = False
        self.precheck_result: dict | None = None
        self.current_import_succeeded = False
        self._win11_scrollbars: list[Win11ScrollBarAnimator] = []

        self.background = BackgroundWidget()
        self.background.set_paint_enabled(False)
        self.background.setAttribute(Qt.WA_TranslucentBackground, True)
        self.background.setStyleSheet("background-color: transparent;")
        self.stackedWidget.addWidget(self.background)
        self.stackedWidget.setCurrentWidget(self.background)
        self._load_font()
        self.brand_font_family = self._load_brand_font()
        self._build_ui()
        self.apply_theme()
        self.refresh_background()
        self.refresh()
        self.set_pure_background(self.pure_background, save=False)

    def set_pure_background(self, enabled: bool, *, save: bool = True) -> None:
        """Hide ordinary SAT content without destroying the editing state."""
        enabled = bool(enabled)
        self.pure_background = enabled
        if save:
            self.settings.set_pure_background(enabled)
        if hasattr(self, "pure_background_button"):
            self.pure_background_button.blockSignals(True)
            self.pure_background_button.setChecked(enabled)
            self.pure_background_button.blockSignals(False)
        for widget in (
            getattr(self, "brand_logo", None),
            getattr(self, "brand_label", None),
            getattr(self, "brand_subtitle", None),
            getattr(self, "theme_toggle_button", None),
            getattr(self, "background_button", None),
        ):
            if widget is not None:
                widget.setVisible(not enabled)
        if hasattr(self, "main_operation_card"):
            self.main_operation_card.setVisible(not enabled)
        if not enabled and hasattr(self, "main_operation_card"):
            self.refresh()
        self.update()

    def _fit_initial_window_to_available_geometry(self) -> None:
        """Keep the top-level window and fixed action bar inside the work area."""
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if available is None or not available.isValid():
            self.setMinimumSize(820, 560)
            self.resize(980, 720)
            return

        # resize() receives the client size, while frameGeometry() also includes
        # the native title bar. Reserve both a small edge margin and that frame.
        edge_margin = 24
        titlebar_reserve = 64
        fit_width = max(480, available.width() - edge_margin * 2)
        fit_height = max(420, available.height() - edge_margin * 2 - titlebar_reserve)
        minimum_width = min(820, fit_width)
        minimum_height = min(560, fit_height)
        width = max(minimum_width, min(980, fit_width))
        height = max(minimum_height, min(720, fit_height))
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(width, height)
        self.move(
            available.left() + (available.width() - width) // 2,
            available.top() + (available.height() - height) // 2,
        )

    def _load_font(self) -> None:
        app = QApplication.instance()
        bundled_cjk = ASSET_DIR / "SimHei.ttf"
        if bundled_cjk.is_file():
            font_id = QFontDatabase.addApplicationFont(str(bundled_cjk))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    app.setFont(QFont(families[0], 10))
                    return
        available = set(QFontDatabase().families())
        for family in ("Microsoft YaHei UI", "Microsoft YaHei", "DengXian", "SimSun"):
            if family in available:
                app.setFont(QFont(family, 10))
                break

    def _load_brand_font(self) -> str | None:
        """Load the bundled display font for the large homepage brand only."""
        font_path = ASSET_DIR / "Comfortaa-Light.ttf"
        if not font_path.is_file():
            return None
        try:
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id < 0:
                return None
            families = QFontDatabase.applicationFontFamilies(font_id)
            return families[0] if families else None
        except (OSError, RuntimeError):
            return None

    def _card(self, object_name: str = "card") -> QFrame:
        card = OpacityCard() if object_name == "mainOperationCard" else QFrame()
        card.setObjectName(object_name)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        return card

    def _section_title(self, number: str, title: str, hint: str = "") -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(2)
        step = QLabel(number)
        step.setObjectName("sectionStep")
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(step)
        layout.addWidget(heading)
        if hint:
            sub = QLabel(hint)
            sub.setObjectName("mutedLabel")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return layout

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self.background)
        outer.setContentsMargins(28, 16, 28, 14)
        outer.setSpacing(14)

        scroll = QScrollArea()
        scroll.setObjectName("mainScroll")
        scroll.setWidgetResizable(True)
        scroll.setSizeAdjustPolicy(QScrollArea.AdjustIgnored)
        scroll.setMinimumSize(0, 0)
        # The scroll area must be willing to shrink below its content's
        # preferred height so the declared 820x560 compact window remains
        # reachable; the content itself remains scrollable.
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("scrollContent")
        content.setMinimumSize(0, 0)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 16)
        content_layout.setSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.main_scroll = scroll

        brand = self._card("brandCard")
        brand_layout = QHBoxLayout(brand)
        # Keep the wordmark flush with the surrounding card edge after
        # removing the decorative logo from the SAT header.
        brand_layout.setContentsMargins(0, 6, 0, 6)
        brand_layout.setSpacing(0)
        self.brand_logo = None
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        self.brand_label = QLabel("BACoffee")
        self.brand_label.setObjectName("brandTitle")
        if self.brand_font_family:
            brand_font = self.brand_label.font()
            brand_font.setFamily(self.brand_font_family)
            self.brand_label.setFont(brand_font)
        brand_text.addWidget(self.brand_label)
        self.brand_subtitle = QLabel("Student Add Tool")
        self.brand_subtitle.setObjectName("brandSubtitle")
        if self.brand_font_family:
            subtitle_font = self.brand_subtitle.font()
            subtitle_font.setFamily(self.brand_font_family)
            self.brand_subtitle.setFont(subtitle_font)
        brand_text.addWidget(self.brand_subtitle)
        brand_layout.addLayout(brand_text, 1)
        theme_box = QVBoxLayout()
        theme_box.setContentsMargins(0, 0, 0, 0)
        theme_box.setSpacing(4)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addStretch(1)
        theme_buttons = QHBoxLayout()
        theme_buttons.setSpacing(4)
        self.pure_background_button = PureBackgroundButton(
            ASSET_DIR / "coffee_bean.png")
        self.pure_background_button.setObjectName("pureBackgroundButton")
        self.pure_background_button.clicked.connect(
            lambda checked: self.set_pure_background(checked, save=True))
        theme_buttons.addWidget(self.pure_background_button)
        self.theme_toggle_button = QPushButton()
        self.theme_toggle_button.setObjectName("themeButton")
        self.theme_toggle_button.setCheckable(True)
        self.theme_toggle_button.clicked.connect(self.toggle_theme)
        self.pure_background_button.setSizePolicy(
            self.theme_toggle_button.sizePolicy())
        self._theme_toggle_slot = PersistentThemeButtonSlot(
            self.theme_toggle_button)
        theme_buttons.addWidget(self._theme_toggle_slot)
        mode_row.addLayout(theme_buttons)
        theme_box.addLayout(mode_row)
        self.background_button = QPushButton("背景设置")
        self.background_button.setObjectName("backgroundButton")
        self.background_button.setFixedWidth(92)
        self.background_button.clicked.connect(self.open_background_settings)
        background_row = QHBoxLayout()
        background_row.setContentsMargins(0, 0, 0, 0)
        background_row.addStretch(1)
        background_row.addWidget(self.background_button)
        theme_box.addLayout(background_row)
        brand_layout.addLayout(theme_box)
        content_layout.addWidget(brand)

        add_card = self._card("mainOperationCard")
        self.main_operation_card = add_card
        add_layout = QVBoxLayout(add_card)
        add_layout.setContentsMargins(12, 10, 12, 10)
        add_layout.setSpacing(6)
        progress = QLabel("① 添加头像  →  ② 导入完成")
        progress.setObjectName("progressLabel")
        add_layout.addWidget(progress)

        choose_row = QHBoxLayout()
        self.file_button = QPushButton("选择学生图片")
        self.file_button.setObjectName("chooseButton")
        self.file_button.setMinimumHeight(40)
        self.file_button.clicked.connect(self.choose_source)
        choose_row.addWidget(self.file_button)
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setObjectName("mutedLabel")
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        choose_row.addWidget(self.file_label, 1)
        add_layout.addLayout(choose_row)

        self.adjust_button = QPushButton("调整裁剪")
        self.adjust_button.setObjectName("cropAdjustButton")
        self.adjust_button.setMinimumHeight(40)
        self.adjust_button.setCheckable(True)
        self.adjust_button.setChecked(False)
        self.adjust_button.toggled.connect(self.toggle_crop_adjustment)

        preview_grid = QGridLayout()
        preview_grid.setContentsMargins(0, 2, 0, 2)
        preview_grid.setHorizontalSpacing(20)
        preview_grid.setVerticalSpacing(8)
        self.original_preview = PreviewLabel("", minimum=(160, 112))
        self.normalized_preview = PreviewLabel("", minimum=(160, 112))
        for preview in (self.original_preview, self.normalized_preview):
            preview.setMaximumHeight(132)
            preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for column, (title, preview) in enumerate((
            ("原图", self.original_preview),
            ("处理结果", self.normalized_preview),
        )):
            panel = QFrame()
            panel.setObjectName("previewPanel")
            panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(10, 8, 10, 10)
            panel_layout.setSpacing(6)
            label = QLabel(title)
            label.setObjectName("previewTitle")
            panel_layout.addWidget(label, 0, Qt.AlignCenter)
            if title == "处理结果":
                # Overlay the compact crop toggle in the result tile's lower
                # right corner, so it does not consume another vertical row.
                preview_box = QWidget(panel)
                preview_box.setObjectName("resultPreviewBox")
                preview_box_layout = QGridLayout(preview_box)
                preview_box_layout.setContentsMargins(0, 0, 0, 0)
                preview_box_layout.setSpacing(0)
                preview_box_layout.addWidget(preview, 0, 0)
                preview_box_layout.addWidget(
                    self.adjust_button, 0, 0, Qt.AlignRight | Qt.AlignBottom)
                panel_layout.addWidget(preview_box, 1)
            else:
                panel_layout.addWidget(preview, 1)
            preview_grid.addWidget(panel, 0, column)
            preview_grid.setColumnStretch(column, 1)
        add_layout.addLayout(preview_grid)

        self.crop_frame = QFrame()
        self.crop_frame.setObjectName("cropCard")
        crop_layout = QVBoxLayout(self.crop_frame)
        crop_layout.setContentsMargins(8, 6, 8, 8)
        crop_layout.setSpacing(6)
        crop_hint = QLabel("拖动图片调整位置，滚轮调整缩放；完成后返回预览。")
        crop_hint.setObjectName("mutedLabel")
        crop_hint.setWordWrap(True)
        crop_layout.addWidget(crop_hint)
        self.crop_editor = CropPreview()
        self.crop_editor.cropChanged.connect(self.on_crop_changed)
        crop_layout.addWidget(self.crop_editor)
        crop_zoom_row = QHBoxLayout()
        crop_zoom_row.addWidget(QLabel("缩放"))
        self.crop_zoom_slider = QSlider(Qt.Horizontal)
        self.crop_zoom_slider.setRange(70, 160)
        self.crop_zoom_slider.setValue(100)
        self.crop_zoom_slider.valueChanged.connect(self.change_crop_zoom)
        crop_zoom_row.addWidget(self.crop_zoom_slider, 1)
        self.crop_zoom_label = QLabel("100%")
        self.crop_zoom_label.setMinimumWidth(48)
        crop_zoom_row.addWidget(self.crop_zoom_label)
        crop_layout.addLayout(crop_zoom_row)
        crop_actions = QHBoxLayout()
        crop_actions.addStretch(1)
        self.restore_crop_button = QPushButton("恢复自动裁剪")
        self.restore_crop_button.clicked.connect(self.restore_auto_crop)
        crop_actions.addWidget(self.restore_crop_button)
        self.finish_crop_button = QPushButton("完成调整")
        self.finish_crop_button.clicked.connect(self.finish_crop_adjustment)
        crop_actions.addWidget(self.finish_crop_button)
        crop_layout.addLayout(crop_actions)
        self.crop_frame.setVisible(False)
        add_layout.addWidget(self.crop_frame)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 4, 0, 2)
        name_row.setSpacing(6)
        name_caption = QLabel("学生名称：")
        name_caption.setObjectName("nameCaption")
        name_row.addWidget(name_caption)
        self.name_hint_line = QLabel("│")
        self.name_hint_line.setObjectName("nameHint")
        self.name_hint_line.setAlignment(Qt.AlignCenter)
        self.name_hint_line.setFixedWidth(9)
        name_row.addWidget(self.name_hint_line)
        self.name_edit = NameLineEdit()
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setPlaceholderText("")
        self.name_edit.setFixedSize(280, 40)
        self.name_edit.textChanged.connect(self.invalidate_precheck)
        self.name_edit.textChanged.connect(self.refresh)
        self.name_edit.focusChanged.connect(self.refresh_name_hint)
        name_row.addWidget(self.name_edit)
        name_row.addStretch(1)
        add_layout.addLayout(name_row)

        self.filename_label = QLabel()
        self.filename_label.setObjectName("filenameLabel")
        self.filename_label.setVisible(False)
        add_layout.addWidget(self.filename_label)
        self.check_summary_label = QLabel()
        self.check_summary_label.setObjectName("checkSummary")
        self.check_summary_label.setWordWrap(True)
        add_layout.addWidget(self.check_summary_label)

        self.existing_conflict_frame = OpacityCard(radius=11.0)
        self.existing_conflict_frame.setObjectName("conflictCard")
        conflict_layout = QHBoxLayout(self.existing_conflict_frame)
        conflict_text = QVBoxLayout()
        self.conflict_label = QLabel("发现同名头像，不会覆盖现有文件。")
        self.conflict_label.setObjectName("limitLabel")
        self.conflict_label.setWordWrap(True)
        conflict_text.addWidget(self.conflict_label)
        conflict_layout.addLayout(conflict_text, 1)
        self.existing_preview = PreviewLabel("现有头像", minimum=(120, 80))
        self.existing_preview.setMaximumHeight(90)
        self.existing_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        conflict_layout.addWidget(self.existing_preview)
        self.existing_conflict_frame.setVisible(False)
        add_layout.addWidget(self.existing_conflict_frame)

        self.more_options_button = QPushButton("学生服装")
        self.more_options_button.setObjectName("clothingButton")
        self.more_options_button.setMinimumHeight(40)
        self.more_options_button.setCheckable(True)
        self.more_options_button.toggled.connect(self.toggle_more_options)
        self.options_frame = OpacityCard(radius=11.0)
        self.options_frame.setObjectName("advancedCard")
        options_layout = QFormLayout(self.options_frame)
        options_layout.setContentsMargins(8, 6, 8, 6)
        options_layout.setVerticalSpacing(4)
        self.variant_edit = QLineEdit()
        self.variant_edit.setMinimumHeight(32)
        self.variant_edit.setPlaceholderText("可选，例如：Sportswear")
        self.variant_edit.setClearButtonEnabled(True)
        self.variant_edit.textChanged.connect(self.invalidate_precheck)
        self.variant_edit.textChanged.connect(self.refresh)
        options_layout.addRow("学生服装：", self.variant_edit)
        self.options_frame.setVisible(False)
        add_layout.addWidget(self.more_options_button, 0, Qt.AlignLeft)
        add_layout.addWidget(self.options_frame)

        self.library_status_label = QLabel()
        self.library_status_label.setObjectName("libraryStatus")
        self.library_status_label.setWordWrap(True)

        # Keep the existing actions inside the main card.  The library status
        # stays at the left of this row while the import action remains at the
        # right; the optional clothing control belongs to the main content.
        operation_row = QWidget(add_card)
        operation_row.setObjectName("operationRow")
        operation_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        operation_layout = QHBoxLayout(operation_row)
        operation_layout.setContentsMargins(0, 6, 0, 0)
        operation_layout.setSpacing(10)
        self.library_status_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred)
        operation_layout.addWidget(self.library_status_label, 1)

        self.import_state_label = QLabel()
        self.import_state_label.setObjectName("statusValue")
        self.import_state_label.setWordWrap(True)
        self.import_state_label.setVisible(False)
        self.result_label = QLabel()
        self.result_label.setObjectName("resultHint")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setVisible(False)
        operation_layout.addWidget(self.result_label, 1)
        self.import_button = QPushButton("确认导入")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setMinimumHeight(42)
        self.import_button.clicked.connect(self.import_student)
        self.import_button.setMinimumWidth(150)
        self.import_button.setMaximumWidth(190)
        operation_layout.addWidget(self.import_button, 0)
        self.continue_button = QPushButton("继续添加下一位")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setMinimumHeight(42)
        self.continue_button.setMinimumWidth(150)
        self.continue_button.setMaximumWidth(190)
        self.continue_button.clicked.connect(self.continue_next_student)
        self.continue_button.setVisible(False)
        operation_layout.addWidget(self.continue_button, 0)
        add_layout.addWidget(operation_row)
        self.operation_row = operation_row
        content_layout.addWidget(add_card)
        content_layout.addStretch(1)
        # Compatibility alias for existing layout diagnostics and theme code;
        # there is no independent bottom-bar widget anymore.
        self.bottom_bar = self.main_operation_card

        _install_win11_scrollbars(self, scroll, PALETTES[self.current_theme]["accent"])

    def toggle_more_options(self, expanded: bool) -> None:
        self.options_frame.setVisible(expanded)

    def refresh_name_hint(self, *_args) -> None:
        if not hasattr(self, "name_hint_line"):
            return
        self.name_hint_line.setVisible(
            not self.name_edit.text() and not self.name_edit.hasFocus())

    def toggle_crop_adjustment(self, expanded: bool) -> None:
        self.crop_adjusting = bool(expanded)
        self.crop_frame.setVisible(self.crop_adjusting)
        self.normalized_preview.setVisible(not self.crop_adjusting)
        if self.crop_adjusting:
            if self.inspection is not None and self.inspection.input_image is not None:
                if self.crop_box is None:
                    self.crop_box = self.inspection.crop_box or default_crop_box(
                        self.inspection.input_image)
                self.crop_editor.set_source(self.inspection.input_image, self.crop_box)
            self.adjust_button.setText("收起裁剪调整")
        else:
            self.adjust_button.setText("调整裁剪")

    def on_crop_changed(self, crop_box) -> None:
        self.crop_box = tuple(int(value) for value in crop_box)
        if self.source_path is not None:
            self.refresh()

    def change_crop_zoom(self, value: int) -> None:
        self.crop_zoom_label.setText(f"{value}%")
        if self.inspection is None or self.inspection.input_image is None:
            return
        current = self.crop_box or self.inspection.crop_box
        if current is None:
            return
        height, width = self.inspection.input_image.shape[:2]
        auto = self.auto_crop_box or current
        base_side = auto[2] - auto[0]
        side = base_side * max(70, min(160, value)) / 100.0
        center = ((current[0] + current[2]) / 2, (current[1] + current[3]) / 2)
        side = max(1, min(int(round(side)), width, height))
        x = max(0, min(int(round(center[0] - side / 2)), width - side))
        y = max(0, min(int(round(center[1] - side / 2)), height - side))
        self.crop_box = (x, y, x + side, y + side)
        self.crop_editor.set_crop_box(self.crop_box)
        self.refresh()

    def restore_auto_crop(self) -> None:
        if self.auto_crop_box is None:
            return
        self.crop_box = self.auto_crop_box
        self.crop_zoom_slider.blockSignals(True)
        self.crop_zoom_slider.setValue(100)
        self.crop_zoom_slider.blockSignals(False)
        self.crop_zoom_label.setText("100%")
        self.crop_editor.set_crop_box(self.crop_box)
        self.refresh()

    def finish_crop_adjustment(self) -> None:
        self.adjust_button.setChecked(False)

    def choose_repair_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "修复学生库位置")
        if selected:
            self.set_student_folder(Path(selected))

    def layout_visibility_errors(self) -> list[str]:
        """Return layout errors observable after the window has been shown."""
        errors: list[str] = []
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        frame = self.frameGeometry()
        if available is None or not available.isValid():
            errors.append("没有可用的屏幕工作区")
        elif not (
            frame.left() >= available.left()
            and frame.top() >= available.top()
            and frame.right() <= available.right()
            and frame.bottom() <= available.bottom()
        ):
            errors.append(
                f"窗口超出工作区：frame={frame.getRect()} available={available.getRect()}"
            )

        client = self.rect()

        def visible_inside(widget: QWidget, label: str) -> None:
            if not widget.isVisible():
                errors.append(f"{label} 不可见")
                return
            rect = widget.rect()
            rect.moveTopLeft(widget.mapTo(self, QPoint(0, 0)))
            if not (
                client.contains(rect.topLeft())
                and client.contains(rect.bottomRight())
            ):
                errors.append(f"{label} 超出客户区：{rect.getRect()} client={client.getRect()}")

        def visible_inside_scroll_content(widget: QWidget, label: str) -> None:
            if not widget.isVisible():
                errors.append(f"{label} 不可见")
                return
            rect = widget.rect()
            rect.moveTopLeft(widget.mapTo(self.main_scroll.widget(), QPoint(0, 0)))
            content_rect = self.main_scroll.widget().rect()
            if not (
                content_rect.contains(rect.topLeft())
                and content_rect.contains(rect.bottomRight())
            ):
                errors.append(
                    f"{label} 超出滚动内容：{rect.getRect()} "
                    f"content={content_rect.getRect()}"
                )

        # The compact row belongs to the main card and may scroll with it at
        # the minimum window size; it must not be treated as a fixed bottom bar.
        visible_inside_scroll_content(self.operation_row, "主卡片底部操作行")
        visible_inside_scroll_content(self.pure_background_button, "纯净背景按钮")
        if not self.pure_background:
            visible_inside(self.theme_toggle_button, "主题切换按钮")
            visible_inside(self.background_button, "背景设置按钮")
        if self.main_scroll.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff:
            errors.append("主内容允许横向滚动")
        if (
            self.main_scroll.widget().sizeHint().height() > self.main_scroll.viewport().height()
            and not self.main_scroll.verticalScrollBar().isVisible()
        ):
            errors.append("主内容需要滚动但纵向滚动条不可用")
        return errors

    def _stylesheet(self) -> str:
        p = PALETTES[self.current_theme]
        card = _homepage_card_rgba(p["card"], self.card_opacity, self.current_theme)
        card_alt = _homepage_card_rgba(
            p["card_alt"], self.card_opacity, self.current_theme)
        input_color = _material_rgba(p["input"], max(0, self.card_opacity - 20))
        button = _material_rgba(p["button"], max(0, self.card_opacity - 20))
        button_hover = _material_rgba(
            p["button_hover"], max(0, self.card_opacity - 20))
        brand_font_rule = (
            f"font-family: '{self.brand_font_family}';"
            if self.brand_font_family else ""
        )
        return f"""
            QWidget {{ color: {p['text']}; font-family: 'Microsoft YaHei UI'; font-size: 10pt; }}
            QMainWindow, QDialog {{ background: {p['window']}; color: {p['text']}; }}
            QScrollArea, QWidget#scrollContent {{ border: none; background: transparent; }}
            QFrame#card {{ background: {card}; border: 1px solid {p['border']};
                border-radius: 14px; }}
            QFrame#brandCard {{ background: transparent; border: none; }}
            QFrame#mainOperationCard, QFrame#advancedCard, QFrame#conflictCard,
            QFrame#detailsFrame, QWidget#operationRow {{ background: transparent; border: none; }}
            QLabel#brandTitle {{ color: {p['accent']}; {brand_font_rule} font-size: 24pt; font-weight: bold; }}
            QLabel#brandSubtitle {{ color: {p['muted']}; {brand_font_rule} font-size: 12pt; font-weight: normal; }}
            QLabel#sectionStep {{ color: {p['accent']}; font-size: 9pt; font-weight: bold; }}
            QLabel#sectionTitle {{ color: {p['text']}; font-size: 13pt; font-weight: bold; }}
            QLabel#progressLabel {{ color: {p['accent']}; font-size: 10pt; font-weight: bold; }}
            QFrame#previewPanel, QFrame#cropCard {{ background: {p['preview_panel']};
                border: 1px solid {p['border']}; border-radius: 9px; }}
            QLabel#previewTitle {{ color: {p['text']}; font-size: 10pt; font-weight: bold; }}
            QLabel#advancedTitle {{ color: {p['text']}; font-weight: bold; }}
            QLabel#mutedLabel, QLabel#mutedLabel {{ color: {p['muted']}; }}
            QLabel#checkSummary, QLabel#libraryStatus {{ color: {p['muted']}; padding-top: 2px; }}
            QLabel#statusValue {{ color: {p['text']}; font-weight: bold; }}
            QLabel#resultHint {{ color: {p['muted']}; }}
            QLabel#limitLabel {{ color: {p['warning']}; }}
            QLabel#filenameLabel {{ color: {p['muted']}; font-size: 9pt; }}
            QLabel#nameCaption {{ color: {p['text']}; font-weight: bold; }}
            QLabel#nameHint {{ color: {p['accent']}; font-size: 16pt; font-weight: bold; }}
            QLabel#statusPill {{ color: {p['accent_text']}; background: {p['accent']};
                border-radius: 9px; padding: 4px 9px; font-weight: bold; }}
            QPushButton {{ background: {button}; border: 1px solid {p['border']};
                border-radius: 8px; padding: 7px 12px; min-height: 36px; color: {p['text']}; }}
            QPushButton#chooseButton {{ padding: 6px 12px; min-height: 34px; font-weight: bold;
                border: 1px solid {p['accent']}; }}
            QPushButton#cropAdjustButton, QPushButton#clothingButton {{
                padding: 5px 10px; min-height: 32px; }}
            QPushButton:hover {{ background: {button_hover}; border-color: {p['accent']}; }}
            QPushButton:disabled {{ color: {p['muted']}; background: {card_alt}; border-color: {p['border']}; }}
            QPushButton#themeButton, QPushButton#backgroundButton {{ padding: 4px 8px; min-height: 30px; }}
            QPushButton#pureBackgroundButton {{ padding: 4px 10px; min-height: 30px;
                background: transparent; color: {p['muted']}; border-color: {p['border']}; }}
            QPushButton#pureBackgroundButton:hover {{ background: {button_hover};
                color: {p['accent']}; border-color: {p['accent']}; }}
            QPushButton#pureBackgroundButton:checked {{ background: transparent;
                color: {p['accent']}; border-color: {p['border']}; }}
            QPushButton#pureBackgroundButton:checked:hover {{ background: {button_hover};
                border-color: {p['accent']}; }}
            QPushButton#themeButton:checked {{ background: {p['accent']}; color: {p['accent_text']}; border-color: {p['accent']}; }}
            QPushButton#primaryButton {{ background: {p['accent']}; color: {p['accent_text']};
                border: none; padding: 10px 18px; min-height: 42px; font-weight: bold; }}
            QPushButton#primaryButton:hover {{ background: {p['accent_hover']}; }}
            QWidget#operationRow QPushButton {{ background: {button}; color: {p['text']}; }}
            QWidget#operationRow QPushButton:hover {{ background: {button_hover}; }}
            QWidget#operationRow QPushButton#primaryButton {{ background: {p['accent']}; color: {p['accent_text']}; }}
            QWidget#operationRow QPushButton#primaryButton:hover {{ background: {p['accent_hover']}; }}
            QWidget#operationRow QPushButton#primaryButton:enabled {{
                background: {p['accent']}; color: {p['accent_text']}; border: none;
            }}
            QWidget#operationRow QPushButton#primaryButton:disabled {{
                background: {card_alt}; color: {p['muted']}; border: 1px solid {p['border']};
            }}
            QWidget#operationRow QPushButton#primaryButton:enabled:hover {{
                background: {p['accent_hover']}; color: {p['accent_text']}; border: none;
            }}
            QPushButton#primaryButton:enabled {{
                background: {p['accent']}; color: {p['accent_text']}; border: none;
            }}
            QPushButton#primaryButton:disabled {{
                background: {card_alt}; color: {p['muted']}; border: 1px solid {p['border']};
            }}
            QPushButton#primaryButton:enabled:hover {{
                background: {p['accent_hover']}; color: {p['accent_text']}; border: none;
            }}
            QLineEdit {{ background: {input_color}; border: 1px solid {p['border']};
                border-radius: 8px; padding: 7px; color: {p['text']}; selection-background-color: {p['accent']}; }}
            QLineEdit#nameEdit {{ background: transparent; border: none; border-radius: 0;
                padding: 5px 0 3px 0; color: {p['text']}; selection-background-color: {p['accent']}; }}
            QLineEdit#nameEdit:focus {{ border-bottom: 2px solid {p['accent']}; padding-bottom: 1px; }}
            QListWidget {{ background: {input_color}; border: 1px solid {p['border']};
                border-radius: 9px; padding: 5px; color: {p['text']}; }}
            QListWidget::item {{ padding: 4px; }}
            QSlider::groove:horizontal {{ height: 6px; background: {card_alt}; border: 1px solid {p['border']}; border-radius: 3px; }}
            QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; background: {p['accent']}; border: 1px solid {p['border']}; border-radius: 8px; }}
            QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 3px; }}
            QLabel#dialogTitle {{ color: {p['text']}; font-size: 13pt; font-weight: bold; }}
        """

    def apply_theme_to(self, widget: QWidget) -> None:
        widget.setStyleSheet(self._stylesheet())

    def _refresh_brand_subtitle(self) -> None:
        """Keep only the S/A/T initials in the current theme accent color."""
        accent = PALETTES[self.current_theme]["accent"]
        text = PALETTES[self.current_theme]["text"]
        brand_font = self.brand_font_family or "Microsoft YaHei UI"
        self.brand_label.setText(
            f"<span style=\"font-family:'{brand_font}';font-size:24pt;"
            f"font-weight:bold;color:{accent}\">BAC</span>"
            f"<span style=\"font-family:'{brand_font}';font-size:24pt;"
            f"font-weight:bold;color:{text}\">offee</span>")
        self.brand_label.setTextFormat(Qt.RichText)
        self.brand_subtitle.setText(
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{accent}\">S</span>"
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{text}\">tudent </span>"
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{accent}\">A</span>"
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{text}\">dd </span>"
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{accent}\">T</span>"
            f"<span style=\"font-family:'{brand_font}';font-size:12pt;"
            f"color:{text}\">ool</span>")
        self.brand_subtitle.setTextFormat(Qt.RichText)

    def _apply_homepage_card_visuals(self) -> None:
        """Push the selected alpha into the dedicated homepage card painters."""
        palette = PALETTES[self.current_theme]
        fill = _qcolor_from_css(_homepage_card_rgba(
            palette["card"], self.card_opacity, self.current_theme))
        alternate_fill = _qcolor_from_css(
            _homepage_card_rgba(
                palette["card_alt"], self.card_opacity, self.current_theme))
        border = QColor(palette["border"])
        for widget in (
            getattr(self, "main_operation_card", None),
        ):
            if isinstance(widget, OpacityCard):
                widget.set_card_visual(fill, border)
        for widget in (
            getattr(self, "options_frame", None),
        ):
            if isinstance(widget, OpacityCard):
                widget.set_card_visual(fill, border)
        for widget in (
            getattr(self, "existing_conflict_frame", None),
        ):
            if isinstance(widget, OpacityCard):
                widget.set_card_visual(alternate_fill, border)

    def _refresh_opacity_cards(self) -> None:
        """Re-polish and repaint homepage cards after a live slider change."""
        for widget in (
            getattr(self, "main_operation_card", None),
            getattr(self, "options_frame", None),
            getattr(self, "existing_conflict_frame", None),
        ):
            if widget is None:
                continue
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()
        if hasattr(self, "main_scroll"):
            self.main_scroll.viewport().update()
        self.update()

    def _lock_content_layout(self) -> None:
        """Keep theme-only restyles from changing the confirmed SAT geometry."""
        if self._content_layout_locked or not hasattr(self, "main_scroll"):
            return
        content = self.main_scroll.widget()
        layouts = []
        for widget in (content, *content.findChildren(QWidget)):
            layout = widget.layout()
            if layout is not None:
                layout.setEnabled(False)
                layouts.append(layout)
        self._locked_content_layouts = layouts
        self._content_layout_locked = bool(layouts)

    def _release_content_layout(self) -> None:
        if not self._content_layout_locked or not hasattr(self, "main_scroll"):
            return
        for layout in getattr(self, "_locked_content_layouts", []):
            layout.setEnabled(True)
        self._locked_content_layouts = []
        self._content_layout_locked = False

    def apply_theme(self, *, preserve_layout: bool = False) -> None:
        if preserve_layout:
            self._lock_content_layout()
        style_sheet = self._stylesheet()
        self.setStyleSheet(style_sheet)
        # Keep the compact operation row's state selectors local to the
        # scroll-content branch.  This preserves enabled/disabled QSS colors
        # for the primary action while theme changes still re-polish it once.
        if hasattr(self, "operation_row"):
            self.operation_row.setStyleSheet(style_sheet)
        self._apply_homepage_card_visuals()
        self._refresh_opacity_cards()
        p = PALETTES[self.current_theme]
        self._refresh_brand_subtitle()
        self.pure_background_button.set_theme(p["accent"])
        self._apply_title_bar_theme(p)
        current_label = "深色" if self.current_theme == "dark" else "浅色"
        target_label = "浅色" if self.current_theme == "dark" else "深色"
        self.theme_toggle_button.setText(current_label)
        self.theme_toggle_button.setToolTip(f"切换为{target_label}")
        self.theme_toggle_button.setAccessibleName(
            f"主题切换，当前{current_label}，点击切换为{target_label}")
        self.theme_toggle_button.setChecked(True)
        for preview in (self.original_preview, self.normalized_preview,
                        self.existing_preview):
            preview.set_theme(p, self.card_opacity)
        for animator in self._win11_scrollbars:
            animator.set_theme(p["accent"], self.current_theme == "dark")
        self.background.theme = self.current_theme
        self.background.set_overlay_transparency(self.overlay_transparency)
        self.background.update()
        self.apply_native_title_bar(self)

    def _apply_title_bar_theme(self, palette: dict[str, str]) -> None:
        """Keep the BAC Fluent title bar transparent and theme-synchronous."""
        self.titleBar.setStyleSheet(
            "QWidget#satTitleBar { background-color: transparent; border: none; }"
            "QLabel { background: transparent; }"
        )
        self.titleBar.titleLabel.setStyleSheet(
            f"color: {palette['titlebar_text']}; background: transparent; "
            "font: 13px 'Segoe UI', 'Microsoft YaHei UI'; padding: 0 4px;"
        )
        normal = QColor(palette["titlebar_text"])
        transparent = QColor(0, 0, 0, 0)
        hover = QColor(palette["border"])
        for button in (self.titleBar.minBtn, self.titleBar.maxBtn):
            button.setNormalColor(normal)
            button.setHoverColor(normal)
            button.setPressedColor(normal)
            button.setNormalBackgroundColor(transparent)
            button.setHoverBackgroundColor(hover)
            button.setPressedBackgroundColor(hover)
        close_button = self.titleBar.closeBtn
        close_button.setNormalColor(normal)
        close_button.setHoverColor(QColor("#FFFFFF"))
        close_button.setPressedColor(QColor("#FFFFFF"))
        close_button.setNormalBackgroundColor(transparent)
        close_button.setHoverBackgroundColor(QColor("#C42B1C"))
        close_button.setPressedBackgroundColor(QColor("#A5261A"))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        self.background.paint_to(painter, self.rect())
        painter.end()

    def set_theme(self, theme: str) -> None:
        if theme not in PALETTES or theme == self.current_theme:
            self.apply_theme(preserve_layout=True)
            return
        self.current_theme = theme
        self.settings.set_theme(theme)
        self.card_opacity = self.settings.card_opacity(theme)
        self.overlay_transparency = self.settings.overlay_transparency(theme)
        self.apply_theme(preserve_layout=True)
        self.refresh_background()

    def toggle_theme(self) -> None:
        """Toggle between the two persisted SAT themes."""
        self.set_theme("light" if self.current_theme == "dark" else "dark")

    def set_card_opacity(self, value: int) -> None:
        self.card_opacity = self.settings.set_card_opacity(
            value, self.current_theme)
        self.apply_theme(preserve_layout=True)
        if hasattr(self, "background_dialog") and self.background_dialog is not None:
            self.apply_theme_to(self.background_dialog)

    def restore_card_opacity(self) -> None:
        self.card_opacity = self.settings.restore_card_opacity(self.current_theme)
        self.apply_theme(preserve_layout=True)
        if hasattr(self, "background_dialog") and self.background_dialog is not None:
            self.apply_theme_to(self.background_dialog)

    def set_overlay_transparency(self, value: int) -> None:
        self.overlay_transparency = self.settings.set_overlay_transparency(
            value, self.current_theme)
        self.background.set_overlay_transparency(self.overlay_transparency)
        if hasattr(self, "background_dialog") and self.background_dialog is not None:
            self.apply_theme_to(self.background_dialog)

    def restore_overlay_transparency(self) -> None:
        self.set_overlay_transparency(OVERLAY_TRANSPARENCY_DEFAULT)

    def refresh_background(self) -> None:
        background = self.settings.background_path(self.current_theme)
        self.background.set_background(background, self.current_theme)
        if hasattr(self, "background_dialog") and self.background_dialog is not None:
            self.background_dialog.refresh()

    def open_background_settings(self) -> None:
        dialog = BackgroundSettingsDialog(self)
        self.background_dialog = dialog
        self.apply_theme_to(dialog)
        dialog.exec_()
        self.background_dialog = None

    def apply_native_title_bar(self, widget: QWidget | None = None) -> None:
        """Apply BAC's DWM caption, text, and border attributes safely."""
        if os.name != "nt":
            return
        widget = widget or self
        try:
            hwnd = int(widget.winId())
            dark = self.current_theme == "dark"
            enabled = ctypes.c_int(1 if dark else 0)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
            if result != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
            p = PALETTES[self.current_theme]
            for attribute, value in (
                (35, p["titlebar"]),
                (36, p["titlebar_text"]),
                (34, p["border"]),
            ):
                color = QColor(value)
                color_ref = ctypes.c_int(
                    color.red() | (color.green() << 8) | (color.blue() << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(color_ref), ctypes.sizeof(color_ref))
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_native_title_bar(self)
        if not self._initial_visual_refresh_scheduled:
            self._initial_visual_refresh_scheduled = True
            QTimer.singleShot(0, self._finish_initial_visual_refresh)

    def _finish_initial_visual_refresh(self) -> None:
        """Complete one visual refresh after the first real show event."""
        if self._initial_visual_refresh_done:
            return
        self._initial_visual_refresh_done = True
        self.refresh_background()
        self._apply_homepage_card_visuals()
        # Polish already-created child widgets once after the first real show.
        # This avoids a full-window re-polish, which would recalculate the
        # main card, while still making the first screenshot fully styled.
        for widget in self.findChildren(QWidget):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.updateGeometry()
            widget.update()
        self.background.update()
        if hasattr(self, "main_scroll"):
            self.main_scroll.update()
            self.main_scroll.viewport().update()
        for widget in (
            getattr(self, "main_operation_card", None),
            getattr(self, "options_frame", None),
            getattr(self, "existing_conflict_frame", None),
        ):
            if widget is not None:
                widget.update()
        self.update()

    def invalidate_precheck(self) -> None:
        self.precheck_result = None
        if hasattr(self, "screenshot_preview"):
            self.screenshot_preview.set_image(None)
        if hasattr(self, "screenshot_label"):
            self.screenshot_label.setText("尚未进行真实识别预检；仍可完成格式导入")

    def choose_bac_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择 BACoffee 文件夹")
        if selected:
            self.set_student_folder(Path(selected))

    def choose_student_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "直接选择 students 文件夹")
        if selected:
            self.set_student_folder(Path(selected))

    def set_student_folder(self, selection: Path) -> None:
        try:
            context = student_library_context_or_error(selection, TOOL_ROOT)
            if not context.ready or context.external_dir is None:
                raise PathSelectionError(context.error or BUILTIN_LIBRARY_GUIDANCE)
            self.settings.set_student_library(context.external_dir)
            self.library_context = context
            self.student_dir = context.external_dir
            self.automatic_student_library = False
            self.invalidate_precheck()
            self.refresh()
        except PathSelectionError as exc:
            QMessageBox.warning(self, "学生库不可用", str(exc))

    def choose_source(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择学生图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if selected:
            self.source_path = Path(selected)
            self.auto_crop_box = None
            self.crop_box = None
            self.crop_adjusting = False
            self.adjust_button.setChecked(False)
            self.file_label.setText(self.source_path.name)
            self.file_label.setToolTip(str(self.source_path))
            self._apply_filename_defaults(self.source_path)
            self.result_label.clear()
            self.result_label.setVisible(False)
            self.invalidate_precheck()
            self.refresh()

    def _apply_filename_defaults(self, source_path: Path) -> None:
        """Fill the ordinary-flow fields from the selected PNG filename."""
        stem = source_path.stem.strip()
        match = re.match(r"^(.+?)\s*\(([^()]+)\)$", stem)
        if match:
            name, variant = match.group(1).strip(), match.group(2).strip()
        else:
            name, variant = stem, ""
        self.name_edit.blockSignals(True)
        self.variant_edit.blockSignals(True)
        self.name_edit.setText(name)
        self.variant_edit.setText(variant)
        self.variant_edit.blockSignals(False)
        self.name_edit.blockSignals(False)
        self.refresh_name_hint()
        # Keep the optional variant editor collapsed in the ordinary flow.
        self.more_options_button.setChecked(False)

    def choose_background(self) -> None:
        # Compatibility entry point for callers of the initial user version.
        self.open_background_settings()

    def restore_background(self) -> None:
        self.settings.restore_default_background(self.current_theme)
        self.refresh_background()

    def _target_dir_for_check(self) -> Path:
        return self.student_dir or (TOOL_ROOT / "__未选择学生库__")

    def refresh(self) -> None:
        self._release_content_layout()
        palette = PALETTES[self.current_theme]
        self.inspection = inspect_avatar(
            self.source_path,
            self.name_edit.text(),
            self.variant_edit.text(),
            self._target_dir_for_check(),
            self.crop_box,
        )
        self.library_context = (
            student_library_context_or_error(self.student_dir, TOOL_ROOT)
            if self.student_dir is not None
            else StudentLibraryContext(None, None, None, BUILTIN_LIBRARY_GUIDANCE)
        )
        if self.library_context.external_dir is not None:
            self.student_dir = self.library_context.external_dir
        self.library_conflict = check_student_library_duplicate(
            self.library_context,
            self.inspection.filename or "",
        )
        if self.inspection.input_image is None:
            self.original_preview.set_image(None)
        else:
            self.original_preview.set_image(_bgr_to_display(self.inspection.input_image))
            if self.auto_crop_box is None:
                self.auto_crop_box = default_crop_box(self.inspection.input_image)
            if self.crop_box is None:
                self.crop_box = self.inspection.crop_box or self.auto_crop_box
            if self.crop_adjusting:
                self.crop_editor.set_crop_box(self.crop_box)
        self.normalized_preview.set_image(
            _rgba_to_display(self.inspection.normalized)
            if self.inspection.normalized is not None else None)
        existing = None
        if self.library_conflict.status in {"builtin", "both"}:
            existing = self.library_conflict.builtin_path
        elif self.library_conflict.status == "external":
            existing = self.library_conflict.external_path
        if existing:
            try:
                self.existing_preview.placeholder = "同名现有头像"
                self.existing_preview.set_image(_rgba_to_display(read_rgba_file(existing)))
            except AvatarImportError:
                self.existing_preview.set_image(None)
        else:
            self.existing_preview.placeholder = "无同名文件"
            self.existing_preview.set_image(None)
        self.existing_conflict_frame.setVisible(bool(existing) and not self.current_import_succeeded)
        if self.library_conflict.status == "external":
            self.conflict_label.setText("外置学生库已有同名头像，可确认替换。")
        elif self.library_conflict.status in {"builtin", "both"}:
            self.conflict_label.setText("内置学生库已有同名头像，无法导入。")
        else:
            self.conflict_label.setText("发现同名头像，不会覆盖现有文件。")

        if self.inspection.filename:
            self.filename_label.setText(f"将保存为：{self.inspection.filename}")
            self.filename_label.setVisible(True)
        else:
            self.filename_label.setText("")
            self.filename_label.setVisible(False)
        self.refresh_name_hint()
        failures = [item["text"] for item in self.inspection.items if item["status"] == "失败"]
        library_failure = (not self.library_context.ready) or any(
            "学生库目录不存在" in failure or "学生库目录不可写" in failure
            for failure in failures)
        builtin_block = self.library_conflict.status in {"builtin", "both"}
        duplicate_error = self.library_conflict.status == "error"
        if not self.library_context.ready:
            self.library_status_label.setText(
                BUILTIN_LIBRARY_GUIDANCE)
        elif self.automatic_student_library:
            self.library_status_label.setText("已找到学生头像库。")
        else:
            self.library_status_label.setText("已找到学生头像库。")

        if self.current_import_succeeded:
            self.check_summary_label.setText("导入已完成，可继续添加下一位学生。")
            self.check_summary_label.setStyleSheet(f"color: {palette['success']};")
        elif not self.library_context.ready:
            self.check_summary_label.setText(
                BUILTIN_LIBRARY_GUIDANCE)
            self.check_summary_label.setStyleSheet(f"color: {palette['danger']};")
        elif duplicate_error:
            self.check_summary_label.setText(
                self.library_conflict.error or "无法读取学生库，请重新选择完整的 BACoffee 文件夹。")
            self.check_summary_label.setStyleSheet(f"color: {palette['danger']};")
        elif builtin_block:
            self.check_summary_label.setText("请更换学生名称或学生服装。")
            self.check_summary_label.setStyleSheet(f"color: {palette['danger']};")
        elif library_failure:
            self.check_summary_label.setText(
                "未找到可用的学生头像库，请确认工具位于完整 BACoffee 软件包中。")
            self.check_summary_label.setStyleSheet(f"color: {palette['danger']};")
        elif failures:
            if self.inspection.fatal_reason == "图片无法解码":
                text = "无法读取这张图片，请换一张图片。"
            elif self.source_path is None:
                text = "请选择一张学生图片。"
            else:
                text = "这张图片暂时无法导入，请换一张图片或调整名称。"
            self.check_summary_label.setText(text)
            self.check_summary_label.setStyleSheet(f"color: {palette['danger']};")
        elif self.inspection.can_import and self.student_dir:
            self.check_summary_label.setText("自动检查已完成，可以导入。")
            self.check_summary_label.setStyleSheet(f"color: {palette['success']};")
        else:
            self.check_summary_label.setText("选择头像后，工具会即时完成必要检查。")
            self.check_summary_label.setStyleSheet(f"color: {palette['warning']};")
        if not self.library_context.ready:
            primary_status = BUILTIN_LIBRARY_GUIDANCE
        elif self.current_import_succeeded:
            primary_status = "导入成功"
        elif duplicate_error:
            primary_status = "无法读取学生库"
        elif builtin_block:
            primary_status = "无法导入"
        elif library_failure:
            primary_status = "未找到可用的学生头像库，请确认工具位于完整 BACoffee 软件包中。"
        elif self.source_path is None:
            primary_status = "请选择学生图片"
        elif not self.name_edit.text().strip():
            primary_status = "请填写学生名称"
        elif failures:
            primary_status = "图片暂时无法导入"
        elif self.inspection.can_import:
            primary_status = "准备完成，可以导入"
        else:
            primary_status = "请确认图片和名称"
        self.import_state_label.setText(primary_status)
        status_color = palette['success'] if primary_status in {"准备完成，可以导入", "导入成功"} else (
            palette['warning'] if primary_status in {"请选择学生图片", "请填写学生名称", "请确认图片和名称"}
            else palette['danger'])
        self.import_state_label.setStyleSheet(f"color: {status_color}; font-weight: bold;")
        can_import = bool(
            self.inspection.can_import and self.student_dir is not None
            and self.library_context.ready
            and self.library_conflict.status not in {"builtin", "both", "error"}
            and not self.current_import_succeeded)
        self.import_button.setEnabled(can_import)
        self.import_button.setVisible(not self.current_import_succeeded)
        self.continue_button.setVisible(self.current_import_succeeded)

    def choose_screenshot(self) -> None:
        if self.inspection is None or self.inspection.normalized is None or self.student_dir is None:
            QMessageBox.information(self, "请先完成基础检查", "请先选择头像和学生库，并通过基础检查。")
            return
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择邀请列表截图", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if not selected:
            return
        try:
            result, marked = invitation_recognition_precheck(
                Path(selected), self.inspection.normalized, self.student_dir)
            self.precheck_result = result
            if marked is not None:
                self.screenshot_preview.set_image(_bgr_to_display(marked))
            best = result.get("best")
            if best:
                second = best.get("second_name") or "无现有模板"
                self.screenshot_label.setText(
                    f"{result['status']}：最佳分数 {best['candidate_score']:.4f}；"
                    f"同位置第二名 {second} ({best['second_score']:.4f})；"
                    f"分差 {best['margin']:.4f}；{result['reason']}")
            else:
                self.screenshot_label.setText(f"{result['status']}：{result['reason']}")
            self.refresh()
        except (AvatarImportError, OSError) as exc:
            self.precheck_result = {"status": "unknown", "reason": str(exc)}
            self.screenshot_preview.set_image(None)
            self.screenshot_label.setText(f"识别预检失败：{exc}")
            self.refresh()

    def import_student(self) -> None:
        if self.inspection is None or not self.inspection.can_import or self.student_dir is None:
            return
        normalized = self.inspection.normalized
        filename = self.inspection.filename
        if filename is None or normalized is None:
            return
        replace = False

        # Re-resolve the selected BAC root before any confirmation dialog.  The
        # preview is only a preflight; the actual write must use a fresh,
        # same-root duplicate result.
        context = student_library_context_or_error(self.student_dir, TOOL_ROOT)
        conflict = check_student_library_duplicate(context, filename)
        if (
            not context.ready
            or context.external_dir is None
            or conflict.status in {"builtin", "both", "error"}
        ):
            self.library_context = context
            self.library_conflict = conflict
            self.refresh()
            return

        target_path = context.external_dir / filename
        if conflict.external_path is not None:
            target_path = conflict.external_path

        if conflict.external_path is not None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("发现同名头像")
            box.setText(f"已存在同名头像：{target_path.name}")
            box.setInformativeText("选择“替换”才会覆盖现有文件；默认取消。")
            replace_button = box.addButton("替换", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is not replace_button:
                return
            replace = True

        # A file can appear while the user is deciding.  Repeat the check
        # immediately before the write, including a late external collision.
        for _ in range(2):
            context = student_library_context_or_error(self.student_dir, TOOL_ROOT)
            conflict = check_student_library_duplicate(context, filename)
            if (
                not context.ready
                or context.external_dir is None
                or conflict.status in {"builtin", "both", "error"}
            ):
                self.library_context = context
                self.library_conflict = conflict
                self.refresh()
                return
            target_path = context.external_dir / filename
            if conflict.external_path is None or replace:
                if conflict.external_path is not None:
                    target_path = conflict.external_path
                break
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("发现同名头像")
            box.setText(f"已存在同名头像：{conflict.external_path.name}")
            box.setInformativeText("选择“替换”才会覆盖现有文件；默认取消。")
            replace_button = box.addButton("替换", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is not replace_button:
                self.refresh()
                return
            replace = True
        try:
            write_import_atomic(target_path, normalized, replace=replace)
            decoded = read_rgba_file(target_path)
            if decoded.shape != (FINAL_SIZE, FINAL_SIZE, 4):
                raise AvatarImportError("最终文件尺寸或模式校验失败")
            self.current_import_succeeded = True
            self.import_button.setEnabled(False)
            self.continue_button.setVisible(True)
            self.import_state_label.setText("导入成功")
            self.import_state_label.setStyleSheet(
                f"color: {PALETTES[self.current_theme]['success']}; font-weight: bold;")
            self.result_label.setText(
                f"{target_path.name} 已导入。\n{IMPORT_SUCCESS_NOTICE}")
            self.result_label.setVisible(True)
            self.refresh()
        except (AvatarImportError, OSError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def continue_next_student(self) -> None:
        self.source_path = None
        self.auto_crop_box = None
        self.crop_box = None
        self.crop_adjusting = False
        self.file_label.setText("尚未选择文件")
        self.file_label.setToolTip("")
        self.name_edit.clear()
        self.variant_edit.clear()
        self.current_import_succeeded = False
        self.continue_button.setVisible(False)
        self.more_options_button.setChecked(False)
        self.adjust_button.setChecked(False)
        self.crop_zoom_slider.blockSignals(True)
        self.crop_zoom_slider.setValue(100)
        self.crop_zoom_slider.blockSignals(False)
        self.crop_zoom_label.setText("100%")
        self.invalidate_precheck()
        self.result_label.clear()
        self.result_label.setVisible(False)
        self.refresh()


def main() -> int:
    arguments = sys.argv[1:]
    self_test = "--self-test" in arguments
    layout_self_test = "--layout-self-test" in arguments
    diagnostic_mode = self_test or layout_self_test
    watchdog = None
    if diagnostic_mode:
        _begin_self_test_evidence(arguments)
        _write_self_test_stage("2 QApplication creation starting")
        watchdog = _start_self_test_watchdog()

    app = QApplication(sys.argv)
    if diagnostic_mode:
        _write_self_test_stage("2 QApplication created")
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(PRODUCT_NAME)
    app.setWindowIcon(QIcon(str(ASSET_DIR / "BACoffee_Student_Add_Tool.ico")))
    app.setFont(QFont("Microsoft YaHei UI", 10))

    if self_test:
        # The ordinary self-test is deliberately fast and does not depend on
        # the full image/background/window construction path.  The full
        # construction check remains in --layout-self-test below.
        _write_self_test_stage(
            "3 UserAvatarImportWindow construction start "
            "(fast self-test skips full window)"
        )
        _write_self_test_stage(
            "4 UserAvatarImportWindow construction complete "
            "(no full window needed)"
        )
        _write_self_test_stage("5 entered --self-test branch")
        app.quit()
        app.closeAllWindows()
        app.processEvents()
        if watchdog is not None:
            watchdog.cancel()
        _write_self_test_stage("6 window/Qt cleanup complete")
        return 0

    if layout_self_test:
        _write_self_test_stage("3 UserAvatarImportWindow construction start")
        window = UserAvatarImportWindow()
        _write_self_test_stage("4 UserAvatarImportWindow construction complete")
        _write_self_test_stage("5 entered --layout-self-test branch")
        window.show()
        app.processEvents()
        errors = window.layout_visibility_errors()
        screen = window.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        _safe_diagnostic_output(
            f"layout available={available.getRect() if available else None} "
            f"frame={window.frameGeometry().getRect()} client={window.rect().getRect()}"
        )
        if errors:
            _safe_diagnostic_output("LAYOUT_FAIL: " + "；".join(errors), error=True)
            window.hide()
            window.close()
            window.deleteLater()
            app.closeAllWindows()
            app.quit()
            app.processEvents()
            if watchdog is not None:
                watchdog.cancel()
            _write_self_test_stage("6 window/Qt cleanup complete")
            return 1
        _safe_diagnostic_output(
            "LAYOUT_PASS: main-card operation row and top controls are laid out correctly"
        )
        window.hide()
        window.close()
        window.deleteLater()
        app.closeAllWindows()
        app.quit()
        app.processEvents()
        if watchdog is not None:
            watchdog.cancel()
        _write_self_test_stage("6 window/Qt cleanup complete")
        return 0

    window = UserAvatarImportWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    exit_code = main()
    if "--self-test" in sys.argv[1:] or "--layout-self-test" in sys.argv[1:]:
        _write_self_test_stage("7 about to exit process")
        _flush_diagnostic_streams()
        os._exit(exit_code)
    raise SystemExit(exit_code)
