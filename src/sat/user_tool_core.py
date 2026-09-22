from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


CARD_OPACITY_DEFAULT = 56
CARD_OPACITY_DEFAULT_LIGHT = 76
CARD_OPACITY_MIN = 0
CARD_OPACITY_MAX = 100
OVERLAY_TRANSPARENCY_DEFAULT = 72
OVERLAY_TRANSPARENCY_DEFAULT_LIGHT = 80
OVERLAY_TRANSPARENCY_MIN = 0
OVERLAY_TRANSPARENCY_MAX = 100


class PathSelectionError(ValueError):
    pass


BUILTIN_LIBRARY_GUIDANCE = "无法确认内置学生库，请选择完整的 BACoffee 文件夹。"


@dataclass(frozen=True)
class StudentLibraryContext:
    """The two student-library views belonging to one BACoffee root."""

    bac_root: Path | None
    builtin_dir: Path | None
    external_dir: Path | None
    error: str | None = None

    @property
    def ready(self) -> bool:
        return (
            self.error is None
            and self.bac_root is not None
            and self.builtin_dir is not None
            and self.external_dir is not None
        )


@dataclass(frozen=True)
class StudentLibraryDuplicate:
    """Case-insensitive collision result for one generated avatar filename."""

    status: str
    builtin_path: Path | None = None
    external_path: Path | None = None
    error: str | None = None


