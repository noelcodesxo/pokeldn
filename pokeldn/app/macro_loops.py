"""Reusable loops are ordinary v1 macros; insertion embeds an independent repeat."""
from copy import deepcopy
from pathlib import Path

from pokeldn.app import macros
from pokeldn.pad.macro import Macro, MacroError, compile_macro, loads


def library() -> Path:
    return macros.library() / "Loops"


def entries() -> list[macros.Entry]:
    found = macros._entries(library())
    for i, entry in enumerate(found):
        if not entry.error:
            try:
                load(entry.path)
            except (OSError, UnicodeDecodeError, MacroError) as error:
                found[i] = macros.Entry(entry.path, entry.name, str(error))
    return found


def _timed(steps: list, source: Macro) -> list:
    out = deepcopy(steps)
    for step in out:
        if "repeat" in step:
            if not step["steps"]:
                raise MacroError("a saved loop cannot contain an empty repeat")
            step["steps"] = _timed(step["steps"], source)
        elif "wait" not in step:
            step.setdefault("ms", source.press_ms)
            step.setdefault("after", source.gap_ms)
    return out


def prepare(source: Macro, steps: list, name: str, count: int) -> Macro:
    """Freeze inherited timings before the body leaves its originating macro."""
    if not name.strip():
        raise MacroError("give the loop a name")
    # Validate before traversing or compiling untrusted steps.
    checked = loads(Macro(press_ms=source.press_ms, gap_ms=source.gap_ms, loop=steps).dumps())
    saved = Macro(name=name.strip(), press_ms=checked.press_ms, gap_ms=checked.gap_ms,
                  loop=_timed(checked.loop, checked), loops=count)
    repeat(saved)  # includes the extra nesting and expansion required by insertion
    return saved


def repeat(saved: Macro, count: int | None = None) -> dict:
    checked = loads(saved.dumps())
    if checked.setup or not checked.loop:
        raise MacroError("a saved loop needs a nonempty body and no Run once steps")
    block = {"repeat": checked.loops if count is None else count,
             "steps": _timed(checked.loop, checked), "note": checked.name}
    candidate = loads(Macro(loop=[block]).dumps())
    compile_macro(candidate)
    return block


def save(source: Macro, steps: list, name: str, count: int) -> Path:
    saved = prepare(source, steps, name, count)
    return macros.save(saved, macros._free_path(saved.name, library()))


def load(path: Path) -> Macro:
    saved = macros.load(path)
    repeat(saved)
    return saved


def destinations(macro: Macro):
    """Yield every destination list, identified by its section and repeat indices."""
    def walk(section, parents, steps):
        yield section, parents, steps
        for i, step in enumerate(steps):
            if "repeat" in step:
                yield from walk(section, (*parents, i), step["steps"])
    for section in ("setup", "loop"):
        yield from walk(section, (), getattr(macro, section))


def insert(macro: Macro, saved: Macro, section: str, parents: tuple[int, ...], position: int,
           count: int | None = None) -> Macro:
    """Return a validated whole candidate; neither input is mutated."""
    candidate = deepcopy(macro)
    targets = {(s, p): steps for s, p, steps in destinations(candidate)}
    if (section, parents) not in targets:
        raise MacroError("choose an existing destination")
    steps = targets[section, parents]
    if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= len(steps):
        raise MacroError("choose an existing insertion position")
    steps.insert(position, repeat(saved, count))
    candidate = loads(candidate.dumps())
    compile_macro(candidate)
    return candidate
