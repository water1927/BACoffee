from __future__ import annotations

import sys
from pathlib import Path

# BAAS ships a portable Python runtime whose native modules live beside
# python.exe.  Add that directory when this source is launched from BACoffee.
_runtime_dir = str(Path(sys.executable).resolve().parent)
if _runtime_dir not in sys.path:
    sys.path.insert(0, _runtime_dir)

import argparse
import ctypes
from copy import deepcopy
import hashlib
import importlib
import json
import os
import re
import secrets  # Required by external BAAS NumPy in the packaged worker.
import shutil
import site
import socket
import subprocess
import threading
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
# GUI assets are shared beside both entry points, rather than copied into EXEs.
RESOURCE_DIR = APP_DIR if getattr(sys, "frozen", False) else BUNDLE_DIR
DATA_DIR = APP_DIR / "data"
EMBEDDED_STUDENT_ASSET_DIR = BUNDLE_DIR / "assets" / "students"
RELEASE_STUDENT_ASSET_DIR = APP_DIR / "assets" / "students"
STUDENT_ASSET_DIR = (
    RELEASE_STUDENT_ASSET_DIR
    if getattr(sys, "frozen", False) and RELEASE_STUDENT_ASSET_DIR.is_dir()
    else EMBEDDED_STUDENT_ASSET_DIR)
RELEASE_MANIFEST_FILE = APP_DIR / "release-manifest.json"
BUILD_METADATA_FILE_NAME = "build-metadata.json"
USER_STUDENT_DIR = APP_DIR / "user_assets" / "students"
STUDENT_PNG_MAX_BYTES = 5 * 1024 * 1024
STUDENT_AVATAR_SIZE = 92
UI_ASSET_DIR = RESOURCE_DIR / "assets" / "ui"
BACKGROUND_DIR = RESOURCE_DIR / "assets" / "background"
EASTER_EGG_FILE = BACKGROUND_DIR / "居然是彩蛋吗！.txt"
LIGHT_BACKGROUNDS = ["青空笺语.png", "光 · 青叶 · 望.jpg"]
DARK_BACKGROUNDS = ["夜与月色.jpg", "Suger Rush!!!!.jpg"]
LIGHT_BACKGROUND_LABELS = [Path(name).stem for name in LIGHT_BACKGROUNDS]
DARK_BACKGROUND_LABELS = [Path(name).stem for name in DARK_BACKGROUNDS]
CUSTOM_BACKGROUND_LABEL = "自定义图片"
BRANDING_DIR = RESOURCE_DIR / "assets" / "branding"
COFFEE_LOGO = BRANDING_DIR / "Coffee.png"
COFFEE_DARK_LOGO = BRANDING_DIR / "Coffee_Dark.png"
COFFEE_ICON = BRANDING_DIR / "Coffee.ico"
FONT_DIR = RESOURCE_DIR / "assets" / "fonts"
COMFORTAA_LIGHT = FONT_DIR / "Comfortaa-Light.ttf"
SETTINGS_FILE = DATA_DIR / "settings.json"
PROFILE_DIR = DATA_DIR / "baas_profile"
LOG_FILE = DATA_DIR / "BACoffee.log"
RESULT_FILE = DATA_DIR / "last_result.json"
POWER_DIAGNOSTIC_FILE = DATA_DIR / "power_diagnostic_last.json"
RESUME_DIAGNOSTIC_FILE = DATA_DIR / "resume_diagnostic_last.json"
HIBERNATE_STATUS_FILE = DATA_DIR / "hibernate_helper_status.json"
SCHEDULE_TASK_NAME = "BACoffee-Cafe"
GUI_INSTANCE_SERVER_NAME = "BACoffee.GUI.v1"
GUI_INSTANCE_MUTEX_NAME = "Local\\BACoffee.GUI.v1"
SCHEDULE_STATUS_FILE = DATA_DIR / "schedule_status.json"
PORTABLE_BAAS = APP_DIR / "third_party" / "BAAS"
DEFAULT_BAAS = PORTABLE_BAAS
RUNTIME_DIR = APP_DIR / "runtime"
PORTABLE_SITE_PACKAGES = RUNTIME_DIR / "Lib" / "site-packages"
PORTABLE_PYTHON = RUNTIME_DIR / "python.exe"
DEFAULT_PYTHON = PORTABLE_PYTHON
DEFAULT_MUMU_MANAGER = ""
CN_GAME_SERVERS = ("官服", "B服")


def _sha256_file(path: Path) -> str | None:
    """Return a file hash without loading the complete file into memory."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest().upper()


def _load_build_metadata() -> dict:
    """Load the build metadata embedded in the EXE or copied beside it."""
    candidates = [BUNDLE_DIR / BUILD_METADATA_FILE_NAME]
    if APP_DIR / BUILD_METADATA_FILE_NAME not in candidates:
        candidates.append(APP_DIR / BUILD_METADATA_FILE_NAME)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def source_compatibility_diagnostic() -> dict:
    """Compare the runtime's旁置 source with the build input recorded in metadata."""
    source_path = APP_DIR / "BACoffee.py" if getattr(sys, "frozen", False) else Path(__file__).resolve()
    metadata = _load_build_metadata()
    metadata_expected = str(metadata.get("source_sha256", "")).strip().upper()
    env_expected = os.environ.get("BACOFFEE_EXPECTED_SOURCE_SHA256", "").strip().upper()
    expected = env_expected or metadata_expected
    actual = _sha256_file(source_path)
    enforced = bool(expected)
    ok = bool(actual) and (not enforced or actual == expected)
    if getattr(sys, "frozen", False) and not metadata_expected:
        ok = False
    return {
        "source_path": str(source_path),
        "actual_sha256": actual,
        "expected_sha256": expected or None,
        "build_id": str(metadata.get("build_id", "")),
        "build_metadata_present": bool(metadata),
        "enforced": enforced or getattr(sys, "frozen", False),
        "ok": ok,
    }


def print_source_compatibility_diagnostic(diagnostic: dict) -> None:
    status = "通过" if diagnostic.get("ok") else "失败"
    print(
        f"[源码一致性] {status}；build_id={diagnostic.get('build_id') or '未提供'}；"
        f"actual_sha256={diagnostic.get('actual_sha256') or '缺失'}；"
        f"expected_sha256={diagnostic.get('expected_sha256') or '未提供'}")


class CafeInvitationRecoveryRequired(RuntimeError):
    """The invitation bridge reached an unsafe/unknown state and needs recovery."""


INVITATION_BUDGET_SECONDS = 90.0
INVITATION_DISCOVERY_RESERVE_SECONDS = 20.0
INVITATION_DISCOVERY_PAGE_LIMIT = 45
INVITATION_RELOCATION_PAGE_LIMIT = 5
BASH_HIGH_CONFIDENCE_SCORE = 0.82
BASH_RECHECK_MIN_SCORE = 0.64
BASH_MIN_MARGIN = 0.08
BASH_RECHECK_CENTER_TOLERANCE = 5
BASH_CROP_ADJUST_MIN_SCORE = 0.55


def runtime_site_packages(_baas_root: Path | None = None) -> Path:
    """Return only the package directory shipped with this release."""
    return PORTABLE_SITE_PACKAGES


def add_runtime_site_packages(baas_root: Path | None = None) -> Path:
    """Make the isolated runtime available to worker-side imports."""
    packages = runtime_site_packages(baas_root)
    if not packages.is_dir():
        raise RuntimeError(f"发行版运行时缺失：{packages}")
    site.addsitedir(str(packages))
    return packages


def runtime_python(_baas_root: Path | None = None) -> Path:
    """Return only the interpreter shipped with this release."""
    return PORTABLE_PYTHON


def portable_install_missing() -> list[tuple[str, Path]]:
    required = [
        ("BACoffee 主程序", APP_DIR / "BACoffee.py"),
        ("便携 Python", PORTABLE_PYTHON),
        ("便携 Python 依赖", PORTABLE_SITE_PACKAGES),
        ("便携 BAAS 主程序", PORTABLE_BAAS / "main.py"),
        ("便携 BAAS 配置模板", PORTABLE_BAAS / "config" / "cn" / "config.json"),
        ("便携 BAAS 事件模板", PORTABLE_BAAS / "config" / "cn" / "event.json"),
        ("便携 OCR 资源", PORTABLE_BAAS / "core" / "ocr" / "baas_ocr_client" / "bin"),
    ]
    if getattr(sys, "frozen", False):
        required.append(("构建元数据", APP_DIR / BUILD_METADATA_FILE_NAME))
    return [(label, path) for label, path in required if not path.exists()]


def require_portable_install() -> None:
    missing = portable_install_missing()
    if missing:
        details = "\n".join(f"- {label}: {path}" for label, path in missing)
        raise RuntimeError(
            "BACoffee 发行内容不完整，请完整解压后再运行。缺失项：\n" + details)

DEFAULT_SETTINGS = {
    "baas_root": str(DEFAULT_BAAS),
    "baas_python": str(DEFAULT_PYTHON),
    "cafe2": True,
    "collect_reward": False,
    "invite_student": True,
    "invite_allow_exchange_student": False,
    "auto_download_game_updates": False,
    "pat_rounds": 4,
    "schedule_times": ["04:00", "07:10", "10:20", "13:30", "16:00", "19:10", "22:20", "01:30"],
    "schedule_override": None,
    "game_server": "官服",
    "hibernate_after_success": True,
    "wake_stabilization_seconds": 10,
    "scheduled_task_timeout_minutes": 30,
    "settings_version": 9,
    "mumu_manager_path": DEFAULT_MUMU_MANAGER,
    "mumu_vm_index": 1,
    "mumu_android_version": "15",
    "mumu_start_wait_seconds": 30,
    "theme": "system",
    "background_image": "",
    "light_background_choice": LIGHT_BACKGROUNDS[0],
    "dark_background_choice": DARK_BACKGROUNDS[0],
    "custom_light_background": "",
    "custom_dark_background": "",
    "background_opacity": 50,
    "hide_home_brand": False,
    "minimize_to_tray": False,
    "tray_notice_seen": False,
    "window_x": None,
    "window_y": None,
    "students_cafe1": [],
    "students_cafe2": [],
    "bash_students_cafe1": [],
    "bash_students_cafe2": [],
    "students": [
        {"name": "夏", "label": "夏", "avatar": "https://static.kivo.wiki/images/students/%E6%9F%9A%E9%B8%9F%20%E5%A4%8F/avatar.png"},
        {"name": "青叶", "label": "青叶", "avatar": "https://static.kivo.wiki/images/students/%E5%86%85%E6%B5%B7%20%E9%9D%92%E5%8F%B6/original/Student_Portrait_CH0288_Collection.png"},
        {"name": "光", "label": "光", "avatar": "https://static.kivo.wiki/images/students/%E6%A9%98%20%E5%85%89/original/Student_Portrait_CH0242_Collection.png"},
        {"name": "未花", "label": "未花", "avatar": "https://static.kivo.wiki/images/students/%E5%9C%A3%E5%9B%AD%E6%9C%AA%E8%8A%B1/avatar.png"},
        {"name": "花子(泳装)", "label": "花子（泳装）", "avatar": "https://static.kivo.wiki/images/students/%E6%B5%A6%E5%92%8C%20%E8%8A%B1%E5%AD%90/%E6%B3%B3%E8%A3%85/avatar.png"},
    ],
}


def normalize_game_server(value: object) -> str:
    return value if isinstance(value, str) and value in CN_GAME_SERVERS else "官服"


def load_settings() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_user_student_dir()
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
    raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    data = DEFAULT_SETTINGS | raw
    # A release must never inherit an external BAAS or Python interpreter from
    # an older developer installation. MuMu remains user-selectable below.
    data["baas_root"] = str(PORTABLE_BAAS)
    data["baas_python"] = str(PORTABLE_PYTHON)
    if int(raw.get("settings_version", 1)) < 2:
        data["hibernate_after_success"] = True
        data["settings_version"] = 2
    preferred_python = runtime_python()
    if data.get("baas_python") != str(preferred_python):
        data["baas_python"] = str(preferred_python)
    legacy_background = data.get("background_image", "")
    if legacy_background and not data.get("custom_light_background") and not data.get("custom_dark_background"):
        data["custom_light_background"] = legacy_background
        data["custom_dark_background"] = legacy_background
    if data.get("light_background_choice") == "光，青叶与希望.jpg":
        data["light_background_choice"] = "光 · 青叶 · 望.jpg"
    game_server = data.get("game_server")
    normalized_game_server = normalize_game_server(game_server)
    if normalized_game_server != game_server:
        append_log("[渠道] 配置值无效，已回退到官服。")
        data["game_server"] = normalized_game_server
    # The old one-shot defer fields were only consumed by a blocking worker
    # sleep.  Remove them during migration so they can never drive execution.
    data.pop("scheduled_defer_until", None)
    data.pop("scheduled_defer_period", None)
    data["settings_version"] = 9
    legacy_names = [item["name"] for item in data.get("students", []) if item.get("name")]
    # A present empty list is an intentional user choice for a fresh install;
    # only older settings that lack the fields receive the legacy migration.
    if "students_cafe1" not in raw:
        data["students_cafe1"] = list(legacy_names)
    if "students_cafe2" not in raw:
        data["students_cafe2"] = list(legacy_names)
    if "bash_students_cafe1" not in raw:
        data["bash_students_cafe1"] = []
    if "bash_students_cafe2" not in raw:
        data["bash_students_cafe2"] = []
    if data != raw:
        save_settings(data)
    return data


def ensure_user_student_dir() -> Path:
    """Create the persistent external student directory when possible."""
    try:
        USER_STUDENT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        append_log(f"[学生库] 无法创建外置学生库，继续使用内置库：{exc}")
    return USER_STUDENT_DIR


def _student_name_key(name: str) -> str:
    return str(name).casefold()


def _student_files(root: Path):
    try:
        entries = sorted(root.iterdir(), key=lambda item: (item.name.casefold(), item.name))
    except OSError as exc:
        append_log(f"[学生库] 无法读取目录 {root}：{exc}")
        return
    for path in entries:
        if path.is_file():
            yield path


def builtin_student_asset_dir() -> Path:
    """Return the one authoritative built-in student directory for this run."""
    if getattr(sys, "frozen", False) and RELEASE_STUDENT_ASSET_DIR.is_dir():
        return RELEASE_STUDENT_ASSET_DIR
    return EMBEDDED_STUDENT_ASSET_DIR


def validate_builtin_student(path: Path) -> tuple[bool, str]:
    """Validate a packaged student avatar without imposing the external 92x92 rule."""
    if path.suffix.casefold() != ".png":
        return False, "不是 PNG 文件"
    try:
        if path.stat().st_size > STUDENT_PNG_MAX_BYTES:
            return False, "文件超过 5 MB"
    except OSError as exc:
        return False, f"无法读取文件信息：{exc}"
    try:
        image = _read_cv_image(path)
    except Exception as exc:
        return False, f"PNG 解码异常：{exc}"
    if image is None or image.size == 0:
        return False, "PNG 无法解码"
    if image.ndim != 3 or image.shape[2] != 4:
        return False, "不是四通道 RGBA"
    height, width = image.shape[:2]
    if not (0 < width <= 4096 and 0 < height <= 4096):
        return False, f"尺寸不合理：{width}x{height}"
    return True, ""


def inspect_builtin_student_library() -> tuple[Path, list[Path], list[tuple[str, str]]]:
    """Inspect the selected built-in library and return valid and invalid files."""
    root = builtin_student_asset_dir()
    valid_paths = []
    invalid = []
    for path in _student_files(root) or ():
        if path.suffix.casefold() != ".png":
            continue
        valid, reason = validate_builtin_student(path)
        if valid:
            valid_paths.append(path)
        else:
            invalid.append((path.name, reason))
    return root, valid_paths, invalid


def expected_builtin_student_count() -> int | None:
    """Read the optional release manifest count when it is shipped beside the EXE."""
    if not RELEASE_MANIFEST_FILE.is_file():
        return None
    try:
        manifest = json.loads(RELEASE_MANIFEST_FILE.read_text(encoding="utf-8"))
        return sum(
            1 for item in manifest.get("files", [])
            if item.get("path", "").casefold().startswith("assets/students/")
            and item.get("path", "").casefold().endswith(".png")
        )
    except Exception as exc:
        append_log(f"[学生库] 无法读取发行清单 {RELEASE_MANIFEST_FILE}：{exc}")
        return None


def validate_user_student(path: Path) -> tuple[bool, str]:
    """Validate an external student avatar without modifying the user file."""
    if not path.name or path.name in {".", ".."} or "/" in path.name or "\\" in path.name:
        return False, "文件名为空或包含路径分隔符"
    if path.suffix.casefold() != ".png":
        return False, "不是 PNG 文件"
    try:
        if path.stat().st_size > STUDENT_PNG_MAX_BYTES:
            return False, "文件超过 5 MB"
    except OSError as exc:
        return False, f"无法读取文件信息：{exc}"
    try:
        image = _read_cv_image(path)
    except Exception as exc:
        return False, f"PNG 解码异常：{exc}"
    if image is None or image.size == 0:
        return False, "PNG 无法解码"
    if image.ndim != 3 or image.shape[2] != 4:
        return False, "不是四通道 RGBA"
    if image.shape[0] != STUDENT_AVATAR_SIZE or image.shape[1] != STUDENT_AVATAR_SIZE:
        return False, f"尺寸不是 {STUDENT_AVATAR_SIZE}x{STUDENT_AVATAR_SIZE}"
    return True, ""


def load_student_catalog() -> dict[str, dict]:
    """Return one merged, validated catalog keyed by Windows-style filename."""
    ensure_user_student_dir()
    catalog: dict[str, dict] = {}

    builtin_dir, builtin_paths, invalid_builtin = inspect_builtin_student_library()
    for name, reason in invalid_builtin:
        append_log(f"[学生库] 忽略内置文件 {name}：{reason}")
    expected_count = expected_builtin_student_count()
    count_note = f"；发行清单数量：{expected_count}" if expected_count is not None else ""
    global _CATALOG_LOG_SUMMARY
    summary = (str(builtin_dir), len(builtin_paths), expected_count)
    if summary != _CATALOG_LOG_SUMMARY:
        append_log(f"[学生库] 内置 {len(builtin_paths)} 张{count_note}")
        _CATALOG_LOG_SUMMARY = summary
    if expected_count is not None and expected_count != len(builtin_paths):
        append_log(
            f"[学生库] 内置库数量与发行清单不一致：实际 {len(builtin_paths)}，清单 {expected_count}")

    for path in builtin_paths:
        key = _student_name_key(path.name)
        if key in catalog:
            append_log(f"[学生库] 内置文件名冲突，保留：{catalog[key]['name']}；忽略：{path.name}")
            continue
        catalog[key] = {"name": path.name, "path": path, "source": "builtin"}

    for path in _student_files(USER_STUDENT_DIR) or ():
        if path.name.casefold() == "readme.txt":
            continue
        valid, reason = validate_user_student(path)
        if not valid:
            append_log(f"[学生库] 忽略外置文件 {path.name}：{reason}")
            continue
        key = _student_name_key(path.name)
        if key in catalog:
            existing = catalog[key]
            if existing["source"] == "builtin":
                append_log(
                    f"[学生库] 外置文件冲突，内置优先：{existing['name']}；忽略外置：{path.name}")
            else:
                append_log(
                    f"[学生库] 外置文件名仅大小写冲突，按排序保留：{existing['name']}；忽略：{path.name}")
            continue
        catalog[key] = {"name": path.name, "path": path, "source": "user"}
    return dict(sorted(catalog.items(), key=lambda item: (item[1]["name"].casefold(), item[1]["name"])))


def resolve_student_avatar(name: str, catalog: dict[str, dict] | None = None) -> dict | None:
    """Resolve a configured PNG filename to its validated file and source."""
    if not isinstance(name, str) or not name.strip():
        return None
    catalog = catalog or load_student_catalog()
    return catalog.get(_student_name_key(name.strip()))


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_settings_atomic(settings: dict) -> None:
    """Persist settings by replacing a same-directory temporary file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_name(f"{SETTINGS_FILE.name}.{os.getpid()}.tmp")
    payload = json.dumps(settings, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SETTINGS_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def schedule_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the current half-day window as an absolute local [start, end)."""
    current = now or datetime.now()
    if current.hour < 4:
        start = (current - timedelta(days=1)).replace(
            hour=16, minute=0, second=0, microsecond=0)
        end = current.replace(hour=4, minute=0, second=0, microsecond=0)
    elif current.hour < 16:
        start = current.replace(hour=4, minute=0, second=0, microsecond=0)
        end = current.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        start = current.replace(hour=16, minute=0, second=0, microsecond=0)
        end = (current + timedelta(days=1)).replace(
            hour=4, minute=0, second=0, microsecond=0)
    return start, end


def schedule_period_anchor(now: datetime | None = None) -> datetime:
    """Return the current 04:00/16:00 schedule boundary."""
    return schedule_window(now)[0]


def schedule_period_key(now: datetime | None = None) -> str:
    return schedule_period_anchor(now).isoformat(timespec="minutes")


def resolve_first_deferred_time(now: datetime, hour: int, minute: int) -> datetime:
    """Resolve an HH:mm input to a future point inside the current half-day window."""
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ValueError("请输入有效的 HH:mm 时间。")
    start, end = schedule_window(now)
    current_minute = now.replace(second=0, microsecond=0)
    target = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if target == current_minute:
        # The current minute may already be in progress when Task Scheduler
        # receives the request, so normalize it to the next executable minute.
        target = current_minute + timedelta(minutes=1)
    elif target <= now:
        # Only the post-midnight portion of the current night window may cross
        # to the next calendar date.  Other past times are outside this window.
        target += timedelta(days=1)
    if target <= now:
        raise ValueError("输入时间必须晚于当前时间。")
    if target < start:
        raise ValueError("输入时间不属于当前 04:00–16:00 或 16:00–04:00 时段。")
    if target >= end:
        raise ValueError(f"输入时间必须早于本时段结束边界 {end:%H:%M}。")
    return target


def build_deferred_runs(first: datetime, end: datetime) -> list[datetime]:
    """Build first, first+3h10m, ... while every point remains before end."""
    if first >= end:
        raise ValueError("首次推迟时间必须早于本时段结束边界。")
    runs = []
    current = first
    while current < end:
        runs.append(current)
        current += timedelta(hours=3, minutes=10)
    return runs


def _parse_schedule_datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def valid_schedule_override(settings: dict, now: datetime | None = None) -> dict | None:
    """Return a successfully applied, current-window override with future runs."""
    current = now or datetime.now()
    raw = settings.get("schedule_override")
    if not isinstance(raw, dict) or raw.get("applied") is not True:
        return None
    start = _parse_schedule_datetime(raw.get("period_start"))
    end = _parse_schedule_datetime(raw.get("period_end"))
    first = _parse_schedule_datetime(raw.get("first_run"))
    run_times = [_parse_schedule_datetime(item) for item in raw.get("run_times", [])]
    if start is None or end is None or first is None or not run_times:
        return None
    expected_start, expected_end = schedule_window(current)
    if start != expected_start or end != expected_end or current >= end:
        return None
    if not (start <= first < end):
        return None
    if any(item is None or not (start <= item < end) for item in run_times):
        return None
    normalized_runs = sorted(run_times)
    if normalized_runs != run_times or len(set(normalized_runs)) != len(normalized_runs):
        return None
    if not any(item > current for item in normalized_runs):
        return None
    normalized = deepcopy(raw)
    normalized.update({
        "period_start": start.isoformat(timespec="seconds"),
        "period_end": end.isoformat(timespec="seconds"),
        "first_run": first.isoformat(timespec="seconds"),
        "run_times": [item.isoformat(timespec="seconds") for item in normalized_runs],
    })
    return normalized


def format_schedule_target(target: datetime, now: datetime | None = None) -> str:
    """Format a target time without sending Chinese text through locale.strftime."""
    current = now or datetime.now()
    clock_text = target.strftime("%H:%M")
    if target.date() == current.date():
        return f"今天 {clock_text}"
    if target.date() == (current + timedelta(days=1)).date():
        return f"明天 {clock_text}"
    return f"{target.strftime('%m')}月{target.strftime('%d')}日 {clock_text}"


def _read_schedule_status() -> dict:
    if not SCHEDULE_STATUS_FILE.is_file():
        return {}
    try:
        return json.loads(SCHEDULE_STATUS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _run_wake_task_update(request_id: str = "") -> subprocess.CompletedProcess:
    script = APP_DIR / "Install-WakeTask.ps1"
    if not script.is_file():
        raise RuntimeError("发行内容不完整：缺少 Install-WakeTask.ps1。")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *(["-RequestId", request_id] if request_id else [])],
        cwd=str(APP_DIR), text=True, encoding="utf-8", errors="replace", capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _verify_schedule_update(candidate: dict, result: subprocess.CompletedProcess) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "PowerShell 更新失败").strip()
        raise RuntimeError(detail[-1200:])
    status = _read_schedule_status()
    if status.get("success") is not True:
        raise RuntimeError(str(status.get("error") or "唤醒计划更新后校验失败。"))
    override = candidate.get("schedule_override") or {}
    if status.get("override_updated_at") != override.get("updated_at"):
        raise RuntimeError("唤醒计划状态不是本次更新产生的结果。")
    if status.get("override_active") is not True:
        raise RuntimeError("唤醒计划未确认当前时段的临时触发器。")
    if status.get("wake_to_run") is not True:
        raise RuntimeError("Windows 任务未确认 WakeToRun。")
    verified = set(status.get("verified_trigger_starts") or [])
    expected = {
        item.replace("+00:00", "")
        for item in override.get("run_times", [])
    }
    if not expected.issubset(verified):
        missing = ", ".join(sorted(expected - verified))
        raise RuntimeError(f"唤醒计划缺少临时触发器：{missing}")
    append_log(f"[schedule] verified triggers={status.get('trigger_count', len(verified))}")


def apply_schedule_override(override: dict) -> tuple[bool, str]:
    """Apply a current-window override with settings/task rollback on failure."""
    old_settings = deepcopy(load_settings())
    candidate = deepcopy(old_settings)
    pending = deepcopy(override)
    pending["applied"] = False
    pending["updated_at"] = datetime.now().isoformat(timespec="seconds")
    candidate["schedule_override"] = pending
    period = f"{pending.get('period_start')}..{pending.get('period_end')}"
    runs = ",".join(str(item) for item in pending.get("run_times", []))
    append_log(f"[schedule] defer requested; period={period}; first={pending.get('first_run')}; runs={runs}")
    try:
        save_settings_atomic(candidate)
        append_log("[schedule] updating Windows wake task")
        result = _run_wake_task_update()
        append_log(f"[schedule] wake task update result={'success' if result.returncode == 0 else 'failed'}; exit_code={result.returncode}")
        _verify_schedule_update(candidate, result)
        committed = deepcopy(candidate)
        committed["schedule_override"]["applied"] = True
        save_settings_atomic(committed)
        append_log("[schedule] override committed")
        return True, ""
    except Exception as exc:
        try:
            save_settings_atomic(old_settings)
        except Exception as restore_exc:
            return False, f"{exc}；设置回滚失败：{restore_exc}"
        return False, str(exc)


def _verify_default_schedule_update(result, request_id: str, started: datetime,
                                    *, restoring: bool = False) -> None:
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "计划安装失败").strip()[-1200:])
    status = _read_schedule_status()
    if status.get("request_id") != request_id:
        raise RuntimeError("默认计划状态不是本次安装产生的结果。")
    try:
        installed_at = datetime.fromisoformat(status["installed_at"])
        installed_at = installed_at.astimezone().replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("默认计划缺少有效的安装确认时间。")
    if not started <= installed_at <= datetime.now() + timedelta(seconds=5):
        raise RuntimeError("默认计划安装确认时间已陈旧或无效。")
    try:
        planned_at = datetime.fromisoformat(status["planned_at"]).astimezone().replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("默认计划缺少本次触发器计算时间。")
    if not started <= planned_at <= installed_at:
        raise RuntimeError("默认计划触发器计算时间无效。")
    allowed = ("default", "invalid_or_expired") if restoring else ("default",)
    if (status.get("success") is not True or status.get("installed") is not True
            or status.get("override_active") is not False
            or status.get("override_status") not in allowed
            or status.get("wake_to_run") is not True):
        raise RuntimeError(str(status.get("error") or "Windows 未确认恢复默认唤醒计划。"))
    triggers = status.get("verified_triggers") or []
    expected = {"01:30", "04:00", "07:10", "10:20", "13:30", "16:00", "19:10", "22:20"}
    clocks = set()
    starts = set(status.get("verified_trigger_starts") or [])
    for trigger in triggers:
        try:
            first = datetime.fromisoformat(trigger["start_boundary"])
            clock = first.strftime("%H:%M")
            next_at = planned_at.replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)
            if next_at <= planned_at:
                next_at += timedelta(days=1)
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("默认计划触发器回读无效。")
        if (trigger.get("trigger_type") != "Daily" or trigger.get("days_interval") != 1
                or trigger.get("enabled") is not True or first != next_at
                or first.isoformat(timespec="seconds") not in starts):
            raise RuntimeError("默认计划回读包含非每日、禁用或日期不符的触发器。")
        clocks.add(clock)
    if len(triggers) != 8 or len(starts) != 8 or clocks != expected or status.get("trigger_count") != 8:
        raise RuntimeError("Windows 回读未确认原有八个每日默认时刻。")


def restore_default_schedule() -> tuple[bool, str]:
    """Restore the default task and keep settings/task failures visible."""
    original = deepcopy(load_settings())
    candidate = deepcopy(original)
    candidate["schedule_override"] = None
    request_id = secrets.token_hex(16)
    started = datetime.now()
    result = None
    try:
        save_settings_atomic(candidate)
        result = _run_wake_task_update(request_id)
        _verify_default_schedule_update(result, request_id, started)
        append_log("[schedule] default schedule verified; daily triggers=8")
        return True, ""
    except Exception as exc:
        try:
            save_settings_atomic(original)
        except Exception as restore_exc:
            return False, f"{exc}；设置回滚失败：{restore_exc}；请核对实际计划状态。"
        status = _read_schedule_status()
        # A successful script with an invalid confirmation may already have
        # registered the default task. Reinstall the saved schedule explicitly.
        if result is not None and result.returncode == 0:
            try:
                rollback_id = secrets.token_hex(16)
                rollback_started = datetime.now()
                rollback = _run_wake_task_update(rollback_id)
                rollback_status = _read_schedule_status()
                if rollback_status.get("request_id") != rollback_id:
                    raise RuntimeError("计划回滚确认不是本次结果")
                if valid_schedule_override(original):
                    _verify_schedule_update(original, rollback)
                else:
                    _verify_default_schedule_update(rollback, rollback_id, rollback_started, restoring=True)
            except Exception as rollback_exc:
                return False, f"{exc}；系统计划回滚失败：{rollback_exc}；原设置已恢复，实际计划请核对。"
            return False, f"{exc}；原设置和原计划已恢复。"
        if status.get("request_id") == request_id and status.get("rollback_succeeded") is False:
            return False, f"{exc}；系统计划回滚失败：{status.get('rollback_error', '')}；原设置已恢复，实际计划请核对。"
        return False, f"{exc}；原设置已恢复，未确认恢复默认计划成功。"


_IPC_USER_SID = None


def _ipc_security(kernel):
    """Same-user mutexes work for elevated schedules and ordinary manual workers."""
    global _IPC_USER_SID
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if _IPC_USER_SID is None:
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            data = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            text = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                _IPC_USER_SID = text.value
            finally:
                kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            kernel.CloseHandle(token)
    class Attributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
    descriptor = ctypes.c_void_p()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    # This labels only our newly created IPC object, not system/global settings.
    sddl = f"D:(A;;GA;;;{_IPC_USER_SID})S:(ML;;NW;;;ME)"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return Attributes(ctypes.sizeof(Attributes), descriptor, False), descriptor


class _NamedMutex:
    """A kernel mutex, automatically abandoned/released if its process exits."""
    def __init__(self, purpose, path=None):
        from ctypes import wintypes
        scope = os.path.normcase(os.path.abspath(str(path or DATA_DIR)))
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        attributes, descriptor = _ipc_security(self.kernel)
        try:
            self.handle = self.kernel.CreateMutexW(ctypes.byref(attributes), False, f"Local\\BACoffee.{purpose}.{digest}")
        finally:
            self.kernel.LocalFree(descriptor)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.held = False

    def acquire(self, timeout=0):
        result = self.kernel.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if result in (0, 0x80):
            self.held = True
            return True
        if result != 0x102:
            raise ctypes.WinError(ctypes.get_last_error())
        return False

    def close(self):
        if self.handle:
            if self.held:
                self.kernel.ReleaseMutex(self.handle)
                self.held = False
            self.kernel.CloseHandle(self.handle)
            self.handle = None


_LOG_THREAD_LOCK = threading.RLock()
_LOG_MAINTAINED = {}
_LOG_RETRY_AT = {}
_LOG_PENDING = []
_CATALOG_LOG_SUMMARY = None


def _trim_main_log(text: str, now: datetime) -> str:
    cutoff = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    kept = []
    keep_record = True
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if match:
            try:
                keep_record = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") >= cutoff
            except ValueError:
                keep_record = True
        elif line.startswith("["):
            # An unknown record must not be discarded using a previous date.
            keep_record = True
        if keep_record:
            kept.append(line)
    return "".join(kept)


def _maintain_main_log(now: datetime) -> None:
    """Called only while the cross-process append/maintenance mutex is held."""
    key = str(LOG_FILE)
    if _LOG_MAINTAINED.get(key) == now.date() or time.monotonic() < _LOG_RETRY_AT.get(key, 0):
        return
    temporary = None
    try:
        if LOG_FILE.exists():
            with LOG_FILE.open("r", encoding="utf-8", newline="") as handle:
                original = handle.read()
            retained = _trim_main_log(original, now)
            if retained != original:
                fd, name = tempfile.mkstemp(prefix=".BACoffee-log-", suffix=".tmp", dir=str(LOG_FILE.parent))
                temporary = Path(name)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(retained)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, LOG_FILE)
        _LOG_MAINTAINED[key] = now.date()
        _LOG_RETRY_AT.pop(key, None)
    except (OSError, UnicodeError) as exc:
        _LOG_RETRY_AT[key] = time.monotonic() + 30
        print(f"[日志] 七天维护暂缓，原文件保留：{exc}", flush=True)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def append_log(message: str) -> None:
    now = datetime.now()
    line = f"[{now:%Y-%m-%d %H:%M:%S}] {message}"
    with _LOG_THREAD_LOCK:
        mutex = None
        _LOG_PENDING.append(line + "\n")
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            mutex = _NamedMutex("log", LOG_FILE)
            if not mutex.acquire(5):
                raise TimeoutError("日志锁等待超时")
            _maintain_main_log(now)
            with LOG_FILE.open("a", encoding="utf-8", newline="") as handle:
                handle.write("".join(_LOG_PENDING))
            _LOG_PENDING.clear()
        except (OSError, TimeoutError) as exc:
            # Never append without the mutex: a concurrent replace would lose it.
            print(f"[日志] 写入暂缓，将在后续写入时重试：{exc}", flush=True)
        finally:
            if mutex is not None:
                mutex.close()
    print(line, flush=True)


class WorkerCancelled(BaseException):
    """Bypass BAAS broad Exception handlers and their automatic game recovery."""


_WORKER_TOKEN = ""
_WORKER_CANCELLED = threading.Event()
_WORKER_BAAS = None
_WORKER_MONITOR_STOP = threading.Event()


def _manual_pending() -> bool:
    mutex = _NamedMutex("manual")
    try:
        return not mutex.acquire(0)
    finally:
        mutex.close()


def _owner_state() -> dict:
    try:
        value = json.loads((DATA_DIR / "worker-owner.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_owner(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "worker-owner.json"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def worker_checkpoint() -> None:
    if _WORKER_CANCELLED.is_set():
        raise WorkerCancelled("被手动任务接管")


def worker_sleep(seconds: float) -> None:
    if _WORKER_TOKEN:
        if _WORKER_CANCELLED.wait(max(0, seconds)):
            worker_checkpoint()
    else:
        time.sleep(seconds)





def _guard_baas_actions(thread_obj) -> None:
    """Fence asynchronous click threads too, without editing third-party code."""
    for target, names in (
        (thread_obj, ("click", "click_thread", "get_screenshot_array", "solve", "exit_emulator")),
        (getattr(thread_obj, "control", None), ("click", "swipe", "long_click", "scroll")),
        (getattr(thread_obj, "connection", None), ("launch_app", "close_current_app", "start_app")),
    ):
        if target is None:
            continue
        for name in names:
            original = getattr(target, name, None)
            if not callable(original):
                continue
            def guarded(*args, _original=original, **kwargs):
                worker_checkpoint()
                value = _original(*args, **kwargs)
                worker_checkpoint()
                return value
            setattr(target, name, guarded)


def _stop_owned_ocr(result: dict) -> None:
    pid = result.get("ocr_server_pid")
    created = result.get("ocr_server_created")
    if not pid or not created:
        return
    try:
        import psutil
        process = psutil.Process(int(pid))
        if (abs(process.create_time() - float(created)) < 0.01
                and os.path.normcase(process.exe()) == os.path.normcase(result.get("ocr_server_exe", ""))):
            process.terminate()
            try:
                process.wait(3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(3)
    except Exception as exc:
        append_log(f"[任务互斥] 本轮 OCR 收尾提示：{exc}")


class _OwnedJob:
    """Own the payload and its spawned controllers, excluding explicit helpers/VM launch."""
    def __init__(self, name=None, silent_breakaway=False):
        from ctypes import wintypes
        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        if name is None:
            self.handle = self.kernel.CreateJobObjectW(None, None)
        else:
            attributes, descriptor = _ipc_security(self.kernel)
            try:
                self.handle = self.kernel.CreateJobObjectW(ctypes.byref(attributes), name)
            finally:
                self.kernel.LocalFree(descriptor)
        limits = Extended()
        limits.basic.flags = 0x2000 | (0x1000 if silent_breakaway else 0x800)  # KILL_ON_CLOSE; separate instance lifetime
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def attach(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 76):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_empty(self):
        deadline = time.monotonic() + 5
        while True:
            accounting = ctypes.create_string_buffer(48)
            if not self.kernel.QueryInformationJobObject(self.handle, 1, accounting, 48, None):
                raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.c_uint32.from_buffer(accounting, 40).value == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("本轮控制进程尚未全部退出，拒绝交接")
            worker_sleep(0.01)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _forward_worker_output(stream):
    """Drain payload output even after the supervisor's output sink disappears."""
    import codecs
    import io

    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
    output = sys.stdout

    def forward(text):
        nonlocal output
        if output is not None and text:
            try:
                output.write(text)
                output.flush()
            except (OSError, ValueError, UnicodeError):
                # Do not flush a broken pipe again during interpreter shutdown:
                # that would replace the payload's exit status with Python's 120.
                if sys.stdout is output:
                    sys.stdout = None
                output = None

    try:
        while True:
            chunk = stream.read1(8192)
            if not chunk:
                break
            forward(decoder.decode(chunk))
        forward(decoder.decode(b"", final=True))
    except (OSError, ValueError):
        pass
    finally:
        stream.close()


def run_worker(scheduled: bool = False) -> int:
    """Own execution until the isolated payload and its control threads exit."""
    manual = _NamedMutex("manual") if not scheduled else None
    execution = _NamedMutex("execution")
    process = None
    token = secrets.token_hex(16)
    owned = False
    barrier = None
    job = None
    launch_job = None
    output_thread = None
    try:
        if manual is not None and not manual.acquire(0):
            append_log("[任务互斥] 已有手动任务／正在接管，不重复启动。")
            return 75
        if scheduled:
            if _manual_pending():
                append_log("[任务互斥] 计划任务跳过：手动任务执行中或正在接管。")
                return 0
            if not execution.acquire(0):
                append_log("[任务互斥] 计划任务跳过：已有任务执行中，不排队。")
                return 0
            if _manual_pending():
                append_log("[任务互斥] 计划任务跳过：手动请求优先。")
                return 0
        else:
            if not execution.acquire(0):
                append_log("[任务互斥] 正在停止计划任务，准备手动执行。")
                deadline = time.monotonic() + 35
                while not execution.acquire(0.1):
                    if time.monotonic() >= deadline:
                        append_log("[任务互斥] 无法安全交接：旧任务未在 35 秒内退出；本次手动任务未启动。")
                        return 75
        owned = True
        # The payload barrier also fences a child surviving a crashed supervisor.
        barrier = _NamedMutex("payload")
        if not barrier.acquire(5):
            append_log("[任务互斥] 旧执行器尚未退出，无法安全交接。")
            return 75
        previous = _owner_state()
        _drain_previous_jobs(previous.get("token", ""))
        handoff = bool(not scheduled and (previous.get("handoff_preserve") or (
            previous.get("mode") == "scheduled" and (
                previous.get("exit_code") == 124 or not previous.get("finished")))))
        state = {"token": token, "pid": os.getpid(), "payload_ready": False, "mode": "scheduled" if scheduled else "manual",
                 "cancel_requested": False, "handoff_preserve": False}
        _write_owner(state)
        # Only the execution owner may commit GUI-requested settings or cancel helpers.
        if not scheduled and os.environ.get("BACOFFEE_MANUAL_SETTINGS"):
            settings = json.loads(os.environ["BACOFFEE_MANUAL_SETTINGS"])
            if not isinstance(settings, dict):
                raise ValueError("手动任务设置不是对象")
            save_settings(settings)
        invalidate_hibernate_request("execution_owner_started")
        barrier.close()
        barrier = None
        environment = os.environ.copy()
        environment["BACOFFEE_WORKER_TOKEN"] = token
        environment["BACOFFEE_HANDOFF_PRESERVE"] = "1" if handoff else "0"
        environment.pop("BACOFFEE_MANUAL_SETTINGS", None)
        command = [sys.executable, "-X", "utf8", str(APP_DIR / "BACoffee.py"), "--worker-payload"]
        if scheduled:
            command.append("--scheduled")
        job = _OwnedJob(_task_job_name(token, "payload"))
        launch_job = _OwnedJob(_task_job_name(token, "launch"), silent_breakaway=True)
        process = subprocess.Popen(command, cwd=str(APP_DIR), env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output_thread = threading.Thread(
            target=_forward_worker_output, args=(process.stdout,),
            name="BACoffeePayloadOutput", daemon=True)
        output_thread.start()
        job.attach(process)
        state["payload_ready"] = True
        _write_owner(state)
        cancelled = False
        cancel_deadline = None
        while process.poll() is None:
            if scheduled and _manual_pending() and not cancelled:
                cancelled = True
                cancel_deadline = time.monotonic() + 8
                state.update(cancel_requested=True, handoff_preserve=True)
                _write_owner(state)
                append_log("[任务互斥] 手动请求已登记，正在取消计划任务并收尾。")
            if cancelled and time.monotonic() >= cancel_deadline:
                # Terminate only this job's payload/controllers; VM launch and fenced
                # delayed helpers explicitly break away. Never kill by process name.
                job.terminate()
                process.wait(timeout=5)
                job.wait_empty()
                launch_job.terminate()
                launch_job.wait_empty()
                append_log("[任务互斥] 计划执行器未及时响应，已停止本轮执行器，保留 MuMu。")
                break
            time.sleep(0.05)
        if scheduled and _manual_pending() and not cancelled:
            cancelled = True
            state.update(cancel_requested=True, handoff_preserve=True)
            _write_owner(state)
        if cancelled:
            launch_job.terminate()
            launch_job.wait_empty()
            result = {}
            try:
                result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if result.get("worker_token") != token:
                result = {"worker_token": token, "scheduled": True, "stages": {}}
            _stop_owned_ocr(result)
            invalidate_hibernate_request("manual_takeover")
            result.update(state="interrupted", success=False, message="被手动任务接管",
                          will_hibernate=False, finished=datetime.now().isoformat(timespec="seconds"))
            RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            append_log("[task] task finished; state=interrupted; reason=被手动任务接管；本轮收尾完成，释放执行权。")
            return 0
        return process.returncode
    except (OSError, TimeoutError) as exc:
        append_log(f"[任务互斥] 执行权或启动控制进程无法安全收束：{exc}；本次未放行并行执行。")
        return 75
    finally:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if job is not None:
                job.terminate()
                job.wait_empty()
                job.close()
            if launch_job is not None:
                launch_job.terminate()
                launch_job.wait_empty()
                launch_job.close()
            if owned:
                current = _owner_state()
                if current.get("token") == token:
                    current["finished"] = True
                    if process is not None:
                        current["exit_code"] = process.returncode
                    _write_owner(current)
            if barrier is not None:
                barrier.close()
            execution.close()
            if manual is not None:
                manual.close()
        finally:
            if output_thread is not None:
                output_thread.join(timeout=2)
            elif process is not None and process.stdout is not None:
                process.stdout.close()


def install_baas_operation_info_filter(logger_class) -> None:
    """Suppress successful action coordinates at BAAS's actual INFO source."""
    original = logger_class.info
    if getattr(original, "_bacoffee_operation_filter", False):
        return
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    point = rf"\(\s*{number}\s*,\s*{number}\s*\)"
    operation = re.compile(
        rf"(?:Click(?: \d+ times)? @ {point}|swipe from {point} --> {point})")

    def info(self, message):
        if isinstance(message, str) and operation.fullmatch(message):
            return None
        return original(self, message)

    info._bacoffee_operation_filter = True
    logger_class.info = info


def worker_payload(scheduled: bool) -> int:
    barrier = _NamedMutex("payload")
    try:
        if not barrier.acquire(5):
            return 75
        return _worker_payload_owned(scheduled)
    finally:
        barrier.close()


def _worker_payload_owned(scheduled: bool) -> int:
    global _WORKER_TOKEN
    _WORKER_TOKEN = os.environ.get("BACOFFEE_WORKER_TOKEN", "")
    state = _owner_state()
    ready_deadline = time.monotonic() + 5
    while state.get("token") == _WORKER_TOKEN and not state.get("payload_ready"):
        if time.monotonic() >= ready_deadline:
            return 75
        worker_sleep(0.01)
        state = _owner_state()
    if not _WORKER_TOKEN or state.get("token") != _WORKER_TOKEN or state.get("finished"):
        append_log("[任务互斥] 拒绝无执行权的任务执行器。")
        return 75
    # If the supervisor crashed, do not continue with an unowned device session.
    supervisor = ctypes.WinDLL("kernel32", use_last_error=True)
    supervisor.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    supervisor.OpenProcess.restype = ctypes.c_void_p
    parent_handle = supervisor.OpenProcess(0x00100000, False, int(state["pid"]))
    if not parent_handle:
        return 75
    supervisor.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    supervisor.CloseHandle.argtypes = [ctypes.c_void_p]
    def monitor():
        while not _WORKER_MONITOR_STOP.wait(0.1):
            if supervisor.WaitForSingleObject(parent_handle, 0) == 0:
                # Exit all payload control threads if the execution owner disappeared.
                os._exit(75)
            current = _owner_state()
            if current.get("token") != _WORKER_TOKEN or current.get("cancel_requested"):
                _WORKER_CANCELLED.set()
                if _WORKER_BAAS is not None:
                    _WORKER_BAAS.signal_stop()
    watcher = threading.Thread(target=monitor, name="BACoffeeHandoff", daemon=True)
    watcher.start()
    try:
        return _run_worker_owned(scheduled)
    except WorkerCancelled:
        append_log("[任务互斥] 计划任务已停止自动动作，等待交接收尾。")
        return 0
    finally:
        _WORKER_MONITOR_STOP.set()
        watcher.join(timeout=1)
        supervisor.CloseHandle(parent_handle)


def _helper_current() -> bool:
    """Called inside the helper execution fence, including between cleanup steps."""
    token = os.environ.get("BACOFFEE_WORKER_TOKEN", "")
    state = _owner_state()
    if token and (state.get("token") != token or state.get("cancel_requested")):
        return False
    if _manual_pending():
        if token and state.get("token") == token:
            state["handoff_preserve"] = True
            _write_owner(state)
        return False
    return True


def _helper_wait(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _helper_current():
            return False
        worker_sleep(min(0.1, max(0, deadline - time.monotonic())))
    return _helper_current()


class _HelperAction:
    """Fence delayed cleanup/sleep against the execution owner and manual intent."""
    def __init__(self):
        self.token = os.environ.get("BACOFFEE_WORKER_TOKEN", "")
        self.mutex = None
        self.allowed = False

    def __enter__(self):
        self.mutex = _NamedMutex("execution")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            state = _owner_state()
            if (self.token and (state.get("token") != self.token or state.get("cancel_requested"))) or _manual_pending():
                return False
            if self.mutex.acquire(0.1):
                state = _owner_state()
                self.allowed = not _manual_pending() and (not self.token or (
                    state.get("token") == self.token and not state.get("cancel_requested")))
                return self.allowed
        return False

    def __exit__(self, *_):
        if self.mutex is not None:
            self.mutex.close()


def find_mumu_manager(settings: dict | None = None) -> Path | None:
    """Resolve MuMuManager from user settings, common installs, or registry."""
    settings = settings or load_settings()
    candidates = []
    configured_text = str(settings.get("mumu_manager_path", "")).strip().strip('"')
    if configured_text:
        candidates.append(Path(configured_text))
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(variable, "").strip()
        if not root:
            continue
        base = Path(root)
        candidates.extend((
            base / "Netease" / "MuMu" / "nx_main" / "MuMuManager.exe",
            base / "Netease" / "GameViewer" / "nx_main" / "MuMuManager.exe",
        ))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if os.name == "nt":
        try:
            import winreg
            uninstall_keys = (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MuMuPlayer",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MuMuPlayer-12.0",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MuMuPlayerGlobal",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MuMuPlayerGlobal-12.0",
            )
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for uninstall_key in uninstall_keys:
                    try:
                        with winreg.OpenKey(hive, uninstall_key) as key:
                            uninstall = str(winreg.QueryValueEx(key, "UninstallString")[0]).strip('"')
                        candidate = Path(uninstall).parent / "nx_main" / "MuMuManager.exe"
                        if candidate.is_file():
                            return candidate
                    except OSError:
                        continue
        except ImportError:
            pass
    return None


def resolve_mumu_manager_in_directory(directory: Path | str) -> Path | None:
    """Resolve MuMuManager.exe from a selected MuMu installation folder only."""
    root = Path(directory).expanduser()
    if not root.is_dir():
        return None
    candidates = []
    if root.name.lower() != "nx_main":
        candidates.append(root / "nx_main" / "MuMuManager.exe")
    candidates.append(root / "MuMuManager.exe")
    for candidate in candidates:
        if candidate.is_file() and candidate.name.lower() == "mumumanager.exe":
            return candidate.resolve()
    return None


MUMU_INFO_QUERY_TIMEOUT_SECONDS = 3.0
MUMU_ADB_QUERY_TIMEOUT_SECONDS = 5.0
MUMU_LAUNCH_COMMAND_TIMEOUT_SECONDS = 30.0


def _signed_subprocess_returncode(returncode):
    """Convert Windows' unsigned representation of a negative exit code."""
    if returncode is None:
        return None
    value = int(returncode)
    return value - 2 ** 32 if value >= 2 ** 31 else value


def _diagnostic_text(value, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text[-limit:]


def _wait_process_exit(process, timeout: float) -> bool:
    if process is None:
        return True
    try:
        process.wait(timeout=max(0.0, float(timeout)))
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None


def _close_process_pipes(process) -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(process, stream_name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _run_taskkill_for_process_tree(pid: int) -> dict:
    """Run targeted taskkill with bounded wait and no output pipes."""
    result = {
        "attempted": True,
        "exit_code": None,
        "timed_out": False,
        "exception": None,
    }
    killer = None
    try:
        killer = subprocess.Popen(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not _wait_process_exit(killer, 3):
            result["timed_out"] = True
            try:
                killer.kill()
            except OSError:
                pass
            _wait_process_exit(killer, 1)
        result["exit_code"] = _signed_subprocess_returncode(killer.poll())
    except Exception as exc:
        result["exception"] = repr(exc)
        if killer is not None:
            try:
                killer.kill()
            except OSError:
                pass
            _wait_process_exit(killer, 1)
    return result


def _terminate_mumu_process_tree(process) -> dict:
    """Terminate only this manager process tree and always return in bounded time."""
    cleanup = {
        "attempted": False,
        "succeeded": False,
        "taskkill_exit_code": None,
        "taskkill_timed_out": False,
        "fallback_kill_attempted": False,
        "process_exited": process is None or process.poll() is not None,
        "exception": None,
    }
    if process is None or process.poll() is not None:
        cleanup["succeeded"] = cleanup["process_exited"]
        return cleanup
    cleanup["attempted"] = True
    if os.name == "nt":
        taskkill = _run_taskkill_for_process_tree(process.pid)
        cleanup["taskkill_exit_code"] = taskkill.get("exit_code")
        cleanup["taskkill_timed_out"] = bool(taskkill.get("timed_out"))
        cleanup["exception"] = taskkill.get("exception")
        cleanup["process_exited"] = _wait_process_exit(process, 0.5)
    if not cleanup["process_exited"]:
        cleanup["fallback_kill_attempted"] = True
        try:
            process.kill()
        except OSError as exc:
            cleanup["exception"] = cleanup["exception"] or repr(exc)
        cleanup["process_exited"] = _wait_process_exit(process, 1)
    cleanup["succeeded"] = bool(cleanup["process_exited"])
    return cleanup


def _read_capture_file(handle, limit: int = 8192) -> bytes:
    try:
        handle.flush()
        handle.seek(0)
        return handle.read(limit)
    except (OSError, ValueError):
        return b""


def _task_job_name(token: str, kind: str) -> str:
    scope = os.path.normcase(os.path.abspath(str(DATA_DIR)))
    identity = hashlib.sha256((scope + ":" + str(token)).encode("utf-8")).hexdigest()
    return f"Local\\BACoffee.{kind}.{identity}"


def _drain_previous_jobs(token: str) -> None:
    if not token:
        return
    # Drain the payload first: it must no longer be able to create a launch.
    # Reopening the named jobs also covers an abruptly exited supervisor.
    for kind in ("payload", "launch"):
        job = _OwnedJob(_task_job_name(token, kind), silent_breakaway=(kind == "launch"))
        try:
            job.terminate()
            job.wait_empty()
        finally:
            job.close()


class _LaunchManagerProcess:
    """Create the manager atomically in its task's separately supervised job.

    Child instance processes are not in this job. There is no untracked interval
    between starting the manager and assigning its process handle to the job.
    """
    def __init__(self, command, stdout, stderr, flags):
        from ctypes import wintypes
        import msvcrt
        class Startup(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
                        ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
                        *[(n, wintypes.DWORD) for n in ("x", "y", "cx", "cy", "cols", "rows", "fill", "flags")],
                        ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
                        ("reserved_bytes", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
                        ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]
        class StartupEx(ctypes.Structure):
            _fields_ = [("startup", Startup), ("attributes", ctypes.c_void_p)]
        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                        ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]
        self.args = command
        self.returncode = None
        self.handle = None
        self.job = _OwnedJob(_task_job_name(_WORKER_TOKEN, "launch"), silent_breakaway=True)
        self.kernel = self.job.kernel
        self.kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        self.kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        self.kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        self.kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(StartupEx), ctypes.POINTER(ProcessInfo)]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        attributes_ready = False
        null_input = None
        inherited = []
        try:
            size = ctypes.c_size_t()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            storage = ctypes.create_string_buffer(size.value)
            if not self.kernel.InitializeProcThreadAttributeList(storage, 2, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            attributes_ready = True
            null_input = open(os.devnull, "rb")
            handles = [msvcrt.get_osfhandle(f.fileno()) for f in (null_input, stdout, stderr)]
            for handle in handles:
                inherited.append((handle, os.get_handle_inheritable(handle)))
                os.set_handle_inheritable(handle, True)
            handle_list = (wintypes.HANDLE * 3)(*handles)
            jobs = (wintypes.HANDLE * 1)(self.job.handle)
            for key, value in ((0x00020002, handle_list), (0x0002000D, jobs)):
                if not self.kernel.UpdateProcThreadAttribute(storage, 0, key, value, ctypes.sizeof(value), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup = StartupEx()
            startup.startup.cb = ctypes.sizeof(StartupEx)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = ctypes.cast(storage, ctypes.c_void_p)
            info = ProcessInfo()
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            creation = flags | 0x00080000 | 0x01000000  # EXTENDED_STARTUPINFO_PRESENT | BREAKAWAY
            if not self.kernel.CreateProcessW(command[0], command_line, None, None, True,
                                               creation, None, None, ctypes.byref(startup), ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, self.pid = info.process, info.pid
            self.kernel.CloseHandle(info.thread)
        except BaseException:
            self.close()
            raise
        finally:
            for handle, previous in inherited:
                os.set_handle_inheritable(handle, previous)
            if null_input is not None:
                null_input.close()
            if attributes_ready:
                self.kernel.DeleteProcThreadAttributeList(storage)

    def poll(self):
        if self.returncode is None and self.handle and self.kernel.WaitForSingleObject(self.handle, 0) == 0:
            code = ctypes.c_uint32()
            if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        delay = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = self.kernel.WaitForSingleObject(self.handle, delay)
        if result == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def kill(self):
        # Manager identity is the original process handle, never a name/PID scan.
        if self.poll() is None and not self.kernel.TerminateProcess(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if self.poll() is None:
                self.kill()
                self.wait(3)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.job is not None:
            self.job.close()
            self.job = None


def _run_mumu_manager_command(manager: Path, arguments: list[str], timeout: float) -> dict:
    """Run one MuMu command with bounded process and pipe handling."""
    sanitize_external_runtime()
    command = [str(manager), *[str(argument) for argument in arguments]]
    started = time.perf_counter()
    process = None
    stdout_file = None
    stderr_file = None
    capture_paths = []
    stdout = b""
    stderr = b""
    timed_out = False
    exception = None
    capture_cleanup_exception = None
    cleanup = {
        "attempted": False,
        "succeeded": None,
        "taskkill_exit_code": None,
        "taskkill_timed_out": False,
        "fallback_kill_attempted": False,
        "process_exited": None,
        "exception": None,
    }
    try:
        flags = 0
        if os.name == "nt":
            flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        stdout_fd, stdout_path = tempfile.mkstemp(prefix="bacoffee-mumu-stdout-", suffix=".tmp")
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="bacoffee-mumu-stderr-", suffix=".tmp")
        capture_paths.extend((stdout_path, stderr_path))
        stdout_file = os.fdopen(stdout_fd, "w+b")
        stderr_file = os.fdopen(stderr_fd, "w+b")
        tracked_launch = bool(_WORKER_TOKEN and "launch" in arguments and arguments[0] == "control")
        if tracked_launch:
            worker_checkpoint()
            process = _LaunchManagerProcess(command, stdout_file, stderr_file, flags)
        else:
            process = subprocess.Popen(
                command, stdout=stdout_file, stderr=stderr_file, creationflags=flags)

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if isinstance(process, _LaunchManagerProcess):
                process.kill()
                process.wait(3)
                cleanup.update(attempted=True, succeeded=True, process_exited=True)
            else:
                cleanup = _terminate_mumu_process_tree(process)
    except Exception as exc:
        exception = repr(exc)
        if process is not None:
            if isinstance(process, _LaunchManagerProcess):
                process.kill()
                process.wait(3)
            else:
                _terminate_mumu_process_tree(process)
    stdout = _read_capture_file(stdout_file)
    stderr = _read_capture_file(stderr_file)
    for handle in (stdout_file, stderr_file):
        if handle is None:
            continue
        try:
            handle.close()
        except OSError:
            pass
    capture_cleanup_deadline = time.monotonic() + 1.5
    for capture_path in capture_paths:
        path_cleanup_exception = None
        while True:
            try:
                os.unlink(capture_path)
                break
            except OSError as exc:
                path_cleanup_exception = repr(exc)
                remaining = capture_cleanup_deadline - time.monotonic()
                if remaining <= 0:
                    break
                worker_sleep(min(0.05, remaining))
        if path_cleanup_exception and Path(capture_path).exists():
            capture_cleanup_exception = capture_cleanup_exception or path_cleanup_exception
    if isinstance(process, _LaunchManagerProcess):
        process.close()
    cleanup_succeeded = cleanup.get("succeeded")
    if capture_cleanup_exception:
        cleanup_succeeded = False
    return {
        "command": command,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "exit_code": _signed_subprocess_returncode(process.returncode if process else None),
        "timed_out": timed_out,
        "stdout": _diagnostic_text(stdout),
        "stderr": _diagnostic_text(stderr),
        "exception": exception,
        "cleanup_attempted": bool(cleanup.get("attempted")),
        "cleanup_succeeded": cleanup_succeeded,
        "cleanup_taskkill_exit_code": cleanup.get("taskkill_exit_code"),
        "cleanup_taskkill_timed_out": bool(cleanup.get("taskkill_timed_out")),
        "cleanup_fallback_kill_attempted": bool(cleanup.get("fallback_kill_attempted")),
        "cleanup_process_exited": cleanup.get("process_exited"),
        "cleanup_exception": cleanup.get("exception"),
        "capture_cleanup_exception": capture_cleanup_exception,
    }


def _decode_mumu_json(output: str) -> dict | None:
    text = (output or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value if isinstance(value, dict) else None
            except ValueError:
                continue
    return None


def _mumu_payload_error_code(payload: dict | None):
    if not payload:
        return 0
    value = payload.get("errcode", payload.get("error_code", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mumu_payload_adb_port(payload: dict | None) -> tuple[str, int] | None:
    if not payload or _mumu_payload_error_code(payload) != 0:
        return None
    raw_port = payload.get("adb_port", payload.get("adbPort"))
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    host = str(payload.get("adb_host", payload.get("adbHost", "127.0.0.1"))).strip()
    return host or "127.0.0.1", port


def _mumu_formula_port(vm_index: int) -> int:
    return 16384 + max(0, int(vm_index)) * 32


def _mumu_port_result(
    port: int | None,
    source: str,
    verified: bool,
    diagnostic: dict,
    host: str = "127.0.0.1",
) -> dict:
    return {
        "port": int(port) if port is not None else None,
        "host": host or "127.0.0.1",
        "source": source,
        "verified": bool(verified),
        "diagnostic": diagnostic,
    }


def _mumu_port_result_for_settings(settings: dict) -> dict:
    vm_index = max(0, int(settings.get("mumu_vm_index", 1)))
    manager = find_mumu_manager(settings)
    if manager is None:
        result = _mumu_port_result(
            _mumu_formula_port(vm_index),
            "formula",
            False,
            {"reason": "未找到 MuMuManager.exe，候选端口尚未验证"},
        )
        append_log(
            f"[MuMu] 未找到管理器，保留候选 ADB 端口 {result['port']}；"
            "该端口必须在启动后通过 ADB 验证。")
        return result
    return query_mumu_adb_port(manager, vm_index)


def query_mumu_adb_port(manager: Path, vm_index: int) -> dict:
    """Query MuMu safely and return a labelled, not-implicitly-trusted port result."""
    index = max(0, int(vm_index))
    formula = _mumu_formula_port(index)
    manager_path = str(Path(manager).resolve())
    info = _run_mumu_manager_command(
        manager, ["info", "--vmindex", str(index)], MUMU_INFO_QUERY_TIMEOUT_SECONDS)
    info_payload = _decode_mumu_json(info.get("stdout", ""))
    diagnostic = {
        "manager": manager_path,
        "vm_index": index,
        "info": info,
    }
    info_error = _mumu_payload_error_code(info_payload)
    if not info_payload or info.get("timed_out") or info.get("exception") or info.get("exit_code") != 0 or info_error != 0:
        reason = (
            info_payload.get("errmsg") if info_payload else None
        ) or info.get("exception") or info.get("stderr") or "无法确认实例是否存在"
        diagnostic["reason"] = str(reason)
        append_log(f"[MuMu] 查询实例 {index} 状态失败：{reason}；不生成可交给 BAAS 的端口。")
        return _mumu_port_result(None, "manager_info", False, diagnostic)

    adb = _run_mumu_manager_command(
        manager, ["adb", "--vmindex", str(index)], MUMU_ADB_QUERY_TIMEOUT_SECONDS)
    adb_payload = _decode_mumu_json(adb.get("stdout", ""))
    diagnostic["adb"] = adb
    adb_port = _mumu_payload_adb_port(adb_payload)
    if adb_port is not None and not adb.get("timed_out") and not adb.get("exception") and adb.get("exit_code") == 0:
        host, port = adb_port
        diagnostic["reason"] = "管理器返回了 ADB 地址；仍需真实 ADB 连通性验证"
        append_log(
            f"[MuMu] 实例 {index} 的管理器 ADB 查询完成：{host}:{port}；"
            f"耗时 {adb['elapsed_seconds']:.3f} 秒。")
        return _mumu_port_result(port, "manager_adb", False, diagnostic, host)

    reason = (
        adb_payload.get("errmsg") if adb_payload else None
    ) or adb.get("exception") or adb.get("stderr") or "管理器未返回有效 adb_port"
    diagnostic["reason"] = str(reason)
    diagnostic["formula_candidate"] = formula
    append_log(
        f"[MuMu] 实例 {index} 的 ADB 查询未返回有效端口：{reason}；"
        f"暂保留候选端口 {formula}，来源=formula，启动后必须验证。")
    return _mumu_port_result(formula, "formula", False, diagnostic)


def mumu_port_is_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except OSError:
        return False


def baas_adb_path(settings: dict) -> Path:
    return PORTABLE_SITE_PACKAGES / "adbutils" / "binaries" / "adb.exe"


def mumu_adb_is_ready(settings: dict, port: int) -> tuple[bool, str]:
    """Require a connected, booted device and a valid screenshot—not merely an open TCP port."""
    adb = baas_adb_path(settings)
    if not adb.is_file():
        return False, f"ADB 不存在：{adb}"
    serial = f"127.0.0.1:{int(port)}"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        connected = subprocess.run(
            [str(adb), "connect", serial], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=8, creationflags=flags)
        state = subprocess.run(
            [str(adb), "-s", serial, "get-state"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=8, creationflags=flags)
        if state.returncode != 0 or state.stdout.strip() != "device":
            detail = (state.stderr or state.stdout or connected.stderr or connected.stdout).strip()
            if "offline" in detail.lower():
                subprocess.run(
                    [str(adb), "disconnect", serial], capture_output=True, timeout=8,
                    creationflags=flags)
            return False, detail or "ADB 状态不是 device"
        boot = subprocess.run(
            [str(adb), "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=flags)
        if boot.stdout.strip() != "1":
            return False, "Android 尚未完成启动"
        shot = subprocess.run(
            [str(adb), "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=12, creationflags=flags)
        if shot.returncode != 0 or not shot.stdout.startswith(b"\x89PNG") or len(shot.stdout) < 10000:
            return False, "ADB 已连接，但模拟器画面尚不可读取"
        return True, "device/boot/screenshot 均正常"
    except Exception as exc:
        return False, str(exc)


def _selected_mumu_state(manager: Path, vm_index: int) -> dict:
    info = _run_mumu_manager_command(manager, ["info", "--vmindex", str(vm_index)], MUMU_INFO_QUERY_TIMEOUT_SECONDS)
    payload = _decode_mumu_json(info.get("stdout", ""))
    if (info.get("timed_out") or info.get("exception") or info.get("exit_code") != 0
            or not isinstance(payload, dict) or not payload):
        raise RuntimeError(f"无法确认实例 {vm_index} 状态：{info}")
    error = payload.get("error_code", payload.get("errcode", 0))
    if str(error) != "0" or ("index" in payload and str(payload["index"]) != str(vm_index)):
        raise RuntimeError(f"实例 {vm_index} 状态查询错误：{payload}")
    process_started = payload.get("is_process_started")
    android_started = payload.get("is_android_started")
    if type(process_started) is not bool or type(android_started) is not bool:
        raise RuntimeError(f"实例 {vm_index} 状态字段无效：{payload}")
    if not process_started and android_started:
        raise RuntimeError(f"实例 {vm_index} 启动状态矛盾：{payload}")
    return {"running": process_started, "android_started": android_started, "info": info}


def launch_selected_mumu(settings: dict, timeout: int = 180, port_result: dict | None = None, query_callback=None) -> bool:
    """Discover an endpoint only after the selected instance is started."""
    manager = find_mumu_manager(settings)
    if manager is None:
        raise FileNotFoundError("找不到 MuMuManager.exe，请在设置中选择模拟器位置。")
    vm_index = max(0, int(settings.get("mumu_vm_index", 1)))
    port_result = port_result if port_result is not None else {}
    worker_checkpoint()
    state = _selected_mumu_state(manager, vm_index)
    if not state["running"]:
        append_log(f"[MuMu] 实例 {vm_index} 未启动，正在启动。")
        command = ["control", "--vmindex", str(vm_index)]
        version = str(settings.get("mumu_android_version", "")).strip()
        if version in {"12", "15"}:
            command += ["--version", version]
        command.append("launch")
        launched = _run_mumu_manager_command(manager, command, MUMU_LAUNCH_COMMAND_TIMEOUT_SECONDS)
        payload = _decode_mumu_json(launched.get("stdout", ""))
        if (launched.get("timed_out") or launched.get("exception") or launched.get("exit_code") != 0
                or (payload and _mumu_payload_error_code(payload) not in (0,))):
            raise RuntimeError(f"启动 MuMu 实例 {vm_index} 失败：{launched}")
        wait_seconds = max(0, int(settings.get("mumu_start_wait_seconds", DEFAULT_SETTINGS["mumu_start_wait_seconds"])))
        append_log(f"[MuMu] 实例 {vm_index} 已发送启动命令，等待 {wait_seconds} 秒后查询真实地址。")
        worker_sleep(wait_seconds)
    else:
        append_log(f"[MuMu] 实例 {vm_index} 已运行，直接查询真实地址。")
    deadline = time.monotonic() + timeout
    ready_count = 0
    last_detail = "尚未查询"
    last_log_detail = None
    next_log = 0.0
    next_query = 0.0
    current = {}
    while time.monotonic() < deadline:
        worker_checkpoint()
        if not current or time.monotonic() >= next_query:
            query = lambda: query_mumu_adb_port(manager, vm_index)
            refreshed = query_callback(query) if query_callback is not None else query()
            next_query = time.monotonic() + 5
            if not refreshed.get("port"):
                raise RuntimeError(f"无法查询实例 {vm_index} 地址：{refreshed.get('diagnostic')}")
            # Preserve a previously returned manager address over a later formula.
            if not current or refreshed.get("source") == "manager_adb" or current.get("source") == "formula":
                if current.get("port") != refreshed.get("port"):
                    ready_count = 0
                current = refreshed
        ready, detail = mumu_adb_is_ready(settings, int(current["port"]))
        worker_checkpoint()
        last_detail = detail
        ready_count = ready_count + 1 if ready else 0
        if ready_count >= 2:
            current["verified"] = True
            current["discovery_source"] = current["source"]
            current.setdefault("diagnostic", {})["verification"] = detail
            port_result.clear()
            port_result.update(current)
            append_log(f"[MuMu] 实例 {vm_index} 已就绪：{current['host']}:{current['port']}；来源={current['source']}；ADB／启动／截图通过。")
            return True
        if detail != last_log_detail or time.monotonic() >= next_log:
            append_log(f"[MuMu] 等待实例 {vm_index} 就绪：{detail}")
            last_log_detail = detail
            next_log = time.monotonic() + 15
        worker_sleep(2)
    append_log(f"[MuMu] 实例 {vm_index} 就绪超时：{last_detail}")
    return False


def selected_student_files(settings: dict, cafe_no: int) -> list[str]:
    key = f"bash_students_cafe{cafe_no}"
    catalog = load_student_catalog()
    selected = []
    seen = set()
    for name in settings.get(key, []):
        entry = resolve_student_avatar(name, catalog)
        if entry is None or entry["name"] in seen:
            continue
        selected.append(entry["name"])
        seen.add(entry["name"])
    return selected


def build_invitation_student_snapshot(settings: dict) -> dict[int, tuple[str, ...]]:
    """Freeze the GUI's ordered BASH filenames for one worker run."""
    snapshot = {}
    for cafe_no in (1, 2):
        snapshot[cafe_no] = tuple(selected_student_files(settings, cafe_no))
    return snapshot


def _read_cv_image(path: Path):
    cv2 = importlib.import_module("cv2")
    np = importlib.import_module("numpy")
    raw = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)


def _bash_scaled_point(frame, x: int, y: int) -> tuple[int, int]:
    height, width = frame.shape[:2]
    return int(x * width / 1920), int(y * height / 1080)


def _bash_tap(self, frame, x: int, y: int) -> None:
    px, py = _bash_scaled_point(frame, x, y)
    self.click(px, py)


def _bash_logical_point(frame, x: int, y: int) -> tuple[int, int]:
    """Convert screenshot pixels to the BAAS 1280x720 logical coordinate space."""
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return 0, 0
    return int(round(x * 1280 / width)), int(round(y * 720 / height))


def _invitation_screen_contract(self) -> dict:
    """Record the actual screenshot/control contract used by the current thread."""
    frame = getattr(self, "latest_img_array", None)
    if frame is None:
        self.update_screenshot_array()
        frame = self.latest_img_array
    height, width = frame.shape[:2]
    screenshot = getattr(self, "screenshot", None)
    control = getattr(self, "control", None)
    screenshot_backend = getattr(screenshot, "method", None)
    control_backend = getattr(control, "method", None)
    ratio = getattr(self, "ratio", None)
    contract = {
        "screenshot_size": [int(width), int(height)],
        "ratio": float(ratio) if ratio is not None else None,
        "screenshot_backend": screenshot_backend,
        "control_backend": control_backend,
        "logical_size": [1280, 720],
    }
    if getattr(self, "bacoffee_last_screen_contract", contract) != contract:
        append_log(f"[邀请契约] 画面契约变化：{contract}")
    self.bacoffee_last_screen_contract = contract
    return contract


def _detect_known_baas_screen(self, candidates: tuple[str, ...]) -> str:
    """Best-effort screen evidence after a BAAS state transition."""
    try:
        from core import image as baas_image
        self.update_screenshot_array()
        for name in candidates:
            if baas_image.compare_image(self, name):
                return name
    except Exception as exc:
        append_log(f"[邀请状态] 页面确认失败：{exc}")
    return "unknown"


def _bash_template_match(image, template, template_scale: float = 1.0, strict: bool = False) -> tuple[float, tuple[int, int], tuple[int, int]]:
    """Match a BASH PNG, preserving its alpha mask when present."""
    cv2 = importlib.import_module("cv2")
    if image is None or template is None or image.size == 0 or template.size == 0:
        if strict:
            raise ValueError("模板匹配输入不可用")
        return 0.0, (0, 0), (0, 0)
    image_rgb = image[:, :, :3] if image.ndim == 3 and image.shape[2] == 4 else image
    template_rgb = template[:, :, :3] if template.ndim == 3 and template.shape[2] == 4 else template
    if abs(template_scale - 1.0) > 0.01:
        size = (max(1, int(template.shape[1] * template_scale)),
                max(1, int(template.shape[0] * template_scale)))
        template = cv2.resize(template, size, interpolation=cv2.INTER_LINEAR)
        template_rgb = template[:, :, :3] if template.ndim == 3 and template.shape[2] == 4 else template
    if template_rgb.shape[0] > image_rgb.shape[0] or template_rgb.shape[1] > image_rgb.shape[1]:
        factor = min(image_rgb.shape[0] / template_rgb.shape[0], image_rgb.shape[1] / template_rgb.shape[1]) * 0.9
        if factor < 0.3:
            if strict:
                raise ValueError("模板与画面比例不可用")
            return 0.0, (0, 0), (0, 0)
        size = (max(1, int(template_rgb.shape[1] * factor)), max(1, int(template_rgb.shape[0] * factor)))
        template_rgb = cv2.resize(template_rgb, size, interpolation=cv2.INTER_LINEAR)
        template = cv2.resize(template, size, interpolation=cv2.INTER_LINEAR)
    try:
        if template.ndim == 3 and template.shape[2] == 4:
            mask = template[:, :, 3]
            result = cv2.matchTemplate(image_rgb, template_rgb, cv2.TM_CCOEFF_NORMED, mask=mask)
        else:
            result = cv2.matchTemplate(image_rgb, template_rgb, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, location = cv2.minMaxLoc(result)
        return float(maximum), location, (template_rgb.shape[1], template_rgb.shape[0])
    except Exception:
        if strict:
            raise
        return 0.0, (0, 0), (0, 0)


def _bash_invitation_ticket_available(self) -> tuple[bool, object]:
    """Perform BASH's invitation-ticket status check and open the list when available."""
    self.update_screenshot_array()
    frame = self.latest_img_array
    recover = _read_cv_image(UI_ASSET_DIR / "recover-ui.png")
    template_scale = frame.shape[1] / 1920.0
    recover_x1, recover_y1 = _bash_scaled_point(frame, 1750, 0)
    recover_x2, recover_y2 = _bash_scaled_point(frame, 1920, 120)
    recover_roi = frame[recover_y1:recover_y2, recover_x1:recover_x2]
    score, location, matched_size = _bash_template_match(recover_roi, recover, template_scale)
    if score >= 0.7:
        self.click(recover_x1 + location[0] + matched_size[0] // 2,
                   recover_y1 + location[1] + matched_size[1] // 2)
        worker_sleep(1)
        self.update_screenshot_array()
        frame = self.latest_img_array
        self.logger.info("BASH restored hidden cafe UI before invitation.")

    height, width = frame.shape[:2]
    x1, y1 = _bash_scaled_point(frame, 1240, 900)
    x2, y2 = _bash_scaled_point(frame, 1420, 1040)
    roi = frame[y1:y2, x1:x2]
    cooldown = _read_cv_image(UI_ASSET_DIR / "no_invitation_ticket.png")
    available = _read_cv_image(UI_ASSET_DIR / "invitation_ticket.png")
    cooldown_score, _, _ = _bash_template_match(roi, cooldown, template_scale)
    available_score, _, _ = _bash_template_match(roi, available, template_scale)
    self.logger.info(
        f"BASH invitation ticket: available={available_score:.3f}, cooldown={cooldown_score:.3f}")
    if cooldown_score > 0.6:
        self.logger.info("Invitation ticket is on cooldown; skip invitation.")
        return False, frame
    if available_score < 0.6:
        self.logger.warning("BASH could not determine invitation-ticket state; skip safely.")
        return False, frame
    _bash_tap(self, frame, 1334, 975)
    worker_sleep(5)
    return True, frame


def _bash_invitation_ticket_state(self) -> dict:
    """Read the invitation-ticket state without clicking or restoring UI."""
    self.update_screenshot_array()
    frame = getattr(self, "latest_img_array", None)
    result = {
        "state": "unknown",
        "available_score": 0.0,
        "cooldown_score": 0.0,
        "frame": frame,
    }
    if frame is None or getattr(frame, "size", 0) == 0:
        append_log("[邀请券预判] available_score=0.000; cooldown_score=0.000; state=unknown; reason=no_frame")
        return result

    height, width = frame.shape[:2]
    template_scale = width / 1920.0
    x1, y1 = _bash_scaled_point(frame, 1240, 900)
    x2, y2 = _bash_scaled_point(frame, 1420, 1040)
    roi = frame[y1:y2, x1:x2]
    cooldown = _read_cv_image(UI_ASSET_DIR / "no_invitation_ticket.png")
    available = _read_cv_image(UI_ASSET_DIR / "invitation_ticket.png")
    cooldown_score, _, _ = _bash_template_match(roi, cooldown, template_scale)
    available_score, _, _ = _bash_template_match(roi, available, template_scale)
    result["available_score"] = round(float(available_score), 4)
    result["cooldown_score"] = round(float(cooldown_score), 4)
    if cooldown_score > 0.6:
        result["state"] = "cooldown"
    elif available_score >= 0.6:
        result["state"] = "available"
    if result["state"] == "unknown":
        append_log(f"[邀请券预判] 无法可靠识别：available={available_score:.3f}；cooldown={cooldown_score:.3f}；frame={width}x{height}")
    return result


def _bash_restore_hidden_cafe_ui(self) -> bool:
    """Restore only the hidden cafe chrome when the explicit BASH template matches."""
    self.update_screenshot_array()
    frame = self.latest_img_array
    recover = _read_cv_image(UI_ASSET_DIR / "recover-ui.png")
    if frame is None or recover is None:
        return False
    height, width = frame.shape[:2]
    template_scale = width / 1920.0
    recover_x1, recover_y1 = _bash_scaled_point(frame, 1750, 0)
    recover_x2, recover_y2 = _bash_scaled_point(frame, 1920, 120)
    recover_roi = frame[recover_y1:recover_y2, recover_x1:recover_x2]
    score, location, matched_size = _bash_template_match(
        recover_roi, recover, template_scale)
    if score < 0.7:
        return False
    center_x = recover_x1 + location[0] + matched_size[0] // 2
    center_y = recover_y1 + location[1] + matched_size[1] // 2
    logical_x, logical_y = _bash_logical_point(frame, center_x, center_y)
    append_log(
        f"[邀请入口] hidden cafe UI matched; pixel=({center_x},{center_y}); "
        f"logical=({logical_x},{logical_y})")
    self.click(logical_x, logical_y, wait_over=True)
    worker_sleep(1)
    self.update_screenshot_array()
    return True


def _detect_bash_avatar_matches(
    image_array,
    templates: dict[str, object],
    return_diagnostics: bool = False,
    allow_exchange_student: bool = False,
):
    """Detect BASH avatars with stable crops and alpha-aware template scores."""
    cv2 = importlib.import_module("cv2")
    np = importlib.import_module("numpy")
    frame = image_array
    height, width = frame.shape[:2]
    diagnostics = {
        "frame_size": [int(width), int(height)],
        "scale": round(width / 1920.0, 4),
        "circles": [],
        "candidate_evidence": [],
        "borderline_candidates": [],
    }
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scale = frame.shape[1] / 1920.0
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(1, int(80 * scale)),
        param1=100, param2=16, minRadius=max(10, int(40 * scale)), maxRadius=max(11, int(50 * scale)),
    )
    if circles is None:
        diagnostics["reason"] = "no_circles"
        return ([], diagnostics) if return_diagnostics else []

    # The released portraits are 90-92 px source assets. At 1280x720 the
    # expected in-game portrait is therefore about 61 px, independent of the
    # small radius jitter returned by HoughCircles.
    expected_side = max(40, int(round(92 * scale)))
    stable_variants = (
        (0, 0, 0.94),
        (0, 0, 1.00),
        (0, 0, 1.06),
        (-1, 0, 1.00),
        (1, 0, 1.00),
    )
    prepared_templates = {}
    for name, template in templates.items():
        rgb = template[:, :, :3] if template.ndim == 3 and template.shape[2] >= 3 else template
        alpha = template[:, :, 3] if template.ndim == 3 and template.shape[2] == 4 else None
        original_rgb = cv2.resize(rgb, (96, 96))
        rgb = cv2.resize(rgb, (96, 96), interpolation=cv2.INTER_AREA)
        if alpha is None:
            alpha_mask = np.ones((96, 96), dtype=np.uint8) * 255
        else:
            alpha_mask = cv2.resize(alpha, (96, 96), interpolation=cv2.INTER_LINEAR)
        yy, xx = np.ogrid[:96, :96]
        circular_mask = ((xx - 47.5) ** 2 + (yy - 47.5) ** 2 <= 47.0 ** 2)
        alpha_mask = np.where((alpha_mask >= 24) & circular_mask, 255, 0).astype(np.uint8)
        if int(np.count_nonzero(alpha_mask)) < 100:
            alpha_mask = np.where(circular_mask, 255, 0).astype(np.uint8)
        prepared_templates[name] = {
            "original_rgb": original_rgb,
            "rgb": rgb,
            "mask": alpha_mask,
        }

    padded_frames = {}

    def crop_at_box(x, y, side):
        pad = side + 4
        if side not in padded_frames:
            padded_frames[side] = cv2.copyMakeBorder(
                frame, pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
        padded = padded_frames[side]
        crop = padded[y + pad:y + pad + side, x + pad:x + pad + side]
        return cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)

    def stable_crop(cx, cy, side, offset_x, offset_y):
        side = max(8, int(side))
        pad = side + 4
        x = int(round(cx + offset_x + pad - side / 2))
        y = int(round(cy + offset_y + pad - side / 2))
        box = (x - pad, y - pad, side)
        return crop_at_box(*box), box

    badge = None
    # This is the BASH portrait marker for a student who is already in either
    # cafe. lobby_badge.png is a different top-right cafe UI asset.
    badge_path = UI_ASSET_DIR / "号店.png"
    if badge_path.is_file():
        raw = np.fromfile(str(badge_path), dtype=np.uint8)
        badge = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    results = []
    for cx, cy, radius in sorted(np.round(circles[0]).astype(int), key=lambda row: row[1]):
        detail = {
            "circle": [int(cx), int(cy), int(radius)],
            "template_scores": {},
            "accepted": False,
        }
        diagnostics["circles"].append(detail)
        if not int(600 * scale) < cx < int(750 * scale):
            detail["reason"] = "outside_x_range"
            continue
        left, top = max(0, cx - radius), max(0, cy - radius)
        right, bottom = min(frame.shape[1], cx + radius), min(frame.shape[0], cy + radius)
        crop = frame[top:bottom, left:right]
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            detail["reason"] = "empty_crop"
            continue
        original_crop = cv2.resize(crop, (96, 96))
        stable_crops = [
            stable_crop(cx, cy, expected_side * size_factor, offset_x, offset_y)
            for offset_x, offset_y, size_factor in stable_variants
        ]
        if badge is not None:
            badge_rgb = badge[:, :, :3] if badge.ndim == 3 and badge.shape[2] == 4 else badge
            bh, bw = badge_rgb.shape[:2]
            badge_roi = original_crop[96 - bh:, 96 - bw:]
            if badge_roi.shape[:2] == (bh, bw):
                badge_score = float(cv2.matchTemplate(
                    badge_roi, badge_rgb, cv2.TM_CCOEFF_NORMED).max())
                if badge_score >= 0.8:
                    detail["badge_score"] = round(badge_score, 4)
                    if not allow_exchange_student:
                        detail["reason"] = "already_in_cafe"
                        continue
                    detail["already_in_cafe"] = True
        original_scores = {}
        corrected_scores = {}
        best_boxes = {}
        initial_boxes = {box for _, box in stable_crops}
        for name, prepared in prepared_templates.items():
            original_score = float(cv2.matchTemplate(
                original_crop,
                prepared["original_rgb"],
                cv2.TM_CCOEFF_NORMED,
            ).max())
            original_scores[name] = round(original_score, 4)
            corrected_score = -1.0
            for candidate_crop, candidate_box in stable_crops:
                score = float(cv2.matchTemplate(
                    candidate_crop,
                    prepared["rgb"],
                    cv2.TM_CCOEFF_NORMED,
                    mask=prepared["mask"],
                ).max())
                if score > corrected_score:
                    corrected_score = score
                    best_boxes[name] = candidate_box
            corrected_scores[name] = round(corrected_score, 4)

        ranked = sorted(corrected_scores.items(), key=lambda item: item[1], reverse=True)
        best_name, best_score = ranked[0] if ranked else (None, -1.0)
        second_name, second_score = ranked[1] if len(ranked) > 1 else (None, -1.0)
        margin = best_score - second_score if second_name is not None else 1.0
        adjustment = None
        if (best_name is not None and best_name in best_boxes
                and BASH_CROP_ADJUST_MIN_SCORE <= best_score < BASH_HIGH_CONFIDENCE_SCORE
                and round(margin, 4) >= BASH_MIN_MARGIN):
            base_name, base_score = best_name, best_score
            initial_box = best_boxes[base_name]
            side = initial_box[2]
            # Use the unshifted box at the already-selected size as the fixed
            # origin. An initial +/-1 crop must not become a second origin.
            x, y, side = next(
                box for (offset_x, offset_y, _), (_, box) in zip(stable_variants, stable_crops)
                if offset_x == offset_y == 0 and box[2] == side)
            initial_offset = (initial_box[0] - x, initial_box[1] - y)
            offsets = [(-1, 0), (1, 0), (0, -1), (0, 1),
                       (-2, 0), (2, 0), (0, -2), (0, 2)]
            near = [(dx, dy) for dx in range(-3, 4) for dy in range(-3, 4)
                    if 0 < dx * dx + dy * dy <= 9 and (dx, dy) not in offsets]
            offsets.extend(sorted(near, key=lambda offset: (
                offset[0] * offset[0] + offset[1] * offset[1], offset[1], offset[0])))
            reused_offsets = [list(offset) for offset in offsets
                              if (x + offset[0], y + offset[1], side) in initial_boxes]
            chosen_offset = initial_offset
            attempted_offsets = []
            for dx, dy in offsets:
                if (x + dx, y + dy, side) in initial_boxes:
                    continue  # This exact crop was already scored initially.
                attempted_offsets.append([dx, dy])
                adjusted_crop = crop_at_box(x + dx, y + dy, side)
                scores = {
                    name: round(float(cv2.matchTemplate(
                        adjusted_crop, prepared["rgb"], cv2.TM_CCOEFF_NORMED,
                        mask=prepared["mask"]).max()), 4)
                    for name, prepared in prepared_templates.items()
                }
                adjusted_rank = sorted(scores.items(), key=lambda item: item[1], reverse=True)
                adjusted_name, adjusted_score = adjusted_rank[0]
                runner_name, runner_score = adjusted_rank[1] if len(adjusted_rank) > 1 else (None, -1.0)
                adjusted_margin = round(adjusted_score - runner_score, 4) if runner_name is not None else 1.0
                if (adjusted_name == base_name and adjusted_score > best_score
                        and adjusted_margin >= BASH_MIN_MARGIN):
                    corrected_scores = scores
                    best_score = adjusted_score
                    second_name, second_score = runner_name, runner_score
                    margin = adjusted_margin
                    chosen_offset = (dx, dy)
                    if best_score >= BASH_HIGH_CONFIDENCE_SCORE:
                        break
            adjustment = {
                "name": base_name, "before_score": base_score,
                "after_score": best_score, "offset": list(chosen_offset),
                "base_box": [x, y, side], "positions": len(attempted_offsets),
                "initial_offset": list(initial_offset),
                "attempted_offsets": attempted_offsets, "reused_offsets": reused_offsets,
                "applied": best_score > base_score,
            }
            detail["crop_adjustment"] = adjustment

        detail["template_scores_original"] = original_scores
        detail["template_scores"] = corrected_scores
        detail["best_name"] = best_name
        detail["best_score"] = round(best_score, 4)
        detail["original_best_score"] = (
            original_scores.get(best_name) if best_name is not None else None)
        detail["second_name"] = second_name
        detail["second_score"] = round(second_score, 4) if second_name is not None else None
        detail["margin"] = round(margin, 4)
        evidence = {
            "name": best_name,
            "circle": [int(cx), int(cy), int(radius)],
            "score": round(best_score, 4),
            "original_score": detail["original_best_score"],
            "second_name": second_name,
            "second_score": detail["second_score"],
            "margin": round(margin, 4),
        }
        if adjustment is not None:
            evidence["crop_adjustment"] = adjustment
        diagnostics["candidate_evidence"].append(evidence)
        if (
            best_name is not None
            and best_score >= BASH_HIGH_CONFIDENCE_SCORE
            and margin >= BASH_MIN_MARGIN
        ):
            results.append((best_name, int(cx), int(cy), best_score))
            detail["accepted"] = True
            detail["reason"] = "matched_crop_adjustment" if adjustment and adjustment["applied"] else "matched_high_confidence"
            detail["acceptance_source"] = "single_frame_crop" if adjustment and adjustment["applied"] else "single_frame"
        elif (
            best_name is not None
            and best_score >= BASH_RECHECK_MIN_SCORE
            and margin >= BASH_MIN_MARGIN
        ):
            detail["reason"] = "borderline_recheck"
            diagnostics["borderline_candidates"].append(evidence)
        elif best_name is not None and best_score >= BASH_RECHECK_MIN_SCORE:
            detail["reason"] = "margin_too_small"
        else:
            detail["reason"] = "below_threshold"
    return (results, diagnostics) if return_diagnostics else results


def _bash_recheck_borderline_candidates(
        self, frame, templates, matches, diagnostics, allow_exchange_student: bool = False):
    """Use one independent rescue frame only when this frame has no passing match."""
    if matches or not diagnostics.get("borderline_candidates"):
        return matches, diagnostics, frame
    worker_checkpoint()
    self.update_screenshot_array()
    second_frame = self.latest_img_array
    from core import image as baas_image
    if not baas_image.compare_image(self, "cafe_invitation-ticket"):
        raise CafeInvitationRecoveryRequired("补救取帧时无法确认有效邀请列表")
    second_matches, second_diagnostics = _detect_bash_avatar_matches(
        second_frame, templates, return_diagnostics=True,
        allow_exchange_student=allow_exchange_student)
    for detail in second_diagnostics.get("circles", []):
        if detail.get("accepted"):
            detail["acceptance_source"] = "rescue_frame_crop" if detail.get("crop_adjustment", {}).get("applied") else "rescue_frame"
    second_diagnostics["rescue_frame"] = True
    return second_matches, second_diagnostics, second_frame


def _summarize_bash_diagnostics(diagnostics: dict, page: int, bottom_diff=None) -> dict:
    """Keep formal logs small while retaining the useful per-page evidence."""
    reasons = {}
    selected_best_scores = {}
    selected_best_scores_original = {}
    margin_evidence = []
    candidates = []
    for detail in diagnostics.get("circles", []):
        reason = detail.get("reason", "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
        for name, score in detail.get("template_scores", {}).items():
            selected_best_scores[name] = max(
                float(score), selected_best_scores.get(name, float("-inf")))
        for name, score in detail.get("template_scores_original", {}).items():
            selected_best_scores_original[name] = max(
                float(score), selected_best_scores_original.get(name, float("-inf")))
        if detail.get("template_scores"):
            margin_evidence.append({
                "name": detail.get("best_name"),
                "score": detail.get("best_score"),
                "original_score": detail.get("original_best_score"),
                "second_name": detail.get("second_name"),
                "second_score": detail.get("second_score"),
                "margin": detail.get("margin"),
                "accepted": bool(detail.get("accepted")),
            })
        if detail.get("accepted"):
            candidates.append({
                "name": detail.get("best_name"),
                "score": detail.get("best_score"),
                "original_score": detail.get("original_best_score"),
                "second_name": detail.get("second_name"),
                "second_score": detail.get("second_score"),
                "margin": detail.get("margin"),
                "circle": detail.get("circle"),
                "acceptance_source": detail.get("acceptance_source"),
            })
    margin_evidence.sort(key=lambda item: item.get("score") or -1.0, reverse=True)
    summary = {
        "page": page,
        "frame_size": diagnostics.get("frame_size"),
        "circle_count": len(diagnostics.get("circles", [])),
        "selected_best_scores": {
            name: round(score, 4)
            for name, score in selected_best_scores.items()
            if score != float("-inf")
        },
        "selected_best_scores_original": {
            name: round(score, 4)
            for name, score in selected_best_scores_original.items()
            if score != float("-inf")
        },
        "margin_evidence": margin_evidence[:12],
        "filtered_reasons": reasons,
        "candidates": candidates,
    }
    if bottom_diff is not None:
        summary["bottom_diff"] = round(float(bottom_diff), 3)
    if diagnostics.get("reason"):
        summary["detector_reason"] = diagnostics["reason"]
    if diagnostics.get("rechecks"):
        summary["rechecks"] = diagnostics["rechecks"]
    adjustments = []
    rechecks = diagnostics.get("rechecks", [])
    for frame_number, items in (
        (1, [detail["crop_adjustment"] for detail in diagnostics.get("circles", [])
             if "crop_adjustment" in detail]),
        (2, diagnostics.get("recheck_crop_adjustments", [])),
    ):
        for item in items:
            check = next((row for row in rechecks if row.get("name") == item["name"]), None)
            adjustments.append({
                "name": item["name"], "frame": frame_number,
                "before_score": item["before_score"], "after_score": item["after_score"],
                "offset": item["offset"], "positions": item["positions"],
                "applied": item["applied"],
                **{key: item[key] for key in ("base_box", "initial_offset", "attempted_offsets", "reused_offsets") if key in item},
            })
    if adjustments:
        summary["crop_adjustments"] = adjustments
    return summary


def _brief_bash_diagnostics(summary: dict) -> dict:
    """Keep routine pages compact; the original six full pages stay in memory."""
    brief = {key: summary[key] for key in (
        "page", "phase", "candidate", "discovery_page", "circle_count",
        "selected_best_scores", "filtered_reasons", "bottom_diff", "detector_reason") if key in summary}
    brief.setdefault("phase", "discovery")
    candidate_names = [row.get("name") for row in summary.get("candidates", []) if row.get("name")]
    candidate_names.extend(row["name"] for row in summary.get("rechecks", []) if row.get("accepted") and row.get("name"))
    brief["candidates"] = list(dict.fromkeys(candidate_names))
    if summary.get("candidates"):
        brief["accepted"] = [{key: row.get(key) for key in ("name", "score", "acceptance_source")}
                             for row in summary["candidates"]]
    applied = [row for row in summary.get("crop_adjustments", []) if row.get("applied")]
    if applied:
        brief["crop_adjustments"] = [
            {key: value for key, value in row.items() if key not in {"attempted_offsets", "reused_offsets"}}
            for row in applied]
    if summary.get("rechecks"):
        brief["rechecks"] = [{key: row.get(key) for key in ("name", "second_score", "accepted")}
                             for row in summary["rechecks"]]
    return brief


def bash_invite_student(self, cafe_no=1, student_snapshot=None):
    """Use BASH for bounded candidate discovery and BAAS for invitation state transitions."""
    cv2 = importlib.import_module("cv2")
    np = importlib.import_module("numpy")
    from core.exception import FunctionCallTimeout, RequestHumanTakeOver
    from module import cafe_reward

    allow_exchange_student = bool(getattr(self.config, "cafe_reward_allow_exchange_student", False))
    append_log(f"[邀请选项] No.{cafe_no} 允许邀请换装角色={'开启' if allow_exchange_student else '关闭'}")
    started = time.perf_counter()
    deadline = time.monotonic() + INVITATION_BUDGET_SECONDS
    self.bacoffee_invitation_deadline = deadline
    if student_snapshot is None:
        student_snapshot = getattr(self, "bacoffee_student_snapshot", None)
    selected = tuple((student_snapshot or {}).get(cafe_no, ())) if student_snapshot is not None else None
    diagnostic_history = []
    contract = None
    relocation_stats = {
        "quick_swipes": 0,
        "window_swipes": 0,
        "recognition_pages": 0,
        "candidate_attempts": [],
        "candidate_failures": [],
    }
    ticket_precheck = None
    discovery_stats = {
        "pages": 0,
        "cached_candidates": [],
        "stop_reason": "",
        "rechecks": 0,
        "top_swipes": 0,
        "progress_refreshes": 0,
    }

    def relocation_snapshot():
        snapshot = dict(relocation_stats)
        snapshot["candidate_attempts"] = list(relocation_stats["candidate_attempts"])
        snapshot["candidate_failures"] = list(relocation_stats["candidate_failures"])
        snapshot["total_swipes"] = (
            relocation_stats["quick_swipes"] + relocation_stats["window_swipes"])
        snapshot["remaining_budget_seconds"] = round(
            max(0.0, deadline - time.monotonic()), 3)
        return snapshot

    active_confirmation = None

    def finish(
        status: str,
        *,
        candidate: str | None = None,
        score: float | None = None,
        page: int | None = None,
        reason: str = "",
        known_screen: str = "unknown",
        screen_contract: dict | None = None,
        invalid_templates: list[str] | None = None,
        ticket_state: dict | None = None,
    ) -> dict:
        result = {
            "status": status,
            "cafe_no": cafe_no,
            "cafe_attempt": getattr(self, "bacoffee_cafe_attempt", 1),
            "selected": list(selected or ()),
            "candidate": candidate,
            "score": round(float(score), 4) if score is not None else None,
            "page": page,
            "reason": reason,
            "known_screen": known_screen,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        if active_confirmation is not None and candidate == active_confirmation.get("candidate"):
            result["confirmation_steps"] = [dict(step) for step in active_confirmation["steps"][-14:]]
            result["last_confirmation_feature"] = active_confirmation.get("last_feature")
            result["confirmation_phase"] = active_confirmation.get("phase")
            result["awaiting_after"] = active_confirmation.get("awaiting_after")
            result["menu_confirmed"] = bool(active_confirmation.get("menu_confirmed"))
        if screen_contract is not None:
            result["screen_contract"] = screen_contract
        if invalid_templates:
            result["invalid_templates"] = list(invalid_templates)
        if ticket_state is not None:
            result["ticket_precheck"] = {
                key: ticket_state[key]
                for key in ("state", "available_score", "cooldown_score")
                if key in ticket_state
            }
        if discovery_stats["pages"] or discovery_stats["stop_reason"]:
            result["discovery"] = dict(discovery_stats)
            result["discovery"]["elapsed_seconds"] = round(discovery_stats.get("elapsed_seconds", time.perf_counter() - started), 3)
        if relocation_stats["recognition_pages"] or relocation_stats["candidate_attempts"]:
            result["relocation"] = relocation_snapshot()
        if status in {"timeout", "error", "uncertain", "not_found", "interrupted"} and diagnostic_history:
            result["diagnostic_tail"] = diagnostic_history[-6:]
        run_results = getattr(self, "bacoffee_invitation_results", None)
        if isinstance(run_results, list):
            run_results.append(result)
        run_result = getattr(self, "bacoffee_run_result", None)
        if isinstance(run_result, dict):
            run_result.setdefault("invitation_results", []).append(result)
        if result.get("diagnostic_tail"):
            append_log(f"[BASH诊断尾部] No.{cafe_no} attempt={result['cafe_attempt']} "
                       + json.dumps(result["diagnostic_tail"], ensure_ascii=False, separators=(",", ":")))
        append_log(f"[邀请结果] No.{cafe_no} attempt={result['cafe_attempt']} 状态={status}；对象={result.get('candidate') or '无'}；原因={result.get('reason', '')}")
        return result

    def timeout_result(reason: str, candidate: str | None = None, page: int | None = None):
        return finish(
            "timeout",
            candidate=candidate,
            page=page,
            reason=reason,
            screen_contract=contract,
            invalid_templates=invalid_templates)

    def budget_expired(reason: str, candidate: str | None = None, page: int | None = None):
        worker_checkpoint()
        if not self.flag_run:
            raise RequestHumanTakeOver("用户中止邀请")
        if time.monotonic() >= deadline:
            if not discovery_stats["stop_reason"]:
                discovery_stats["stop_reason"] = "异常保护：无有效进度达到期限"
            return timeout_result(reason, candidate, page)
        return None

    def renew_deadline(progress=False):
        nonlocal deadline
        deadline = time.monotonic() + INVITATION_BUDGET_SECONDS
        self.bacoffee_invitation_deadline = deadline
        if progress:
            discovery_stats["progress_refreshes"] += 1

    def read_list_frame():
        from core import image as baas_image
        worker_checkpoint()
        if not self.flag_run:
            raise RequestHumanTakeOver("用户中止邀请")
        self.update_screenshot_array()
        frame = self.latest_img_array
        if (frame is None or getattr(frame, "ndim", 0) != 3 or not frame.size
                or not baas_image.compare_image(self, "cafe_invitation-ticket")):
            raise CafeInvitationRecoveryRequired("搜索或重定位时无法确认有效邀请列表")
        return frame

    def list_roi(frame):
        height, width = frame.shape[:2]
        return cv2.cvtColor(
            frame[int(280 * height / 1080):int(906 * height / 1080),
                  int(599 * width / 1920):int(1321 * width / 1920)], cv2.COLOR_BGR2GRAY)

    def roi_diff(first, second):
        return float(np.mean(np.abs(first.astype(np.int16) - second.astype(np.int16))))

    verified_top_roi = None

    def ensure_list_top():
        nonlocal verified_top_roi
        # Entry/reopening does not guarantee a reset scroll position. Probe only
        # this valid list. Bound initial normalization separately; after discovery
        # its measured page count bounds the return trip, with progress renewal.
        swipe_limit = discovery_stats["pages"] + 2 if discovery_stats["pages"] else 512
        top_swipes = 0
        frame = read_list_frame()
        previous = list_roi(frame)
        if verified_top_roi is not None and roi_diff(previous, verified_top_roi) < 3.0:
            return frame  # Already at the top verified during this invitation.
        stable = 0
        while stable < 2:
            if time.monotonic() >= deadline or top_swipes >= swipe_limit:
                raise FunctionCallTimeout("邀请列表归顶无进度超时或达到异常滑动保护上限")
            self.swipe(640, 213, 640, 520, duration=0.4, post_sleep_time=1)
            discovery_stats["top_swipes"] += 1
            top_swipes += 1
            frame = read_list_frame()
            current = list_roi(frame)
            if roi_diff(current, previous) < 3.0:
                stable += 1
            else:
                stable = 0
                renew_deadline(progress=True)
            previous = current
        verified_top_roi = previous.copy()
        return frame

    if selected is None:
        return finish("error", reason="worker 未提供固定学生选择快照")
    chosen = selected
    if not chosen:
        self.logger.warning(f"No.{cafe_no} Cafe: no BASH avatar selected, skip invitation.")
        return finish("no_selection", reason="GUI 未选择 BASH 学生", known_screen="cafe_menu")

    catalog = load_student_catalog()
    templates = {}
    invalid_templates = []
    for name in chosen:
        entry = resolve_student_avatar(name, catalog)
        if entry is None:
            invalid_templates.append(name)
            append_log(f"[邀请模板] No.{cafe_no} 学生不可用或已删除：{name}")
            continue
        path = entry["path"]
        raw = np.fromfile(str(path), dtype=np.uint8)
        template = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if template is not None and template.size and template.ndim == 3 and template.shape[2] == 4:
            templates[name] = template
        else:
            invalid_templates.append(name)
            append_log(f"[邀请模板] No.{cafe_no} PNG 无法解码或不是 RGBA：{name}")
    if not templates:
        self.logger.warning("BASH student avatar templates cannot be loaded.")
        return finish(
            "error",
            reason="所选 PNG 均不存在或无法解码",
            known_screen="cafe_menu",
            invalid_templates=invalid_templates)

    self.logger.info("BASH 2026-09-05 avatar priority: " + ", ".join(Path(x).stem for x in chosen))
    if invalid_templates:
        append_log(f"[邀请模板] No.{cafe_no} 无效 PNG：{invalid_templates}")
    if budget_expired("进入邀请页前已达到单店邀请预算"):
        return timeout_result("进入邀请页前已达到单店邀请预算")

    # Restore hidden cafe chrome before the first ticket precheck. The restore
    # helper is itself guarded by an explicit template match; after this point
    # the available path goes directly to BAAS without repeating the restore.
    _bash_restore_hidden_cafe_ui(self)
    ticket_precheck = _bash_invitation_ticket_state(self)
    ticket_state = ticket_precheck.get("state")
    if ticket_state in {"cooldown", "unknown"}:
        reason = (
            "BASH 预判邀请券处于冷却状态，跳过 BAAS 邀请入口"
            if ticket_state == "cooldown"
            else "BASH 无法可靠判定邀请券状态，安全跳过 BAAS 邀请入口")
        return finish(
            "no_ticket",
            reason=reason,
            known_screen="cafe_menu",
            screen_contract=_invitation_screen_contract(self),
            invalid_templates=invalid_templates,
            ticket_state=ticket_precheck)

    try:
        entry_status = cafe_reward.to_invitation_ticket(self, True)
    except FunctionCallTimeout:
        # BAAS entry may time out before any student is clicked. Recheck the
        # ticket once, so an explicit cooldown does not trigger recovery.
        retry_ticket_state = _bash_invitation_ticket_state(self)
        if retry_ticket_state.get("state") == "cooldown":
            return finish(
                "no_ticket",
                reason="BAAS 进入邀请页超时后确认邀请券已冷却，安全跳过",
                known_screen="cafe_menu",
                screen_contract=_invitation_screen_contract(self),
                invalid_templates=invalid_templates,
                ticket_state=retry_ticket_state)
        if _detect_known_baas_screen(self, ("cafe_menu",)) == "cafe_menu":
            return finish(
                "no_ticket",
                reason="BAAS 进入邀请页超时但仍在咖啡厅菜单，安全跳过",
                known_screen="cafe_menu",
                screen_contract=_invitation_screen_contract(self),
                invalid_templates=invalid_templates,
                ticket_state=retry_ticket_state)
        raise
    except RequestHumanTakeOver:
        raise
    except Exception as exc:
        return finish(
            "error",
            reason=f"BAAS 进入邀请页异常：{exc}",
            invalid_templates=invalid_templates)

    append_log(f"[邀请入口] No.{cafe_no} BAAS 返回：{entry_status}")
    if entry_status == "cafe_invitation-ticket-invalid":
        contract = _invitation_screen_contract(self)
        return finish(
            "no_ticket",
            reason="BAAS 判定邀请券不可用",
            known_screen="cafe_invitation-ticket-invalid",
            screen_contract=contract,
            invalid_templates=invalid_templates)
    if entry_status != "cafe_invitation-ticket":
        contract = _invitation_screen_contract(self)
        return finish(
            "uncertain",
            reason=f"BAAS 未确认有效邀请页：{entry_status}",
            screen_contract=contract,
            invalid_templates=invalid_templates)
    if budget_expired("进入有效邀请页后已达到单店邀请预算"):
        return timeout_result("进入有效邀请页后已达到单店邀请预算")

    contract = _invitation_screen_contract(self)
    found = set()
    match_data = {}
    previous_roi = None

    def confirm_candidate(frame, cx, cy, name, score, page):
        nonlocal active_confirmation
        timeout = budget_expired(
            "确认候选前已达到单店邀请预算", candidate=name, page=page)
        if timeout:
            return timeout
        logical_x, logical_y = _bash_logical_point(frame, cx, cy)
        ratio = getattr(self, "ratio", None)
        expected_pixel = (
            int(round(785 * ratio)), int(round(logical_y * ratio))) if ratio is not None else None
        append_log(
            f"[邀请候选] No.{cafe_no} candidate={name}; score={score:.4f}; "
            f"page={page}; pixel=({cx},{cy}); logical=({logical_x},{logical_y}); "
            f"expected_baas_pixel={expected_pixel}")
        self.logger.info(f"Invite BASH target {Path(name).stem} ({score:.3f}) via BAAS y={logical_y}")
        def persist_step(step):
            try:
                append_log(f"[邀请确认] No.{cafe_no} attempt={getattr(self, 'bacoffee_cafe_attempt', 1)} candidate={name}; "
                           + json.dumps(step, ensure_ascii=False, separators=(",", ":")))
            except (WorkerCancelled, RequestHumanTakeOver):
                raise
            except Exception:
                pass  # A diagnostic sink must not change the confirmation action.

        confirmation_context = {"cafe_no": cafe_no, "candidate": name, "steps": [],
                                "phase": "opening_candidate", "on_step": persist_step}
        active_confirmation = confirmation_context

        def preserve_failure(status, exc):
            try:
                persist_step({"event": status, "last_feature": confirmation_context.get("last_feature"),
                              "phase": confirmation_context.get("phase"), "menu_confirmed": bool(confirmation_context.get("menu_confirmed"))})
            except Exception:
                pass
            try:
                finish(status, candidate=name, score=score, page=page,
                       reason=f"确认阶段 {confirmation_context.get('phase')}：{exc}",
                       known_screen="cafe_menu" if confirmation_context.get("menu_confirmed") else "unknown",
                       screen_contract=contract, invalid_templates=invalid_templates)
            except Exception:
                pass  # Preserve the original cancellation/timeout even if recording fails.

        try:
            accepted = cafe_reward.checkConfirmInvite(self, logical_y, bac_candidate=confirmation_context)
        except (WorkerCancelled, RequestHumanTakeOver) as exc:
            preserve_failure("interrupted", exc)
            raise
        except FunctionCallTimeout as exc:
            preserve_failure("timeout", exc)
            raise
        except Exception as exc:
            preserve_failure("uncertain", exc)
            raise CafeInvitationRecoveryRequired(f"No.{cafe_no} invitation confirmation failed: {exc}") from exc
        else:
            persist_step({"event": "end", "status": "accepted" if accepted else "rejected",
                          "last_feature": confirmation_context.get("last_feature"),
                          "menu_confirmed": bool(confirmation_context.get("menu_confirmed"))})

        if time.monotonic() >= deadline:
            return timeout_result("BAAS 确认后已达到单店邀请预算", name, page)
        if not accepted:
            known_screen = _detect_known_baas_screen(
                self, ("cafe_invitation-ticket", "cafe_invitation-ticket-invalid"))
            if known_screen == "cafe_invitation-ticket":
                return finish(
                    "confirm_rejected",
                    candidate=name,
                    score=score,
                    page=page,
                    reason="BAAS 确认策略拒绝并安全返回邀请列表：" + str(confirmation_context["steps"][-1].get("reason", "") if confirmation_context["steps"] else "原权限拒绝"),
                    known_screen=known_screen,
                    screen_contract=contract,
                    invalid_templates=invalid_templates)
            if known_screen == "cafe_invitation-ticket-invalid":
                return finish(
                    "no_ticket",
                    candidate=name,
                    score=score,
                    page=page,
                    reason="候选被拒绝后邀请券状态已变化",
                    known_screen=known_screen,
                    screen_contract=contract,
                    invalid_templates=invalid_templates)
            return finish(
                "uncertain",
                candidate=name,
                score=score,
                page=page,
                reason="候选拒绝后未能确认仍在邀请列表",
                known_screen=known_screen,
                screen_contract=contract,
                invalid_templates=invalid_templates)

        known_screen = _detect_known_baas_screen(self, ("cafe_menu",))
        if known_screen == "cafe_menu":
            self.logger.info(f"BAAS invitation confirmed for {Path(name).stem}; cafe_menu verified.")
            return finish(
                "success",
                candidate=name,
                score=score,
                page=page,
                reason="BAAS 确认成功且已回到咖啡厅菜单",
                known_screen=known_screen,
                screen_contract=contract,
                invalid_templates=invalid_templates)
        return finish(
            "uncertain",
            candidate=name,
            score=score,
            page=page,
            reason="BAAS 确认函数返回成功，但未验证 cafe_menu",
            known_screen=known_screen,
            screen_contract=contract,
            invalid_templates=invalid_templates)

    def close_and_reopen():
        timeout = budget_expired("重定位前已达到单店邀请预算")
        if timeout:
            return None
        try:
            cafe_reward.to_cafe(self, True)
            _bash_restore_hidden_cafe_ui(self)
            reopened = cafe_reward.to_invitation_ticket(self, True)
        except (FunctionCallTimeout, RequestHumanTakeOver):
            raise
        except Exception as exc:
            append_log(f"[邀请入口] No.{cafe_no} 唯一重定位异常：{exc}")
            return "unknown"
        append_log(f"[邀请入口] No.{cafe_no} 唯一重定位返回：{reopened}")
        return reopened

    try:
        frame = ensure_list_top()
        renew_deadline()
        page = 0
        bottom_cycles = 0
        matches = []
        while True:
            page += 1
            timeout = budget_expired("搜索无有效进度达到异常保护期限", page=page)
            if timeout:
                return timeout
            discovery_stats["pages"] = page
            matches, diagnostics = _detect_bash_avatar_matches(
                frame, templates, return_diagnostics=True,
                allow_exchange_student=allow_exchange_student)
            if not matches and diagnostics.get("borderline_candidates"):
                matches, diagnostics, frame = _bash_recheck_borderline_candidates(
                    self, frame, templates, matches, diagnostics,
                    allow_exchange_student=allow_exchange_student)
                discovery_stats["rechecks"] += 1
            page_seen = set()
            for name, cx, cy, score in matches:
                if name in page_seen:
                    continue
                page_seen.add(name)
                found.add(name)
                match_data.setdefault(name, (cx, cy, score, page))
            discovery_stats["cached_candidates"] = [name for name in chosen if name in found]
            roi = list_roi(frame)
            diff = None if previous_roi is None else roi_diff(roi, previous_roi)
            if diff is not None:
                if diff < 3.0:
                    bottom_cycles += 1
                else:
                    bottom_cycles = 0
                    renew_deadline(progress=True)
            summary = _summarize_bash_diagnostics(diagnostics, page, diff)
            diagnostic_history.append(summary)
            if len(diagnostic_history) > 6:
                diagnostic_history.pop(0)
            append_log(f"[BASH识别] No.{cafe_no} " + json.dumps(
                _brief_bash_diagnostics(summary), ensure_ascii=False, separators=(",", ":")))
            timeout = budget_expired("当前页识别后无有效进度达到异常保护期限", page=page)
            if timeout:
                return timeout
            if chosen[0] in page_seen:
                discovery_stats["stop_reason"] = "首选找到"
                break
            if bottom_cycles >= 2:
                discovery_stats["stop_reason"] = "列表到底：连续两次向下滑动后布局不变"
                break
            previous_roi = roi.copy()
            self.swipe(640, 520, 640, 213, duration=0.4, post_sleep_time=1)
            frame = read_list_frame()
    except (WorkerCancelled, RequestHumanTakeOver):
        discovery_stats["stop_reason"] = "取消"
        finish("interrupted", reason="搜索取消或执行权已交接", screen_contract=contract)
        raise
    except FunctionCallTimeout:
        discovery_stats["stop_reason"] = "异常保护"
        finish("timeout", reason="邀请列表归顶或搜索异常保护到期", screen_contract=contract)
        raise
    except Exception as exc:
        discovery_stats["stop_reason"] = "异常：列表状态或设备错误"
        finish("uncertain", reason=str(exc), screen_contract=contract)
        raise CafeInvitationRecoveryRequired(f"邀请列表搜索异常：{exc}") from exc
    discovery_stats["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    append_log(f"[邀请发现] No.{cafe_no} " + json.dumps(discovery_stats, ensure_ascii=False, separators=(",", ":")))
    # Repositioning and confirmation each start with fresh bounded time. Search
    # duration is not carried over as an already-expired absolute deadline.
    renew_deadline()

    if not found:
        self.logger.warning("BASH invitation scan reached the bottom without any selected student; stop this invitation attempt.")
        try:
            cafe_reward.to_cafe(self, True)
        except (FunctionCallTimeout, RequestHumanTakeOver):
            raise
        except Exception as exc:
            return finish(
                "error",
                reason=f"未发现候选且 BAAS 返回咖啡厅失败：{exc}",
                screen_contract=contract,
                invalid_templates=invalid_templates)
        return finish(
            "not_found",
            reason="发现阶段结束且未发现合格候选，安全跳过邀请",
            known_screen="cafe_menu",
            screen_contract=contract,
            invalid_templates=invalid_templates)

    candidate_order = [name for name in chosen if name in found]
    target = candidate_order[0]
    target_page = match_data[target][3]
    self.logger.info(
        f"Best BASH avatar: {Path(target).stem}; first seen on page {target_page}; "
        f"candidates={candidate_order}")

    current_visible = {name: (cx, cy, score) for name, cx, cy, score in matches}
    use_current_frame = target in current_visible
    reopened = "cafe_invitation-ticket" if use_current_frame else close_and_reopen()
    if reopened is None:
        return timeout_result("唯一重定位前已达到单店邀请预算", target, target_page)
    if reopened == "cafe_invitation-ticket-invalid":
        contract = _invitation_screen_contract(self)
        return finish(
            "no_ticket",
            candidate=target,
            page=target_page,
            reason="唯一重定位后邀请券不可用",
            known_screen=reopened,
            screen_contract=contract,
            invalid_templates=invalid_templates)
    if reopened != "cafe_invitation-ticket":
        contract = _invitation_screen_contract(self)
        return finish(
            "uncertain",
            candidate=target,
            page=target_page,
            reason=f"唯一重定位未确认有效邀请页：{reopened}",
            screen_contract=contract,
            invalid_templates=invalid_templates)
    contract = _invitation_screen_contract(self)

    # Discovery page numbers are used as bounded anchors for each candidate.
    # The list may overlap between screenshots, so each anchor allows a small
    # local recognition window rather than assuming the candidate is on one
    # exact frame. All movement and recognition remains globally bounded.
    def relocation_action(action, candidate=target, candidate_page=target_page):
        try:
            return action()
        except (WorkerCancelled, RequestHumanTakeOver):
            finish("interrupted", candidate=candidate, page=candidate_page,
                   reason="重定位取消或执行权已交接", screen_contract=contract)
            raise
        except FunctionCallTimeout as exc:
            finish("timeout", candidate=candidate, page=candidate_page,
                   reason=f"重定位异常保护：{exc}", screen_contract=contract)
            raise
        except Exception as exc:
            finish("uncertain", candidate=candidate, page=candidate_page,
                   reason=f"重定位列表或设备异常：{exc}", screen_contract=contract)
            raise CafeInvitationRecoveryRequired(f"重定位列表或设备异常：{exc}") from exc

    current_scroll = page - 1 if use_current_frame else 0
    if not use_current_frame:
        frame = relocation_action(ensure_list_top)
        renew_deadline()
    last_rejected = None
    last_missing = None

    def move_to_candidate_anchor(candidate, candidate_page):
        nonlocal current_scroll
        desired_scroll = max(0, candidate_page - 2)
        while current_scroll != desired_scroll:
            timeout = budget_expired(
                "候选快速定位时已达到单店邀请预算", candidate, candidate_page)
            if timeout:
                return timeout
            anchor_roi = list_roi(read_list_frame())
            direction = 1 if desired_scroll > current_scroll else -1
            if direction > 0:
                self.swipe(640, 520, 640, 213, duration=0.2, post_sleep_time=0.8)
            else:
                self.swipe(640, 213, 640, 520, duration=0.2, post_sleep_time=0.8)
            next_frame = read_list_frame()
            if roi_diff(list_roi(next_frame), anchor_roi) >= 3.0:
                renew_deadline(progress=True)
            current_scroll += direction
            relocation_stats["quick_swipes"] += 1
            append_log(
                f"[邀请重定位] No.{cafe_no} candidate={candidate}; "
                f"discovery_page={candidate_page}; quick_swipes={relocation_stats['quick_swipes']}; "
                f"recognition_pages={relocation_stats['recognition_pages']}; "
                f"remaining_budget_seconds={relocation_snapshot()['remaining_budget_seconds']}")
        return None

    def safe_invitation_state(candidate, candidate_page):
        known_screen = _detect_known_baas_screen(
            self, ("cafe_invitation-ticket", "cafe_invitation-ticket-invalid"))
        if known_screen == "cafe_invitation-ticket":
            return None
        if known_screen == "cafe_invitation-ticket-invalid":
            return finish(
                "no_ticket",
                candidate=candidate,
                page=candidate_page,
                reason="已发现候选但重定位后邀请券状态已变化",
                known_screen=known_screen,
                screen_contract=contract,
                invalid_templates=invalid_templates)
        return finish(
            "uncertain",
            candidate=candidate,
            page=candidate_page,
            reason="已发现候选但重定位后无法确认仍在邀请列表",
            known_screen=known_screen,
            screen_contract=contract,
            invalid_templates=invalid_templates)

    for candidate in candidate_order:
        candidate_page = match_data[candidate][3]
        timeout = budget_expired(
            "处理下一候选前已达到单店邀请预算", candidate, candidate_page)
        if timeout:
            return timeout
        remaining_pages = INVITATION_RELOCATION_PAGE_LIMIT - relocation_stats["recognition_pages"]
        if remaining_pages <= 0:
            return finish(
                "uncertain",
                candidate=candidate,
                page=candidate_page,
                reason="已发现候选但重定位识别页数上限已用尽",
                known_screen="cafe_invitation-ticket",
                screen_contract=contract,
                invalid_templates=invalid_templates)

        if last_rejected is not None or last_missing is not None:
            state_result = safe_invitation_state(candidate, candidate_page)
            if state_result is not None:
                return state_result

        on_current_frame = use_current_frame and candidate == target
        timeout = None if on_current_frame else relocation_action(
            lambda: move_to_candidate_anchor(candidate, candidate_page), candidate, candidate_page)
        if timeout:
            return timeout

        candidate_visible = False
        local_previous_roi = None
        local_window_limit = min(2, remaining_pages)
        for local_page in range(1, local_window_limit + 1):
            timeout = budget_expired(
                f"候选 {candidate} 重定位第 {local_page} 页前已达到单店邀请预算",
                candidate,
                candidate_page,
            )
            if timeout:
                return timeout
            relocation_stats["recognition_pages"] += 1
            if not (on_current_frame and local_page == 1):
                frame = relocation_action(read_list_frame, candidate, candidate_page)
                matches, diagnostics = _detect_bash_avatar_matches(
                    frame, templates, return_diagnostics=True,
                    allow_exchange_student=allow_exchange_student)
            if not matches and diagnostics.get("borderline_candidates"):
                matches, diagnostics, frame = relocation_action(
                    lambda: _bash_recheck_borderline_candidates(
                        self, frame, templates, matches, diagnostics,
                        allow_exchange_student=allow_exchange_student), candidate, candidate_page)
                relocation_stats["rechecks"] = relocation_stats.get("rechecks", 0) + len(
                    diagnostics.get("rechecks", []))
            visible = {}
            for name, cx, cy, score in matches:
                if name in candidate_order and name not in visible:
                    visible[name] = (cx, cy, score)
            height, width = frame.shape[:2]
            roi = cv2.cvtColor(
                frame[int(280 * height / 1080):int(906 * height / 1080),
                      int(599 * width / 1920):int(1321 * width / 1920)],
                cv2.COLOR_BGR2GRAY)
            diff = None if local_previous_roi is None else float(np.mean(
                np.abs(roi.astype(np.int16) - local_previous_roi.astype(np.int16))))
            summary = _summarize_bash_diagnostics(
                diagnostics, relocation_stats["recognition_pages"], diff)
            summary["phase"] = "relocation"
            summary["candidate"] = candidate
            summary["discovery_page"] = candidate_page
            diagnostic_history.append(summary)
            if len(diagnostic_history) > 6:
                diagnostic_history.pop(0)
            append_log(
                f"[BASH识别] No.{cafe_no} "
                + json.dumps(_brief_bash_diagnostics(summary), ensure_ascii=False, separators=(",", ":")))

            if diff is not None and diff >= 3.0:
                renew_deadline(progress=True)
            if candidate in visible:
                renew_deadline()
                candidate_visible = True
                cx, cy, score = visible[candidate]
                attempt_entry = {
                    "candidate": candidate,
                    "discovery_page": candidate_page,
                    "status": "pending",
                }
                relocation_stats["candidate_attempts"].append(attempt_entry)
                result = confirm_candidate(
                    frame, cx, cy, candidate, score, candidate_page)
                attempt_entry["status"] = result["status"]
                append_log(
                    "[邀请重定位] "
                    + json.dumps(relocation_snapshot(), ensure_ascii=False, separators=(",", ":")))
                if result["status"] == "success":
                    return result
                if result["status"] == "confirm_rejected":
                    last_rejected = result
                    timeout = budget_expired(
                        "候选被拒绝后已达到单店邀请预算", candidate, candidate_page)
                    if timeout:
                        return timeout
                    if result.get("known_screen") != "cafe_invitation-ticket":
                        return result
                    break
                return result

            local_previous_roi = roi.copy()
            if diff is not None and diff < 3.0:
                break
            if local_page < local_window_limit:
                timeout = budget_expired(
                    f"候选 {candidate} 重定位滑动前已达到单店邀请预算",
                    candidate,
                    candidate_page,
                )
                if timeout:
                    return timeout
                direction = 1 if current_scroll <= max(0, candidate_page - 2) else -1
                if direction > 0:
                    relocation_action(lambda: self.swipe(640, 520, 640, 213, duration=0.4, post_sleep_time=1), candidate, candidate_page)
                else:
                    relocation_action(lambda: self.swipe(640, 213, 640, 520, duration=0.4, post_sleep_time=1), candidate, candidate_page)
                current_scroll += direction
                relocation_stats["window_swipes"] += 1

        if candidate_visible:
            continue

        last_missing = {"candidate": candidate, "page": candidate_page}
        relocation_stats["candidate_failures"].append({
            "candidate": candidate,
            "discovery_page": candidate_page,
            "reason": "not_visible_in_bounded_window",
        })
        append_log(
            "[邀请重定位] "
            + json.dumps(relocation_snapshot(), ensure_ascii=False, separators=(",", ":")))
        state_result = safe_invitation_state(candidate, candidate_page)
        if state_result is not None:
            return state_result

    if last_missing is not None:
        return finish(
            "uncertain",
            candidate=last_missing["candidate"],
            page=last_missing["page"],
            reason="已发现候选但受限重定位窗口内未重新找到",
            known_screen="cafe_invitation-ticket",
            screen_contract=contract,
            invalid_templates=invalid_templates)
    if last_rejected is not None:
        return last_rejected
    return finish(
        "uncertain",
        candidate=target,
        page=target_page,
        reason="已完成一次受限重定位，但未能安全确认任何候选",
        known_screen="unknown",
        screen_contract=contract,
        invalid_templates=invalid_templates)


def bash_cafe_implement(self):
    """Run each cafe serially: BAAS invitation bridge first, then interaction."""
    from module import cafe_reward

    student_snapshot = getattr(self, "bacoffee_student_snapshot", None)

    def invite_and_handle(cafe_no):
        try:
            result = bash_invite_student(self, cafe_no, student_snapshot)
        finally:
            if getattr(self, "bacoffee_invitation_deadline", None) is not None:
                self.bacoffee_invitation_deadline = None
        status = result["status"]
        if status == "success":
            append_log(f"[咖啡厅协调] No.{cafe_no} 邀请成功，继续摸头。")
            return True
        if status in {"no_ticket", "no_selection", "not_found", "confirm_rejected"}:
            if result.get("known_screen") != "cafe_menu":
                try:
                    cafe_reward.to_cafe(self, True)
                    result["known_screen"] = "cafe_menu"
                    append_log(f"[咖啡厅协调] No.{cafe_no} 已由 BAAS 返回 cafe_menu。")
                except Exception as exc:
                    append_log(f"[咖啡厅协调] No.{cafe_no} 无法返回 cafe_menu：{exc}")
                    raise CafeInvitationRecoveryRequired(
                        f"No.{cafe_no} safe return to cafe_menu failed: {exc}") from exc
            append_log(
                f"[咖啡厅协调] No.{cafe_no} 邀请状态={status}，按指南跳过邀请并继续摸头。")
            return True
        if status == "cancelled":
            append_log(f"[咖啡厅协调] No.{cafe_no} 邀请被用户中止，不进入自动恢复。")
            return False
        append_log(
            f"[咖啡厅协调] No.{cafe_no} 邀请状态={status}，停止后续摸头并请求咖啡厅恢复。")
        raise CafeInvitationRecoveryRequired(
            f"No.{cafe_no} invitation bridge status={status}; "
            f"reason={result.get('reason', '')}")

    self.to_main_page()
    cafe_reward.to_cafe(self, True)
    if self.config.cafe_reward_collect_hour_reward and cafe_reward.get_cafe_earning_status(self):
        cafe_reward.collect(self)
        cafe_reward.to_cafe(self, False)

    self.logger.info("No.1 Cafe: invitation stage starts before interaction.")
    if self.config.cafe_reward_use_invitation_ticket:
        if not invite_and_handle(1):
            return False
    self.logger.info("No.1 Cafe: interaction stage starts.")
    cafe_reward.interaction_for_cafe_solve_method3(self)

    if self.config.cafe_reward_has_no2_cafe:
        self.logger.info("Switch to No.2 Cafe after No.1 Cafe is fully complete.")
        cafe_reward.to_no2_cafe(self)
        self.logger.info("No.2 Cafe: invitation stage starts before interaction.")
        if self.config.cafe_reward_use_invitation_ticket:
            if not invite_and_handle(2):
                return False
        self.logger.info("No.2 Cafe: interaction stage starts.")
        cafe_reward.interaction_for_cafe_solve_method3(self)
    return True


def package_name_for_server(baas_root: Path, game_server: str) -> str | None:
    """Read the selected channel's package from BAAS's single static mapping."""
    try:
        static_config = json.loads(
            (baas_root / "config" / "static.json").read_text(encoding="utf-8"))
        package_name = static_config.get("package_name", {}).get(game_server)
        return str(package_name) if package_name else None
    except (OSError, ValueError, AttributeError):
        return None


def update_profile_adb_port(profile: Path, port_result: dict) -> None:
    """Persist the endpoint confirmed by the post-launch ADB check."""
    config_path = Path(profile) / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    port = port_result.get("port")
    if not port_result.get("verified") or not port:
        raise RuntimeError("MuMu ADB 端口尚未通过真实 ADB 验证，不能交给 BAAS。")
    if str(config.get("adbPort")) != str(port):
        config["adbPort"] = str(port)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")


def prepare_profile(settings: dict, port_result: dict | None = None) -> Path:
    if not port_result or not port_result.get("verified"):
        raise RuntimeError("仅在实例地址验证后准备 BAAS 配置")
    baas_root = Path(settings["baas_root"])
    source = baas_root / "config" / "cn"
    required_templates = ("config.json", "event.json")
    missing_templates = [name for name in required_templates if not (source / name).is_file()]
    if missing_templates:
        raise FileNotFoundError(f"找不到 BAAS 配置：{source}")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = PROFILE_DIR / "config.json"
    if not config_path.exists():
        # Copy only the clean runtime template. Do not clone the whole BAAS
        # config directory, which may contain backups or device-specific data.
        shutil.copy2(source / "config.json", config_path)
    event_path = PROFILE_DIR / "event.json"
    if not event_path.exists():
        # BAAS Scheduler reads and updates event.json beside the user's
        # config.json.  Copy only the clean template when it is absent so new
        # profiles work while existing scheduling state remains untouched.
        shutil.copy2(source / "event.json", event_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manager = find_mumu_manager(settings)
    vm_index = max(0, int(settings.get("mumu_vm_index", 1)))
    adb_port = port_result.get("port")
    if not adb_port:
        reason = port_result.get("diagnostic", {}).get("reason", "未知原因")
        raise RuntimeError(f"无法为 BAAS 准备 MuMu ADB 端口：{reason}")
    program = manager.parent / "MuMuNxMain.exe" if manager else Path(config.get("program_address", ""))
    game_server = normalize_game_server(settings.get("game_server"))
    config.update({
        # BACoffee starts the selected instance itself and waits for its actual
        # ADB port, so BAAS must not start a hard-coded emulator a second time.
        "open_emulator_stat": False,
        "emulator_wait_time": "60",
        "emulatorIsMultiInstance": True,
        "emulatorMultiInstanceNumber": vm_index,
        "multiEmulatorName": "mumu",
        "program_address": str(program).replace("\\", "/"),
        "adbPort": str(adb_port),
        "server": game_server,
        "autostart": False,
        "then": "无动作",
        "cafe_reward_has_no2_cafe": bool(settings["cafe2"]),
        "cafe_reward_collect_hour_reward": bool(settings["collect_reward"]),
        "cafe_reward_use_invitation_ticket": bool(settings["invite_student"]),
        "cafe_reward_allow_exchange_student": bool(settings.get("invite_allow_exchange_student", False)),
        "cafe_reward_affection_pat_round": int(settings["pat_rounds"]),
        "cafe_reward_invite1_criterion": "name",
        "cafe_reward_invite2_criterion": "name",
        "favorStudent1": list(settings["students_cafe1"]),
        "favorStudent2": list(settings["students_cafe2"]),
    })
    if config["server"] not in CN_GAME_SERVERS:
        raise ValueError("不支持的游戏渠道")
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")
    return PROFILE_DIR


class KeepAwake:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self):
        self.acquired = False

    def __enter__(self):
        if os.name == "nt":
            ctypes.set_last_error(0)
            state = ctypes.windll.kernel32.SetThreadExecutionState(
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
            error = ctypes.get_last_error()
            if not state:
                append_log(
                    f"[power] execution-state lock failed; result=0; GetLastError={error}.")
                raise OSError(error, "SetThreadExecutionState(ES_SYSTEM_REQUIRED) failed")
            self.acquired = True
            append_log(
                f"[power] execution-state lock acquired; result={int(state)}; GetLastError={error}.")
        return self

    def __exit__(self, *_):
        if os.name == "nt" and self.acquired:
            ctypes.set_last_error(0)
            state = ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
            error = ctypes.get_last_error()
            self.acquired = False
            if state:
                append_log(
                    f"[power] execution-state lock released; result={int(state)}; GetLastError={error}.")
            else:
                append_log(
                    f"[power] execution-state release failed; result=0; GetLastError={error}.")


def hold_wake_stabilization(seconds: int) -> None:
    """Keep Windows awake while a scheduled worker settles after wake."""
    with KeepAwake():
        worker_sleep(seconds)


def sanitize_external_runtime() -> None:
    """Prevent the packaged GUI's Qt DLL/plugin paths from leaking into MuMu."""
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        os.environ.pop(key, None)
    if os.name == "nt":
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def close_mumu_completely(settings: dict | None = None) -> None:
    """Gracefully stop the selected VM, then close the MuMu main application itself."""
    sanitize_external_runtime()
    settings = settings or load_settings()
    manager = find_mumu_manager(settings)
    vm_index = max(0, int(settings.get("mumu_vm_index", 1)))
    if manager is not None:
        subprocess.run(
            [str(manager), "control", "-v", str(vm_index), "shutdown"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    try:
        add_runtime_site_packages(Path(load_settings()["baas_root"]))
        psutil = importlib.import_module("psutil")
        targets = []
        for process in psutil.process_iter(["name", "exe"]):
            try:
                if (process.info.get("name") or "").lower() == "mumunxmain.exe":
                    targets.append(process)
                    process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive = psutil.wait_procs(targets, timeout=5)
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as exc:
        append_log("关闭 MuMu 主程序时出现提示：" + str(exc))


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def last_input_tick() -> int:
    if os.name != "nt":
        return 0
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return int(info.dwTime)


def input_idle_seconds(input_tick: int) -> float | None:
    """Return input-idle time for the current Windows session."""
    if os.name != "nt" or not input_tick:
        return None
    # LASTINPUTINFO.dwTime is a 32-bit tick count. Keep the subtraction in
    # the same 32-bit domain so it also works across its periodic wraparound.
    current_tick = int(ctypes.windll.kernel32.GetTickCount64()) & 0xFFFFFFFF
    elapsed_ms = (current_tick - int(input_tick)) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def recent_resume_event(max_age_seconds: int = 600) -> dict | None:
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(
            ["wevtutil.exe", "qe", "System",
             "/q:*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and (EventID=1)]]",
             "/c:1", "/rd:true", "/f:xml"],
            capture_output=True, timeout=10,
        )
        value = completed.stdout.decode("utf-8-sig", errors="replace").strip()
        if completed.returncode != 0 or not value:
            detail = completed.stderr.decode("utf-8", errors="replace").strip() or "没有返回事件数据"
            append_log(f"[唤醒诊断] Windows 事件查询无结果（{completed.returncode}）：{detail}")
            return None
        import xml.etree.ElementTree as ET
        root = ET.fromstring(value)
        namespace = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
        created = root.find("e:System/e:TimeCreated", namespace)
        if created is None:
            return None
        system_time = created.attrib["SystemTime"]
        if system_time.endswith("Z") and "." in system_time:
            base, fraction = system_time[:-1].split(".", 1)
            fraction = (fraction + "000000")[:6]
            resumed = datetime.fromisoformat(f"{base}.{fraction}+00:00")
        else:
            resumed = datetime.fromisoformat(system_time.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - resumed).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return None
        values = []
        for item in root.findall("e:EventData/e:Data", namespace):
            if item.text:
                values.append(item.text)
        message = " ".join(values)
        return {
            "age_seconds": age,
            "bacoffee_wake": "BACoffee-Cafe" in message,
            "message": " ".join(message.split()),
        }
    except Exception as exc:
        append_log("[唤醒诊断] 读取 Windows 唤醒事件失败：" + str(exc))
        return None


def run_cafe_with_recovery(thread_obj, cafe_reward) -> bool:
    """Run cafe directly with bounded, cafe-only unknown-screen recovery."""
    from core import picture
    from core.exception import FunctionCallTimeout

    original_co_detect = picture.co_detect
    had_attempt = "bacoffee_cafe_attempt" in vars(thread_obj)
    original_attempt = getattr(thread_obj, "bacoffee_cafe_attempt", None)
    run_result = getattr(thread_obj, "bacoffee_run_result", None)

    def resilient_co_detect(*args, **kwargs):
        requested_timeout = min(int(kwargs.get("time_out", 600)), 120)
        invitation_deadline = getattr(thread_obj, "bacoffee_invitation_deadline", None)
        if invitation_deadline is not None:
            remaining = invitation_deadline - time.monotonic()
            if remaining <= 0:
                raise FunctionCallTimeout("BACoffee invitation budget reached.")
            requested_timeout = min(requested_timeout, max(1, int(remaining)))
        kwargs["time_out"] = requested_timeout
        kwargs.setdefault("tentative_click", True)
        kwargs.setdefault("tentative_x", 1238)
        kwargs.setdefault("tentative_y", 45)
        kwargs.setdefault("max_fail_cnt", 18)
        return original_co_detect(*args, **kwargs)

    for attempt in range(1, 3):
        thread_obj.bacoffee_cafe_attempt = attempt
        picture.co_detect = resilient_co_detect
        try:
            append_log(f"[咖啡厅恢复] 第 {attempt}/2 次执行；未知画面将尝试点击右上角退出。")
            return bool(cafe_reward.implement(thread_obj))
        except (FunctionCallTimeout, CafeInvitationRecoveryRequired) as exc:
            append_log(f"[咖啡厅恢复] 第 {attempt}/2 次需要恢复：{exc}")
            if attempt >= 2:
                return False
            picture.co_detect = original_co_detect
            if isinstance(run_result, dict):
                run_result["cafe_recovery_count"] = 1
            append_log("[咖啡厅恢复] 正在重启游戏后进行最后一次重试。")
            if not thread_obj.solve("restart"):
                append_log("[咖啡厅恢复] 游戏重启失败，取消本轮咖啡厅任务。")
                return False
        finally:
            picture.co_detect = original_co_detect
            if had_attempt:
                thread_obj.bacoffee_cafe_attempt = original_attempt
            elif "bacoffee_cafe_attempt" in vars(thread_obj):
                del thread_obj.bacoffee_cafe_attempt
            if getattr(thread_obj, "bacoffee_invitation_deadline", None) is not None:
                thread_obj.bacoffee_invitation_deadline = None
    return False


def collect_power_diagnostics() -> dict:
    """Capture local Windows power evidence without changing system state."""
    diagnostic = {"captured_at": datetime.now().isoformat(timespec="seconds"), "commands": {}}
    output_encoding = (
        f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else "utf-8")
    for label, arguments in (
        ("requests", ["/requests"]),
        ("waketimers", ["/waketimers"]),
        ("lastwake", ["/lastwake"]),
        ("capabilities", ["/a"]),
    ):
        try:
            completed = subprocess.run(
                ["powercfg.exe", *arguments], capture_output=True,
                encoding=output_encoding, errors="replace", timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = (completed.stdout or completed.stderr or "").strip()
            diagnostic["commands"][label] = {
                "exit_code": completed.returncode,
                "output": output,
            }
            compact = " | ".join(line.strip() for line in output.splitlines() if line.strip())
            append_log(
                f"[power-diag] powercfg {arguments[0]} exit={completed.returncode}; "
                f"详见 {POWER_DIAGNOSTIC_FILE.name}")
        except Exception as exc:
            diagnostic["commands"][label] = {"exception": repr(exc)}
            append_log(f"[power-diag] powercfg {arguments[0]} exception={exc!r}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POWER_DIAGNOSTIC_FILE.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
    return diagnostic


def collect_resume_diagnostics() -> dict:
    """Record the wake source immediately after SetSuspendState returns."""
    diagnostic = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "commands": {},
    }
    output_encoding = (
        f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else "utf-8")
    commands = (
        ("lastwake", ["powercfg.exe", "/lastwake"]),
        ("waketimers", ["powercfg.exe", "/waketimers"]),
        ("system_power_events", [
            "wevtutil.exe", "qe", "System",
            "/q:*[System[(EventID=1 or EventID=42 or EventID=107 or EventID=506)]]",
            "/rd:true", "/c:16", "/f:text",
        ]),
    )
    for label, command in commands:
        try:
            completed = subprocess.run(
                command, capture_output=True, encoding=output_encoding, errors="replace",
                timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = (completed.stdout or completed.stderr or "").strip()
            diagnostic["commands"][label] = {
                "exit_code": completed.returncode,
                "output": output,
            }
            compact = " | ".join(line.strip() for line in output.splitlines() if line.strip())
            append_log(
                f"[wake] {label} captured immediately after resume; exit={completed.returncode}; "
                f"详见 {RESUME_DIAGNOSTIC_FILE.name}")
        except Exception as exc:
            diagnostic["commands"][label] = {"exception": repr(exc)}
            append_log(f"[wake] {label} capture exception={exc!r}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_DIAGNOSTIC_FILE.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
    return diagnostic


HIBERNATE_HANDOFF_TIMEOUT_SECONDS = 3.0
HIBERNATE_STATUS_TERMINAL_STATES = {
    "cancelled_by_new_worker",
    "cancelled_parent_handoff_timeout",
    "cancelled_parent_dispatch_failed",
    "cancelled_parent_guard_failure",
    "cancelled_stale_request",
    "cancelled_stale_after_resume",
    "guard_acquire_failed",
    "failed",
    "resumed_after_hibernate",
    "fallback_dispatched",
}


def _new_hibernate_request_id() -> str:
    return secrets.token_hex(16)


def _read_hibernate_status() -> dict:
    mutex = _NamedMutex("power-state")
    try:
        if not mutex.acquire(3):
            return {}
        return _read_hibernate_status_unlocked()
    finally:
        mutex.close()


def _read_hibernate_status_unlocked() -> dict:
    try:
        if not HIBERNATE_STATUS_FILE.is_file():
            return {}
        value = json.loads(HIBERNATE_STATUS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_hibernate_status(status: dict) -> None:
    mutex = _NamedMutex("power-state")
    try:
        if not mutex.acquire(3):
            raise TimeoutError("休眠状态锁等待超时")
        _write_hibernate_status_unlocked(status)
    finally:
        mutex.close()


def _write_hibernate_status_unlocked(status: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HIBERNATE_STATUS_FILE.write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_hibernate_status_if_current(request_id: str, **updates) -> bool:
    mutex = _NamedMutex("power-state")
    try:
        if not mutex.acquire(3):
            return False
        status = _read_hibernate_status_unlocked()
        if status.get("request_id") != request_id or str(status.get("state", "")).startswith("cancelled"):
            return False
        status.update(updates)
        _write_hibernate_status_unlocked(status)
        return True
    finally:
        mutex.close()





def invalidate_hibernate_request(reason: str = "new_worker_started") -> None:
    mutex = _NamedMutex("power-state")
    try:
        if not mutex.acquire(3):
            raise TimeoutError("无法安全取消旧休眠请求")
        _invalidate_hibernate_request_unlocked(reason)
    finally:
        mutex.close()


def _invalidate_hibernate_request_unlocked(reason: str = "new_worker_started") -> None:
    """Invalidate a delayed helper before a new worker can start doing work."""
    status = _read_hibernate_status()
    request_id = status.get("request_id")
    if not request_id or status.get("state") in HIBERNATE_STATUS_TERMINAL_STATES:
        return
    status.update(
        state="cancelled_by_new_worker",
        invalidated_at=datetime.now().isoformat(timespec="seconds"),
        invalidation_reason=reason,
    )
    _write_hibernate_status(status)
    append_log(
        f"[power] invalidated pending hibernate request; request_id={request_id}; "
        f"reason={reason}.")


def request_hibernate(delay_seconds: int = 5) -> bool:
    """Dispatch a helper and wait until it owns the wake lock before returning."""
    worker_checkpoint()
    expected_delay = max(1, delay_seconds)
    request_id = _new_hibernate_request_id()
    _write_hibernate_status({
        "request_id": request_id,
        "started": datetime.now().isoformat(timespec="seconds"),
        "state": "dispatching_helper",
        "delay_seconds": expected_delay,
    })
    command = [
        sys.executable, "-X", "utf8", str(APP_DIR / "BACoffee.py"),
        "--hibernate-helper", "--hibernate-delay", str(expected_delay),
        "--hibernate-request-id", request_id,
    ]
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | (getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000) if _WORKER_TOKEN else 0)
    )
    for attempt in range(1, 4):
        try:
            process = subprocess.Popen(
                command, cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=creationflags,
            )
            append_log(
                f"[power] hibernate helper dispatched; pid={process.pid}; "
                f"attempt={attempt}; delay={expected_delay}s; request_id={request_id}.")
            handoff_deadline = time.monotonic() + HIBERNATE_HANDOFF_TIMEOUT_SECONDS
            while time.monotonic() < handoff_deadline:
                status = _read_hibernate_status()
                if status.get("request_id") != request_id:
                    append_log(
                        f"[power] hibernate handoff cancelled by request replacement; "
                        f"request_id={request_id}.")
                    return False
                state = status.get("state")
                if state == "guard_acquired":
                    append_log(
                        f"[power] hibernate helper guard acquired; request_id={request_id}.")
                    return True
                if state in HIBERNATE_STATUS_TERMINAL_STATES:
                    if state == "guard_acquire_failed":
                        _update_hibernate_status_if_current(
                            request_id,
                            state="cancelled_parent_guard_failure",
                            helper_failure_state=state,
                            cancelled_at=datetime.now().isoformat(timespec="seconds"),
                            message="helper could not acquire its execution-state lock",
                        )
                    append_log(
                        f"[power] hibernate helper handoff failed; state={state}; "
                        f"request_id={request_id}.")
                    return False
                worker_sleep(0.1)
            _update_hibernate_status_if_current(
                request_id,
                state="cancelled_parent_handoff_timeout",
                cancelled_at=datetime.now().isoformat(timespec="seconds"),
                message="helper did not acquire its execution-state lock in time",
            )
            append_log(
                f"[power] hibernate helper did not acquire its guard within "
                f"{HIBERNATE_HANDOFF_TIMEOUT_SECONDS:.1f}s; request cancelled; "
                f"request_id={request_id}.")
            return False
        except Exception as exc:
            winerror = getattr(exc, "winerror", None)
            last_error = ctypes.get_last_error() if os.name == "nt" else 0
            append_log(
                f"[power] hibernate helper dispatch failed; attempt={attempt}/3; "
                f"winerror={winerror}; GetLastError={last_error}; exception={exc!r}.")
            if attempt < 3:
                worker_sleep(3)
    _update_hibernate_status_if_current(
        request_id,
        state="cancelled_parent_dispatch_failed",
        cancelled_at=datetime.now().isoformat(timespec="seconds"),
        message="helper process could not be dispatched",
    )
    return False


def hibernate_helper(
    delay_seconds: int = 5,
    request_id: str | None = None,
    existing_guard: KeepAwake | None = None,
) -> int:
    """Acquire the helper wake lock, then request hibernation after the wait."""
    expected_delay = max(1, delay_seconds)
    owns_request = request_id is None
    request_id = request_id or _new_hibernate_request_id()
    status = _read_hibernate_status()
    if owns_request:
        status = {
            "request_id": request_id,
            "started": datetime.now().isoformat(timespec="seconds"),
            "state": "waiting_for_guard",
            "delay_seconds": expected_delay,
        }
        _write_hibernate_status(status)
    elif status.get("request_id") != request_id:
        append_log(
            f"[power-helper] stale request ignored before guard acquisition; "
            f"request_id={request_id}.")
        return 0
    elif status.get("state") in HIBERNATE_STATUS_TERMINAL_STATES:
        append_log(
            f"[power-helper] cancelled request ignored before guard acquisition; "
            f"state={status.get('state')}; request_id={request_id}.")
        return 0

    guard = existing_guard or KeepAwake()
    guard_active = bool(getattr(guard, "acquired", False))

    def save_current(**updates) -> bool:
        nonlocal status
        if not _update_hibernate_status_if_current(request_id, **updates):
            return False
        status = _read_hibernate_status()
        return True

    def release_guard() -> None:
        nonlocal guard_active
        if guard_active:
            guard.__exit__(None, None, None)
            guard_active = False

    try:
        try:
            if not guard_active:
                guard.__enter__()
                guard_active = bool(getattr(guard, "acquired", False)) or os.name != "nt"
        except Exception as exc:
            save_current(
                state="guard_acquire_failed",
                message=repr(exc),
                failed_at=datetime.now().isoformat(timespec="seconds"),
            )
            append_log(
                f"[power-helper] execution-state lock acquisition failed; "
                f"request_id={request_id}; exception={exc!r}.")
            return 1

        current = _read_hibernate_status()
        if (current.get("request_id") != request_id
                or current.get("state") in HIBERNATE_STATUS_TERMINAL_STATES):
            append_log(
                f"[power-helper] request cancelled while acquiring guard; "
                f"request_id={request_id}.")
            return 0
        if not save_current(
                state="guard_acquired",
                guard_acquired_at=datetime.now().isoformat(timespec="seconds")):
            append_log(
                f"[power-helper] request replaced during guard acquisition; "
                f"request_id={request_id}.")
            return 0
        append_log(
            f"[power-helper] execution-state lock acquired; waiting "
            f"{expected_delay}s; request_id={request_id}.")
        wait_started_wall = time.time()
        wait_started_monotonic = time.monotonic()
        save_current(
            wait_started_at=datetime.fromtimestamp(
                wait_started_wall).isoformat(timespec="seconds"),
            wait_started_monotonic=wait_started_monotonic,
        )
        worker_sleep(expected_delay)
        actual_elapsed = max(
            0.0,
            time.monotonic() - wait_started_monotonic,
            time.time() - wait_started_wall,
        )
        stale_limit = expected_delay + 30
        if actual_elapsed > stale_limit:
            save_current(
                state="cancelled_stale_after_resume",
                actual_elapsed_seconds=round(actual_elapsed, 3),
                stale_limit_seconds=stale_limit,
                message="sleep wait crossed a long system suspend/resume interval",
                cancelled_at=datetime.now().isoformat(timespec="seconds"),
            )
            append_log(
                "[power-helper] stale hibernate request cancelled after resume; "
                f"expected_delay={expected_delay}s; actual_elapsed={actual_elapsed:.3f}s."
            )
            return 0
        if os.name != "nt":
            save_current(state="failed", message="Windows is required")
            return 1
        try:
            powerprof = ctypes.WinDLL("PowrProf.dll", use_last_error=True)
            set_suspend_state = powerprof.SetSuspendState
            set_suspend_state.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
            set_suspend_state.restype = ctypes.c_bool
        except Exception as exc:
            save_current(state="failed", message=repr(exc))
            append_log(f"[power-helper] failed to load SetSuspendState: {exc!r}.")
            return 1

        for attempt in range(1, 4):
            current = _read_hibernate_status()
            if current.get("request_id") != request_id:
                append_log(
                    f"[power-helper] request invalidated before API; "
                    f"request_id={request_id}.")
                return 0
            ctypes.set_last_error(0)
            if not save_current(
                    state="requesting_hibernate", attempt=attempt,
                    requested_at=datetime.now().isoformat(timespec="seconds")):
                return 0
            append_log(
                f"[power-helper] sleep requested; mode=hibernate; API=SetSuspendState; "
                f"attempt={attempt}/3; request_id={request_id}.")
            current = _read_hibernate_status()
            if (current.get("request_id") != request_id
                    or current.get("state") in HIBERNATE_STATUS_TERMINAL_STATES):
                append_log(
                    f"[power-helper] request invalidated immediately before API; "
                    f"request_id={request_id}.")
                return 0
            release_guard()
            with _HelperAction() as allowed:
                if not allowed:
                    save_current(state="cancelled_by_new_worker")
                    return 0
                succeeded = bool(set_suspend_state(True, False, False))
            last_error = ctypes.get_last_error()
            save_current(
                api_result=succeeded, last_error=last_error,
                returned_at=datetime.now().isoformat(timespec="seconds"))
            if succeeded:
                # A successful SetSuspendState normally returns only after the next resume.
                save_current(
                    state="resumed_after_hibernate",
                    resumed_at=datetime.now().isoformat(timespec="seconds"))
                collect_resume_diagnostics()
                append_log(
                    f"[power-helper] sleep API returned after resume; result=True; "
                    f"GetLastError={last_error}; helper exiting without another sleep request.")
                return 0
            append_log(
                f"[power-helper] sleep API result=False; GetLastError={last_error}; "
                f"attempt={attempt}/3; request_id={request_id}.")
            save_current()
            if attempt < 3:
                guard = KeepAwake()
                try:
                    guard.__enter__()
                    guard_active = bool(getattr(guard, "acquired", False))
                except Exception as exc:
                    save_current(
                        state="guard_acquire_failed",
                        message=repr(exc),
                        failed_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    append_log(
                        f"[power-helper] retry guard acquisition failed; "
                        f"request_id={request_id}; exception={exc!r}.")
                    return 1
                save_current(
                    state="retry_guard_acquired",
                    guard_acquired_at=datetime.now().isoformat(timespec="seconds"))
                worker_sleep(5)
        append_log(
            "[power-helper] SetSuspendState failed after 3 attempts; "
            "dispatching shutdown.exe /h once as a non-blocking fallback.")
        try:
            fallback_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            with _HelperAction() as allowed:
                if not allowed:
                    save_current(state="cancelled_by_new_worker")
                    return 0
                fallback = subprocess.Popen(
                    ["shutdown.exe", "/h"], cwd=str(APP_DIR),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True, creationflags=fallback_flags,
                )
                # Keep the execution fence until the owned fallback finishes.
                try:
                    fallback.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    fallback.terminate()
                    fallback.wait(timeout=3)
            save_current(
                state="fallback_dispatched", fallback="shutdown.exe /h",
                fallback_pid=fallback.pid)
            append_log(
                f"[power-helper] shutdown.exe fallback dispatched once; pid={fallback.pid}; "
                "helper exiting without waiting across hibernation.")
            return 0
        except Exception as exc:
            winerror = getattr(exc, "winerror", None)
            last_error = ctypes.get_last_error()
            save_current(
                state="failed", message=repr(exc), winerror=winerror,
                last_error=last_error)
            append_log(
                f"[power-helper] hibernate failed; fallback dispatch error={exc!r}; "
                f"winerror={winerror}; GetLastError={last_error}; no further retries.")
            return 1
    finally:
        release_guard()


def dispatch_timeout_recovery(hibernate_after_cleanup: bool) -> bool:
    """Start recovery outside the timed-out scheduled worker process."""
    command = [
        sys.executable, "-X", "utf8", str(APP_DIR / "BACoffee.py"),
        "--timeout-recovery-helper",
    ]
    if hibernate_after_cleanup:
        command.append("--hibernate-after-timeout")
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | (getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000) if _WORKER_TOKEN else 0)
    )
    for attempt in range(1, 4):
        try:
            process = subprocess.Popen(
                command, cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=creationflags,
            )
            append_log(
                f"[watchdog] timeout recovery dispatched; pid={process.pid}; "
                f"hibernate_after_cleanup={hibernate_after_cleanup}; attempt={attempt}.")
            return True
        except Exception as exc:
            append_log(
                f"[watchdog] recovery dispatch failed; attempt={attempt}/3; "
                f"winerror={getattr(exc, 'winerror', None)}; "
                f"GetLastError={ctypes.get_last_error() if os.name == 'nt' else 0}; "
                f"exception={exc!r}.")
            if attempt < 3:
                worker_sleep(3)
    return False


def timeout_recovery_helper(hibernate_after_cleanup: bool) -> int:
    with _HelperAction() as allowed:
        if not allowed:
            append_log("[watchdog-helper] 旧任务恢复请求已失效，不清理 MuMu、不休眠。")
            return 0
        return _timeout_recovery_owned(hibernate_after_cleanup)


def _timeout_recovery_owned(hibernate_after_cleanup: bool) -> int:
    """Clean a timed-out task after its worker exits, then optionally hibernate."""
    power_guard = KeepAwake()
    power_guard.__enter__()
    try:
        append_log("[watchdog-helper] waiting 5s for the timed-out worker to exit.")
        if not _helper_wait(5):
            append_log("[watchdog-helper] 手动请求到达，取消延迟清理并保留 MuMu。")
            return 0
        settings = load_settings()
        try:
            timed_out_result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            if timed_out_result.get("worker_token") == os.environ.get("BACOFFEE_WORKER_TOKEN"):
                _stop_owned_ocr(timed_out_result)
            else:
                append_log("[watchdog-helper] 忽略不属于本轮的 OCR 记录。")
        except Exception as exc:
            append_log(f"[watchdog-helper] OCR cleanup note: {exc!r}.")
        try:
            if not _helper_current():
                return 0
            append_log("[watchdog-helper] closing MuMu started by the timed-out task.")
            close_mumu_completely(settings)
            append_log("[watchdog-helper] MuMu cleanup completed.")
        except Exception as exc:
            append_log(f"[watchdog-helper] MuMu cleanup failed: {exc!r}.")
        if not hibernate_after_cleanup:
            append_log("[watchdog-helper] user activity was detected; keeping Windows awake.")
            return 0
        append_log("[watchdog-helper] no user activity detected; preparing to hibernate after timeout.")
        collect_power_diagnostics()
        return hibernate_helper(3, existing_guard=power_guard)
    finally:
        power_guard.__exit__(None, None, None)


MUMU_INTERACTIVE_PROCESS_NAMES = {
    "mumunxmain.exe",
    "mumunxdevice.exe",
    "mumunxheadless.exe",
    "mumuplayer.exe",
    "mumuvmmheadless.exe",
}


def active_mumu_user_processes() -> list[str]:
    """Return visible/runtime MuMu processes that indicate an existing user session."""
    try:
        import psutil
        found = {
            str(process.info.get("name") or "")
            for process in psutil.process_iter(["name"])
            if str(process.info.get("name") or "").lower() in MUMU_INTERACTIVE_PROCESS_NAMES
        }
        return sorted(found, key=str.lower)
    except Exception as exc:
        append_log(f"[用户使用保护] 检测 MuMu 进程时出现提示：{exc}")
        return []


def confirm_scheduled_run_with_active_mumu(processes: list[str]) -> bool:
    """Ask before taking over an existing MuMu session; only explicit No cancels."""
    if os.name != "nt":
        return False
    process_text = "、".join(processes) if processes else "MuMu"
    message = (
        f"检测到模拟器或游戏已经在运行（{process_text}），可能正在被使用。\n\n"
        "是否仍要执行本次咖啡厅任务？\n\n"
        "选择“否”将跳过本次任务；选择“是”将继续执行，且任务结束后会保留模拟器和游戏。\n\n"
        "60 秒内未选择将自动执行本次任务。"
    )
    flags = 0x00000004 | 0x00000020 | 0x00000100 | 0x00010000 | 0x00040000
    try:
        message_box_timeout = ctypes.windll.user32.MessageBoxTimeoutW
        message_box_timeout.restype = ctypes.c_int
        answer = message_box_timeout(
            None, message, "BACoffee 自动任务确认", flags, 0, 60_000)
    except Exception:
        answer = ctypes.windll.user32.MessageBoxW(
            None, message, "BACoffee 自动任务确认", flags)
    # Only an explicit click on No cancels this occurrence. A timeout from
    # MessageBoxTimeoutW (32000), Yes, or any other non-No result continues.
    return answer != 7


GAME_UPDATE_MIN_SCORE = 0.80
SERVER_MAINTENANCE_MIN_SCORE = 0.85
# Current 1280x720 BAAS frames and notice geometry match the supplied crop at 1:1.
SERVER_MAINTENANCE_REFERENCE_WIDTH = 1280


class ServerMaintenanceSkipped(Exception):
    """BAC-only control flow after BAAS has stopped the current startup."""


class GameUpdateDownloadSkipped(Exception):
    """Stop only this startup when automatic game update downloads are disabled."""


def _server_maintenance_matches(frame, template, cache: dict, min_score: float = SERVER_MAINTENANCE_MIN_SCORE, strict: bool = False) -> bool:
    """Inspect only the central notice region of an existing in-memory frame."""
    try:
        if frame is None or template is None or frame.size == 0 or template.size == 0:
            raise ValueError("维护/更新匹配输入不可用")
        if strict and (frame.ndim != 3 or frame.shape[2] not in (3, 4) or template.ndim != 3 or template.shape[2] not in (3, 4)):
            raise ValueError("维护/更新画面格式不可用")
        height, width = frame.shape[:2]
        if cache.get("width") != width:
            # An all-opaque alpha mask is redundant and can yield non-finite
            # OpenCV masked correlations on flat notification backgrounds.
            matched_template = template
            if template.ndim == 3 and template.shape[2] == 4 and (template[:, :, 3] == 255).all():
                matched_template = template[:, :, :3]
            scale = width / SERVER_MAINTENANCE_REFERENCE_WIDTH
            if abs(scale - 1.0) > 0.01:
                cv2 = importlib.import_module("cv2")
                scaled = cv2.resize(matched_template, (
                    max(1, int(matched_template.shape[1] * scale)),
                    max(1, int(matched_template.shape[0] * scale))), interpolation=cv2.INTER_LINEAR)
            else:
                scaled = matched_template
            cache.update(width=width, template=scaled)
        roi = frame[int(height * 0.20):int(height * 0.75),
                    int(width * 0.20):int(width * 0.80)]
        score, _, _ = _bash_template_match(roi, cache["template"], strict=strict)
        if strict and not float("-inf") < score < float("inf"):
            raise ValueError("维护/更新匹配结果不可用")
        return min_score <= score < float("inf")
    except Exception:
        if strict:
            raise
        return False


def _maintenance_state_checkpoint() -> None:
    worker_checkpoint()
    if _WORKER_TOKEN:
        owner = _owner_state()
        if (owner.get("token") != _WORKER_TOKEN or owner.get("finished")
                or owner.get("cancel_requested")):
            raise WorkerCancelled("维护接续状态执行权已失效")


def _read_maintenance_state():
    _maintenance_state_checkpoint()
    path = DATA_DIR / "maintenance_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("schema") != 1
                or not isinstance(state.get("pending"), dict)
                or any(not isinstance(key, str) or not isinstance(value, dict)
                       or not isinstance(value.get("detected_at"), str)
                       for key, value in state["pending"].items())):
            raise ValueError("状态结构无效")
        return state
    except FileNotFoundError:
        return {"schema": 1, "pending": {}}
    except (OSError, ValueError) as exc:
        append_log(f"[维护接续] 状态读取失败，保留原文件，本轮仍检查维护与更新：{exc}")
        return None


def _write_maintenance_state(state) -> None:
    _maintenance_state_checkpoint()
    if state is None:
        raise ValueError("原接续状态不可读，不能安全覆盖")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DATA_DIR / "maintenance_state.json.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _maintenance_state_checkpoint()
        os.replace(temporary, DATA_DIR / "maintenance_state.json")
    finally:
        temporary.unlink(missing_ok=True)



def _restart_with_maintenance_guard(thread_obj, allow_update_download: bool = True,
                                    on_maintenance=None, diagnostics=None):
    """Protect every startup frame and require the original home-page detection."""
    templates = []
    for filename in ("server_maintenance.png", "game_update_required.png"):
        try:
            template = _read_cv_image(UI_ASSET_DIR / filename)
            if template is None or template.size == 0 or template.ndim != 3 or template.shape[2] not in (3, 4):
                raise ValueError("图片无效")
        except Exception as exc:
            raise RuntimeError(f"启动保护图片无法读取：{filename}") from exc
        templates.append(template)
    from core.exception import RequestHumanTakeOver
    original = thread_obj.update_screenshot_array
    original_main = thread_obj.to_main_page
    had_instance_method = "update_screenshot_array" in vars(thread_obj)
    had_instance_main = "to_main_page" in vars(thread_obj)
    callback_name = "_bacoffee_game_update_match"
    had_instance_callback = callback_name in vars(thread_obj)
    original_callback = getattr(thread_obj, callback_name, None)
    before_name = "_bacoffee_game_update_before_confirm"
    had_before = before_name in vars(thread_obj)
    original_before = getattr(thread_obj, before_name, None)
    detected = None
    protection_error = None
    main_error = None
    main_attempted = False
    home_confirmed = False
    frames_checked = 0
    cache, download_cache = {}, {}

    def matches(template, template_cache, threshold):
        nonlocal protection_error
        try:
            return _server_maintenance_matches(
                getattr(thread_obj, "latest_img_array", None), template, template_cache, threshold, strict=True)
        except Exception as exc:
            protection_error = RuntimeError("启动维护/更新检测不可用，已停止本轮游戏操作。")
            thread_obj.flag_run = False
            raise RequestHumanTakeOver(str(protection_error)) from exc

    def match_update_download():
        worker_checkpoint()
        return matches(templates[1], download_cache, GAME_UPDATE_MIN_SCORE)

    def update_and_check(*args, **kwargs):
        nonlocal detected, frames_checked
        if detected or protection_error:
            raise RequestHumanTakeOver("BACoffee startup stopped")
        value = original(*args, **kwargs)
        worker_checkpoint()
        frames_checked += 1
        if matches(templates[0], cache, SERVER_MAINTENANCE_MIN_SCORE):
            worker_checkpoint()
            detected = "maintenance"
            thread_obj.flag_run = False
            if on_maintenance is not None:
                on_maintenance()
            raise RequestHumanTakeOver("BACoffee server maintenance")
        if not allow_update_download and match_update_download():
            worker_checkpoint()
            detected = "update_download"
            thread_obj.flag_run = False
            raise RequestHumanTakeOver("BACoffee update download disabled")
        return value

    def before_download_confirm(image_name):
        nonlocal detected
        _maintenance_state_checkpoint()
        if not allow_update_download:
            detected = "update_download"
            thread_obj.flag_run = False
            raise RequestHumanTakeOver("BACoffee update download disabled")
        worker_sleep(1.0)
        _maintenance_state_checkpoint()
        thread_obj.update_screenshot_array()
        _maintenance_state_checkpoint()
        if match_update_download():
            return True
        from core import image
        return image.compare_image(thread_obj, image_name)

    def confirm_main(*args, **kwargs):
        nonlocal main_attempted, home_confirmed, main_error
        main_attempted = True
        try:
            value = original_main(*args, **kwargs)
            worker_checkpoint()
            home_confirmed = bool(value is not False and frames_checked and thread_obj.flag_run
                                  and not detected and not protection_error and not main_error)
            return value
        except Exception as exc:
            if not detected and not protection_error:
                main_error = exc
            raise

    thread_obj.update_screenshot_array = update_and_check
    thread_obj.to_main_page = confirm_main
    try:
        setattr(thread_obj, before_name, before_download_confirm)
        if allow_update_download:
            setattr(thread_obj, callback_name, match_update_download)
        try:
            value = thread_obj.solve("restart")
            worker_checkpoint()
            if value and not main_attempted and not detected and not protection_error:
                confirm_main()
        except RequestHumanTakeOver:
            if not detected and not protection_error:
                raise
            value = False
    finally:
        if had_before:
            setattr(thread_obj, before_name, original_before)
        elif before_name in vars(thread_obj):
            delattr(thread_obj, before_name)
        if allow_update_download:
            if had_instance_callback:
                setattr(thread_obj, callback_name, original_callback)
            elif callback_name in vars(thread_obj):
                delattr(thread_obj, callback_name)
        if had_instance_main:
            thread_obj.to_main_page = original_main
        else:
            del thread_obj.to_main_page
        if had_instance_method:
            thread_obj.update_screenshot_array = original
        else:
            del thread_obj.update_screenshot_array
        if diagnostics is not None:
            diagnostics["startup_home_confirmed"] = home_confirmed
            diagnostics["startup_frames_checked"] = frames_checked
    worker_checkpoint()
    if protection_error:
        raise protection_error
    if detected == "maintenance":
        raise ServerMaintenanceSkipped()
    if detected == "update_download":
        raise GameUpdateDownloadSkipped()
    if main_error:
        raise main_error
    if value and not home_confirmed:
        raise RuntimeError("启动流程未实际确认游戏主页，已停止本轮任务。")
    return value



def _run_worker_owned(scheduled: bool = False) -> int:
    global _WORKER_BAAS
    worker_checkpoint()
    started_at = datetime.now()
    maintenance_enabled = True
    compatibility = source_compatibility_diagnostic()
    append_log(f"[版本] build_id={compatibility.get('build_id', 'unknown')}; "
               f"source_sha256={compatibility.get('actual_sha256', 'unknown')}; "
               f"app_dir={APP_DIR}; script={Path(__file__).resolve()}")
    if not compatibility.get("ok"):
        append_log(
            "[源码一致性] 失败：旁置 BACoffee.py 与构建输入不一致，拒绝启动 worker；"
            f"actual={compatibility.get('actual_sha256')}; "
            f"expected={compatibility.get('expected_sha256')}."
        )
        return 3
    sanitize_external_runtime()
    settings = load_settings()
    allow_update_download = bool(settings.get("auto_download_game_updates", False))
    invitation_student_snapshot = build_invitation_student_snapshot(settings)
    baas_root = Path(settings["baas_root"]).resolve()
    game_server = normalize_game_server(settings.get("game_server"))
    target_package = package_name_for_server(baas_root, game_server)
    append_log(f"[渠道] 已选择：{game_server}")
    input_tick_at_start = last_input_tick()
    input_idle_at_start = input_idle_seconds(input_tick_at_start)
    resume_event = recent_resume_event() if scheduled else None
    resume_age = resume_event["age_seconds"] if resume_event else None
    wake_by_task = bool(resume_event and resume_event["bacoffee_wake"])
    result = {
        "started": started_at.isoformat(timespec="seconds"),
        "maintenance_detection_enabled": maintenance_enabled,
        "state": "running",
        "scheduled": scheduled,
        "worker_token": _WORKER_TOKEN,
        "build_id": compatibility.get("build_id"),
        "source_sha256": compatibility.get("actual_sha256"),
        "app_dir": str(APP_DIR),
        "script_path": str(Path(__file__).resolve()),
        "cafe_recovery_count": 0,
        "recent_resume_seconds": resume_age,
        "wake_event_from_bacoffee_task": wake_by_task,
        "last_input_tick_at_start": input_tick_at_start,
        "input_idle_seconds_at_start": input_idle_at_start,
        "success": False,
        "stages": {},
        "invitation_student_snapshot": {
            str(cafe_no): list(names)
            for cafe_no, names in invitation_student_snapshot.items()
        },
    }
    maintenance_state = _read_maintenance_state()
    maintenance_scope = f"{game_server}:{int(settings.get('mumu_vm_index', 1))}"
    pending_at_start = (maintenance_scope in maintenance_state["pending"]
                        if maintenance_state is not None else None)
    result.update(maintenance_pending_at_start=pending_at_start, startup_home_confirmed=False,
                  maintenance_state_readable=maintenance_state is not None,
                  maintenance_state_saved=False, maintenance_state_cleared=False)
    if pending_at_start:
        append_log("[维护接续] 检测到上次维护记录，本次继续检查维护与更新。")

    def save_maintenance_pending():
        _maintenance_state_checkpoint()
        if maintenance_state is not None:
            maintenance_state["pending"][maintenance_scope] = {
                "detected_at": datetime.now().isoformat(timespec="seconds")}
        try:
            _write_maintenance_state(maintenance_state)
            result["maintenance_state_saved"] = True
        except WorkerCancelled:
            raise
        except Exception as exc:
            result["maintenance_state_error"] = str(exc)

    run_started = time.perf_counter()
    preserve_existing_mumu = os.environ.get("BACOFFEE_HANDOFF_PRESERVE") == "1"

    if scheduled:
        settle_seconds = min(30, max(5, int(settings.get("wake_stabilization_seconds", 10))))
        append_log(f"[wake] waiting {settle_seconds}s for Windows, storage, network, and services to settle.")
        hold_wake_stabilization(settle_seconds)
        required_paths_ready = baas_root.is_dir() and Path(settings.get("baas_python", "")).is_file()
        result["wake_stabilization_seconds"] = settle_seconds
        result["required_paths_ready"] = required_paths_ready
        append_log(
            f"[wake] system ready check completed; required_paths="
            f"{'ready' if required_paths_ready else 'missing'}.")
        if not required_paths_ready:
            append_log("[wake] required runtime paths are unavailable; task will fail into normal cleanup.")
        existing_mumu = active_mumu_user_processes()
        result["preexisting_mumu_processes"] = existing_mumu
        if existing_mumu:
            append_log(
                "[用户使用保护] 计划任务启动前检测到 MuMu 已在运行："
                + "、".join(existing_mumu))
            if not confirm_scheduled_run_with_active_mumu(existing_mumu):
                result.update({
                    "success": True,
                    "state": "cancelled",
                    "skipped": True,
                    "skip_reason": "MuMu 已在使用，用户明确选择否",
                    "message": "检测到 MuMu 正在使用，本次计划任务已跳过。",
                    "finished": datetime.now().isoformat(timespec="seconds"),
                    "total_seconds": round(time.perf_counter() - run_started, 3),
                })
                append_log("[用户使用保护] 用户明确选择不执行；本次任务已跳过，MuMu 保持原状。")
                RESULT_FILE.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                return 0
            preserve_existing_mumu = True
            result["preserve_existing_mumu"] = True
            append_log("[用户使用保护] 用户确认继续；任务结束后将保留模拟器和游戏。")

    watchdog_cancel = threading.Event()
    watchdog_thread = None
    if scheduled:
        timeout_minutes = min(
            120, max(5, int(settings.get("scheduled_task_timeout_minutes", 30))))
        result["scheduled_task_timeout_minutes"] = timeout_minutes

        def timeout_watchdog():
            if watchdog_cancel.wait(timeout_minutes * 60):
                return
            if _WORKER_CANCELLED.is_set() or _manual_pending():
                return
            end_tick = last_input_tick()
            end_idle = input_idle_seconds(end_tick)
            user_interacted_at_timeout = bool(
                input_tick_at_start and end_tick != input_tick_at_start)
            input_available_at_timeout = (
                input_idle_at_start is not None and end_idle is not None)
            unattended_at_timeout = bool(
                not user_interacted_at_timeout and (
                    wake_by_task or (
                        input_available_at_timeout
                        and input_idle_at_start >= 600 and end_idle >= 600)))
            result.update(
                state="timed_out", success=False,
                message=f"计划任务执行超过 {timeout_minutes} 分钟，已强制结束",
                timed_out=True,
                timed_out_at=datetime.now().isoformat(timespec="seconds"),
                total_seconds=round(time.perf_counter() - run_started, 3),
                user_interacted_at_timeout=user_interacted_at_timeout,
                unattended_at_timeout=unattended_at_timeout,
            )
            append_log(
                f"[watchdog] task exceeded {timeout_minutes} minutes; "
                f"active_stage={result.get('active_stage', 'unknown')}; "
                f"user_interacted={user_interacted_at_timeout}; "
                f"unattended={unattended_at_timeout}.")
            try:
                RESULT_FILE.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                append_log(f"[watchdog] failed to persist timeout result: {exc!r}.")
            if preserve_existing_mumu:
                append_log(
                    "[watchdog] pre-existing MuMu session is protected; "
                    "leaving MuMu and Windows awake.")
            else:
                should_sleep_after_timeout = bool(
                    settings.get("hibernate_after_success") and unattended_at_timeout)
                dispatch_timeout_recovery(should_sleep_after_timeout)
            append_log("[watchdog] terminating timed-out worker so later schedules are not blocked.")
            os._exit(124)

        watchdog_thread = threading.Thread(
            target=timeout_watchdog, name="BACoffeeScheduledWatchdog", daemon=True)
        watchdog_thread.start()
        append_log(f"[watchdog] scheduled task deadline armed: {timeout_minutes} minutes.")

    def run_stage(name, function):
        started = time.perf_counter()
        result["active_stage"] = name
        worker_checkpoint()
        if name in {"启动所选 MuMu 实例", "启动游戏并进入主界面", "执行咖啡厅摸头与邀请"}:
            append_log(f"[阶段] {name}：开始。")
        try:
            value = function()
            worker_checkpoint()
        except (ServerMaintenanceSkipped, GameUpdateDownloadSkipped) as exc:
            result["stages"][name] = {
                "seconds": round(time.perf_counter() - started, 3),
                "status": "skipped_update_download" if isinstance(exc, GameUpdateDownloadSkipped) else "skipped_maintenance"}
            raise
        except WorkerCancelled:
            result["stages"][name] = {"seconds": round(time.perf_counter() - started, 3), "status": "interrupted"}
            raise
        except Exception as exc:
            elapsed = time.perf_counter() - started
            result["stages"][name] = {"seconds": round(elapsed, 3), "status": "exception"}
            append_log(f"[阶段] {name}：异常（{elapsed:.1f} 秒）：{exc}")
            raise
        elapsed = time.perf_counter() - started
        status = "returned_false" if value is False else "completed"
        result["stages"][name] = {"seconds": round(elapsed, 3), "status": status}
        RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        append_log(
            f"[阶段] {name}：{'返回失败' if value is False else '完成'}（{elapsed:.1f} 秒）。")
        return value

    old_cwd = Path.cwd()
    main_instance = None
    thread_obj = None
    power_guard = None
    try:
        append_log(f"[task] task started; state=running; mode={'scheduled' if scheduled else 'manual'}; instance={settings.get('mumu_vm_index', 1)}.")
        power_guard = KeepAwake()
        power_guard.__enter__()
        append_log(
            "[启动诊断] "
            f"模式={'计划任务' if scheduled else '手动执行'}；"
            f"成功后休眠={'开启' if settings.get('hibernate_after_success') else '关闭'}；"
            f"最近唤醒={f'{resume_age:.1f} 秒前' if resume_age is not None else '未检测到'}；"
            f"输入计时={input_tick_at_start}；"
            f"启动时空闲={f'{input_idle_at_start:.1f} 秒' if input_idle_at_start is not None else '不可读取'}。")
        # Check the selected runtime/templates before starting any instance.
        for template in ("config.json", "event.json"):
            if not (baas_root / "config" / "cn" / template).is_file():
                raise FileNotFoundError(f"找不到 BAAS 配置模板：{template}")
        def query_stage(query):
            value = run_stage("查询所选 MuMu ADB 端口", query)
            result["active_stage"] = "启动所选 MuMu 实例"
            return value
        port_result = {}
        if not run_stage(
            "启动所选 MuMu 实例",
            lambda: launch_selected_mumu(settings, port_result=port_result, query_callback=query_stage),
        ):
            raise RuntimeError("所选 MuMu 实例未能启动或 ADB 端口未就绪。")
        profile = run_stage("生成 BACoffee 独立配置", lambda: prepare_profile(settings, port_result))
        run_stage("写入已验证 MuMu ADB 端口", lambda: update_profile_adb_port(profile, port_result))
        site_packages = add_runtime_site_packages(baas_root)
        sys.path.insert(0, str(baas_root))
        os.chdir(baas_root)
        import_started = time.perf_counter()
        result["active_stage"] = "加载 BAAS 模块"
        from main import Main
        from core.config.config_set import ConfigSet
        from module import cafe_reward
        from core.utils import Logger
        install_baas_operation_info_filter(Logger)
        # BAAS owns several asynchronous control paths. Guard the shared classes
        # before init_all_data creates their instances; no third-party file changes.
        from core.device.Control import Control
        from core.device.Screenshot import Screenshot
        from core.device.connection import Connection
        for target, names in (
            (Control, ("click", "swipe", "long_click", "scroll")),
            (Screenshot, ("screenshot",)),
            (Connection, ("launch_app", "close_current_app", "get_current_package", "start_app")),
        ):
            for method in names:
                original = getattr(target, method, None)
                if callable(original):
                    def guarded(*args, _original=original, **kwargs):
                        worker_checkpoint()
                        value = _original(*args, **kwargs)
                        worker_checkpoint()
                        return value
                    setattr(target, method, guarded)
        import_elapsed = time.perf_counter() - import_started
        result["stages"]["加载 BAAS 模块"] = {"seconds": round(import_elapsed, 3), "status": "completed"}
        append_log(f"[阶段] 加载 BAAS 模块：完成（{import_elapsed:.1f} 秒）。")

        # Replace the whole cafe coordinator so invitation and interaction are
        # explicit serial stages in each cafe. BAAS remains responsible for
        # navigation and affection interaction only.
        cafe_reward.implement = bash_cafe_implement

        main_instance = run_stage("启动 BAAS 与 OCR 服务", lambda: Main(ocr_needed=["zh-cn", "en-us"]))
        if main_instance.ocr is None:
            raise RuntimeError("BAAS OCR 服务启动失败。请确认 BAAS 目录可写，并查看 BAAS OCR 日志。")
        if main_instance.ocr.client.server_process is not None:
            result["ocr_server_pid"] = main_instance.ocr.client.server_process.pid
            import psutil
            result["ocr_server_created"] = psutil.Process(result["ocr_server_pid"]).create_time()
            result["ocr_server_exe"] = str(main_instance.ocr.client.exe_path)
            RESULT_FILE.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        config_set = run_stage("读取任务配置", lambda: ConfigSet(str(profile)))
        thread_obj = run_stage(
            "创建 BAAS 任务线程", lambda: main_instance.get_thread(config_set, name="BACoffee"))
        _WORKER_BAAS = thread_obj
        _guard_baas_actions(thread_obj)
        thread_obj.bacoffee_student_snapshot = invitation_student_snapshot
        thread_obj.bacoffee_invitation_results = []
        thread_obj.bacoffee_run_result = result
        append_log(
            "[邀请快照] worker 已固定学生选择："
            + json.dumps(result["invitation_student_snapshot"], ensure_ascii=False, separators=(",", ":")))
        if not run_stage("连接并初始化 MuMu 模拟器", thread_obj.init_all_data):
            package_text = target_package or "未知包名"
            raise RuntimeError(
                f"所选{game_server}未安装或连接初始化失败（目标包：{package_text}），"
                "请检查模拟器中是否已安装所选游戏渠道。")
        _guard_baas_actions(thread_obj)
        detected_package = str(getattr(thread_obj, "package_name", target_package or "未知包名"))
        append_log(f"[渠道] BAAS 模式=CN；目标包={detected_package}")
        if scheduled and not wake_by_task:
            delayed_event = recent_resume_event(900)
            if delayed_event:
                resume_event = delayed_event
                resume_age = delayed_event["age_seconds"]
                wake_by_task = bool(delayed_event["bacoffee_wake"])
                result["recent_resume_seconds"] = resume_age
                result["wake_event_from_bacoffee_task"] = wake_by_task
                append_log(
                    "[启动诊断] 延迟复查到 Windows 唤醒事件："
                    f"{resume_age:.1f} 秒前；来源包含 BACoffee-Cafe={'是' if wake_by_task else '否'}。")
        if not run_stage("启动游戏并进入主界面", lambda: _restart_with_maintenance_guard(thread_obj, allow_update_download, save_maintenance_pending, result)):
            raise RuntimeError("游戏启动或进入主界面失败。")
        if allow_update_download and result["startup_home_confirmed"] and pending_at_start:
            _maintenance_state_checkpoint()
            updated_state = dict(maintenance_state, pending=dict(maintenance_state["pending"]))
            del updated_state["pending"][maintenance_scope]
            try:
                _write_maintenance_state(updated_state)
                result["maintenance_state_cleared"] = True
            except WorkerCancelled:
                raise
            except Exception as exc:
                result["maintenance_state_error"] = str(exc)
                raise RuntimeError("已确认主页，但维护接续状态清除失败；原记录保留。") from exc
        if not run_stage("执行咖啡厅摸头与邀请", lambda: run_cafe_with_recovery(thread_obj, cafe_reward)):
            raise RuntimeError("咖啡厅任务未能完成。")
        if preserve_existing_mumu:
            result["stages"]["通知 BAAS 关闭模拟器"] = {
                "seconds": 0.0, "status": "skipped_for_existing_user_session"}
            append_log("[用户使用保护] 跳过 BAAS 关闭模拟器步骤。")
        else:
            run_stage("通知 BAAS 关闭模拟器", thread_obj.exit_emulator)
        result["success"] = True
        result["state"] = "success"
        result["message"] = (
            "咖啡厅任务完成；检测到任务前已有 MuMu 会话，模拟器和游戏已保留。"
            if preserve_existing_mumu else "咖啡厅任务完成，模拟器已关闭。")
        append_log(result["message"])
        return_code = 0
    except GameUpdateDownloadSkipped:
        result.update(success=False, state="skipped_update_download", skipped=True,
                      skip_reason="update_download_disabled", message="更新下载跳过")
        return_code = 0
        append_log("[游戏更新] 已关闭自动下载，本次任务跳过；后续计划保留。")
    except ServerMaintenanceSkipped:
        result.update(success=False, state="skipped_maintenance", skipped=True,
                      skip_reason="server_maintenance", message="维护跳过")
        return_code = 0
        if result["maintenance_state_saved"]:
            append_log("[维护] 检测到服务器维护，本次任务跳过；已保存待更新状态，后续任务继续检查。")
        else:
            append_log("[维护] 维护已停止，接续状态保存失败；后续计划保留。" + result.get("maintenance_state_error", ""))
    except WorkerCancelled:
        preserve_existing_mumu = True
        result.update(success=False, state="interrupted", message="被手动任务接管")
        return_code = 0
        append_log("[任务互斥] 被手动任务接管，停止本轮自动执行并保留 MuMu。")
    except Exception as exc:
        import traceback
        result["message"] = str(exc)
        result["state"] = "failed"
        result["traceback"] = traceback.format_exc()
        append_log("任务失败：" + str(exc))
        return_code = 1
    finally:
        watchdog_cancel.set()
        if _WORKER_CANCELLED.is_set():
            preserve_existing_mumu = True
            result.update(success=False, state="interrupted", message="被手动任务接管")
        try:
            if (main_instance is not None and main_instance.ocr is not None
                    and main_instance.ocr.client.server_process is not None):
                main_instance.ocr.client.stop_server()
        except Exception as exc:
            append_log("关闭 OCR 服务时出现提示：" + str(exc))
        if preserve_existing_mumu:
            result["stages"]["清理 MuMu 虚拟机与主程序"] = {
                "seconds": 0.0, "status": "skipped_for_existing_user_session"}
            append_log("[用户使用保护] 已跳过 MuMu 清理；任务前已有的模拟器和游戏保持运行。")
        else:
            try:
                close_started = time.perf_counter()
                append_log("[阶段] 清理 MuMu 虚拟机与主程序：开始。")
                worker_checkpoint()
                close_mumu_completely(settings)
                close_elapsed = time.perf_counter() - close_started
                result["stages"]["清理 MuMu 虚拟机与主程序"] = {
                    "seconds": round(close_elapsed, 3), "status": "completed"}
                append_log(f"MuMu 虚拟机和主程序均已关闭（清理耗时 {close_elapsed:.1f} 秒）。")
            except WorkerCancelled:
                preserve_existing_mumu = True
                result.update(success=False, state="interrupted", message="被手动任务接管")
            except Exception as exc:
                append_log("关闭 MuMu 时出现提示：" + str(exc))
        try:
            os.chdir(old_cwd)
        except Exception as exc:
            append_log("恢复主程序工作目录时出现提示：" + str(exc))
        watchdog_cancel.set()
        result["finished"] = datetime.now().isoformat(timespec="seconds")
        result["total_seconds"] = round(time.perf_counter() - run_started, 3)
        append_log(
            f"[task] task finished; state={result['state']}; exit_code={return_code}; "
            f"elapsed={result['total_seconds']:.1f}s; cafe_recovery_count={result.get('cafe_recovery_count', 0)}.")
        RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if _WORKER_CANCELLED.is_set():
        if power_guard is not None:
            power_guard.__exit__(None, None, None)
        worker_checkpoint()
    input_tick_at_end = last_input_tick()
    input_idle_at_end = input_idle_seconds(input_tick_at_end)
    if scheduled and not wake_by_task:
        final_event = recent_resume_event(max(900, int(result.get("total_seconds", 0)) + 900))
        if final_event:
            resume_event = final_event
            resume_age = final_event["age_seconds"]
            wake_by_task = bool(final_event["bacoffee_wake"])
            result["recent_resume_seconds"] = resume_age
            result["wake_event_from_bacoffee_task"] = wake_by_task
    user_interacted = bool(input_tick_at_start and input_tick_at_end != input_tick_at_start)
    result["last_input_tick_at_end"] = input_tick_at_end
    result["input_idle_seconds_at_end"] = input_idle_at_end
    result["user_interacted_during_run"] = user_interacted
    input_available = input_idle_at_start is not None and input_idle_at_end is not None
    unattended = bool(
        not user_interacted and (
            wake_by_task
            or (input_available and input_idle_at_start >= 600 and input_idle_at_end >= 600)
        )
    )
    result["unattended"] = unattended
    should_hibernate = bool(
        scheduled and settings.get("hibernate_after_success")
        and unattended and not preserve_existing_mumu and not _WORKER_CANCELLED.is_set()
    )
    result["will_hibernate"] = should_hibernate
    append_log(
        "[休眠诊断] "
        f"计划任务唤醒={'是' if wake_by_task else '否'}；"
        f"结束时空闲={f'{input_idle_at_end:.1f} 秒' if input_idle_at_end is not None else '不可读取'}；"
        f"执行期间键鼠变化={'是' if user_interacted else '否'}；"
        f"无人使用判定={'是' if unattended else '否'}；"
        f"任务结果={'维护跳过' if result['state'] == 'skipped_maintenance' else ('更新下载跳过' if result['state'] == 'skipped_update_download' else ('成功' if result['success'] else '失败'))}；"
        f"将休眠={'是' if should_hibernate else '否'}；"
        f"总耗时={result['total_seconds']:.1f} 秒。")
    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if should_hibernate:
        append_log("计划任务执行期间未检测到使用；完成后回到休眠。")
        append_log("[power] execution-state lock is retained through final I/O and helper handoff.")
        result["power_state"] = "stabilizing_before_hibernate"
        RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        worker_sleep(3)
        collect_power_diagnostics()
        result["power_state"] = "dispatching_hibernate_helper"
        result["hibernate_dispatch_succeeded"] = request_hibernate(5)
        result["power_state"] = (
            "hibernate_helper_dispatched" if result["hibernate_dispatch_succeeded"]
            else "hibernate_dispatch_failed")
        RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif scheduled:
        if preserve_existing_mumu:
            reason = "任务启动前检测到已有 MuMu 会话并经用户确认继续"
        elif not input_available:
            reason = "当前计划任务无法读取登录会话的键鼠状态，请重新安装/更新休眠唤醒任务"
        elif user_interacted:
            reason = "任务期间检测到键鼠操作"
        else:
            reason = "任务开始时电脑仍在使用，空闲不足 10 分钟"
        append_log(reason + "；保持开机，不休眠。")
    if power_guard is not None:
        power_guard.__exit__(None, None, None)
        power_guard = None
    return return_code


def worker_command(scheduled: bool = False) -> list[str]:
    python = runtime_python()
    if not python.is_file():
        raise RuntimeError(f"发行版 Python 不存在：{python}。请完整解压 BACoffee。")
    cmd = [str(python), "-X", "utf8", str(APP_DIR / "BACoffee.py"), "--worker"]
    if scheduled:
        cmd.append("--scheduled")
    return cmd


def is_wake_task_installed() -> bool:
    """Use Windows Task Scheduler as the source of truth, not the status cache."""
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", SCHEDULE_TASK_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception as exc:
        append_log(f"[schedule] failed to query Windows task '{SCHEDULE_TASK_NAME}': {exc!r}.")
        return False


def self_test() -> int:
    missing = portable_install_missing()
    if missing:
        for label, path in missing:
            print(f"[缺失] {label}: {path}")
        print("[失败] 发行内容不完整，请完整解压后重试。")
        return 2
    compatibility = source_compatibility_diagnostic()
    print_source_compatibility_diagnostic(compatibility)
    settings = load_settings()
    checks = []
    baas = Path(settings["baas_root"])
    checks.append(("BAAS 主程序", baas / "main.py"))
    checks.append(("BAAS 咖啡厅模块", baas / "module" / "cafe_reward.py"))
    checks.append(("MuMu 管理器", find_mumu_manager(settings) or Path(settings.get("mumu_manager_path", ""))))
    checks.append(("BAAS Python", Path(settings["baas_python"])))
    failed = not compatibility.get("ok")
    if not compatibility.get("ok"):
        print("[失败] EXE与旁置源码版本不一致，拒绝把本次自检作为有效证据。")
    for label, path in checks:
        ok = path.exists()
        print(f"{'[正常]' if ok else '[缺失]'} {label}: {path}")
        failed |= not ok
    try:
        # Self-test checks templates only; it must not discover/start a device or
        # write an unverified endpoint into a shared runtime profile.
        for template in ("config.json", "event.json"):
            json.loads((baas / "config" / "cn" / template).read_text(encoding="utf-8"))
        print("[正常] BAAS 配置模板可读取；运行配置将在设备验证后生成。")
        site_packages = add_runtime_site_packages(baas)
        builtin_dir, builtin_paths, invalid_builtin = inspect_builtin_student_library()
        expected_count = expected_builtin_student_count()
        expected_note = f"；发行清单数量：{expected_count}" if expected_count is not None else ""
        print(
            f"[正常] 内置学生库：{builtin_dir.resolve()}；"
            f"有效数量：{len(builtin_paths)}{expected_note}")
        if invalid_builtin:
            print(f"[失败] 内置学生库无效文件：{invalid_builtin}")
            failed = True
        if not builtin_dir.is_dir():
            print(f"[失败] 内置学生库目录不存在：{builtin_dir}")
            failed = True
        if expected_count is not None and expected_count != len(builtin_paths):
            print(
                f"[失败] 内置学生库数量不匹配：实际 {len(builtin_paths)}，"
                f"清单 {expected_count}")
            failed = True
        sys.path.insert(0, str(baas))
        old_cwd = Path.cwd()
        os.chdir(baas)
        cv2 = importlib.import_module("cv2")
        importlib.import_module("core.config.config_set")
        os.chdir(old_cwd)
        print(f"[正常] BAAS 依赖可加载：OpenCV {cv2.__version__}")
    except Exception as exc:
        try:
            os.chdir(old_cwd)
        except Exception:
            pass
        print(f"[失败] 配置准备：{exc}")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "self_test_error.txt").write_text(
            repr(exc) + "\n" + __import__("traceback").format_exc(), encoding="utf-8")
        failed = True
    print("[提示] 自检不会启动模拟器，也不会安装计划任务。")
    return int(failed)


def test_mumu_environment() -> int:
    """Packaged-build diagnostic: query MuMu without starting or changing the VM."""
    sanitize_external_runtime()
    settings = load_settings()
    manager = find_mumu_manager(settings)
    if manager is None:
        print("[缺失] 未找到 MuMuManager.exe，请在设置中选择 MuMu 管理器。")
        return 2
    vm_index = max(0, int(settings.get("mumu_vm_index", 1)))
    port_result = query_mumu_adb_port(manager, vm_index)
    diagnostic = {
        "manager": str(manager),
        "vm_index": vm_index,
        "port_result": port_result,
        "qt_plugin_path": os.environ.get("QT_PLUGIN_PATH"),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "mumu_environment_test.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[MuMu] 实例 {vm_index}：端口={port_result.get('port')}；"
        f"来源={port_result.get('source')}；"
        f"真实验证={'是' if port_result.get('verified') else '否'}。")
    return 0 if port_result.get("port") else 2


def launch_gui() -> None:
    gui_settings = load_settings()
    add_runtime_site_packages(Path(gui_settings["baas_root"]))
    from PyQt5.QtCore import QProcess, QSize
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QListWidgetItem,
                                 QMainWindow, QMessageBox, QVBoxLayout, QWidget)
    from qfluentwidgets import (BodyLabel, CardWidget, ListWidget, PrimaryPushButton,
                                PushButton, SpinBox, SubtitleLabel, SwitchButton,
                                TextEdit, Theme, TitleLabel, setTheme, setThemeColor)

    settings = gui_settings
    cache_avatars(settings)
    app = QApplication.instance() or QApplication(sys.argv)
    setTheme(Theme.AUTO)
    setThemeColor("#00A4E4")

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self._force_quit = False
            self._tray_notice_shown = bool(settings.get("tray_notice_seen", False))
            self.students = [dict(x) for x in settings["students"]]
            self.setWindowTitle("BACoffee - 蔚蓝档案咖啡厅助手")
            self.resize(940, 650)
            self.setMinimumSize(820, 560)
            central = QWidget()
            self.setCentralWidget(central)
            outer = QVBoxLayout(central)
            outer.setContentsMargins(20, 18, 20, 18)

            title = TitleLabel("BACoffee")
            outer.addWidget(title)
            subtitle = BodyLabel("只做咖啡厅摸头 · BAAS 执行引擎 · 可休眠唤醒")
            subtitle.setStyleSheet("color:#5b6472;margin-bottom:10px")
            outer.addWidget(subtitle)

            body = QHBoxLayout()
            outer.addLayout(body, 1)
            left = CardWidget()
            left_layout = QVBoxLayout(left)
            left_layout.addWidget(SubtitleLabel("运行设置"))
            self.cafe2 = SwitchButton(left)
            self.collect = SwitchButton(left)
            self.invite = SwitchButton(left)
            self.hibernate = SwitchButton(left)
            self.cafe2.setChecked(settings["cafe2"])
            self.collect.setChecked(settings["collect_reward"])
            self.invite.setChecked(settings["invite_student"])
            self.hibernate.setChecked(settings["hibernate_after_success"])
            for label_text, switch in (("处理 2 号咖啡厅", self.cafe2),
                                       ("领取咖啡厅收益", self.collect),
                                       ("使用邀请券", self.invite),
                                       ("成功后自动休眠", self.hibernate)):
                switch.setOnText("开")
                switch.setOffText("关")
                switch_row = QHBoxLayout()
                switch_row.addWidget(BodyLabel(label_text))
                switch_row.addStretch(1)
                switch_row.addWidget(switch)
                left_layout.addLayout(switch_row)
            round_row = QHBoxLayout()
            round_row.addWidget(BodyLabel("最多摸头轮数"))
            self.rounds = SpinBox()
            self.rounds.setRange(1, 8)
            self.rounds.setValue(settings["pat_rounds"])
            round_row.addWidget(self.rounds)
            left_layout.addLayout(round_row)
            note = BodyLabel("每天按设定计划时间检查一次；错过的任务会在开机后补跑。")
            note.setWordWrap(True)
            note.setStyleSheet("color:#5b6472")
            left_layout.addWidget(note)
            self.status = SubtitleLabel("就绪")
            self.status.setWordWrap(True)
            left_layout.addWidget(self.status)
            self.logger_box = TextEdit()
            self.logger_box.setReadOnly(True)
            self.logger_box.setPlaceholderText("任务日志会实时显示在这里")
            self.logger_box.setMinimumHeight(150)
            left_layout.addWidget(self.logger_box, 1)
            body.addWidget(left, 2)

            right = CardWidget()
            right_layout = QVBoxLayout(right)
            right_layout.addWidget(SubtitleLabel("邀请学生优先顺序"))
            self.list = ListWidget()
            self.list.setIconSize(QSize(58, 58))
            self.list.setSpacing(4)
            right_layout.addWidget(self.list, 1)
            list_buttons = QHBoxLayout()
            for text_, callback in (("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1)), ("移除", self.remove_selected)):
                button = PushButton(text_)
                button.clicked.connect(callback)
                list_buttons.addWidget(button)
            list_buttons.addStretch(1)
            right_layout.addLayout(list_buttons)
            source = BodyLabel("从上到下依次尝试；头像来自 Kivo 并保存在本机。")
            source.setStyleSheet("color:#5b6472")
            right_layout.addWidget(source)
            body.addWidget(right, 3)
            self.redraw()

            actions = QHBoxLayout()
            run_button = PrimaryPushButton("立即执行一次")
            run_button.clicked.connect(self.run_now)
            save_button = PushButton("保存设置")
            save_button.clicked.connect(self.save_only)
            task_button = PushButton("安装/更新休眠唤醒")
            task_button.clicked.connect(self.install_task)
            log_button = PushButton("打开日志")
            log_button.clicked.connect(lambda: os.startfile(LOG_FILE if LOG_FILE.exists() else DATA_DIR))
            for button in (run_button, save_button, task_button):
                actions.addWidget(button)
            actions.addStretch(1)
            actions.addWidget(log_button)
            outer.addLayout(actions)
            self.worker = QProcess(self)
            self.worker.setProcessChannelMode(QProcess.MergedChannels)
            self.worker.readyReadStandardOutput.connect(self.read_worker_output)
            self.worker.finished.connect(self.worker_finished)

        def avatar_path(self, index, student):
            return DATA_DIR / "avatars" / f"{student['name']}.png"

        def redraw(self):
            self.list.clear()
            for index, student in enumerate(self.students):
                item = QListWidgetItem(f"{index + 1}.  {student['label']}")
                path = self.avatar_path(index, student)
                if path.exists():
                    item.setIcon(QIcon(str(path)))
                item.setSizeHint(QSize(250, 66))
                self.list.addItem(item)

        def move(self, delta):
            old = self.list.currentRow()
            if old < 0:
                return
            new = max(0, min(len(self.students) - 1, old + delta))
            if old != new:
                self.students[old], self.students[new] = self.students[new], self.students[old]
                self.redraw()
                self.list.setCurrentRow(new)

        def remove_selected(self):
            row = self.list.currentRow()
            if row >= 0:
                self.students.pop(row)
                self.redraw()

        def collect_settings(self):
            settings.update({
                "cafe2": self.cafe2.isChecked(),
                "collect_reward": self.collect.isChecked(),
                "invite_student": self.invite.isChecked(),
                "hibernate_after_success": self.hibernate.isChecked(),
                "pat_rounds": self.rounds.value(),
                "students": self.students,
            })
            save_settings(settings)
            prepare_profile(settings)

        def save_only(self):
            try:
                self.collect_settings()
                cache_avatars(settings)
                self.status.setText("设置已保存")
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", str(exc))

        def run_now(self):
            try:
                if self.worker.state() != QProcess.NotRunning:
                    QMessageBox.information(self, "正在运行", "咖啡厅任务已经在执行。")
                    return
                self.collect_settings()
                cmd = worker_command(False)
                self.logger_box.clear()
                self.logger_box.append("正在启动 BAAS 咖啡厅执行器……")
                self.worker.setWorkingDirectory(str(APP_DIR))
                self.worker.start(cmd[0], cmd[1:])
                if not self.worker.waitForStarted(5000):
                    raise RuntimeError(self.worker.errorString())
                self.status.setText("正在执行咖啡厅任务")
            except Exception as exc:
                QMessageBox.critical(self, "启动失败", str(exc))

        def read_worker_output(self):
            raw = bytes(self.worker.readAllStandardOutput())
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self.logger_box.append(text)

        def worker_finished(self, exit_code, _exit_status):
            self.read_worker_output()
            if exit_code == 0:
                self.status.setText("任务完成")
                QMessageBox.information(self, "BACoffee", "咖啡厅任务已完成。")
            else:
                self.status.setText("任务失败，请查看下方日志")
                QMessageBox.warning(self, "BACoffee", "任务没有完成，错误已经显示在运行日志中。")

        def install_task(self):
            try:
                self.collect_settings()
                ps = APP_DIR / "Install-WakeTask.ps1"
                result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], text=True, capture_output=True)
                if result.returncode == 0:
                    QMessageBox.information(self, "计划任务", "休眠唤醒计划已安装。")
                else:
                    QMessageBox.critical(self, "安装失败", result.stderr or result.stdout)
            except Exception as exc:
                QMessageBox.critical(self, "安装失败", str(exc))

    window = Window()
    window.show()
    app.exec_()


def cache_avatars(settings: dict) -> None:
    target = DATA_DIR / "avatars"
    target.mkdir(parents=True, exist_ok=True)
    for student in settings.get("students", []):
        path = target / f"{student['name']}.png"
        if path.exists() or not student.get("avatar"):
            continue
        try:
            request = urllib.request.Request(student["avatar"], headers={"User-Agent": "BACoffee/0.1 personal-use"})
            with urllib.request.urlopen(request, timeout=12) as response:
                path.write_bytes(response.read())
        except Exception as exc:
            append_log(f"头像缓存失败（{student['name']}）：{exc}")


def launch_gui_v2() -> None:
    settings = load_settings()
    baas_root = Path(settings["baas_root"])
    add_runtime_site_packages(baas_root)

    from PyQt5.QtCore import QProcess, QProcessEnvironment, QSize, Qt
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import (QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel,
                                 QMainWindow, QMessageBox, QScrollArea, QToolButton,
                                 QVBoxLayout, QWidget)
    from qfluentwidgets import (BodyLabel, CardWidget, ComboBox, LineEdit, ListWidget,
                                PrimaryPushButton, PushButton, SpinBox, SubtitleLabel,
                                SwitchButton, TextEdit, Theme, TitleLabel, setTheme,
                                setThemeColor, isDarkTheme, qconfig)

    theme_map = {"system": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}
    theme_text = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
    text_theme = {value: key for key, value in theme_text.items()}
    app = QApplication.instance() or QApplication(sys.argv)
    setTheme(theme_map.get(settings.get("theme"), Theme.AUTO))
    setThemeColor("#00A4E4")

    static = json.loads((baas_root / "config" / "static.json").read_text(encoding="utf-8"))
    class StudentSelectorDialog(QDialog):
        def __init__(self, parent, selected, cafe_no):
            super().__init__(parent)
            # Keep the normal title and close button, but remove Qt's unused
            # context-help (?) button from this dialog.
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            self.catalog = load_student_catalog()
            self.selected = [name for name in selected if name.casefold() in self.catalog]
            self.buttons = {}
            self.setWindowTitle(f"{cafe_no} 号咖啡厅 · 选择邀请学生")
            self.resize(900, 680)
            self.setMinimumSize(760, 560)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(12)
            layout.addWidget(SubtitleLabel("点击头像选择学生"))
            tip = BodyLabel("点击顺序就是邀请优先级；数字 1 会最先尝试。再次点击可取消选择。")
            tip.setWordWrap(True)
            layout.addWidget(tip)
            self.search = LineEdit()
            self.search.setPlaceholderText("按 学生 罗马音 搜索，例如 Natsu、Kisaki")
            self.search.textChanged.connect(self.render_grid)
            layout.addWidget(self.search)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            self.grid_host = QWidget()
            self.grid = QGridLayout(self.grid_host)
            self.grid.setContentsMargins(4, 4, 4, 4)
            self.grid.setSpacing(10)
            scroll.setWidget(self.grid_host)
            layout.addWidget(scroll, 1)

            actions = QHBoxLayout()
            clear = PushButton("清空选择")
            clear.clicked.connect(self.clear_selection)
            cancel = PushButton("取消")
            cancel.clicked.connect(self.reject)
            confirm = PrimaryPushButton("确认选择")
            confirm.clicked.connect(self.accept)
            actions.addWidget(clear)
            actions.addStretch(1)
            actions.addWidget(cancel)
            actions.addWidget(confirm)
            layout.addLayout(actions)
            self.render_grid()

        def render_grid(self, *_):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.buttons.clear()
            query = self.search.text().strip().lower()
            names = [entry["name"] for entry in self.catalog.values()
                     if not query or query in Path(entry["name"]).stem.lower()]
            for position, name in enumerate(names):
                button = QToolButton()
                button.setCheckable(True)
                button.setChecked(name in self.selected)
                button.setIcon(QIcon(str(self.catalog[name.casefold()]["path"])))
                button.setIconSize(QSize(72, 72))
                button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                button.setFixedSize(124, 124)
                button.clicked.connect(lambda _checked=False, value=name: self.toggle_student(value))
                self.buttons[name] = button
                self.grid.addWidget(button, position // 6, position % 6)
            self.refresh_badges()

        def toggle_student(self, name):
            if name in self.selected:
                self.selected.remove(name)
            else:
                self.selected.append(name)
            self.refresh_badges()

        def refresh_badges(self):
            accent = "#FF79A8" if isDarkTheme() else "#28A9F1"
            accent_fill = "rgba(255,121,168,45)" if isDarkTheme() else "rgba(40,169,241,45)"
            accent_hover = "rgba(255,121,168,24)" if isDarkTheme() else "rgba(40,169,241,24)"
            for name, button in self.buttons.items():
                rank = self.selected.index(name) + 1 if name in self.selected else None
                button.setChecked(rank is not None)
                label = Path(name).stem
                button.setText(f"{rank}. {label}" if rank else label)
                button.setStyleSheet(
                    "QToolButton:checked { border: 2px solid #00A4E4; border-radius: 8px; "
                    "background-color: rgba(0, 164, 228, 40); }"
                )

        def clear_selection(self):
            self.selected.clear()
            self.refresh_badges()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.priorities = [selected_student_files(settings, 1), selected_student_files(settings, 2)]
            self.setWindowTitle("BACoffee - 蔚蓝档案咖啡厅助手")
            self.resize(1120, 720)
            self.setMinimumSize(940, 620)
            central = QWidget()
            central.setObjectName("bacoffeeRoot")
            self.setCentralWidget(central)
            outer = QVBoxLayout(central)
            outer.setContentsMargins(22, 18, 22, 18)
            outer.setSpacing(12)

            outer.addWidget(TitleLabel("BACoffee"))
            outer.addWidget(BodyLabel("BAAS 咖啡厅执行引擎 · BASH 风格邀请优先级 · 休眠唤醒"))
            content = QHBoxLayout()
            content.setSpacing(12)
            outer.addLayout(content, 1)

            settings_card = CardWidget()
            settings_layout = QVBoxLayout(settings_card)
            settings_layout.setContentsMargins(18, 16, 18, 16)
            settings_layout.addWidget(SubtitleLabel("咖啡厅设置"))
            self.cafe2 = self.add_switch(settings_layout, "处理 2 号咖啡厅", settings["cafe2"])
            self.collect = self.add_switch(settings_layout, "领取咖啡厅收益", settings["collect_reward"])
            self.invite = self.add_switch(settings_layout, "使用邀请券", settings["invite_student"])
            self.hibernate = self.add_switch(settings_layout, "成功后自动休眠", settings["hibernate_after_success"])

            round_row = QHBoxLayout()
            round_row.addWidget(BodyLabel("最多摸头轮数"))
            round_row.addStretch(1)
            self.rounds = SpinBox()
            self.rounds.setRange(1, 8)
            self.rounds.setValue(settings["pat_rounds"])
            round_row.addWidget(self.rounds)
            settings_layout.addLayout(round_row)

            theme_row = QHBoxLayout()
            theme_row.addWidget(BodyLabel("界面模式"))
            theme_row.addStretch(1)
            self.theme = ComboBox()
            self.theme.addItems(["跟随系统", "浅色", "深色"])
            self.theme.setCurrentText(theme_text.get(settings.get("theme"), "跟随系统"))
            self.theme.currentTextChanged.connect(self.change_theme)
            theme_row.addWidget(self.theme)
            settings_layout.addLayout(theme_row)

            note = BodyLabel("计划时间：01:30、04:00、07:10、10:20、13:30、16:00、19:10、22:20")
            note.setWordWrap(True)
            settings_layout.addWidget(note)
            self.status = SubtitleLabel("就绪")
            settings_layout.addWidget(self.status)
            self.log_box = TextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setPlaceholderText("启动后，BAAS 和 MuMu 的运行信息会显示在这里")
            settings_layout.addWidget(self.log_box, 1)
            content.addWidget(settings_card, 5)

            invite_card = CardWidget()
            invite_layout = QVBoxLayout(invite_card)
            invite_layout.setContentsMargins(18, 16, 18, 16)
            invite_layout.addWidget(SubtitleLabel("邀请学生"))
            self.selection_labels = []
            for cafe_index in range(2):
                row = QVBoxLayout()
                header = QHBoxLayout()
                header.addWidget(BodyLabel(f"{cafe_index + 1} 号咖啡厅"))
                header.addStretch(1)
                choose = PrimaryPushButton("选择学生")
                choose.clicked.connect(lambda _checked=False, index=cafe_index: self.open_selector(index))
                header.addWidget(choose)
                row.addLayout(header)
                selected_label = BodyLabel()
                selected_label.setWordWrap(True)
                selected_label.setMinimumHeight(92)
                selected_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                self.selection_labels.append(selected_label)
                row.addWidget(selected_label)
                invite_layout.addLayout(row)
            self.refresh_priority()
            invite_layout.addStretch(1)
            tip = BodyLabel("邀请时使用 BASH 的圆形头像识别，按编号从小到大选择；不再受 BAAS 学生名单版本限制。")
            tip.setWordWrap(True)
            invite_layout.addWidget(tip)
            content.addWidget(invite_card, 6)

            actions = QHBoxLayout()
            run_button = PrimaryPushButton("立即执行一次")
            run_button.clicked.connect(self.run_now)
            save_button = PushButton("保存设置")
            save_button.clicked.connect(self.save_only)
            schedule_button = PushButton("安装/更新休眠唤醒")
            schedule_button.clicked.connect(self.install_task)
            log_button = PushButton("打开日志文件")
            log_button.clicked.connect(lambda: os.startfile(LOG_FILE if LOG_FILE.exists() else DATA_DIR))
            for button in (run_button, save_button, schedule_button):
                actions.addWidget(button)
            actions.addStretch(1)
            actions.addWidget(log_button)
            outer.addLayout(actions)

            self.worker = QProcess(self)
            self.worker.setProcessChannelMode(QProcess.MergedChannels)
            clean_env = QProcessEnvironment.systemEnvironment()
            for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
                clean_env.remove(key)
            self.worker.setProcessEnvironment(clean_env)
            self.worker.readyReadStandardOutput.connect(self.read_worker_output)
            self.worker.finished.connect(self.worker_finished)
            qconfig.themeChanged.connect(lambda *_: self.apply_background())
            self.apply_background()

        def add_switch(self, layout, label, checked):
            row = QHBoxLayout()
            row.addWidget(BodyLabel(label))
            row.addStretch(1)
            switch = SwitchButton()
            switch.setOnText("开")
            switch.setOffText("关")
            switch.setChecked(checked)
            row.addWidget(switch)
            layout.addLayout(row)
            return switch

        def change_theme(self, text):
            setTheme(theme_map[text_theme.get(text, "system")])
            self.apply_background()

        def apply_background(self):
            if isDarkTheme():
                self.centralWidget().setStyleSheet(
                    "QWidget#bacoffeeRoot { background-color: rgba(32, 32, 32, 242); }"
                    "CardWidget { background-color: rgba(39, 39, 39, 222); }")
            else:
                self.centralWidget().setStyleSheet(
                    "QWidget#bacoffeeRoot { background-color: rgba(243, 243, 243, 242); }"
                    "CardWidget { background-color: rgba(255, 255, 255, 222); }")
            self.setWindowOpacity(0.97)

        def refresh_priority(self, *_):
            for cafe_index, label in enumerate(self.selection_labels):
                values = self.priorities[cafe_index]
                label.setText("\n".join(f"{i}. {Path(name).stem}" for i, name in enumerate(values, 1))
                              if values else "尚未选择学生（将跳过邀请）")

        def open_selector(self, cafe_index):
            dialog = StudentSelectorDialog(self, self.priorities[cafe_index], cafe_index + 1)
            if dialog.exec_() == QDialog.Accepted:
                self.priorities[cafe_index] = list(dialog.selected)
                self.refresh_priority()

        def collect_settings(self):
            settings.update({
                "cafe2": self.cafe2.isChecked(),
                "collect_reward": self.collect.isChecked(),
                "invite_student": self.invite.isChecked(),
                "hibernate_after_success": self.hibernate.isChecked(),
                "pat_rounds": self.rounds.value(),
                "theme": text_theme.get(self.theme.currentText(), "system"),
                "bash_students_cafe1": self.priorities[0],
                "bash_students_cafe2": self.priorities[1],
            })
            save_settings(settings)
            prepare_profile(settings)

        def save_only(self):
            try:
                self.collect_settings()
                self.status.setText("设置已保存")
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", str(exc))

        def run_now(self):
            try:
                if self.worker.state() != QProcess.NotRunning:
                    QMessageBox.information(self, "正在运行", "咖啡厅任务已经在执行。")
                    return
                self.collect_settings()
                sanitize_external_runtime()
                cmd = worker_command(False)
                self.log_box.clear()
                self.log_box.append("正在启动 BAAS 咖啡厅执行器……")
                self.worker.setWorkingDirectory(str(APP_DIR))
                self.worker.start(cmd[0], cmd[1:])
                if not self.worker.waitForStarted(5000):
                    raise RuntimeError(self.worker.errorString())
                self.status.setText("正在执行咖啡厅任务")
            except Exception as exc:
                QMessageBox.critical(self, "启动失败", str(exc))

        def read_worker_output(self):
            text = bytes(self.worker.readAllStandardOutput()).decode("utf-8", errors="replace").rstrip()
            if text:
                self.log_box.append(text)

        def worker_finished(self, exit_code, _exit_status):
            self.read_worker_output()
            if exit_code == 0:
                self.status.setText("任务完成")
                QMessageBox.information(self, "BACoffee", "咖啡厅任务已完成。")
            else:
                self.status.setText("任务失败，请查看运行日志")
                QMessageBox.warning(self, "BACoffee", "任务没有完成，原因已显示在左侧日志中。")

        def install_task(self):
            try:
                self.collect_settings()
                ps = APP_DIR / "Install-WakeTask.ps1"
                result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], text=True, capture_output=True)
                if result.returncode == 0:
                    QMessageBox.information(self, "计划任务", "休眠唤醒计划已安装。")
                else:
                    QMessageBox.critical(self, "安装失败", result.stderr or result.stdout)
            except Exception as exc:
                QMessageBox.critical(self, "安装失败", str(exc))

    window = Window()
    window.show()
    app.exec_()


def launch_gui_v3() -> None:
    settings = load_settings()
    baas_root = Path(settings["baas_root"])
    add_runtime_site_packages(baas_root)

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BACoffee.Desktop")
        except Exception:
            pass

    from PyQt5.QtCore import (QEvent, QEasingCurve, QObject, QPoint, QPointF,
                              QProcess, QProcessEnvironment, QRect, QRectF, QSize,
                              Qt, QTimer, QVariantAnimation, QSignalBlocker)
    from PyQt5.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QIcon,
                              QImage, QLinearGradient, QPainter, QPainterPath,
                              QPen, QPixmap, QTextOption, QWheelEvent)
    from PyQt5.QtNetwork import QLocalServer, QLocalSocket
    from PyQt5.QtWidgets import (QAction, QApplication, QDialog, QFileDialog,
                                 QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QMessageBox,
                                 QScrollArea, QSizePolicy, QSystemTrayIcon,
                                 QTextEdit, QToolButton, QToolTip, QVBoxLayout, QWidget)
    from qfluentwidgets import (BodyLabel, CardWidget, ComboBox, FluentIcon as FIF,
                                FluentStyleSheet,
                                LineEdit, MSFluentWindow, PrimaryPushButton, PushButton,
                                Slider, SpinBox, SubtitleLabel, SwitchButton, TextEdit,
                                Theme, TitleLabel, isDarkTheme, qconfig, setTheme,
                                setThemeColor)

    theme_map = {"system": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}
    theme_text = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
    text_theme = {value: key for key, value in theme_text.items()}
    app = QApplication.instance() or QApplication(sys.argv)
    if COFFEE_ICON.is_file():
        app.setWindowIcon(QIcon(str(COFFEE_ICON)))
    elif COFFEE_LOGO.is_file():
        app.setWindowIcon(QIcon(str(COFFEE_LOGO)))
    app.setQuitOnLastWindowClosed(False)
    brand_font_family = "Comfortaa Light"
    if COMFORTAA_LIGHT.is_file():
        font_id = QFontDatabase.addApplicationFont(str(COMFORTAA_LIGHT))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            brand_font_family = families[0]
    setTheme(theme_map.get(settings.get("theme"), Theme.AUTO))
    setThemeColor("#00A4E4")

    class GuiMessageBox(QMessageBox):
        """Style only BAC's active Qt messages, retaining QMessageBox results."""
        def exec_(self):
            family = ("Microsoft YaHei UI" if "Microsoft YaHei UI" in QFontDatabase().families()
                      else "Microsoft YaHei")
            font = QFont(family, 10)
            font.setWeight(QFont.Normal)
            self.setFont(font)
            dark = isDarkTheme()
            background, foreground = ("#25282E", "#F4F6F9") if dark else ("#FFFFFF", "#20242B")
            secondary, hover, pressed, border = (("#34383F", "#414750", "#2C3036", "#69717E")
                                                  if dark else ("#F3F5F7", "#E7EDF2", "#DAE3EB", "#A6B0BC"))
            self.setStyleSheet(
                f"QMessageBox {{ background: {background}; color: {foreground}; }}"
                f"QMessageBox QLabel, QMessageBox QTextEdit {{ color: {foreground}; background: transparent; }}"
                f"QMessageBox QPushButton {{ background: {secondary}; color: {foreground}; "
                f"border: 1px solid {border}; border-radius: 6px; padding: 0 16px; }}"
                f"QMessageBox QPushButton:hover {{ background: {hover}; }}"
                f"QMessageBox QPushButton:pressed {{ background: {pressed}; }}"
                'QMessageBox QPushButton[primary="true"] { background: #007EB5; color: white; border-color: #007EB5; }'
                'QMessageBox QPushButton[primary="true"]:hover { background: #006FA0; }'
                'QMessageBox QPushButton[primary="true"]:pressed { background: #005E88; }'
                'QMessageBox QPushButton:focus { border: 2px solid #00A4E4; }')
            layout = self.layout()
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            screen = self.screen() or QApplication.primaryScreen()
            area = screen.availableGeometry()
            text_width = max(120, min(420, area.width() - 136))
            label = self.findChild(QLabel, "qt_msgbox_label")
            if label is not None:
                label.setFont(font)
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setMaximumWidth(text_width)
                measured = label.fontMetrics().boundingRect(
                    QRect(0, 0, text_width, 100000), Qt.TextWordWrap | Qt.TextWrapAnywhere, self.text()).height()
                max_height = max(80, area.height() - 200)
                if measured > max_height or len(self.text()) > 800 or any(len(word) > 100 for word in self.text().split()):
                    # A scrollable, selectable body keeps long errors and paths complete.
                    body = QTextEdit(self)
                    body.setFont(font)
                    body.setReadOnly(True)
                    body.setFrameShape(QTextEdit.NoFrame)
                    body.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                    body.setPlainText(self.text())
                    body.setFixedSize(text_width, min(max_height, max(100, measured + 12)))
                    index = layout.indexOf(label)
                    row, column, rows, columns = layout.getItemPosition(index)
                    layout.removeWidget(label)
                    label.hide()
                    layout.addWidget(body, row, column, rows, columns)
                elif len(self.text()) > 80:
                    label.setMinimumWidth(text_width)
            buttons = self.findChild(QDialogButtonBox)
            if buttons is not None:
                buttons.layout().setSpacing(10)
            for button in self.buttons():
                button.setFont(font)
                button.setFixedHeight(34)
                button.setMinimumWidth(88)
                button.setProperty("primary", self.buttonRole(button) in (self.AcceptRole, self.YesRole, self.ApplyRole))
                button.setAutoDefault(False)
                button.style().unpolish(button)
                button.style().polish(button)
            if self.defaultButton() is not None:
                self.defaultButton().setFocus(Qt.OtherFocusReason)
            self.adjustSize()
            return super().exec_()

        @classmethod
        def _show(cls, icon, parent, title, text, buttons, defaultButton):
            box = cls(icon, title, text, buttons, parent)
            if defaultButton != cls.NoButton:
                box.setDefaultButton(defaultButton)
            try:
                return box.exec_()
            finally:
                box.deleteLater()

        @classmethod
        def question(cls, parent, title, text, buttons=QMessageBox.Yes | QMessageBox.No, defaultButton=QMessageBox.No):
            return cls._show(cls.Question, parent, title, text, buttons, defaultButton)

        @classmethod
        def information(cls, parent, title, text, buttons=QMessageBox.Ok, defaultButton=QMessageBox.NoButton):
            return cls._show(cls.Information, parent, title, text, buttons, defaultButton)

        @classmethod
        def warning(cls, parent, title, text, buttons=QMessageBox.Ok, defaultButton=QMessageBox.NoButton):
            return cls._show(cls.Warning, parent, title, text, buttons, defaultButton)

        @classmethod
        def critical(cls, parent, title, text, buttons=QMessageBox.Ok, defaultButton=QMessageBox.NoButton):
            return cls._show(cls.Critical, parent, title, text, buttons, defaultButton)

    def acquire_instance_mutex():
        if os.name != "nt":
            return None
        try:
            kernel32 = ctypes.windll.kernel32
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, True, GUI_INSTANCE_MUTEX_NAME)
            if not handle:
                return None
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            return handle
        except Exception:
            return None

    def release_instance_mutex(handle):
        if os.name != "nt" or not handle:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def activate_existing_instance() -> bool:
        """Wake the existing GUI process and report whether this is a duplicate."""
        peer = QLocalSocket()
        peer.connectToServer(GUI_INSTANCE_SERVER_NAME)
        if not peer.waitForConnected(350):
            return False
        peer.write(b"ACTIVATE")
        peer.flush()
        peer.waitForBytesWritten(350)
        peer.disconnectFromServer()
        return True

    instance_mutex = acquire_instance_mutex()
    if instance_mutex is False:
        # The mutex closes the startup race before QLocalServer has begun
        # listening. Wait briefly for the owner to publish its endpoint, but
        # never create a second GUI if it does not appear.
        for _ in range(10):
            if activate_existing_instance():
                return
            time.sleep(0.1)
        return
    if activate_existing_instance():
        release_instance_mutex(instance_mutex)
        return

    instance_server = QLocalServer()
    if not instance_server.listen(GUI_INSTANCE_SERVER_NAME):
        # A crashed GUI can leave the local endpoint behind. Only remove it
        # after a real connection attempt failed, then retry once.
        QLocalServer.removeServer(GUI_INSTANCE_SERVER_NAME)
        if not instance_server.listen(GUI_INSTANCE_SERVER_NAME):
            if activate_existing_instance():
                return
            raise RuntimeError(
                f"无法建立 BACoffee 单实例服务：{instance_server.errorString()}")

    window_ref = [None]

    def handle_instance_message(peer):
        try:
            message = bytes(peer.readAll())
        except Exception:
            message = b""
        if b"ACTIVATE" in message and window_ref[0] is not None:
            window_ref[0].restore_from_tray()

    def accept_instance_connection():
        while instance_server.hasPendingConnections():
            peer = instance_server.nextPendingConnection()
            if peer is None:
                continue
            peer.readyRead.connect(lambda peer=peer: handle_instance_message(peer))
            peer.disconnected.connect(peer.deleteLater)
            if peer.bytesAvailable():
                handle_instance_message(peer)

    instance_server.newConnection.connect(accept_instance_connection)

    class Win11ScrollBarAnimator(QObject):
        """Explorer-like thin thumb with a visual-only hover animation."""
        def __init__(self, bar, accent, dark):
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

        def set_theme(self, accent, dark):
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

        def _animate(self, value):
            self.progress = float(value)
            self._apply_style()

        def _color(self):
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

        def _apply_style(self):
            # The bar keeps an 8 px footprint. Only the painted thumb grows,
            # so layouts and scrolling behavior never jump during animation.
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

    def install_win11_scrollbars(owner, scroll_area, accent="#00A4E4"):
        if not hasattr(owner, "_win11_scrollbars"):
            owner._win11_scrollbars = []
        for bar in (scroll_area.verticalScrollBar(), scroll_area.horizontalScrollBar()):
            animator = Win11ScrollBarAnimator(bar, accent, isDarkTheme())
            owner._win11_scrollbars.append(animator)

    class BackgroundPage(QWidget):
        def __init__(self, owner, name):
            super().__init__()
            self.owner = owner
            self.setObjectName(name)
            self.setAttribute(Qt.WA_StyledBackground, True)

        def paintEvent(self, event):
            super().paintEvent(event)

    class BrandTitle(QWidget):
        """Large, rounded BAC wordmark with a simple theme-aware vector fill."""
        def __init__(self):
            super().__init__()
            self.brand_font = QFont(brand_font_family, 36, QFont.Light)
            self.brand_font.setStyleStrategy(QFont.PreferAntialias)
            self.accent = QColor("#28A9F1")
            self.dark = False
            self.setFixedSize(205, 66)

        def set_theme(self, accent, dark):
            self.accent = QColor(accent)
            self.dark = bool(dark)
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            metrics = QFontMetricsF(self.brand_font)
            letters = "BAC"
            gap = 6.0
            widths = [metrics.horizontalAdvance(letter) for letter in letters]
            x = (self.width() - sum(widths) - gap * 2) / 2
            baseline = (self.height() + metrics.ascent() - metrics.descent()) / 2 - 8
            for index, (letter, width) in enumerate(zip(letters, widths)):
                path = QPainterPath()
                path.addText(x, baseline, self.brand_font, letter)
                secondary = (not self.dark and index in (1, 2)) or (self.dark and index == 0)
                fill = QColor("#FFFFFF") if secondary else QColor(self.accent)
                if not self.dark and index in (1, 2):
                    painter.setPen(QPen(self.accent, 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                else:
                    painter.setPen(Qt.NoPen)
                painter.setBrush(fill)
                painter.drawPath(path)
                x += width + gap

    class BrandMotto(QWidget):
        """A small theme-aware motto aligned to the BAC wordmark."""
        text = "⌈ 见过星空的人，再也忘不掉那份孤单的绚烂。 ⌋"

        def __init__(self):
            super().__init__()
            self.motto_font = QFont("幼圆")
            self.motto_font.setPointSizeF(10.0)
            self.motto_font.setStyleStrategy(QFont.PreferAntialias)
            self.accent = QColor("#28A9F1")
            self.dark = False
            self.setFixedSize(390, 28)

        def set_theme(self, accent, dark):
            self.accent = QColor(accent)
            self.dark = bool(dark)
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            metrics = QFontMetricsF(self.motto_font)
            width = metrics.horizontalAdvance(self.text)
            x = self.width() - width - 2
            baseline = (self.height() + metrics.ascent() - metrics.descent()) / 2
            path = QPainterPath()
            path.addText(x, baseline, self.motto_font, self.text)
            edge = QColor(14, 16, 21, 105) if self.dark else QColor(255, 255, 255, 145)
            color = QColor(self.accent)
            color.setAlpha(220)
            painter.setPen(QPen(edge, 0.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(color)
            painter.drawPath(path)

    class StatusPill(BodyLabel):
        """Compact status display that follows the current light/dark accent."""
        def __init__(self, text="就绪"):
            # Passing a string to FluentLabelBase's overloaded constructor
            # dispatches back through self.__init__, which recurses in a subclass.
            super().__init__()
            self._status_text = ""
            self.setAlignment(Qt.AlignCenter)
            self.setFixedHeight(34)
            self.setMaximumWidth(260)
            self.setText(text)

        def setText(self, text):
            self._status_text = str(text)
            super().setText(f"●  {self._status_text}")
            self.refresh_theme()

        def refresh_theme(self):
            value = self._status_text
            if "失败" in value or "错误" in value:
                color = QColor("#FFB45B")
            elif "完成" in value or "保存" in value:
                color = QColor("#45C995")
            elif "执行" in value or "启动" in value:
                color = QColor("#FF79A8" if isDarkTheme() else "#28A9F1")
            else:
                color = QColor("#AEB5C2" if isDarkTheme() else "#687384")
            text = "#F8F9FB" if isDarkTheme() else "#20242B"
            self.setStyleSheet(
                f"color: {text}; background-color: rgba({color.red()},{color.green()},{color.blue()},38); "
                f"border: 1px solid rgba({color.red()},{color.green()},{color.blue()},112); "
                "border-radius: 17px; padding: 4px 14px; font-weight: 600;")

    class SchedulePlanIndicator(QWidget):
        """Read-only task existence status from the latest asynchronous query."""
        def __init__(self):
            super().__init__()
            self._installed = None
            self.setFixedSize(108, 30)
            self.set_installed(None)

        def set_installed(self, installed):
            self._installed = installed
            self.setToolTip(
                "任务存在，是否启用请以 Windows 计划状态为准" if installed is True else
                "休眠唤醒计划未安装" if installed is False else "状态未知：正在查询或查询暂不可用")
            self.update()

        def refresh_theme(self):
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            dark = isDarkTheme()
            if self._installed is True:
                signal = QColor("#47D7A1" if dark else "#19A974")
                label = "已安装"
            else:
                signal = QColor("#A9B0BC" if dark else "#7A8492")
                label = "未安装" if self._installed is False else "状态未知"
            background = QColor(signal)
            background.setAlpha(24 if dark else 18)
            border = QColor(signal)
            border.setAlpha(74 if dark else 62)
            text_color = QColor("#F4F6F9" if dark else "#343A43")

            body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(QPen(border, 1))
            painter.setBrush(background)
            painter.drawRoundedRect(body, 15, 15)

            halo = QColor(signal)
            halo.setAlpha(48)
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(10, 8, 14, 14))
            painter.setBrush(signal)
            painter.drawEllipse(QRectF(14, 12, 6, 6))

            font = QFont("Microsoft YaHei UI", 9)
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(QRectF(29, 0, self.width() - 36, self.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, label)

    class StudentSelectorDialog(QDialog):
        def __init__(self, parent, selected, cafe_no):
            super().__init__(parent)
            self.catalog = load_student_catalog()
            self.selected = [name for name in selected if name.casefold() in self.catalog]
            self.buttons = {}
            self.setWindowTitle(f"{cafe_no} 号咖啡厅 · 选择邀请学生")
            self.resize(920, 690)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(22, 20, 22, 20)
            layout.setSpacing(12)
            layout.addWidget(TitleLabel("选择邀请学生"))
            layout.addWidget(BodyLabel("依次点击头像决定优先级；数字 1 会最先尝试。再次点击即可取消。"))
            self.search = LineEdit()
            self.search.setPlaceholderText("按 学生 罗马音 搜索，例如 Natsu、Kisaki")
            self.search.textChanged.connect(self.render_grid)
            layout.addWidget(self.search)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
            install_win11_scrollbars(self, scroll, getattr(parent, "_accent_color", "#00A4E4"))
            self.grid_host = QWidget()
            self.grid_host.setObjectName("studentGrid")
            self.grid = QGridLayout(self.grid_host)
            self.grid.setContentsMargins(4, 4, 4, 4)
            self.grid.setSpacing(10)
            scroll.setWidget(self.grid_host)
            layout.addWidget(scroll, 1)
            actions = QHBoxLayout()
            clear = PushButton("清空选择")
            clear.clicked.connect(self.clear_selection)
            cancel = PushButton("取消")
            cancel.clicked.connect(self.reject)
            confirm = PrimaryPushButton("确认选择")
            confirm.clicked.connect(self.accept)
            actions.addWidget(clear)
            actions.addStretch(1)
            actions.addWidget(cancel)
            actions.addWidget(confirm)
            layout.addLayout(actions)
            dialog_bg = "#202127" if isDarkTheme() else "#F7F9FC"
            dialog_text = "#F7F8FB" if isDarkTheme() else "#20242B"
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setStyleSheet(
                f"QDialog {{ background-color: {dialog_bg}; color: {dialog_text}; }}"
                f"QDialog QLabel {{ color: {dialog_text}; background: transparent; }}"
                "QWidget#studentGrid, QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            )
            self.render_grid()

        def showEvent(self, event):
            super().showEvent(event)
            self.apply_native_title_bar()

        def apply_native_title_bar(self):
            """Match the native Windows caption to the dialog's active theme."""
            if os.name != "nt":
                return
            try:
                hwnd = int(self.winId())
                dark = isDarkTheme()
                enabled = ctypes.c_int(1 if dark else 0)
                # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on current Windows and
                # 19 on older compatible builds.
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
                if result != 0:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))

                caption = QColor("#202127" if dark else "#F7F9FC")
                caption_ref = ctypes.c_int(
                    caption.red() | (caption.green() << 8) | (caption.blue() << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(caption_ref), ctypes.sizeof(caption_ref))

                title_text = QColor("#F7F8FB" if dark else "#20242B")
                text_ref = ctypes.c_int(
                    title_text.red() | (title_text.green() << 8) | (title_text.blue() << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(text_ref), ctypes.sizeof(text_ref))
            except Exception:
                pass

        def render_grid(self, *_):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.buttons.clear()
            query = self.search.text().strip().lower()
            names = [entry["name"] for entry in self.catalog.values()
                     if not query or query in Path(entry["name"]).stem.lower()]
            for position, name in enumerate(names):
                button = QToolButton()
                button.setCheckable(True)
                button.setIcon(QIcon(str(self.catalog[name.casefold()]["path"])))
                button.setIconSize(QSize(76, 76))
                button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                button.setFixedSize(132, 128)
                student_font = QFont("Segoe UI")
                student_font.setPointSizeF(9.5)
                student_font.setWeight(QFont.Normal)
                button.setFont(student_font)
                button.setToolTip(Path(name).stem)
                button.clicked.connect(lambda _checked=False, value=name: self.toggle(value))
                self.buttons[name] = button
                self.grid.addWidget(button, position // 6, position % 6)
            self.refresh_badges()

        def toggle(self, name):
            if name in self.selected:
                self.selected.remove(name)
            else:
                self.selected.append(name)
            self.refresh_badges()

        def clear_selection(self):
            self.selected.clear()
            self.refresh_badges()

        def refresh_badges(self):
            accent = "#FF79A8" if isDarkTheme() else "#28A9F1"
            accent_fill = "rgba(255,121,168,45)" if isDarkTheme() else "rgba(40,169,241,45)"
            accent_hover = "rgba(255,121,168,24)" if isDarkTheme() else "rgba(40,169,241,24)"
            student_text = "#F7F8FB" if isDarkTheme() else "#20242B"
            for name, button in self.buttons.items():
                rank = self.selected.index(name) + 1 if name in self.selected else None
                button.setChecked(rank is not None)
                button.setText(f"{rank}. {Path(name).stem}" if rank else Path(name).stem)
                button.setStyleSheet(
                    f"QToolButton {{ color: {student_text}; border: 1px solid rgba(127,127,127,45); "
                    "border-radius: 10px; padding: 4px; }"
                    f"QToolButton:hover {{ background-color: {accent_hover}; }}"
                    f"QToolButton:checked {{ border: 2px solid {accent}; background-color: {accent_fill}; }}"
                )

    class ImmersiveToggleButton(QToolButton):
        """A compact clean-background control that labels itself only on hover."""
        def __init__(self, parent=None):
            super().__init__(parent)
            self._hovered = False
            self._text_color = QColor("#20242B")
            self._icon_color = QColor("#FFFFFF")
            self._icon_mask = self._load_icon_mask(UI_ASSET_DIR / "coffee_bean.png")
            self.setCheckable(True)
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("纯净背景")
            self.setFixedSize(34, 34)

        @staticmethod
        def _load_icon_mask(path):
            """Load the supplied white-on-dark icon as a tight alpha mask."""
            source = QImage(str(path))
            if source.isNull():
                return QImage()
            return source.convertToFormat(QImage.Format_ARGB32)

        def set_theme(self, text_color, accent):
            self._text_color = QColor(text_color)
            # Use the active theme accent in every theme so the icon remains
            # visible in light mode and consistent with the rest of the UI.
            self._icon_color = QColor(accent)
            self.update()

        def enterEvent(self, event):
            self._hovered = True
            self.setFixedSize(108, 34)
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event):
            self._hovered = False
            self.setFixedSize(34, 34)
            self.update()
            super().leaveEvent(event)

        def paintEvent(self, event):
            super().paintEvent(event)
            # In clean-background mode this control deliberately becomes only
            # a nearly invisible accent outline, so it does not distract from
            # the artwork below it.
            if self.isChecked():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            if not self._icon_mask.isNull():
                # Keep the original 34x34 button (and 108x34 hover expansion),
                # while fitting the cropped icon inside a slightly smaller,
                # balanced 18x20 area.
                icon_bounds = QRectF(8, 7, 18, 20)
                scaled = self._icon_mask.scaled(
                    int(icon_bounds.width()), int(icon_bounds.height()),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                tinted = QImage(scaled.size(), QImage.Format_ARGB32_Premultiplied)
                tinted.fill(self._icon_color)
                mask_painter = QPainter(tinted)
                mask_painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                mask_painter.drawImage(0, 0, scaled)
                mask_painter.end()
                painter.save()
                painter.setOpacity(51 / 255.0)  # 80% transparent in normal state.
                painter.drawImage(
                    QPointF(
                        icon_bounds.x() + (icon_bounds.width() - scaled.width()) / 2,
                        icon_bounds.y() + (icon_bounds.height() - scaled.height()) / 2,
                    ),
                    tinted,
                )
                painter.restore()
            if self._hovered:
                painter.setPen(QPen(self._text_color))
                label_color = QColor(self._text_color)
                label_color.setAlpha(51)
                painter.setPen(QPen(label_color))
                painter.setFont(QFont("Microsoft YaHei UI", 9))
                painter.drawText(QRectF(32, 0, self.width() - 36, self.height()),
                                 Qt.AlignVCenter | Qt.AlignLeft, "纯净背景")

    class InstantHelpButton(QToolButton):
        """Show the small help bubble immediately on hover or click."""
        def __init__(self, text, parent=None):
            super().__init__(parent)
            self._help_text = text
            self.setAccessibleName("推迟计划说明")
            self.setCursor(Qt.PointingHandCursor)
            self._text_color = QColor("#59616E")
            self._ring_color = QColor(32, 36, 43, 110)
            self._accent_color = QColor("#28A9F1")
            self.setStyleSheet(
                "QToolButton { border: none; background: transparent; padding: 0; }")

        def set_colors(self, text_color, ring_color, accent_color):
            self._text_color = QColor(text_color)
            self._ring_color = QColor(ring_color)
            self._accent_color = QColor(accent_color)
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            center = QRectF(self.rect()).center()
            diameter = max(0.0, min(self.width(), self.height()) - 2.0)
            circle = QRectF(center.x() - diameter / 2,
                            center.y() - diameter / 2, diameter, diameter)
            active = self.underMouse() or self.hasFocus()
            painter.setPen(QPen(self._accent_color if active else self._ring_color, 1.0))
            if active:
                background = QColor(self._ring_color)
                background.setAlpha(12)
                painter.setBrush(background)
            else:
                painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(circle)
            font = QFont("Segoe UI")
            font.setPixelSize(11)
            font.setWeight(QFont.Medium)
            glyph = QPainterPath()
            glyph.addText(0, 0, font, self.text())
            bounds = glyph.boundingRect()
            glyph.translate(center.x() - bounds.center().x(),
                            center.y() - bounds.center().y())
            painter.fillPath(glyph, self._accent_color if active else self._text_color)

        def _show_help(self):
            QToolTip.showText(
                self.mapToGlobal(QPoint(0, self.height() + 4)),
                self._help_text,
                self,
                self.rect(),
                5000,
            )

        def enterEvent(self, event):
            self._show_help()
            super().enterEvent(event)

        def leaveEvent(self, event):
            QToolTip.hideText()
            super().leaveEvent(event)

        def focusInEvent(self, event):
            self._show_help()
            super().focusInEvent(event)

        def focusOutEvent(self, event):
            QToolTip.hideText()
            super().focusOutEvent(event)

        def mousePressEvent(self, event):
            self._show_help()
            super().mousePressEvent(event)

    def button_content_min_width(button, horizontal_padding=28):
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        icon_width = button.iconSize().width() if not button.icon().isNull() else 0
        icon_gap = 8 if icon_width else 0
        return text_width + icon_width + icon_gap + horizontal_padding


    def widget_min_width(widget):
        return max(widget.minimumSizeHint().width(), widget.sizeHint().width())


    class ResponsiveActionLayout(QWidget):
        """Lay out action buttons in columns until their measured widths need wrapping."""
        def __init__(self, widgets, preferred_columns=2, expanding_indices=None, parent=None):
            super().__init__(parent)
            self.grid = QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setHorizontalSpacing(12)
            self.grid.setVerticalSpacing(10)
            self.widgets = list(widgets)
            self.preferred_columns = max(1, min(preferred_columns, len(self.widgets)))
            self._expanding_indices = set(() if expanding_indices is None else expanding_indices)
            self._columns = None
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for index, widget in enumerate(self.widgets):
                widget.setMinimumWidth(max(widget_min_width(widget), button_content_min_width(widget)))
                policy = (QSizePolicy.MinimumExpanding if index in self._expanding_indices
                          else QSizePolicy.Minimum)
                widget.setSizePolicy(policy, QSizePolicy.Fixed)
            self._reflow()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._reflow()

        def _choose_columns(self):
            available = max(0, self.width())
            safe_margin = 12
            for columns in range(self.preferred_columns, 0, -1):
                column_widths = [0] * columns
                for index, widget in enumerate(self.widgets):
                    column = index % columns
                    column_widths[column] = max(column_widths[column], widget.minimumWidth())
                required = sum(column_widths) + self.grid.horizontalSpacing() * (columns - 1)
                if available >= required + safe_margin:
                    return columns
            return 1

        def _reflow(self):
            columns = self._choose_columns()
            if columns == self._columns and self.grid.count() == len(self.widgets):
                return
            self._columns = columns
            while self.grid.count():
                self.grid.takeAt(0)
            for column in range(self.preferred_columns):
                self.grid.setColumnStretch(column, 0)
            self.setMinimumHeight(0)
            for column in range(columns):
                stretches = any(
                    index in self._expanding_indices and index % columns == column
                    for index in range(len(self.widgets)))
                self.grid.setColumnStretch(column, 1 if stretches else 0)
            for index, widget in enumerate(self.widgets):
                self.grid.addWidget(widget, index // columns, index % columns)
            self.setMinimumHeight(self.grid.sizeHint().height())
            self.updateGeometry()


    class ResponsiveDelayRow(QWidget):
        """Keep the defer-time controls together and stack them when necessary."""
        def __init__(self, label, time_edit, apply_button, help_button, parent=None):
            super().__init__(parent)
            self.grid = QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setHorizontalSpacing(8)
            self.grid.setVerticalSpacing(8)
            self.widgets = (label, time_edit, apply_button, help_button)
            self._compact = None
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._reflow()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._reflow()

        def _required_width(self):
            return (sum(widget_min_width(widget) for widget in self.widgets)
                    + self.grid.horizontalSpacing() * 3 + 12)

        def _reflow(self):
            compact = self.width() < self._required_width()
            if compact == self._compact and self.grid.count() == len(self.widgets):
                return
            self._compact = compact
            while self.grid.count():
                self.grid.takeAt(0)
            for column in range(4):
                self.grid.setColumnStretch(column, 0)
            if compact:
                self.grid.addWidget(self.widgets[0], 0, 0, 1, 4)
                for column, widget in enumerate(self.widgets[1:]):
                    self.grid.addWidget(widget, 1, column)
                self.grid.setColumnStretch(0, 1)
            else:
                for column, widget in enumerate(self.widgets):
                    self.grid.addWidget(widget, 0, column)
                self.grid.setColumnStretch(0, 1)
            self.setMinimumHeight(self.grid.sizeHint().height())
            self.updateGeometry()


    class ResponsiveSettingsGrid(QWidget):
        """Keep paired settings in two columns using measured minimum widths."""
        def __init__(self, parent=None):
            super().__init__(parent)
            self.grid = QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setHorizontalSpacing(10)
            self.grid.setVerticalSpacing(14)
            self._rows = []
            self._narrow = None
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def add_row(self, left_label, left_widget, right_label, right_widget):
            self._rows.append((left_label, left_widget, right_label, right_widget))
            self._reflow()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._reflow()

        def _wide_minimum_width(self):
            if not self._rows:
                return 0
            left_label = max(widget_min_width(row[0]) for row in self._rows)
            left_widget = max(widget_min_width(row[1]) for row in self._rows)
            right_label = max(widget_min_width(row[2]) for row in self._rows)
            right_widget = max(widget_min_width(row[3]) for row in self._rows)
            return (left_label + left_widget + right_label + right_widget
                    + 40 + self.grid.horizontalSpacing() * 4 + 12)

        def _reflow(self):
            narrow = self.width() < self._wide_minimum_width()
            if narrow == self._narrow and self.grid.count() == len(self._rows) * 4:
                return
            self._narrow = narrow
            while self.grid.count():
                self.grid.takeAt(0)
            for column in range(5):
                self.grid.setColumnStretch(column, 0)
                self.grid.setColumnMinimumWidth(column, 0)
            if narrow:
                self.grid.setColumnStretch(1, 1)
                for row, (left_label, left_widget, right_label, right_widget) in enumerate(self._rows):
                    base = row * 2
                    self.grid.addWidget(left_label, base, 0)
                    self.grid.addWidget(left_widget, base, 1)
                    self.grid.addWidget(right_label, base + 1, 0)
                    self.grid.addWidget(right_widget, base + 1, 1)
            else:
                self.grid.setColumnStretch(1, 1)
                self.grid.setColumnStretch(4, 1)
                self.grid.setColumnMinimumWidth(2, 40)
                for row, (left_label, left_widget, right_label, right_widget) in enumerate(self._rows):
                    self.grid.addWidget(left_label, row, 0)
                    self.grid.addWidget(left_widget, row, 1)
                    self.grid.addWidget(right_label, row, 3)
                    self.grid.addWidget(right_widget, row, 4)
            self.setMinimumHeight(self.grid.sizeHint().height())
            self.updateGeometry()

    class NoWheelSpinBox(SpinBox):
        def __init__(self, parent=None):
            super().__init__(parent=parent)
            for child in (self.lineEdit(), self.upButton, self.downButton):
                child.installEventFilter(self)

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Wheel:
                self.wheelEvent(event)
                return True
            return super().eventFilter(watched, event)

        def wheelEvent(self, event):
            parent = self.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is None:
                event.ignore()
                return
            viewport = parent.viewport()
            forwarded = QWheelEvent(
                QPointF(viewport.mapFromGlobal(event.globalPos())), event.globalPosF(),
                event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                event.phase(), event.inverted(), event.source())
            QApplication.sendEvent(viewport, forwarded)
            event.accept()  # The page received it; do not scroll it twice.

    class Window(MSFluentWindow):
        def __init__(self):
            super().__init__()
            self._force_quit = False
            self._instance_server = instance_server
            self._instance_mutex = instance_mutex
            # Persist this across launches. Previously v3 reset it to False on
            # every startup even though tray_notice_seen was saved correctly.
            self._tray_notice_shown = bool(settings.get("tray_notice_seen", False))
            self._win11_scrollbars = []
            self._accent_color = "#00A4E4"
            self.priorities = [selected_student_files(settings, 1), selected_student_files(settings, 2)]
            self._background_cache_key = None
            self._background_pixmap = QPixmap()
            self._background_luminance = 0.5
            self._brand_logo_clicks = 0
            self._brand_logo_click_reset_timer = QTimer(self)
            self._brand_logo_click_reset_timer.setSingleShot(True)
            self._brand_logo_click_reset_timer.setInterval(1800)
            self._brand_logo_click_reset_timer.timeout.connect(self.reset_brand_logo_clicks)
            self._preferences_save_timer = QTimer(self)
            self._preferences_save_timer.setSingleShot(True)
            self._preferences_save_timer.setInterval(250)
            self._preferences_save_timer.timeout.connect(self.persist_preferences)
            self.setWindowTitle("BACoffee - 蔚蓝档案咖啡厅助手")
            self.resize(1080, 760)
            self.setMinimumSize(920, 660)
            if COFFEE_ICON.is_file():
                self.setWindowIcon(QIcon(str(COFFEE_ICON)))
            elif COFFEE_LOGO.is_file():
                self.setWindowIcon(QIcon(str(COFFEE_LOGO)))

            self._schedule_query_closed = False
            self._schedule_query_active = False
            self._schedule_query_pending = False
            self._schedule_query_timed_out = False
            self._schedule_query_diagnostic = None
            self.schedule_query = QProcess(self)
            self.schedule_query.setProcessChannelMode(QProcess.MergedChannels)
            self.schedule_query.finished.connect(self._schedule_query_finished)
            self.schedule_query.errorOccurred.connect(self._schedule_query_error)
            self.schedule_query_timeout = QTimer(self)
            self.schedule_query_timeout.setSingleShot(True)
            self.schedule_query_timeout.setInterval(10000)
            self.schedule_query_timeout.timeout.connect(self._schedule_query_timeout)
            self.schedule_query_debounce = QTimer(self)
            self.schedule_query_debounce.setSingleShot(True)
            self.schedule_query_debounce.setInterval(250)
            self.schedule_query_debounce.timeout.connect(self._start_schedule_query)
            self.home_page = BackgroundPage(self, "homePage")
            self.task_page = BackgroundPage(self, "taskPage")
            self.invite_page = BackgroundPage(self, "invitePage")
            self.settings_page = BackgroundPage(self, "settingsPage")
            self._build_home_page()
            self._build_task_page()
            self._build_invite_page()
            self._build_settings_page()
            for switch in (self.cafe2, self.collect, self.invite, self.invite_exchange):
                switch.checkedChanged.connect(lambda *_: self.persist_preferences())
            self.rounds.valueChanged.connect(lambda *_: self._preferences_save_timer.start())
            self.addSubInterface(self.home_page, FIF.HOME, "主页")
            self.addSubInterface(self.task_page, FIF.PLAY, "任务信息")
            self.addSubInterface(self.invite_page, FIF.PEOPLE, "邀请学生")
            self.addSubInterface(self.settings_page, FIF.SETTING, "设置")
            self.update_home_immersive_state()
            self.stackedWidget.currentChanged.connect(self._schedule_page_changed)

            self.worker = QProcess(self)
            self.worker.setProcessChannelMode(QProcess.MergedChannels)
            clean_env = QProcessEnvironment.systemEnvironment()
            for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
                clean_env.remove(key)
            self.worker.setProcessEnvironment(clean_env)
            self.worker.readyReadStandardOutput.connect(self.read_worker_output)
            self.worker.finished.connect(self.worker_finished)
            self.schedule_refresh_timer = QTimer(self)
            self.schedule_refresh_timer.timeout.connect(self.refresh_schedule_status)
            self.schedule_refresh_timer.start(30000)
            qconfig.themeChanged.connect(lambda *_: self.refresh_visuals())
            self.refresh_priority()
            self.refresh_visuals()
            self.setup_tray()
            self.restore_window_position()

        def paintEvent(self, event):
            super().paintEvent(event)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            pixmap = self.background_pixmap()
            strength = max(0.0, min(1.0, settings.get("background_opacity", 50) / 100))
            # 50% now matches the old 100% image strength.  Above 50%, keep the
            # image fully opaque and progressively reduce the theme veil.
            opacity = min(1.0, strength * 2.0)
            boost = max(0.0, (strength - 0.5) * 2.0)
            if not pixmap.isNull() and opacity > 0:
                target = self.rect()
                scale = max(target.width() / pixmap.width(), target.height() / pixmap.height())
                source_w = target.width() / scale
                source_h = target.height() / scale
                source = QRectF((pixmap.width() - source_w) / 2,
                                (pixmap.height() - source_h) / 2,
                                source_w, source_h)
                painter.setOpacity(opacity)
                painter.drawPixmap(QRectF(target), pixmap, source)
                painter.setOpacity(1)
            fade = lambda normal, strong: round(normal + (strong - normal) * boost)
            luminance = self._background_luminance
            gradient = QLinearGradient(0, 0, self.width(), 0)
            if isDarkTheme():
                adaptive = round(max(-18, min(42, (luminance - 0.30) * 92)))
                gradient.setColorAt(0.0, QColor(14, 16, 21, max(0, min(255, fade(178, 76) + adaptive))))
                gradient.setColorAt(0.48, QColor(18, 20, 26, max(0, min(255, fade(112, 38) + adaptive))))
                gradient.setColorAt(1.0, QColor(14, 16, 21, max(0, min(255, fade(190, 84) + adaptive))))
            else:
                adaptive = round(max(-18, min(42, (0.70 - luminance) * 92)))
                gradient.setColorAt(0.0, QColor(250, 252, 255, max(0, min(255, fade(148, 58) + adaptive))))
                gradient.setColorAt(0.48, QColor(250, 252, 255, max(0, min(255, fade(70, 20) + adaptive))))
                gradient.setColorAt(1.0, QColor(250, 252, 255, max(0, min(255, fade(160, 68) + adaptive))))
            painter.fillRect(self.rect(), gradient)

        def apply_native_border(self):
            if os.name != "nt":
                return
            try:
                color = QColor("#292C33" if isDarkTheme() else "#9CB6C8")
                color_ref = ctypes.c_int(color.red() | (color.green() << 8) | (color.blue() << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    int(self.winId()), 34, ctypes.byref(color_ref), ctypes.sizeof(color_ref))
            except Exception:
                pass

        def setup_tray(self):
            self.tray_icon = QSystemTrayIcon(self)
            tray_icon = QIcon(str(COFFEE_ICON if COFFEE_ICON.is_file() else COFFEE_LOGO))
            self.tray_icon.setIcon(tray_icon)
            self.tray_icon.setToolTip("BACoffee")
            # Keep strong Python references: otherwise the menu can be garbage
            # collected while the tray icon is still running.
            self.tray_menu = QMenu(self)
            self.tray_show_action = QAction("显示 BACoffee", self)
            self.tray_show_action.triggered.connect(self.restore_from_tray)
            self.tray_quit_action = QAction("退出", self)
            self.tray_quit_action.triggered.connect(self.quit_from_tray)
            self.tray_menu.addAction(self.tray_show_action)
            self.tray_menu.addSeparator()
            self.tray_menu.addAction(self.tray_quit_action)
            self.tray_icon.setContextMenu(self.tray_menu)
            self.tray_icon.activated.connect(self.tray_activated)
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray_icon.show()

        def tray_activated(self, reason):
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self.restore_from_tray()

        def restore_from_tray(self):
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self.refresh_schedule_indicator()

        def restore_window_position(self):
            x, y = settings.get("window_x"), settings.get("window_y")
            if not isinstance(x, int) or not isinstance(y, int):
                screen = QApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    self.move(
                        area.left() + (area.width() - self.width()) // 2,
                        area.top() + (area.height() - self.height()) // 2,
                    )
                return
            point = QPoint(x + 40, y + 20)
            screen = QApplication.screenAt(point) or QApplication.primaryScreen()
            if screen is None:
                self.move(x, y)
                return
            area = screen.availableGeometry()
            safe_x = max(area.left(), min(x, area.right() - min(self.width(), area.width()) + 1))
            safe_y = max(area.top(), min(y, area.bottom() - min(self.height(), area.height()) + 1))
            self.move(safe_x, safe_y)

        def save_window_position(self):
            geometry = self.normalGeometry() if self.isMaximized() else self.geometry()
            settings["window_x"] = int(geometry.x())
            settings["window_y"] = int(geometry.y())
            save_settings(settings)

        def shutdown_application(self):
            if self._force_quit:
                return
            self._force_quit = True
            self.persist_preferences()
            self.save_window_position()
            self.stop_schedule_query()
            if self.worker.state() != QProcess.NotRunning:
                self.worker.terminate()
                if not self.worker.waitForFinished(3000):
                    self.worker.kill()
                    self.worker.waitForFinished(1000)
            self.tray_icon.hide()
            self.tray_icon.setContextMenu(None)
            self.tray_icon.deleteLater()
            if self._instance_server.isListening():
                self._instance_server.close()
            release_instance_mutex(self._instance_mutex)
            self._instance_mutex = None
            self.close()
            app_instance = QApplication.instance()
            if app_instance is not None:
                app_instance.processEvents()
                app_instance.quit()
            threading.Thread(
                target=self._force_exit_after_tray_quit,
                name="BACoffeeTrayExitFallback",
                daemon=True,
            ).start()

        def quit_from_tray(self):
            self.shutdown_application()

        @staticmethod
        def _force_exit_after_tray_quit():
            # A PyInstaller one-file process can otherwise remain headless if
            # Qt fails to finish teardown after the tray menu has disappeared.
            time.sleep(5)
            os._exit(0)

        def closeEvent(self, event):
            if self._force_quit:
                self.stop_schedule_query()
                self.tray_icon.hide()
                event.accept()
                return
            self.persist_preferences()
            self.save_window_position()
            if (settings.get("minimize_to_tray", DEFAULT_SETTINGS["minimize_to_tray"])
                    and QSystemTrayIcon.isSystemTrayAvailable()):
                event.ignore()
                self.hide()
                if not self._tray_notice_shown:
                    self.tray_icon.showMessage(
                        "BACoffee 已进入系统托盘",
                        "双击托盘图标可恢复窗口；右键可完全退出。",
                        QSystemTrayIcon.Information,
                        3500,
                    )
                    self._tray_notice_shown = True
                    settings["tray_notice_seen"] = True
                    save_settings(settings)
                return
            self.stop_schedule_query()
            self.tray_icon.hide()
            if self._instance_server.isListening():
                self._instance_server.close()
            event.accept()
            QApplication.quit()

        def card(self, title):
            widget = CardWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(12)
            layout.addWidget(SubtitleLabel(title))
            return widget, layout

        def reset_brand_logo_clicks(self):
            self._brand_logo_clicks = 0

        def handle_brand_logo_click(self):
            self._brand_logo_clicks += 1
            self._brand_logo_click_reset_timer.start()
            if self._brand_logo_clicks < 10:
                return
            self._brand_logo_click_reset_timer.stop()
            self._brand_logo_clicks = 0
            if EASTER_EGG_FILE.is_file():
                try:
                    os.startfile(str(EASTER_EGG_FILE))
                except OSError as exc:
                    GuiMessageBox.warning(self, "彩蛋文档", f"无法打开彩蛋文档：{exc}")
            else:
                GuiMessageBox.information(self, "彩蛋文档", "彩蛋文档暂未找到。")

        def _build_home_page(self):
            outer = QGridLayout(self.home_page)
            outer.setContentsMargins(32, 46, 40, 40)
            self.brand_group = QWidget()
            self.brand_group.setStyleSheet("background: transparent;")
            brand_layout = QVBoxLayout(self.brand_group)
            brand_layout.setContentsMargins(0, 0, 0, 0)
            brand_layout.setSpacing(0)
            self.brand_logo = QToolButton()
            self.brand_logo.setAutoRaise(True)
            self.brand_logo.setIconSize(QSize(185, 124))
            self.brand_logo.setFixedSize(205, 128)
            self.brand_logo.setStyleSheet("QToolButton { border: none; background: transparent; }")
            self.brand_logo.clicked.connect(self.handle_brand_logo_click)
            brand_layout.addWidget(self.brand_logo, 0, Qt.AlignRight)
            self.brand_title = BrandTitle()
            brand_layout.addWidget(self.brand_title, 0, Qt.AlignRight)
            self.brand_motto = BrandMotto()
            brand_layout.addWidget(self.brand_motto, 0, Qt.AlignRight)
            outer.addWidget(self.brand_group, 0, 0, Qt.AlignBottom | Qt.AlignRight)

            self.immersive_button = ImmersiveToggleButton()
            self.immersive_button.setChecked(bool(settings.get("hide_home_brand", False)))
            self.immersive_button.toggled.connect(self.toggle_home_brand)
            outer.addWidget(self.immersive_button, 0, 0, Qt.AlignTop | Qt.AlignRight)
            self.brand_group.setVisible(not self.immersive_button.isChecked())

        def _build_task_page(self):
            outer = QVBoxLayout(self.task_page)
            outer.setContentsMargins(36, 54, 36, 28)
            outer.setSpacing(14)
            outer.addWidget(TitleLabel("任务信息"))
            outer.addWidget(BodyLabel("BAAS 咖啡厅执行引擎 · BASH 头像邀请策略 · 智能休眠唤醒"))
            columns = QHBoxLayout()
            columns.setSpacing(14)
            controls, controls_layout = self.card("咖啡厅任务")
            self.cafe2 = self.add_switch(controls_layout, "处理 2 号咖啡厅", settings["cafe2"])
            self.collect = self.add_switch(controls_layout, "领取咖啡厅收益", settings["collect_reward"])
            self.invite = self.add_switch(controls_layout, "使用邀请券", settings["invite_student"])
            self.invite_exchange = self.add_switch(
                controls_layout, "允许邀请换装角色", settings["invite_allow_exchange_student"])
            self.invite_exchange.setToolTip(
                "同一学生的其他装扮已在咖啡厅时，允许按所选装扮替换。")
            self.invite_exchange.setEnabled(self.invite.isChecked())
            self.invite.checkedChanged.connect(self.invite_exchange.setEnabled)
            round_row = QHBoxLayout()
            round_row.addWidget(BodyLabel("最多摸头轮数"))
            round_row.addStretch(1)
            self.rounds = SpinBox()
            self.rounds.setRange(1, 8)
            self.rounds.setValue(settings["pat_rounds"])
            round_row.addWidget(self.rounds)
            controls_layout.addLayout(round_row)
            self.rounds_note = BodyLabel("摸头轮数越多，越不容易漏摸。")
            self.rounds_note.setObjectName("patRoundsHint")
            controls_layout.addWidget(self.rounds_note)
            controls_layout.addStretch(1)
            self.status = StatusPill("就绪")
            controls_layout.addWidget(self.status)
            run_button = PrimaryPushButton("立即执行一次")
            run_button.setIcon(FIF.PLAY)
            run_button.clicked.connect(self.run_now)
            controls_layout.addWidget(run_button)
            columns.addWidget(controls, 4)
            logs, logs_layout = self.card("运行信息")
            self.log_box = TextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setPlaceholderText("BAAS 和 MuMu 的运行信息会显示在这里")
            install_win11_scrollbars(self, self.log_box, self._accent_color)
            logs_layout.addWidget(self.log_box, 1)
            open_log = PushButton("打开完整日志")
            open_log.setIcon(FIF.DOCUMENT)
            open_log.clicked.connect(lambda: os.startfile(LOG_FILE if LOG_FILE.exists() else DATA_DIR))
            logs_layout.addWidget(open_log)
            columns.addWidget(logs, 6)
            outer.addLayout(columns, 1)

        def _build_invite_page(self):
            outer = QVBoxLayout(self.invite_page)
            outer.setContentsMargins(36, 54, 36, 30)
            outer.setSpacing(14)
            outer.addWidget(TitleLabel("邀请学生"))
            description = BodyLabel("沿用 BASH 的头像识别方式。编号代表邀请顺序，两个咖啡厅可以分别配置。")
            description.setWordWrap(True)
            outer.addWidget(description)
            self.selection_labels = []
            for cafe_index in range(2):
                card, layout = self.card(f"{cafe_index + 1} 号咖啡厅")
                label = BodyLabel()
                label.setWordWrap(True)
                label.setMinimumHeight(76)
                self.selection_labels.append(label)
                layout.addWidget(label)
                choose = PrimaryPushButton("选择邀请学生")
                choose.setIcon(FIF.PEOPLE)
                choose.setFixedHeight(40)
                choose.clicked.connect(lambda _checked=False, index=cafe_index: self.open_selector(index))
                layout.addWidget(choose, 0, Qt.AlignLeft)
                outer.addWidget(card)
            invite_note = BodyLabel(
                "如果需要一天八摸同一位学生，则两个咖啡厅都要只选择该位学生。")
            invite_note.setObjectName("hintLabel")
            invite_note.setWordWrap(True)
            outer.addWidget(invite_note)
            outer.addStretch(1)

        def _build_settings_page(self):
            page_layout = QVBoxLayout(self.settings_page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            settings_scroll = QScrollArea()
            settings_scroll.setWidgetResizable(True)
            settings_scroll.setFrameShape(QScrollArea.NoFrame)
            settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            settings_scroll.setStyleSheet(
                "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")
            install_win11_scrollbars(self, settings_scroll, self._accent_color)
            settings_content = QWidget()
            settings_content.setMinimumHeight(920)
            settings_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            settings_scroll.setWidget(settings_content)
            page_layout.addWidget(settings_scroll)

            outer = QVBoxLayout(settings_content)
            outer.setContentsMargins(36, 54, 36, 30)
            outer.setSpacing(14)
            outer.addWidget(TitleLabel("设置"))
            appearance, layout = self.card("自定义外观")
            theme_row = QHBoxLayout()
            theme_row.addWidget(BodyLabel("界面模式"))
            theme_row.addStretch(1)
            self.theme = ComboBox()
            self.theme.addItems(["跟随系统", "浅色", "深色"])
            self.theme.setFixedHeight(38)
            self.theme.setCurrentText(theme_text.get(settings.get("theme"), "跟随系统"))
            self.theme.currentTextChanged.connect(self.change_theme)
            theme_row.addWidget(self.theme)
            layout.addLayout(theme_row)
            opacity_row = QHBoxLayout()
            opacity_row.addWidget(BodyLabel("背景图强度"))
            self.opacity = Slider(Qt.Horizontal)
            self.opacity.setRange(0, 100)
            self.opacity.setValue(int(settings.get("background_opacity", 50)))
            self.opacity.valueChanged.connect(self.preview_opacity)
            opacity_row.addWidget(self.opacity, 1)
            self.opacity_value = BodyLabel(f"{self.opacity.value()}%")
            opacity_row.addWidget(self.opacity_value)
            layout.addLayout(opacity_row)

            light_row = QHBoxLayout()
            light_row.addWidget(BodyLabel("浅色模式背景"))
            light_row.addStretch(1)
            self.light_background = ComboBox()
            self.light_background.addItems(LIGHT_BACKGROUND_LABELS + [CUSTOM_BACKGROUND_LABEL])
            self.light_background.setMinimumWidth(220)
            self.light_background.setFixedHeight(40)
            light_choice = settings.get("light_background_choice", LIGHT_BACKGROUNDS[0])
            light_label = (CUSTOM_BACKGROUND_LABEL if light_choice == CUSTOM_BACKGROUND_LABEL
                           else Path(light_choice).stem)
            self.light_background.setCurrentText(
                light_label if light_label in LIGHT_BACKGROUND_LABELS + [CUSTOM_BACKGROUND_LABEL]
                else LIGHT_BACKGROUND_LABELS[0])
            self.light_background.currentTextChanged.connect(
                lambda value: self.change_background_choice("light", value))
            light_row.addWidget(self.light_background)
            layout.addLayout(light_row)

            dark_row = QHBoxLayout()
            dark_row.addWidget(BodyLabel("深色模式背景"))
            dark_row.addStretch(1)
            self.dark_background = ComboBox()
            self.dark_background.addItems(DARK_BACKGROUND_LABELS + [CUSTOM_BACKGROUND_LABEL])
            self.dark_background.setMinimumWidth(220)
            self.dark_background.setFixedHeight(40)
            dark_choice = settings.get("dark_background_choice", DARK_BACKGROUNDS[0])
            dark_label = (CUSTOM_BACKGROUND_LABEL if dark_choice == CUSTOM_BACKGROUND_LABEL
                          else Path(dark_choice).stem)
            self.dark_background.setCurrentText(
                dark_label if dark_label in DARK_BACKGROUND_LABELS + [CUSTOM_BACKGROUND_LABEL]
                else DARK_BACKGROUND_LABELS[0])
            self.dark_background.currentTextChanged.connect(
                lambda value: self.change_background_choice("dark", value))
            dark_row.addWidget(self.dark_background)
            layout.addLayout(dark_row)

            choose_light_bg = PushButton("导入浅色自定义图片")
            choose_light_bg.setIcon(FIF.PHOTO)
            choose_light_bg.setFixedHeight(40)
            choose_light_bg.clicked.connect(lambda: self.choose_background("light"))
            choose_dark_bg = PushButton("导入深色自定义图片")
            choose_dark_bg.setIcon(FIF.PHOTO)
            choose_dark_bg.setFixedHeight(40)
            choose_dark_bg.clicked.connect(lambda: self.choose_background("dark"))
            reset_bg = PushButton("恢复默认选择")
            reset_bg.setIcon(FIF.SYNC)
            reset_bg.setFixedHeight(40)
            reset_bg.clicked.connect(self.reset_backgrounds)
            bg_actions = ResponsiveActionLayout(
                [choose_light_bg, choose_dark_bg, reset_bg],
                preferred_columns=3,
                expanding_indices=(0, 1))
            layout.addWidget(bg_actions)
            outer.addWidget(appearance)

            self.game_updates_card, update_layout = self.card("游戏更新")
            update_row = QHBoxLayout()
            update_row.addWidget(BodyLabel("自动下载游戏更新"))
            self.game_update_help = InstantHelpButton(
                "打开此选项可自动下载游戏更新，无缝衔接版本更新后的自动化摸头。不过下载会消耗网络流量，"
                "使用手机热点或按流量计费的网络时可能产生额外费用，请谨慎开启。")
            self.game_update_help.setText("！")
            self.game_update_help.setAccessibleName("游戏更新下载风险说明")
            self.game_update_help.setFixedSize(18, 18)
            update_row.addWidget(self.game_update_help)
            update_row.addStretch(1)
            self.auto_download_updates = SwitchButton()
            self.auto_download_updates.setAccessibleName("自动下载游戏更新")
            self.auto_download_updates.setOnText("开")
            self.auto_download_updates.setOffText("关")
            self.auto_download_updates.setChecked(bool(settings.get("auto_download_game_updates", False)))
            update_row.addWidget(self.auto_download_updates)
            update_layout.addLayout(update_row)
            behavior, behavior_layout = self.card("应用行为")
            self.minimize_to_tray = self.add_switch(
                behavior_layout, "关闭窗口后驻留系统托盘", settings.get("minimize_to_tray", DEFAULT_SETTINGS["minimize_to_tray"]))
            self.minimize_to_tray.checkedChanged.connect(self.toggle_tray_behavior)
            self.minimize_to_tray.setToolTip(
                "关闭窗口后隐藏到系统托盘；需要退出时请使用托盘菜单。")
            emulator, emulator_layout = self.card("MuMu 模拟器")
            title_item = emulator_layout.takeAt(0)
            emulator_title = title_item.widget()
            emulator_title_row = QHBoxLayout()
            emulator_title_row.setContentsMargins(0, 0, 0, 0)
            emulator_title_row.addWidget(emulator_title)
            emulator_title_row.addStretch(1)
            self.mumu_help = InstantHelpButton(
                "实例编号与 MuMu 多开器一致，程序会自动读取对应 ADB 端口。\n"
                "启动较慢时，建议将等待时间设为 30 秒以上。\n"
                "官服和 B服共用咖啡厅识别逻辑，请选择模拟器中已安装的版本。")
            self.mumu_help.setText("?")
            self.mumu_help.setAccessibleName("MuMu 设置说明")
            self.mumu_help.setFixedSize(18, 18)
            emulator_title_row.addWidget(self.mumu_help, 0, Qt.AlignRight | Qt.AlignVCenter)
            emulator_layout.insertLayout(0, emulator_title_row)
            manager_row = QHBoxLayout()
            manager_row.addWidget(BodyLabel("模拟器位置"))
            self.mumu_manager_path = LineEdit()
            detected_manager = find_mumu_manager(settings)
            self.mumu_manager_path.setText(
                str(detected_manager or settings.get("mumu_manager_path", "")))
            self.mumu_manager_path.setMinimumWidth(0)
            self.mumu_manager_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.mumu_manager_path.setFixedHeight(40)
            manager_row.addWidget(self.mumu_manager_path, 1)
            choose_manager = PushButton("选择")
            choose_manager.setFixedSize(88, 40)
            choose_manager.clicked.connect(self.choose_mumu_manager)
            manager_row.addWidget(choose_manager)
            emulator_layout.addLayout(manager_row)

            self.game_server = ComboBox()
            self.game_server.addItems(list(CN_GAME_SERVERS))
            self.game_server.setCurrentText(normalize_game_server(settings.get("game_server")))
            self.game_server.setFixedSize(130, 40)
            self.mumu_vm_index = NoWheelSpinBox()
            self.mumu_vm_index.setRange(0, 31)
            self.mumu_vm_index.setValue(int(settings.get("mumu_vm_index", 1)))
            self.mumu_vm_index.setFixedSize(110, 40)
            self.mumu_android_version = ComboBox()
            self.mumu_android_version.addItems(["Android 15", "Android 12", "自动"])
            version = str(settings.get("mumu_android_version", "15"))
            self.mumu_android_version.setCurrentText(
                f"Android {version}" if version in {"12", "15"} else "自动")
            self.mumu_android_version.setFixedSize(150, 40)
            self.mumu_start_wait = NoWheelSpinBox()
            self.mumu_start_wait.setRange(0, 300)
            self.mumu_start_wait.setSuffix(" 秒")
            self.mumu_start_wait.setValue(int(settings.get("mumu_start_wait_seconds", DEFAULT_SETTINGS["mumu_start_wait_seconds"])))
            self.mumu_start_wait.setFixedSize(130, 40)
            emulator_layout.addSpacing(6)
            settings_grid = ResponsiveSettingsGrid()
            settings_grid.add_row(
                BodyLabel("模拟器实例"), self.mumu_vm_index,
                BodyLabel("Android"), self.mumu_android_version)
            settings_grid.add_row(
                BodyLabel("游戏渠道"), self.game_server,
                BodyLabel("启动等待"), self.mumu_start_wait)
            emulator_layout.addWidget(settings_grid)
            self.mumu_resolution_notice = BodyLabel(
                "建议将 MuMu 模拟器虚拟机分辨率设置为 1920 × 1080")
            self.mumu_resolution_notice.setObjectName("mumuResolutionHint")
            self.mumu_resolution_notice.setWordWrap(True)
            self.mumu_resolution_notice.setFont(QFont("Microsoft YaHei UI", 10))
            emulator_layout.addWidget(self.mumu_resolution_notice)
            schedule, schedule_layout = self.card("休眠唤醒")
            self.hibernate = self.add_switch(schedule_layout, "由计划任务唤醒且无人操作时，完成后重新休眠",
                                             settings["hibernate_after_success"])
            delay_label = BodyLabel("首次执行")
            self.schedule_delay_time = LineEdit()
            self.schedule_delay_time.setInputMask("00:00")
            self.schedule_delay_time.setText((datetime.now() + timedelta(minutes=3)).strftime("%H:%M"))
            self.schedule_delay_time.setAlignment(Qt.AlignCenter)
            self.schedule_delay_time.setToolTip("目标时间格式：HH:mm，例如 16:49")
            self.schedule_delay_time.setFixedSize(112, 38)
            self.schedule_defer_button = PushButton("应用")
            self.schedule_defer_button.setFixedSize(76, 38)
            self.schedule_defer_button.clicked.connect(self.defer_next_schedule)
            self.schedule_delay_info = InstantHelpButton(
                "只调整当前 12 小时时段；后续每隔 3 小时 10 分钟执行，到 04:00 或 16:00 自动恢复。")
            self.schedule_delay_info.setText("i")
            self.schedule_delay_info.setFixedSize(18, 18)
            delay_row = ResponsiveDelayRow(
                delay_label, self.schedule_delay_time,
                self.schedule_defer_button, self.schedule_delay_info)
            schedule_layout.addWidget(delay_row)

            self.schedule_status_bar = QWidget()
            self.schedule_status_bar.setObjectName("scheduleStatusBar")
            self.schedule_status_bar.setMinimumHeight(36)
            self.schedule_status_bar.setMaximumHeight(38)
            status_layout = QHBoxLayout(self.schedule_status_bar)
            status_layout.setContentsMargins(12, 0, 12, 0)
            status_layout.setSpacing(8)
            self.schedule_status_icon = BodyLabel("◷")
            self.schedule_status_icon.setFixedWidth(18)
            status_layout.addWidget(self.schedule_status_icon)
            self.schedule_defer_status = BodyLabel("按预设计划运行")
            self.schedule_defer_status.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.schedule_defer_status.setWordWrap(False)
            status_layout.addWidget(self.schedule_defer_status)
            self.schedule_restore_label = BodyLabel()
            self.schedule_restore_label.setObjectName("hintLabel")
            status_layout.addWidget(self.schedule_restore_label)
            self.schedule_status_bar.setAccessibleName("本时段计划状态")
            schedule_layout.addWidget(self.schedule_status_bar)

            self.refresh_schedule_defer_status()

            self.schedule_details_toggle = QToolButton()
            self.schedule_details_toggle.setText("默认计划")
            self.schedule_details_toggle.setCheckable(True)
            self.schedule_details_toggle.setArrowType(Qt.RightArrow)
            self.schedule_details_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.schedule_details_toggle.setAutoRaise(True)
            self.schedule_details_toggle.setCursor(Qt.PointingHandCursor)
            self.schedule_details_toggle.setFont(QFont("Microsoft YaHei UI", 10))
            self.schedule_details_toggle.setAccessibleName("展开默认计划")
            self.schedule_details_toggle.clicked.connect(self.toggle_schedule_details)
            schedule_layout.addWidget(self.schedule_details_toggle)
            self.schedule_details_widget = QWidget()
            details_layout = QVBoxLayout(self.schedule_details_widget)
            details_layout.setContentsMargins(12, 3, 12, 3)
            details_layout.setSpacing(0)
            details = BodyLabel(
                "04:00 · 07:10 · 10:20 · 13:30\n"
                "16:00 · 19:10 · 22:20 · 01:30")
            details.setObjectName("hintLabel")
            details.setWordWrap(False)
            details.setFont(QFont("Microsoft YaHei UI", 10))
            details.setMinimumHeight(details.fontMetrics().lineSpacing() * 2 + 4)
            details_layout.addWidget(details)
            self.schedule_details_layout = details_layout
            self.schedule_details_widget.setVisible(False)
            schedule_layout.addWidget(self.schedule_details_widget)

            task_button = PrimaryPushButton("安装/更新休眠唤醒计划")
            task_button.setIcon(FIF.CALENDAR)
            task_button.setFixedHeight(40)
            task_button.clicked.connect(self.install_task)
            remove_task_button = PushButton("退出休眠唤醒计划")
            remove_task_button.setIcon(FIF.DELETE)
            remove_task_button.setFixedHeight(40)
            remove_task_button.clicked.connect(self.remove_task)
            task_actions = ResponsiveActionLayout(
                [task_button, remove_task_button], preferred_columns=2,
                expanding_indices=(0,))
            schedule_layout.addWidget(task_actions)
            self.schedule_indicator = SchedulePlanIndicator()
            self.refresh_schedule_indicator()
            indicator_row = QHBoxLayout()
            indicator_row.setContentsMargins(0, 0, 0, 0)
            indicator_row.addStretch(1)
            indicator_row.addWidget(self.schedule_indicator, 0, Qt.AlignRight)
            schedule_layout.addLayout(indicator_row)
            outer.addWidget(schedule)
            outer.addWidget(emulator)
            outer.addWidget(self.game_updates_card)
            outer.addWidget(behavior)
            self.game_server.currentTextChanged.connect(lambda *_: self.persist_preferences())
            self.mumu_vm_index.valueChanged.connect(lambda *_: self.persist_preferences())
            self.mumu_android_version.currentTextChanged.connect(
                lambda *_: self.persist_preferences())
            self.mumu_start_wait.valueChanged.connect(lambda *_: self.persist_preferences())
            self.mumu_manager_path.editingFinished.connect(self.persist_preferences)
            self.hibernate.checkedChanged.connect(lambda *_: self.persist_preferences())
            self.auto_download_updates.checkedChanged.connect(self.change_auto_download_updates)
            self.opacity.sliderReleased.connect(self.persist_preferences)
            outer.addStretch(1)
            outer.activate()
            settings_content.setMinimumHeight(max(
                920,
                outer.minimumSize().height(),
                settings_content.minimumSizeHint().height()))

        def add_switch(self, layout, label, checked):
            row = QHBoxLayout()
            row.addWidget(BodyLabel(label))
            row.addStretch(1)
            switch = SwitchButton()
            switch.setOnText("开")
            switch.setOffText("关")
            switch.setChecked(bool(checked))
            row.addWidget(switch)
            layout.addLayout(row)
            return switch

        def background_path(self):
            dark = isDarkTheme()
            mode = "dark" if dark else "light"
            defaults = DARK_BACKGROUNDS if dark else LIGHT_BACKGROUNDS
            choice = settings.get(f"{mode}_background_choice", defaults[0])
            if choice == CUSTOM_BACKGROUND_LABEL:
                custom_value = settings.get(f"custom_{mode}_background", "")
                custom = Path(custom_value) if custom_value else None
                if custom and custom.is_file():
                    return custom
                choice = defaults[0]
            selected = BACKGROUND_DIR / choice
            fallback = BACKGROUND_DIR / defaults[0]
            return selected if selected.is_file() else fallback

        def background_pixmap(self):
            path = self.background_path()
            key = str(path)
            if key != self._background_cache_key:
                self._background_pixmap = QPixmap(key)
                self._background_cache_key = key
                self._background_luminance = self.measure_background_luminance(self._background_pixmap)
            return self._background_pixmap

        @staticmethod
        def measure_background_luminance(pixmap):
            if pixmap.isNull():
                return 0.5
            sample = pixmap.toImage().scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            total = weight = 0.0
            for y in range(sample.height()):
                for x in range(sample.width()):
                    color = sample.pixelColor(x, y)
                    alpha = color.alphaF()
                    total += alpha * (0.2126 * color.redF() + 0.7152 * color.greenF()
                                      + 0.0722 * color.blueF())
                    weight += alpha
            return total / weight if weight else 0.5

        def refresh_visuals(self):
            if isDarkTheme():
                card_color = "rgba(38,40,47,112)"
                field_color = "rgba(25,27,33,126)"
                button_color = "rgba(66,69,78,112)"
                border = "rgba(126,132,146,52)"
                text_color, hint_color = "#F7F8FB", "#C2C6D0"
                accent = "#FF79A8"
                logo = COFFEE_DARK_LOGO
                immersive_bg = "rgba(30,32,39,51)"
                immersive_border = "rgba(126,132,146,51)"
                immersive_checked_border = "rgba(255,121,168,13)"
            else:
                card_color = "rgba(255,255,255,78)"
                field_color = "rgba(255,255,255,82)"
                button_color = "rgba(255,255,255,94)"
                border = "rgba(130,151,170,62)"
                text_color, hint_color = "#20242B", "#59616E"
                accent = "#28A9F1"
                logo = COFFEE_LOGO
                immersive_bg = "rgba(255,255,255,51)"
                immersive_border = "rgba(130,151,170,51)"
                immersive_checked_border = "rgba(40,169,241,13)"
            setThemeColor(accent)
            rounds_hint = QColor(hint_color)
            rounds_hint.setAlphaF(0.6)
            self.rounds_note.setStyleSheet(
                f"QLabel#patRoundsHint {{ color: rgba({rounds_hint.red()},"
                f"{rounds_hint.green()},{rounds_hint.blue()},{rounds_hint.alpha()}); "
                "font-size: 11px; font-weight: 400; background: transparent; }")
            self._accent_color = accent
            help_ring_color = QColor("#F7F8FB" if isDarkTheme() else "#20242B")
            help_ring_color.setAlpha(110)
            for help_button in (self.schedule_delay_info, self.mumu_help, self.game_update_help):
                help_button.set_colors(hint_color, help_ring_color, accent)
            self.mumu_resolution_notice.setStyleSheet(
                f"QLabel#mumuResolutionHint {{ color: {accent}; "
                "font-family: 'Microsoft YaHei UI'; font-size: 13px; "
                "font-weight: 600; background: transparent; padding: 2px 0; }}")
            status_background = (
                "rgba(25,27,33,28)" if isDarkTheme() else "rgba(255,255,255,20)")
            self.schedule_status_bar.setStyleSheet(
                f"QWidget#scheduleStatusBar {{ background-color: {status_background}; "
                f"border: 1px solid {border}; border-radius: 8px; }}")
            self.schedule_status_icon.setStyleSheet(
                f"color: {hint_color}; font-size: 16px; background: transparent; border: none;")
            self.schedule_restore_label.setStyleSheet(
                f"color: {hint_color}; font-size: 12px; background: transparent; border: none;")
            self.schedule_details_toggle.setStyleSheet(
                f"QToolButton {{ color: {hint_color}; background: transparent; border: none; "
                "padding: 2px 0; text-align: left; }"
                f"QToolButton:hover {{ color: {text_color}; background: transparent; }}")
            for animator in self._win11_scrollbars:
                animator.set_theme(accent, isDarkTheme())
            self.setStyleSheet(
                "MSFluentWindow, BackgroundPage, QStackedWidget { background: transparent; }"
                f"BackgroundPage QLabel {{ color: {text_color}; }}"
                f"BackgroundPage QLabel#hintLabel {{ color: {hint_color}; font-size: 12px; }}"
                f"CardWidget {{ background-color: {card_color}; border: 1px solid {border}; border-radius: 12px; }}"
                f"TextEdit, QTextEdit {{ background-color: {field_color}; color: {text_color}; "
                f"border: 1px solid {border}; border-radius: 8px; }}"
                f"PushButton, ComboBox, SpinBox {{ background-color: {button_color}; color: {text_color}; "
                f"border: 1px solid {border}; }}"
            )
            if logo.is_file():
                self.brand_logo.setIcon(QIcon(str(logo)))
            self.brand_title.set_theme(accent, isDarkTheme())
            self.brand_motto.set_theme(accent, isDarkTheme())
            self.status.refresh_theme()
            self.immersive_button.setStyleSheet(
                f"QToolButton {{ color: {text_color}; background-color: {immersive_bg}; "
                f"border: 1px solid {immersive_border}; "
                "border-radius: 17px; padding: 0; }"
                f"QToolButton:hover {{ border-color: {accent}; }}"
                "QToolButton:checked, QToolButton:checked:hover { color: transparent; background-color: transparent; "
                f"border-color: {immersive_checked_border}; }}"
            )
            self.immersive_button.set_theme(text_color, accent)
            self.schedule_indicator.refresh_theme()
            self.update_home_immersive_state()
            self.apply_native_border()
            for page in (self.home_page, self.task_page, self.invite_page, self.settings_page):
                page.update()
            self.update()

        def change_theme(self, text):
            settings["theme"] = text_theme.get(text, "system")
            setTheme(theme_map[settings["theme"]])
            self.refresh_visuals()
            self.persist_preferences()

        def toggle_tray_behavior(self, enabled):
            settings["minimize_to_tray"] = bool(enabled)
            self.persist_preferences()

        def preview_opacity(self, value):
            settings["background_opacity"] = int(value)
            self.opacity_value.setText(f"{value}%")
            for page in (self.home_page, self.task_page, self.invite_page, self.settings_page):
                page.update()
            self.update()
            self._preferences_save_timer.start()

        def toggle_home_brand(self, hidden):
            settings["hide_home_brand"] = bool(hidden)
            self.update_home_immersive_state()
            save_settings(settings)
            self.update()

        def update_home_immersive_state(self):
            clean_home = (self.immersive_button.isChecked()
                          and self.stackedWidget.currentWidget() is self.home_page)
            self.brand_group.setVisible(not clean_home)
            self.navigationInterface.setVisible(True)
            self.titleBar.setVisible(True)
            if clean_home:
                self.stackedWidget.setStyleSheet("background: transparent; border: none;")
            else:
                FluentStyleSheet.FLUENT_WINDOW.apply(self.stackedWidget)

        def change_background_choice(self, mode, value):
            defaults = LIGHT_BACKGROUNDS if mode == "light" else DARK_BACKGROUNDS
            filenames = {Path(name).stem: name for name in defaults}
            settings[f"{mode}_background_choice"] = (
                CUSTOM_BACKGROUND_LABEL if value == CUSTOM_BACKGROUND_LABEL else filenames.get(value, defaults[0]))
            self._background_cache_key = None
            save_settings(settings)
            self.refresh_visuals()

        def choose_background(self, mode):
            filename, _ = QFileDialog.getOpenFileName(
                self, f"选择{'浅色' if mode == 'light' else '深色'}模式背景图片",
                str(Path.home()), "图片 (*.jpg *.jpeg *.png *.webp *.bmp)")
            if not filename:
                return
            source = Path(filename)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            target = DATA_DIR / (f"custom_background_{mode}" + source.suffix.lower())
            shutil.copy2(source, target)
            settings[f"custom_{mode}_background"] = str(target)
            settings[f"{mode}_background_choice"] = CUSTOM_BACKGROUND_LABEL
            combo = self.light_background if mode == "light" else self.dark_background
            combo.blockSignals(True)
            combo.setCurrentText(CUSTOM_BACKGROUND_LABEL)
            combo.blockSignals(False)
            self._background_cache_key = None
            save_settings(settings)
            self.refresh_visuals()

        def choose_mumu_manager(self):
            current_text = self.mumu_manager_path.text().strip().strip('"')
            current = Path(current_text) if current_text else None
            if current is not None and current.is_file():
                start_dir = current.parent
            elif current is not None and current.is_dir():
                start_dir = current
            else:
                start_dir = Path.home()
            directory = QFileDialog.getExistingDirectory(
                self, "选择 MuMu 模拟器主文件夹", str(start_dir))
            if not directory:
                return
            manager = resolve_mumu_manager_in_directory(directory)
            if manager is None:
                GuiMessageBox.warning(
                    self, "未找到 MuMuManager.exe",
                    "在所选文件夹中未找到 MuMuManager.exe。\n"
                    "请选择 MuMu 模拟器主文件夹或 nx_main 文件夹。")
                return
            resolved = str(manager)
            self.mumu_manager_path.setText(resolved)
            settings["mumu_manager_path"] = resolved
            save_settings(settings)

        def reset_backgrounds(self):
            settings["light_background_choice"] = LIGHT_BACKGROUNDS[0]
            settings["dark_background_choice"] = DARK_BACKGROUNDS[0]
            for combo, value in ((self.light_background, LIGHT_BACKGROUND_LABELS[0]),
                                 (self.dark_background, DARK_BACKGROUND_LABELS[0])):
                combo.blockSignals(True)
                combo.setCurrentText(value)
                combo.blockSignals(False)
            self._background_cache_key = None
            save_settings(settings)
            self.refresh_visuals()

        def refresh_priority(self):
            for index, label in enumerate(self.selection_labels):
                values = self.priorities[index]
                label.setText("  ·  ".join(f"{rank}. {Path(name).stem}" for rank, name in enumerate(values, 1))
                              if values else "尚未选择学生（将跳过邀请）")

        def persist_invite_priorities(self):
            settings["bash_students_cafe1"] = list(self.priorities[0])
            settings["bash_students_cafe2"] = list(self.priorities[1])
            save_settings_atomic(settings)

        def change_auto_download_updates(self, enabled):
            if enabled:
                blocker = QSignalBlocker(self.auto_download_updates)
                self.auto_download_updates.setChecked(False)
                blocker.unblock()
                box = GuiMessageBox(self)
                box.setWindowTitle("确认开启自动下载游戏更新")
                box.setIcon(GuiMessageBox.Warning)
                box.setText(
                    "开启后，BAC 会自动确认游戏内更新包下载，以便衔接版本更新后的自动化摸头。"
                    "下载会消耗网络流量，使用手机热点或按流量计费的网络时可能产生额外费用。是否仍要开启？")
                confirm = box.addButton("确认开启", GuiMessageBox.AcceptRole)
                cancel = box.addButton("取消", GuiMessageBox.RejectRole)
                confirm.setAutoDefault(False)
                box.setDefaultButton(cancel)
                box.setEscapeButton(cancel)
                cancel.setFocus(Qt.OtherFocusReason)
                box.exec_()
                if box.clickedButton() is confirm:
                    blocker = QSignalBlocker(self.auto_download_updates)
                    self.auto_download_updates.setChecked(True)
                    blocker.unblock()
            self.persist_preferences()

        def persist_preferences(self):
            if self._preferences_save_timer.isActive():
                self._preferences_save_timer.stop()
            settings.update({
                "cafe2": self.cafe2.isChecked(),
                "collect_reward": self.collect.isChecked(),
                "invite_student": self.invite.isChecked(),
                "invite_allow_exchange_student": self.invite_exchange.isChecked(),
                "pat_rounds": self.rounds.value(),
                "theme": text_theme.get(self.theme.currentText(), "system"),
                "background_opacity": self.opacity.value(),
                "minimize_to_tray": self.minimize_to_tray.isChecked(),
                "auto_download_game_updates": self.auto_download_updates.isChecked(),
                "game_server": normalize_game_server(self.game_server.currentText()),
                "mumu_manager_path": self.mumu_manager_path.text().strip(),
                "mumu_vm_index": self.mumu_vm_index.value(),
                "mumu_start_wait_seconds": self.mumu_start_wait.value(),
                "mumu_android_version": (
                    self.mumu_android_version.currentText().replace("Android ", "")
                    if self.mumu_android_version.currentText() != "自动" else ""),
                "hibernate_after_success": self.hibernate.isChecked(),
            })
            try:
                save_settings_atomic(settings)
            except Exception as exc:
                GuiMessageBox.critical(self, "保存失败", str(exc))

        def open_selector(self, cafe_index):
            dialog = StudentSelectorDialog(self, self.priorities[cafe_index], cafe_index + 1)
            if dialog.exec_() == QDialog.Accepted:
                self.priorities[cafe_index] = list(dialog.selected)
                self.refresh_priority()
                self.persist_invite_priorities()

        def collect_settings(self, persist=True):
            settings.update({
                "cafe2": self.cafe2.isChecked(),
                "collect_reward": self.collect.isChecked(),
                "invite_student": self.invite.isChecked(),
                "invite_allow_exchange_student": self.invite_exchange.isChecked(),
                "hibernate_after_success": self.hibernate.isChecked(),
                "pat_rounds": self.rounds.value(),
                "theme": text_theme.get(self.theme.currentText(), "system"),
                "background_opacity": self.opacity.value(),
                "minimize_to_tray": self.minimize_to_tray.isChecked(),
                "auto_download_game_updates": self.auto_download_updates.isChecked(),
                "game_server": normalize_game_server(self.game_server.currentText()),
                "mumu_manager_path": self.mumu_manager_path.text().strip(),
                "mumu_vm_index": self.mumu_vm_index.value(),
                "mumu_start_wait_seconds": self.mumu_start_wait.value(),
                "mumu_android_version": (
                    self.mumu_android_version.currentText().replace("Android ", "")
                    if self.mumu_android_version.currentText() != "自动" else ""),
                "bash_students_cafe1": self.priorities[0],
                "bash_students_cafe2": self.priorities[1],
            })
            if persist:
                save_settings(settings)
            return dict(settings)

        def run_now(self):
            try:
                if self.worker.state() != QProcess.NotRunning:
                    GuiMessageBox.information(self, "正在运行", "咖啡厅任务已经在执行。")
                    return
                requested_settings = self.collect_settings(persist=False)
                environment = QProcessEnvironment(self.worker.processEnvironment())
                environment.insert(
                    "BACOFFEE_MANUAL_SETTINGS",
                    json.dumps(requested_settings, ensure_ascii=False),
                )
                self.worker.setProcessEnvironment(environment)
                sanitize_external_runtime()
                cmd = worker_command(False)
                self.log_box.clear()
                self.log_box.append("正在启动 BAAS 咖啡厅执行器……")
                self.worker.setWorkingDirectory(str(APP_DIR))
                self.worker.start(cmd[0], cmd[1:])
                if not self.worker.waitForStarted(5000):
                    raise RuntimeError(self.worker.errorString())
                self.game_server.setEnabled(False)
                self.status.setText("正在执行咖啡厅任务")
            except Exception as exc:
                GuiMessageBox.critical(self, "启动失败", str(exc))

        def read_worker_output(self):
            value = bytes(self.worker.readAllStandardOutput()).decode("utf-8", errors="replace").rstrip()
            if value:
                self.log_box.append(value)
                if "正在停止计划任务，准备手动执行" in value:
                    self.status.setText("正在停止计划任务，准备手动执行")
                elif "mode=manual" in value:
                    self.status.setText("正在执行手动咖啡厅任务")

        def worker_finished(self, exit_code, _exit_status):
            self.read_worker_output()
            self.game_server.setEnabled(True)
            if exit_code == 0:
                try:
                    last_result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    last_result = {}
                if last_result.get("state") == "skipped_maintenance":
                    self.status.setText("维护跳过")
                    message = ("检测到服务器维护，本次任务已停止；后续任务会继续检查。"
                               if last_result.get("maintenance_state_saved") else
                               "检测到服务器维护，本次任务已停止；接续状态保存失败，请查看日志。")
                    GuiMessageBox.information(self, "BACoffee", message)
                    return
                if last_result.get("state") == "skipped_update_download":
                    self.status.setText("更新下载跳过")
                    GuiMessageBox.information(
                        self, "BACoffee", "已关闭自动下载，本次任务已跳过；后续计划保留。")
                    return
                self.status.setText("任务完成")
                message = "咖啡厅任务已完成。"
                if last_result.get("cafe_recovery_count", 0) == 1:
                    message += "\n期间自动恢复 1 次，详情见日志。"
                GuiMessageBox.information(self, "BACoffee", message)
            elif exit_code == 75:
                self.status.setText("任务未启动：已有手动任务或交接未完成，请查看运行日志")
            else:
                self.status.setText("任务失败，请查看运行日志")
                GuiMessageBox.warning(self, "BACoffee", "任务没有完成，原因已显示在运行信息中。")

        def install_task(self):
            try:
                answer = GuiMessageBox.question(
                    self, "安装休眠唤醒计划",
                    "接下来 Windows 将显示 PowerShell 的管理员权限确认窗口。\n\n"
                    "该权限仅用于创建或更新 BACoffee 的计划任务，并启用系统唤醒计时器；"
                    "程序不会获取或保存你的管理员密码。是否继续？",
                    GuiMessageBox.Yes | GuiMessageBox.No, GuiMessageBox.No)
                if answer != GuiMessageBox.Yes:
                    return
                try:
                    self.collect_settings()
                    success, error = restore_default_schedule()
                    settings.clear()
                    settings.update(load_settings())
                    self.refresh_schedule_defer_status()
                    if success:
                        self.status.setText("按预设计划运行")
                        GuiMessageBox.information(self, "计划任务", "已恢复八个每日默认唤醒计划。")
                    else:
                        self.status.setText("恢复默认计划失败，请查看提示")
                        GuiMessageBox.critical(self, "安装失败", error)
                except Exception as exc:
                    GuiMessageBox.critical(self, "安装失败", str(exc))
            finally:
                self.refresh_schedule_indicator()

        def remove_task(self):
            try:
                answer = GuiMessageBox.question(
                    self, "退出休眠唤醒计划",
                    "退出后，BACoffee 将不再按计划自动唤醒或定时运行。确定继续吗？",
                    GuiMessageBox.Yes | GuiMessageBox.No, GuiMessageBox.No)
                if answer != GuiMessageBox.Yes:
                    return
                try:
                    ps = APP_DIR / "Remove-WakeTask.ps1"
                    if not ps.is_file():
                        raise RuntimeError("发行内容不完整：缺少 Remove-WakeTask.ps1。")
                    result_path = Path(os.environ.get("TEMP", str(DATA_DIR))) / (
                        f"BACoffee-remove-{os.getpid()}-{secrets.token_hex(6)}.json")
                    result_path.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
                         "-ResultPath", str(result_path)],
                        text=True, capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    removal_result = {}
                    if result_path.is_file():
                        try:
                            removal_result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                        finally:
                            result_path.unlink(missing_ok=True)
                    if removal_result.get("status") == "success":
                        GuiMessageBox.information(
                            self, "卸载已完成",
                            "休眠唤醒计划卸载已完成。\nBACoffee 将不再自动唤醒电脑或按计划运行。")
                    else:
                        details = (removal_result.get("message") or result.stderr or result.stdout or
                                   f"卸载程序返回错误代码 {result.returncode}，计划可能尚未移除。")
                        GuiMessageBox.critical(self, "退出失败", details)
                except Exception as exc:
                    GuiMessageBox.critical(self, "退出失败", str(exc))
            finally:
                self.refresh_schedule_indicator()

        @staticmethod
        def schedule_target_text(target):
            return format_schedule_target(target)

        def refresh_schedule_defer_status(self):
            if not hasattr(self, "schedule_defer_status"):
                return
            now = datetime.now()
            override = valid_schedule_override(settings, now)
            if override:
                runs = [datetime.fromisoformat(item) for item in override["run_times"]]
                _, end = schedule_window(now)
                run_text = " → ".join(item.strftime("%H:%M") for item in runs)
                self.schedule_defer_status.setText(f"本时段  {run_text}")
                self.schedule_restore_label.setText(f"{end:%H:%M} 恢复")
                self.schedule_status_bar.setToolTip(f"本时段完整计划：{run_text}")
                return
            self.schedule_defer_status.setText("按预设计划运行")
            self.schedule_restore_label.clear()
            self.schedule_status_bar.setToolTip("")

        def toggle_schedule_details(self, expanded):
            self.schedule_details_toggle.setArrowType(
                Qt.DownArrow if expanded else Qt.RightArrow)
            self.schedule_details_toggle.setAccessibleName(
                "收起默认计划" if expanded else "展开默认计划")
            self.schedule_details_widget.setVisible(expanded)
            if expanded:
                self.schedule_details_layout.activate()
                self.schedule_details_widget.adjustSize()
                self.schedule_details_widget.updateGeometry()

        def defer_next_schedule(self):
            try:
                try:
                    now = datetime.now()
                    try:
                        selected_time = datetime.strptime(
                            self.schedule_delay_time.text().strip(), "%H:%M")
                    except ValueError:
                        GuiMessageBox.warning(self, "时间格式不正确", "请输入 HH:mm 格式，例如 16:49。")
                        return
                    target = resolve_first_deferred_time(
                        now, selected_time.hour, selected_time.minute)
                    period_start, period_end = schedule_window(now)
                    runs = build_deferred_runs(target, period_end)
                    run_text = "、".join(self.schedule_target_text(item) for item in runs)
                    reset_text = (
                        f"{period_end:%H:%M} 起恢复预设计划"
                        if period_end.date() == now.date()
                        else f"明天 {period_end:%H:%M} 起恢复预设计划")
                    period_text = f"{period_start:%Y-%m-%d %H:%M} 至 {period_end:%Y-%m-%d %H:%M}"
                    answer = GuiMessageBox.question(
                        self, "确认推迟当前时段",
                        f"当前时段：{period_text}\n"
                        f"本时段临时计划：{run_text}\n"
                        f"{reset_text}\n\n"
                        "确认后会申请管理员权限，立即更新 Windows 唤醒计划。是否继续？",
                        GuiMessageBox.Yes | GuiMessageBox.No, GuiMessageBox.No)
                    if answer != GuiMessageBox.Yes:
                        return
                    override = {
                        "period_start": period_start.isoformat(timespec="seconds"),
                        "period_end": period_end.isoformat(timespec="seconds"),
                        "first_run": target.isoformat(timespec="seconds"),
                        "run_times": [item.isoformat(timespec="seconds") for item in runs],
                        "applied": False,
                        "updated_at": now.isoformat(timespec="seconds"),
                    }
                    success, error = apply_schedule_override(override)
                    settings.clear()
                    settings.update(load_settings())
                    if not success:
                        self.refresh_schedule_defer_status()
                        self.status.setText("按预设时间（推迟未应用）")
                        GuiMessageBox.critical(self, "推迟失败", error)
                        return
                    self.refresh_schedule_defer_status()
                    self.status.setText("本时段计划已更新")
                    GuiMessageBox.information(
                        self, "计划任务", f"本时段计划已更新。\n{run_text}\n{reset_text}")
                except Exception as exc:
                    GuiMessageBox.critical(self, "推迟失败", str(exc))
            finally:
                self.refresh_schedule_indicator()

        def event(self, event):
            if event.type() == QEvent.WindowActivate and hasattr(self, "schedule_query_debounce"):
                self.refresh_schedule_indicator()
            return super().event(event)

        def _schedule_page_changed(self, *_):
            if self.stackedWidget.currentWidget() is self.settings_page:
                self.refresh_schedule_indicator()

        def refresh_schedule_status(self):
            self.refresh_schedule_defer_status()
            self.refresh_schedule_indicator()

        def refresh_schedule_indicator(self):
            if self._schedule_query_closed:
                return
            if self._schedule_query_active:
                self._schedule_query_pending = True
            elif not self.schedule_query_debounce.isActive():
                self.schedule_query_debounce.start()

        def _start_schedule_query(self):
            if self._schedule_query_closed or self._schedule_query_active:
                return
            self.schedule_query.readAllStandardOutput()
            self._schedule_query_active = True
            self._schedule_query_pending = False
            self._schedule_query_timed_out = False
            # One read-only COM lookup: HRESULT identifies absence independent of OS language.
            command = (
                "$ErrorActionPreference='Stop'; $r=@{state='unknown';code='query'}; "
                "try { $s=New-Object -ComObject Schedule.Service; $s.Connect(); $f=$s.GetFolder('\\'); "
                "try { $t=$f.GetTask('BACoffee-Cafe'); $r=@{state='installed';code='0'} } "
                "catch { $e=$_.Exception; while($e.InnerException){$e=$e.InnerException}; "
                "if($e.HResult -eq -2147024894){$r=@{state='missing';code='80070002'}} "
                "else {$r=@{state='unknown';code=('{0:X8}' -f $e.HResult)}} } } "
                "catch { $e=$_.Exception; while($e.InnerException){$e=$e.InnerException}; "
                "$r=@{state='unknown';code=('{0:X8}' -f $e.HResult)} }; $r | ConvertTo-Json -Compress")
            program = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                          "System32/WindowsPowerShell/v1.0/powershell.exe")
            self.schedule_query_timeout.start()
            self.schedule_query.start(program, ["-NoProfile", "-NonInteractive", "-Command", command])

        def _schedule_query_error(self, error):
            if error == QProcess.FailedToStart:
                self._finish_schedule_query(None, "start")

        def _schedule_query_timeout(self):
            if self._schedule_query_active:
                self._schedule_query_timed_out = True
                self.schedule_query.kill()

        def _schedule_query_finished(self, exit_code, exit_status):
            if not self._schedule_query_active:
                return
            state, diagnostic = None, "query"
            if self._schedule_query_timed_out:
                diagnostic = "timeout"
            elif exit_code == 0 and exit_status == QProcess.NormalExit:
                try:
                    result = json.loads(bytes(self.schedule_query.readAllStandardOutput()).decode("utf-8-sig"))
                    state = {"installed": True, "missing": False}.get(result.get("state"))
                    diagnostic = str(result.get("code", "query"))[:32]
                except (ValueError, UnicodeError, AttributeError):
                    diagnostic = "response"
            self._finish_schedule_query(state, diagnostic)

        def _finish_schedule_query(self, installed, diagnostic):
            if not self._schedule_query_active:
                return
            self._schedule_query_active = False
            self.schedule_query_timeout.stop()
            if self._schedule_query_closed:
                return
            self.schedule_indicator.set_installed(installed)
            if installed is None:
                self.schedule_indicator.setToolTip(f"状态未知：计划查询暂不可用（{diagnostic}）")
                if diagnostic != self._schedule_query_diagnostic:
                    append_log(f"[计划状态] 查询暂不可用（{diagnostic}）")
                self._schedule_query_diagnostic = diagnostic
            else:
                self._schedule_query_diagnostic = None
            if self._schedule_query_pending:
                self._schedule_query_pending = False
                self.schedule_query_debounce.start()

        def stop_schedule_query(self):
            self._schedule_query_closed = True
            self._schedule_query_pending = False
            self.schedule_query_debounce.stop()
            self.schedule_query_timeout.stop()
            if hasattr(self, "schedule_refresh_timer"):
                self.schedule_refresh_timer.stop()
            if self.schedule_query.state() != QProcess.NotRunning:
                self.schedule_query.kill()

    window = Window()
    window_ref[0] = window
    window.show()
    app.exec_()


def launch_sat_mode() -> int:
    """Dispatch before BAC's GUI lock; SAT owns its own Qt application/process."""
    if getattr(sys, "frozen", False):
        require_portable_install()
        compatibility = source_compatibility_diagnostic()
        if not compatibility.get("ok"):
            raise RuntimeError("BACoffee shared host/source mismatch; SAT refused.")
        metadata_path = APP_DIR / "sat-build-metadata.json"
        expected = _load_build_metadata().get("sat_metadata_sha256")
        if not expected or _sha256_file(metadata_path) != expected:
            raise RuntimeError("SAT build metadata mismatch; launch refused.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for entry in metadata["sources"].values():
            if _sha256_file(APP_DIR / entry["candidate"]) != entry["sha256"]:
                raise RuntimeError("SAT source mismatch; launch refused.")
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sat"))
    add_runtime_site_packages()
    # Resolve native dependencies from the unchanged portable image runtime.
    if hasattr(os, "add_dll_directory"):
        handles = [os.add_dll_directory(str(path)) for path in (
            RUNTIME_DIR, PORTABLE_SITE_PACKAGES / "numpy" / ".libs",
            PORTABLE_SITE_PACKAGES / "cv2") if path.is_dir()]
    from user_avatar_import_tool import main as sat_main
    from user_avatar_import_tool import _write_self_test_stage, _flush_diagnostic_streams
    arguments = sys.argv[2:]
    allowed = {"--self-test", "--layout-self-test"}
    if any(argument not in allowed for argument in arguments):
        raise RuntimeError("Unsupported SAT argument.")
    sys.argv = [str(APP_DIR / "BACoffee SAT.exe"), *arguments]
    code = sat_main()
    if any(argument in allowed for argument in arguments):
        _write_self_test_stage("7 about to exit shared SAT process")
        _flush_diagnostic_streams()
        os._exit(code)
    return code


def main() -> int:
    if sys.argv[1:2] == ["--sat"]:
        return launch_sat_mode()
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--worker-payload", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--test-mumu-environment", action="store_true")
    parser.add_argument("--power-diagnostics", action="store_true")
    parser.add_argument("--hibernate-helper", action="store_true")
    parser.add_argument("--hibernate-delay", type=int, default=5)
    parser.add_argument("--hibernate-request-id", default=None)
    parser.add_argument("--timeout-recovery-helper", action="store_true")
    parser.add_argument("--hibernate-after-timeout", action="store_true")
    args = parser.parse_args()
    if not (args.power_diagnostics or args.hibernate_helper or args.timeout_recovery_helper):
        try:
            require_portable_install()
        except RuntimeError as exc:
            print(f"[启动失败] {exc}")
            return 2
    if getattr(sys, "frozen", False) and (args.worker or args.self_test):
        compatibility = source_compatibility_diagnostic()
        print_source_compatibility_diagnostic(compatibility)
        if not compatibility.get("ok"):
            print("[启动失败] EXE与旁置源码版本不一致，拒绝委托执行。")
            return 3
        python = PORTABLE_PYTHON
        source = APP_DIR / "BACoffee.py"
        if python.is_file() and source.is_file():
            delegated = [str(python), "-X", "utf8", str(source)]
            if args.worker:
                delegated.append("--worker")
            if args.scheduled:
                delegated.append("--scheduled")
            if args.self_test:
                delegated.append("--self-test")
            sanitize_external_runtime()
            child_environment = os.environ.copy()
            child_environment["BACOFFEE_EXPECTED_SOURCE_SHA256"] = (
                compatibility.get("expected_sha256") or "")
            child_environment["BACOFFEE_BUILD_ID"] = compatibility.get("build_id") or ""
            return subprocess.run(
                delegated,
                cwd=str(APP_DIR),
                check=False,
                env=child_environment,
            ).returncode
    if args.self_test:
        return self_test()
    if args.test_mumu_environment:
        return test_mumu_environment()
    if args.power_diagnostics:
        collect_power_diagnostics()
        return 0
    if args.hibernate_helper:
        return hibernate_helper(args.hibernate_delay, args.hibernate_request_id)
    if args.timeout_recovery_helper:
        return timeout_recovery_helper(args.hibernate_after_timeout)
    if args.worker_payload:
        return worker_payload(args.scheduled)
    if args.worker:
        return run_worker(args.scheduled)
    launch_gui_v3()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
