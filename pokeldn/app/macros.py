"""The controller's macro library: one `.pokemacro` per macro in the Macros folder
[docs/gui.md, The controller]."""
import re
from dataclasses import dataclass
from pathlib import Path

from pokeldn.app.paths import MACROS
from pokeldn.pad.macro import EXTENSION, Macro, MacroError, loads


@dataclass(frozen=True)
class Entry:
    path: Path
    name: str
    error: str = ""         # why the file does not load; such a file is listed, never played


def library() -> Path:
    return MACROS


def entries() -> list[Entry]:
    return _entries(MACROS)


def _entries(directory: Path) -> list[Entry]:
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob(f"*{EXTENSION}")):
        try:
            found.append(Entry(path, loads(path.read_text(encoding="utf-8")).name))
        except (OSError, UnicodeDecodeError, MacroError) as e:
            found.append(Entry(path, path.stem, str(e)))
    return sorted(found, key=lambda e: e.name.casefold())


def _free_path(name: str, directory: Path | None = None) -> Path:
    directory = directory if directory is not None else MACROS
    stem = re.sub(r"[^\w\- ]+", "", name).strip()[:60] or "macro"
    path, n = directory / f"{stem}{EXTENSION}", 2
    while path.exists():
        path, n = directory / f"{stem} {n}{EXTENSION}", n + 1
    return path


def save(macro: Macro, path: Path | None = None) -> Path:
    path = path or _free_path(macro.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(macro.dumps(), encoding="utf-8")
    tmp.replace(path)
    return path


def load(path: Path) -> Macro:
    return loads(Path(path).read_text(encoding="utf-8"))


def import_file(source: str) -> Path:
    """Copies a shared macro into the library under a free name; refuses a file that does not load."""
    macro = loads(Path(source).read_text(encoding="utf-8"))
    return save(macro)


def remove(path: Path) -> None:
    Path(path).unlink(missing_ok=True)
