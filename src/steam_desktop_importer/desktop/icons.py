"""Icon resolution for desktop entries.

The ``Icon=`` key holds either an absolute path or an icon-theme name. §5.1
carries both ``icon_name`` and ``icon_source_path``, so this module turns the
former into the latter.

This is the one place ``pyxdg`` is used, for its icon theme lookup. Theme
lookup involves index files, inheritance chains and size selection that are
not worth reimplementing.

Note that resolving a *source* icon is not the same as assigning a Steam
shortcut icon. §20 requires the shortcut icon to be a persistent absolute path
that the importer writes itself; that is Phase 9 work.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ICON_EXTENSIONS", "resolve_icon"]

ICON_EXTENSIONS = (".png", ".svg", ".svgz", ".xpm")

try:  # pragma: no cover - exercised implicitly by resolve_icon
    from xdg import IconTheme as _IconTheme
except ImportError:  # pragma: no cover
    _IconTheme = None


def resolve_icon(
    icon_name: str | None,
    size: int = 256,
    theme: str | None = None,
    search_paths: list[Path] | None = None,
) -> Path | None:
    """Resolve ``Icon=`` to a file on disk, or ``None`` if not found.

    Args:
        icon_name: The raw ``Icon=`` value: an absolute path or a theme name.
        size: Preferred nominal icon size. Theme lookup picks the best match.
        theme: Icon theme name. Defaults to pyxdg's configured theme.
        search_paths: Extra directories to check for ``<name><extension>``,
            used for provider trees that ship icons outside a theme.
    """
    if not icon_name:
        return None

    candidate = Path(icon_name)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    # A bare name that already carries an extension is not a theme name.
    if candidate.suffix in ICON_EXTENSIONS:
        stem = candidate.stem
    else:
        stem = icon_name

    for directory in search_paths or []:
        for extension in ICON_EXTENSIONS:
            path = directory / f"{stem}{extension}"
            if path.is_file():
                return path

    if _IconTheme is None:
        return None

    found = (
        _IconTheme.getIconPath(stem, size, theme)
        if theme
        else _IconTheme.getIconPath(stem, size)
    )
    if not found:
        return None
    path = Path(found)
    return path if path.is_file() else None
