"""Reusable bodies preserve timing and are copied transactionally into ordinary macros."""
from copy import deepcopy

import pytest

from pokeldn.app import macro_loops as loops, macros
from pokeldn.pad import macro as m


@pytest.fixture
def library(monkeypatch, tmp_path):
    monkeypatch.setattr(macros, "MACROS", tmp_path / "Macros")
    return loops.library()


def test_library_roundtrip_unique_names_and_separate_listing(library):
    source = m.Macro(loop=[{"press": "A"}])
    a = loops.save(source, source.loop, "Fight / one", 3)
    b = loops.save(source, source.loop, "Fight / one", 3)
    assert a.parent == library and a != b
    assert len(loops.entries()) == 2 and macros.entries() == []
    saved = loops.load(a)
    assert saved.name == "Fight / one" and saved.loops == 3 and saved.setup == []
    assert m.loads(a.read_text()) == saved
    assert m.compile_macro(m.Macro(loop=[loops.repeat(saved)])).loop_ms == 750


def test_nested_default_timings_sticks_waits_and_overrides_are_frozen(library):
    body = [{"press": ["A"], "after": 0}, {"repeat": 2, "steps": [
        {"right": [0.5, -0.5], "ms": 75}, {"wait": 900, "note": "animation"}]}]
    source = m.Macro(press_ms=80, gap_ms=20, loop=body)
    before = deepcopy(source)
    saved = loops.load(loops.save(source, body, "Fight", 1))
    inserted = loops.insert(m.Macro(press_ms=500, gap_ms=700), saved, "loop", (), 0)
    assert m.compile_macro(source).entries == m.compile_macro(inserted).entries
    assert saved.loop[0] == {"press": ["A"], "ms": 80, "after": 0}
    assert saved.loop[1]["steps"][0] == {"right": [0.5, -0.5], "ms": 75, "after": 20}
    assert source == before
    assert m.loads(inserted.dumps()) == inserted


@pytest.mark.parametrize("section,parents,pos", [(s, p, n) for s, p, size in (
    ("setup", (), 2), ("loop", (), 2), ("loop", (0,), 2), ("loop", (0, 0), 1)) for n in range(size + 1)])
def test_exact_root_and_nested_positions_are_independent(section, parents, pos):
    source = m.Macro(loop=[{"press": "X"}])
    saved = loops.prepare(source, source.loop, "Fight", 2)
    destination = m.Macro(setup=[{"press": "HOME"}, {"wait": 100}], loop=[
        {"repeat": 1, "steps": [{"repeat": 1, "steps": [{"press": "B"}]}, {"wait": 200}]}, {"press": "A"}])
    before, original_saved = deepcopy(destination), deepcopy(saved)
    candidate = loops.insert(destination, saved, section, parents, pos, 3)
    target = next(steps for s, p, steps in loops.destinations(candidate) if (s, p) == (section, parents))
    assert target[pos] == {"repeat": 3, "steps": saved.loop, "note": "Fight"}
    assert destination == before and saved == original_saved
    target[pos]["steps"][0]["press"] = "Y"
    assert saved == original_saved and destination == before


def test_library_edit_or_deletion_cannot_change_existing_macro(library):
    source = m.Macro(loop=[{"press": "A"}])
    path = loops.save(source, source.loop, "Fight", 2)
    saved = loops.load(path)
    first = loops.insert(m.Macro(), saved, "loop", (), 0)
    second = loops.insert(m.Macro(), saved, "loop", (), 0)
    before = m.compile_macro(first)
    second.loop[0]["steps"][0]["ms"] = 900
    path.unlink()
    assert m.compile_macro(first) == before


@pytest.mark.parametrize("body,count,name", [([], 1, "Fight"), ([{"repeat": 2, "steps": []}], 1, "Fight"),
    ([{"wait": 0}], 1, "Fight"), ([{"press": "A"}], 0, "Fight"), ([{"press": "A"}], 10001, "Fight"),
    ([{"press": "A"}], True, "Fight"), ([{"press": "A"}], 1.5, "Fight"),
    ([{"press": "A"}], 1, "  "), ([{"press": "invalid"}], 1, "Fight")])
def test_invalid_loops_create_no_files(library, body, count, name):
    with pytest.raises(m.MacroError):
        loops.save(m.Macro(), body, name, count)
    assert not library.exists()


def test_expanded_capacity_and_additional_repeat_depth_are_checked():
    source = m.Macro(loop=[{"press": "A"}])
    with pytest.raises(m.MacroError, match="8192"):
        loops.prepare(source, source.loop, "Fight", 5000)
    body = [{"press": "A"}]
    for _ in range(m.MAX_DEPTH):
        body = [{"repeat": 1, "steps": body}]
    with pytest.raises(m.MacroError, match="deeper"):
        loops.prepare(source, body, "Fight", 1)
    saved = loops.prepare(source, source.loop, "Fight", 1)
    deep = m.Macro(loop=body)
    with pytest.raises(m.MacroError, match="deeper"):
        loops.insert(deep, saved, "loop", (0,) * m.MAX_DEPTH, 0)
    full = m.Macro(setup=[{"repeat": 4095, "steps": [{"press": "B"}]}], loop=[{"press": "B"}])
    before = deepcopy(full)
    with pytest.raises(m.MacroError, match="8192"):
        loops.insert(full, saved, "loop", (), 0)
    assert full == before


def test_invalid_destinations_are_rejected():
    source = m.Macro(loop=[{"press": "A"}])
    saved = loops.prepare(source, source.loop, "Fight", 1)
    for section, parents, position in [("missing", (), 0), ("loop", (1,), 0), ("loop", (), -1), ("loop", (), 2)]:
        with pytest.raises(m.MacroError):
            loops.insert(m.Macro(), saved, section, parents, position)


def test_damaged_files_are_listed_with_actionable_errors(library):
    library.mkdir(parents=True)
    (library / "broken.pokemacro").write_text("not JSON")
    (library / "empty.pokemacro").write_text(m.Macro().dumps())
    entries = loops.entries()
    assert len(entries) == 2 and all(entry.error for entry in entries)


def test_atomic_save_failure_preserves_existing_file(library, monkeypatch):
    from pathlib import Path
    source = m.Macro(loop=[{"press": "A"}])
    path = macros.save(source)
    before = path.read_bytes()
    monkeypatch.setattr(Path, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        macros.save(m.Macro(loop=[{"press": "B"}]), path)
    assert path.read_bytes() == before
    with pytest.raises(OSError):
        loops.save(source, source.loop, "Fight", 2)
    assert loops.entries() == []