def _readable_directory(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        with os.scandir(path):
            return True
    except OSError:
        return False


def overlay_transparency_default(theme: str | None = None) -> int:
    """Return the theme-specific default for the full-window background mask."""
    return (
        OVERLAY_TRANSPARENCY_DEFAULT_LIGHT
        if theme == "light"
        else OVERLAY_TRANSPARENCY_DEFAULT
    )


def card_opacity_default(theme: str | None = None) -> int:
    """Return the theme-specific default for homepage card transparency."""
    return (
        CARD_OPACITY_DEFAULT_LIGHT
        if theme == "light"
        else CARD_OPACITY_DEFAULT
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_safe_student_library(path: Path, tool_root: Path) -> tuple[bool, str]:
    try:
        resolved = path.resolve()
    except OSError:
        return False, "学生库路径无法解析"
    if not resolved.is_dir():
        return False, "学生库目录不存在"
    if resolved.anchor == resolved:
        return False, "不能选择磁盘根目录"
    try:
        home = Path.home().resolve()
    except OSError:
        home = Path.home()
    if resolved == home:
        return False, "不能选择用户主目录本身"
    blocked = [
        Path(os.environ.get("WINDIR", r"C:\Windows")).resolve(),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve(),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve(),
    ]
    if any(resolved == item or _is_relative_to(resolved, item) for item in blocked):
        return False, "不能选择 Windows 或 Program Files 目录"
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        try:
            if _is_relative_to(resolved, Path(meipass).resolve()):
                return False, "不能把临时解包目录作为学生库"
        except OSError:
            pass
    if resolved.name.casefold() != "students":
        return False, "请选择名为 students 的学生库目录"
    if resolved.parent.name.casefold() == "assets":
        return False, "不能使用 assets\\students 作为外置学生库"
    return True, ""


def resolve_student_library(selection: Path, tool_root: Path) -> Path:
    context = resolve_student_library_context(selection, tool_root)
    assert context.external_dir is not None
    return context.external_dir


def _selection_root(selection: Path) -> Path:
    """Normalize a BAC root, user_assets folder, or its students folder."""
    selected = selection.resolve()
    name = selected.name.casefold()
    if name == "students":
        parent_name = selected.parent.name.casefold()
        if parent_name == "assets":
            raise PathSelectionError("不能使用 assets\\students 作为外置学生库")
        if parent_name == "user_assets":
            return selected.parent.parent
        # A direct students choice is only accepted as the external folder when
        # it is visibly under user_assets; this prevents unrelated folders from
        # being paired with a different BAC package on the same disk.
        raise PathSelectionError(
            "所选 students 目录不在 user_assets 中，请选择完整的 BACoffee 文件夹。")
    if name == "user_assets":
        return selected.parent
    return selected


def resolve_student_library_context(selection: Path, tool_root: Path) -> StudentLibraryContext:
    """Resolve both read-only built-in and writable external libraries.

    All paths are derived from one selected BACoffee root.  This deliberately
    does not search the disk or pair directories from different packages.
    """
    try:
        bac_root = _selection_root(selection)
    except (OSError, RuntimeError) as exc:
        raise PathSelectionError(f"所选 BACoffee 文件夹无法解析：{exc}") from exc
    external = bac_root / "user_assets" / "students"
    builtin = bac_root / "assets" / "students"
    valid, reason = is_safe_student_library(external, tool_root)
    if not valid or not _readable_directory(external):
        raise PathSelectionError(
            "所选位置不是可用的学生库。请选择包含 user_assets\\students 的 BACoffee 文件夹，"
            "或选择该目录本身。")
    if not _readable_directory(builtin):
        raise PathSelectionError(BUILTIN_LIBRARY_GUIDANCE)
    try:
        resolved_root = bac_root.resolve()
        resolved_external = external.resolve()
        resolved_builtin = builtin.resolve()
    except OSError as exc:
        raise PathSelectionError(f"学生库路径无法解析：{exc}") from exc
    if (
        resolved_external.parent.parent != resolved_root
        or resolved_builtin.parent.parent != resolved_root
    ):
        raise PathSelectionError(BUILTIN_LIBRARY_GUIDANCE)
    return StudentLibraryContext(
        resolved_root,
        resolved_builtin,
        resolved_external,
    )


def student_library_context_or_error(
    selection: Path | None,
    tool_root: Path,
) -> StudentLibraryContext:
    """Return a non-throwing context for UI refresh and saved settings."""
    if selection is None:
        return StudentLibraryContext(None, None, None, BUILTIN_LIBRARY_GUIDANCE)
    try:
        return resolve_student_library_context(selection, tool_root)
    except PathSelectionError as exc:
        try:
            root = _selection_root(selection)
            external = (root / "user_assets" / "students").resolve()
        except (OSError, PathSelectionError):
            external = selection
        return StudentLibraryContext(None, None, external, str(exc))


def _casefold_file(directory: Path, filename: str) -> Path | None:
    key = filename.casefold()
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise OSError(f"无法读取学生库目录：{directory}") from exc
    for entry in entries:
        try:
            if entry.is_file() and entry.name.casefold() == key:
                return entry
        except OSError as exc:
            raise OSError(f"无法读取学生库文件：{entry}") from exc
    return None


def check_student_library_duplicate(
    context: StudentLibraryContext,
    filename: str,
) -> StudentLibraryDuplicate:
    """Check one generated filename in both libraries using casefold()."""
    if not context.ready:
        return StudentLibraryDuplicate(
            "error",
            error=context.error or BUILTIN_LIBRARY_GUIDANCE,
        )
    if not filename:
        return StudentLibraryDuplicate("none")
    assert context.builtin_dir is not None
    assert context.external_dir is not None
    try:
        builtin_path = _casefold_file(context.builtin_dir, filename)
        external_path = _casefold_file(context.external_dir, filename)
    except OSError as exc:
        return StudentLibraryDuplicate("error", error=str(exc))
    if builtin_path is not None and external_path is not None:
        status = "both"
    elif builtin_path is not None:
        status = "builtin"
    elif external_path is not None:
        status = "external"
    else:
        status = "none"
    return StudentLibraryDuplicate(status, builtin_path, external_path)


def discover_student_libraries(tool_root: Path) -> list[Path]:
    resolved_tool_root = tool_root.resolve()
    # The EXE is either at the package root or one level below it.  Keep this
    # deliberately narrow so development directories and sibling packages
    # cannot become accidental write targets.
    roots = [resolved_tool_root]
    package_parent = resolved_tool_root.parent
    if package_parent != resolved_tool_root:
        roots.append(package_parent)
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root / "user_assets" / "students"
        valid, _ = is_safe_student_library(candidate, tool_root)
        key = str(candidate.resolve()).casefold() if candidate.exists() else ""
        if valid and key not in seen:
            found.append(candidate.resolve())
            seen.add(key)
    return found


class ToolSettings:
    """Settings isolated to the user tool, with legacy background migration."""

    THEMES = ("dark", "light")

    def __init__(
        self,
        tool_root: Path,
        default_background: Path | None = None,
        light_background: Path | None = None,
    ):
        self.tool_root = tool_root.resolve()
        self.data_dir = self.tool_root / "data"
        self.path = self.data_dir / "settings.json"
        self.default_background_source = (
            default_background or self.tool_root / "assets" / "深色背景.png"
        ).resolve()
        self.default_light_background_source = (
            light_background or self.tool_root / "assets" / "浅色背景.jpg"
        ).resolve()
        self.default_background_target = (
            self.data_dir / f"default_background{self.default_background_source.suffix.lower() or '.jpg'}"
        )
        self.default_light_background_target = (
            self.data_dir / f"default_background_light{self.default_light_background_source.suffix.lower() or '.jpg'}"
        )
        self.data: dict = {}
        self.load()

    def load(self) -> dict:
        self.data = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = raw
            except (OSError, ValueError):
                self.data = {}
        # The initial user version stored one `background` value. Treat it as
        # the dark-theme background so existing custom images are preserved.
        if "theme" not in self.data:
            self.data["theme"] = "dark"
        if "dark_background" not in self.data and self.data.get("background"):
            self.data["dark_background"] = self.data["background"]
        self._migrate_legacy_dark_default_reference()
        return self.data

    def _migrate_legacy_dark_default_reference(self) -> None:
        """Keep a cached JPG default from shadowing the new PNG default."""
        legacy = (self.data_dir / "default_background.jpg").resolve()
        target = self.default_background_target.resolve()
        changed = False
        for key in ("dark_background", "background"):
            value = self._deserialize_path(self.data.get(key))
            if value is not None and value == legacy:
                self.data[key] = self._serialize_path(target)
                changed = True
        if changed:
            try:
                self.save()
            except OSError:
                pass

    def theme(self) -> str:
        value = self.data.get("theme")
        return value if value in self.THEMES else "dark"

    def set_theme(self, theme: str) -> None:
        if theme not in self.THEMES:
            raise ValueError(f"不支持的主题：{theme}")
        self.data["theme"] = theme
        self.save()

    def pure_background(self) -> bool:
        """Return SAT's saved pure-background state; invalid values stay off."""
        value = self.data.get("pure_background", False)
        return value if type(value) is bool else False

    def set_pure_background(self, enabled: bool) -> bool:
        """Persist only the SAT-local pure-background toggle."""
        self.data["pure_background"] = bool(enabled)
        self.save()
        return bool(enabled)

    def card_opacity(self, theme: str | None = None) -> int:
        """Return card transparency percentage for the selected theme."""
        theme = theme or self.theme()
        if theme not in self.THEMES:
            theme = "dark"
        default = card_opacity_default(theme)
        raw = self.data.get(
            f"{theme}_card_opacity",
            self.data.get("card_opacity", default),
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(CARD_OPACITY_MIN, min(CARD_OPACITY_MAX, value))

    def set_card_opacity(self, value: int, theme: str | None = None) -> int:
        theme = theme or self.theme()
        if theme not in self.THEMES:
            raise PathSelectionError("透明度主题无效")
        normalized = max(CARD_OPACITY_MIN, min(CARD_OPACITY_MAX, int(value)))
        self.data[f"{theme}_card_opacity"] = normalized
        self.save()
        return normalized

    def restore_card_opacity(self, theme: str | None = None) -> int:
        theme = theme or self.theme()
        return self.set_card_opacity(card_opacity_default(theme), theme)

    def overlay_transparency(self, theme: str | None = None) -> int:
        """Return full-window background-mask transparency for the theme."""
        theme = theme or self.theme()
        if theme not in self.THEMES:
            theme = "dark"
        raw = self.data.get(
            f"{theme}_overlay_transparency",
            self.data.get("overlay_transparency", overlay_transparency_default(theme)),
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = overlay_transparency_default(theme)
        return max(OVERLAY_TRANSPARENCY_MIN, min(OVERLAY_TRANSPARENCY_MAX, value))

    def set_overlay_transparency(self, value: int, theme: str | None = None) -> int:
        theme = theme or self.theme()
        if theme not in self.THEMES:
            raise PathSelectionError("遮罩透明度主题无效")
        normalized = max(OVERLAY_TRANSPARENCY_MIN, min(OVERLAY_TRANSPARENCY_MAX, int(value)))
        self.data[f"{theme}_overlay_transparency"] = normalized
        self.save()
        return normalized

    def restore_overlay_transparency(self, theme: str | None = None) -> int:
        theme = theme or self.theme()
        return self.set_overlay_transparency(overlay_transparency_default(theme), theme)

    def _serialize_path(self, value: Path) -> dict:
        resolved = value.resolve()
        try:
            relative = resolved.relative_to(self.tool_root)
            return {"kind": "relative", "value": relative.as_posix()}
        except ValueError:
            return {"kind": "absolute", "value": str(resolved)}

    def _deserialize_path(self, value: object) -> Path | None:
        if isinstance(value, dict):
            kind = value.get("kind")
            raw = value.get("value")
            if not isinstance(raw, str) or not raw:
                return None
            if kind == "relative":
                return (self.tool_root / raw).resolve()
            if kind == "absolute":
                return Path(raw).resolve()
        if isinstance(value, str) and value:
            raw_path = Path(value)
            return (self.tool_root / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        return None

    def student_library(self) -> Path | None:
        value = self._deserialize_path(self.data.get("student_library"))
        if value is None:
            return None
        valid, _ = is_safe_student_library(value, self.tool_root)
        return value if valid else None

    def _ensure_default_background(self, theme: str = "dark") -> Path:
        if theme == "light":
            target = self.default_light_background_target
            source = self.default_light_background_source
        else:
            target = self.default_background_target
            source = self.default_background_source
        if target.is_file():
            return target
        if not source.is_file():
            return source
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, temporary)
            temporary.replace(target)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return source
        return target

    def background_path(self, theme: str | None = None) -> Path:
        theme = theme or self.theme()
        if theme not in self.THEMES:
            theme = "dark"
        value = self._deserialize_path(self.data.get(f"{theme}_background"))
        if value is not None and value.is_file():
            return value
        return self._ensure_default_background(theme)

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def set_student_library(self, path: Path) -> None:
        valid, reason = is_safe_student_library(path, self.tool_root)
        if not valid:
            raise PathSelectionError(reason)
        self.data["student_library"] = self._serialize_path(path)
        self.save()

    def set_background(self, path: Path, theme: str | None = None) -> Path:
        if not path.is_file():
            raise PathSelectionError("背景文件不存在")
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise PathSelectionError("背景只支持 PNG、JPG、JPEG 或 WEBP")
        theme = theme or self.theme()
        if theme not in self.THEMES:
            raise PathSelectionError("背景主题无效")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        target = self.data_dir / f"custom_background_{theme}{suffix}"
        shutil.copy2(path, target)
        self.data[f"{theme}_background"] = self._serialize_path(target)
        if theme == "dark":
            self.data["background"] = self._serialize_path(target)
        self.save()
        return target

    def restore_default_background(self, theme: str | None = None) -> Path:
        theme = theme or self.theme()
        if theme not in self.THEMES:
            raise PathSelectionError("背景主题无效")
        default = self._ensure_default_background(theme)
        self.data[f"{theme}_background"] = self._serialize_path(default)
        if theme == "dark":
            self.data["background"] = self._serialize_path(default)
        self.save()
        return default
