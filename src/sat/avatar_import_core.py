from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


FINAL_SIZE = 92
# BAC's production matcher resizes external and builtin templates to the same
# 96px comparison canvas and applies a near-full circular mask.  Keep the
# importer geometry full-bleed at 92px so it does not introduce an extra
# 4px transparent frame before that production resize.
INNER_SIZE = FINAL_SIZE
MAX_INPUT_DIMENSION = 4096
MAX_INPUT_PIXELS = 16_000_000
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class AvatarImportError(ValueError):
    pass


@dataclass
class Inspection:
    source_path: Path | None
    filename: str
    target_path: Path | None
    normalized: np.ndarray | None
    input_image: np.ndarray | None
    auto_circle: bool
    items: list[dict]
    fatal_reason: str | None = None
    crop_box: tuple[int, int, int, int] | None = None

    @property
    def can_import(self) -> bool:
        return self.normalized is not None and not any(
            item["status"] == "失败" for item in self.items)


def decode_image(path: Path) -> np.ndarray:
    if not path.is_file():
        raise AvatarImportError("原文件不存在")
    try:
        with Image.open(path) as opened:
            oriented = ImageOps.exif_transpose(opened)
            if oriented.width <= 0 or oriented.height <= 0:
                raise AvatarImportError("图片无法解码")
            pixels = oriented.width * oriented.height
            if pixels > MAX_INPUT_PIXELS:
                ratio = (MAX_INPUT_PIXELS / pixels) ** 0.5
                oriented = oriented.resize(
                    (max(1, int(oriented.width * ratio)), max(1, int(oriented.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            if max(oriented.width, oriented.height) > MAX_INPUT_DIMENSION:
                ratio = MAX_INPUT_DIMENSION / max(oriented.width, oriented.height)
                oriented = oriented.resize(
                    (max(1, int(oriented.width * ratio)), max(1, int(oriented.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            if "A" in oriented.getbands() or oriented.mode in {"P", "LA"}:
                rgba = np.asarray(oriented.convert("RGBA"), dtype=np.uint8)
                return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
            rgb = np.asarray(oriented.convert("RGB"), dtype=np.uint8)
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except AvatarImportError:
        raise
    except Exception as exc:
        raise AvatarImportError("图片无法解码") from exc


def _to_rgba(image: np.ndarray) -> tuple[np.ndarray, bool]:
    if image.ndim == 2:
        rgba = cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)
        rgba[:, :, 3] = 255
        return rgba, True
    channels = image.shape[2]
    if channels == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA), False
    if channels == 3:
        rgba = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        rgba[:, :, 3] = 255
        return rgba, True
    raise AvatarImportError("图片通道数不受支持")


def _visible_bounds(rgba: np.ndarray) -> tuple[int, int, int, int]:
    alpha = rgba[:, :, 3]
    visible = alpha > 8
    if not np.any(visible):
        raise AvatarImportError("图片完全透明或没有有效图像内容")
    ys, xs = np.where(visible)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _clamp_square_box(
    center_x: float, center_y: float, side: int, width: int, height: int
) -> tuple[int, int, int, int]:
    side = max(1, min(int(side), width, height))
    x = int(round(center_x - side / 2))
    y = int(round(center_y - side / 2))
    x = max(0, min(x, width - side))
    y = max(0, min(y, height - side))
    return x, y, x + side, y + side


def default_crop_box(image: np.ndarray) -> tuple[int, int, int, int]:
    """Choose a stable square crop without requiring a face detector."""
    rgba, _ = _to_rgba(image)
    height, width = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    visible = alpha > 8
    if np.any(visible) and np.any(alpha < 250):
        x1, y1, x2, y2 = _visible_bounds(rgba)
        content_width = x2 - x1
        content_height = y2 - y1
        # Do not add an artificial safety margin here.  The production matcher
        # already supplies the circular mask; a 12% margin would make a
        # transparent source smaller than the builtin 92px portrait template.
        side = min(max(content_width, content_height), width, height)
        return _clamp_square_box(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            int(round(side)),
            width,
            height,
        )
    side = min(width, height)
    if width == height:
        center_y = height / 2
    elif height > width:
        center_y = side / 2 + (height - side) * 0.30
    else:
        center_y = height / 2
    return _clamp_square_box(width / 2, center_y, side, width, height)


def _antialiased_circle_alpha() -> np.ndarray:
    scale = 4
    mask = np.zeros((FINAL_SIZE * scale, FINAL_SIZE * scale), dtype=np.uint8)
    center = (FINAL_SIZE * scale // 2, FINAL_SIZE * scale // 2)
    radius = int(INNER_SIZE * scale / 2)
    cv2.circle(mask, center, radius, 255, -1, lineType=cv2.LINE_AA)
    return cv2.resize(mask, (FINAL_SIZE, FINAL_SIZE), interpolation=cv2.INTER_AREA)


def _opaque_content_is_blank(rgba: np.ndarray) -> bool:
    bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
    mean = float(np.mean(bgr))
    deviation = float(np.std(bgr))
    return deviation < 1.0 and (mean < 5.0 or mean > 250.0)


def normalize_avatar_array(
    image: np.ndarray,
    crop_box: tuple[int, int, int, int] | None = None,
) -> tuple[np.ndarray, bool, list[str]]:
    if image is None or image.size == 0:
        raise AvatarImportError("图片为空")
    rgba, auto_circle = _to_rgba(image)
    if auto_circle and _opaque_content_is_blank(rgba):
        raise AvatarImportError("图片是空白内容，无法作为学生头像")
    height, width = rgba.shape[:2]
    crop_box = crop_box or default_crop_box(image)
    x1, y1, x2, y2 = crop_box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise AvatarImportError("裁剪范围无效")
    content = rgba[y1:y2, x1:x2]
    content_h, content_w = content.shape[:2]
    scale = min(INNER_SIZE / content_w, INNER_SIZE / content_h)
    resized_w = max(1, int(round(content_w * scale)))
    resized_h = max(1, int(round(content_h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LANCZOS4
    resized = cv2.resize(content, (resized_w, resized_h), interpolation=interpolation)

    normalized = np.zeros((FINAL_SIZE, FINAL_SIZE, 4), dtype=np.uint8)
    offset_x = (FINAL_SIZE - resized_w) // 2
    offset_y = (FINAL_SIZE - resized_h) // 2
    normalized[offset_y:offset_y + resized_h, offset_x:offset_x + resized_w] = resized
    normalized[:, :, 3] = cv2.multiply(
        normalized[:, :, 3], _antialiased_circle_alpha(), scale=1 / 255.0)
    warnings = [
        "已自动生成圆形透明区域"
        if auto_circle else "已自动裁剪并生成圆形透明区域"
    ]
    return normalized, auto_circle, warnings


def normalize_avatar_file(path: Path) -> tuple[np.ndarray, np.ndarray, bool, list[str]]:
    image = decode_image(path)
    normalized, auto_circle, warnings = normalize_avatar_array(image)
    return image, normalized, auto_circle, warnings


def _strip_png_suffix(value: str) -> str:
    return re.sub(r"(?:\.png)+$", "", value.strip(), flags=re.IGNORECASE)


def build_filename(student_name: str, variant: str = "") -> str:
    name = _strip_png_suffix(student_name)
    variant = _strip_png_suffix(variant)
    if not name:
        raise AvatarImportError("学生名称不能为空")
    stem = f"{name} ({variant})" if variant else name
    if not stem or stem in {".", ".."} or ".." in stem:
        raise AvatarImportError("文件名不能包含 ..")
    if INVALID_FILENAME_CHARS.search(stem) or CONTROL_CHARS.search(stem):
        raise AvatarImportError("文件名包含 Windows 非法字符或控制字符")
    if stem.endswith((" ", ".")):
        raise AvatarImportError("文件名不能以空格或句点结尾")
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        raise AvatarImportError("文件名不能使用 Windows 保留设备名")
    return stem + ".png"


def _directory_is_writable(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".avatar-check-", suffix=".tmp", delete=True):
            return True
    except OSError:
        return False


def inspect_avatar(
    source_path: Path | None,
    student_name: str,
    variant: str,
    target_dir: Path,
    crop_box: tuple[int, int, int, int] | None = None,
) -> Inspection:
    items: list[dict] = []
    filename = ""
    target_path = None
    normalized = None
    input_image = None
    auto_circle = False
    fatal_reason = None

    try:
        filename = build_filename(student_name, variant)
        items.append({"status": "通过", "text": f"文件名合法：{filename}"})
    except AvatarImportError as exc:
        fatal_reason = str(exc)
        items.append({"status": "失败", "text": fatal_reason})

    if filename:
        target_path = target_dir / filename
        if target_path.exists():
            items.append({"status": "警告", "text": "已存在同名头像，确认后才会替换"})
        else:
            items.append({"status": "通过", "text": "目标文件名未重复"})

    if not target_dir.is_dir():
        items.append({"status": "失败", "text": "正式学生库目录不存在"})
    elif not _directory_is_writable(target_dir):
        items.append({"status": "失败", "text": "正式学生库目录不可写"})
    else:
        items.append({"status": "通过", "text": "正式学生库目录存在且可写"})

    if source_path is None:
        items.append({"status": "失败", "text": "尚未选择图片"})
        return Inspection(source_path, filename, target_path, normalized, input_image,
                          auto_circle, items, fatal_reason or "尚未选择图片", crop_box)

    try:
        input_image = decode_image(source_path)
        normalized, auto_circle, warnings = normalize_avatar_array(input_image, crop_box)
        items.append({"status": "通过", "text": "原文件可解码且包含有效图像内容"})
        if warnings:
            items.extend({"status": "警告", "text": warning} for warning in warnings)
        items.append({"status": "通过", "text": "自动结果为 92×92 RGBA 圆形头像"})
        alpha = normalized[:, :, 3]
        if np.any(alpha == 0):
            items.append({"status": "通过", "text": "圆形头像外存在透明区域"})
        else:
            items.append({"status": "失败", "text": "规范化结果没有透明边界"})
            normalized = None
    except AvatarImportError as exc:
        fatal_reason = str(exc)
        items.append({"status": "失败", "text": fatal_reason})
    except Exception as exc:
        fatal_reason = f"图片检查失败：{exc}"
        items.append({"status": "失败", "text": fatal_reason})

    effective_box = crop_box
    if input_image is not None and normalized is not None and effective_box is None:
        effective_box = default_crop_box(input_image)
    return Inspection(source_path, filename, target_path, normalized, input_image,
                      auto_circle, items, fatal_reason, effective_box)


def encode_png_rgba(rgba: np.ndarray) -> bytes:
    if rgba is None or rgba.shape != (FINAL_SIZE, FINAL_SIZE, 4):
        raise AvatarImportError("待保存图片不是 92×92 RGBA")
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    if not ok:
        raise AvatarImportError("PNG 编码失败")
    return bytes(encoded)


def decode_png_bytes(data: bytes) -> np.ndarray:
    raw = np.frombuffer(data, dtype=np.uint8)
    decoded = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.ndim != 3 or decoded.shape[2] != 4:
        raise AvatarImportError("保存后的 PNG 不是 RGBA")
    return cv2.cvtColor(decoded, cv2.COLOR_BGRA2RGBA)


def write_import_atomic(target_path: Path, rgba: np.ndarray, replace: bool = False) -> None:
    if target_path.exists() and not replace:
        raise AvatarImportError("同名文件已存在，拒绝覆盖")
    data = encode_png_rgba(rgba)
    reread = decode_png_bytes(data)
    if not np.array_equal(reread, rgba):
        raise AvatarImportError("保存前内容校验不一致")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target_path.parent, prefix=f".{target_path.stem}-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        saved = decode_png_bytes(temporary.read_bytes())
        if saved.shape != rgba.shape or not np.array_equal(saved, rgba):
            raise AvatarImportError("临时文件复读校验不一致")
        if target_path.exists() and not replace:
            raise AvatarImportError("同名文件在保存过程中出现，拒绝覆盖")
        if replace:
            os.replace(temporary, target_path)
        else:
            os.rename(temporary, target_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_rgba_file(path: Path) -> np.ndarray:
    image = decode_image(path)
    rgba, _ = _to_rgba(image)
    return rgba


def _template_rgba(path: Path) -> np.ndarray | None:
    try:
        image = decode_image(path)
        rgba, _ = _to_rgba(image)
        if rgba.shape[:2] != (FINAL_SIZE, FINAL_SIZE):
            rgba = cv2.resize(rgba, (FINAL_SIZE, FINAL_SIZE), interpolation=cv2.INTER_AREA)
        return rgba
    except AvatarImportError:
        return None


def alpha_aware_score(template_rgba: np.ndarray, crop_bgr: np.ndarray) -> float:
    """Compare one avatar crop with a template while honoring template alpha."""
    if template_rgba is None or crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    if crop_bgr.shape[:2] != (FINAL_SIZE, FINAL_SIZE):
        crop_bgr = cv2.resize(crop_bgr, (FINAL_SIZE, FINAL_SIZE), interpolation=cv2.INTER_AREA)
    template_bgr = cv2.cvtColor(template_rgba[:, :, :3], cv2.COLOR_RGB2BGR).astype(np.float32)
    crop = crop_bgr[:, :, :3].astype(np.float32)
    mask = (template_rgba[:, :, 3].astype(np.float32) / 255.0)
    if float(np.sum(mask)) < 16.0:
        return 0.0
    t_mean = float(np.sum(template_bgr * mask) / np.sum(mask))
    c_mean = float(np.sum(crop * mask) / np.sum(mask))
    centered_template = template_bgr - t_mean
    centered_crop = crop - c_mean
    numerator = float(np.sum(centered_template * centered_crop * mask))
    denominator = float(np.sqrt(
        np.sum(centered_template * centered_template * mask)
        * np.sum(centered_crop * centered_crop * mask)))
    if denominator <= 1e-6:
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))


def invitation_recognition_precheck(
    screenshot_path: Path,
    candidate_rgba: np.ndarray,
    students_dir: Path,
) -> tuple[dict, np.ndarray | None]:
    """Run the developer-only avatar precheck on a real invitation screenshot."""
    screenshot = decode_image(screenshot_path)
    if screenshot.ndim != 3 or screenshot.shape[2] not in (3, 4):
        raise AvatarImportError("识别预检截图必须是彩色图片")
    if screenshot.shape[2] == 4:
        screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
    else:
        screenshot_bgr = screenshot
    height, width = screenshot_bgr.shape[:2]
    if (width, height) not in {(1280, 720), (1920, 1080)}:
        raise AvatarImportError("识别预检只接受 1280×720 或 1920×1080 截图")

    templates: list[tuple[str, np.ndarray]] = []
    for path in sorted(students_dir.glob("*.png"), key=lambda item: item.name.casefold()):
        template = _template_rgba(path)
        if template is not None:
            templates.append((path.name, template))

    scale = width / 1920.0
    gray = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(1, int(80 * scale)),
        param1=100, param2=16, minRadius=max(10, int(40 * scale)),
        maxRadius=max(11, int(50 * scale)),
    )
    marked = screenshot_bgr.copy()
    if circles is None:
        return {
            "status": "unknown",
            "reason": "截图中没有检测到头像圆形候选",
            "frame_size": [width, height],
            "circle_count": 0,
            "template_count": len(templates),
        }, marked

    candidate_results = []
    expected_side = max(40, int(round(92 * scale)))
    for circle in np.round(circles[0]).astype(int):
        cx, cy, radius = map(int, circle)
        half = expected_side // 2
        x1, y1 = cx - half, cy - half
        x2, y2 = x1 + expected_side, y1 + expected_side
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
            continue
        crop = screenshot_bgr[y1:y2, x1:x2]
        crop = cv2.resize(crop, (FINAL_SIZE, FINAL_SIZE), interpolation=cv2.INTER_AREA)
        candidate_score = alpha_aware_score(candidate_rgba, crop)
        existing_scores = sorted(
            ((alpha_aware_score(template, crop), name) for name, template in templates),
            reverse=True,
        )
        second_score, second_name = existing_scores[0] if existing_scores else (0.0, None)
        candidate_results.append({
            "center": [cx, cy],
            "radius": radius,
            "candidate_score": round(float(candidate_score), 4),
            "second_name": second_name,
            "second_score": round(float(second_score), 4),
            "margin": round(float(candidate_score - second_score), 4),
        })

    if not candidate_results:
        return {
            "status": "unknown",
            "reason": "头像候选均超出截图边界",
            "frame_size": [width, height],
            "circle_count": int(len(circles[0])),
            "template_count": len(templates),
        }, marked

    best = max(candidate_results, key=lambda item: item["candidate_score"])
    accepted = (
        best["candidate_score"] >= 0.82 and best["margin"] >= 0.08)
    best["accepted"] = accepted
    color = (60, 210, 80) if accepted else (40, 80, 230)
    cv2.circle(marked, tuple(best["center"]), best["radius"], color, 4)
    cv2.drawMarker(marked, tuple(best["center"]), color, cv2.MARKER_CROSS, 24, 3)
    return {
        "status": "pass" if accepted else "warning",
        "reason": "达到当前头像判断条件" if accepted else "未同时达到当前分数与分差条件",
        "frame_size": [width, height],
        "circle_count": int(len(circles[0])),
        "template_count": len(templates),
        "best": best,
    }, marked


def encode_bgr_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise AvatarImportError("预检结果图片编码失败")
    return bytes(encoded)
