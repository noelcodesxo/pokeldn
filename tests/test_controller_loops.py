"""Capture and insertion failures must never discard a sequence or mutate its destination."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

ft = pytest.importorskip("flet")
from gui.views.controller import ControllerView
from pokeldn.app import macros, macro_loops
from pokeldn.pad import macro as m


class Page:
    def __init__(self):
        self.dialogs = []

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.dialogs.pop()


def walk(control):
    yield control
    for attr in ("content", "controls"):
        child = getattr(control, attr, None)
        for c in child if isinstance(child, list) else [child] if isinstance(child, ft.Control) else []:
            yield from walk(c)


def fields(dialog, cls=ft.TextField):
    return [c for c in walk(dialog.content) if isinstance(c, cls)]


def click(dialog, label):
    next(button for button in dialog.actions if button.content == label).on_click(None)


@pytest.fixture
def view(monkeypatch, tmp_path):
    monkeypatch.setattr(macros, "MACROS", tmp_path / "Macros")
    monkeypatch.setattr(ft.BaseControl, "update", lambda self: None)
    app = SimpleNamespace(page=Page(), board_listeners=[], controllers=[], identities={}, ui=lambda f: f())
    view = ControllerView(app)
    view.sent = []
    view._send_later = lambda op, **kw: view.sent.append((op, kw))
    view.new()
    yield view
    view.sender.shutdown(wait=True)


def record(view, key="A", start=1.0, end=1.1):
    view.pressed_at = start
    view._record(key, end)


def test_capture_isolated_with_timing_boundaries_pause_resume_and_cancel(view):
    before = view.path.read_bytes()
    view.held = {"A"}
    view.last_up = 0.5
    view._start_loop()
    assert not view.held and view.last_up == 0 and view.recording
    record(view)
    record(view, "LS_UP", 1.4, 1.6)
    assert view.macro.loop == [] and view.path.read_bytes() == before
    assert view.loop_capture == [{"press": ["A"], "ms": 100, "after": 300}, {"left": [0.0, 1.0], "ms": 200}]
    view._set_recording(False)
    record(view, "B", 10, 11)
    assert len(view.loop_capture) == 2
    view._set_recording(True)
    record(view, "B", 20, 20.1)
    assert "after" not in view.loop_capture[1]  # pause omitted, source default retained
    view._cancel_loop()
    assert view.loop_capture is None and not view.recording and view.last_up == 0
    assert view.path.read_bytes() == before
    view._set_recording(True)
    record(view)
    assert len(view.macro.loop) == 1  # ordinary recording still works


def test_leave_preserves_capture_and_return_requires_explicit_resume(view, monkeypatch):
    view._start_loop()
    record(view)
    view.held = {"A"}
    view.leave()
    assert not view.recording and not view.held and len(view.loop_capture) == 1
    monkeypatch.setattr("gui.views.controller.threading.Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    view.enter()
    record(view, "B", 20, 20.1)
    assert len(view.loop_capture) == 1
    view._set_recording(True)
    record(view, "B", 30, 30.1)
    assert len(view.loop_capture) == 2 and "after" not in view.loop_capture[0]


def test_finish_save_failure_retry_and_cancel_insert_keep_exactly_one_saved_loop(view, monkeypatch):
    view._start_loop()
    record(view)
    buffer = deepcopy(view.loop_capture)
    before = view.path.read_bytes()
    view._finish_loop()
    dialog = view.app.page.dialogs[-1]
    name, count = fields(dialog)
    name.value, count.value = "Fight", "3"
    real_save = macro_loops.save
    monkeypatch.setattr(macro_loops, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    click(dialog, "Save loop")
    assert view.app.page.dialogs[-1] is dialog and view.loop_capture == buffer
    assert not view.recording and view.path.read_bytes() == before and macro_loops.entries() == []
    monkeypatch.setattr(macro_loops, "save", real_save)
    click(dialog, "Save loop")
    assert view.loop_capture is None and len(macro_loops.entries()) == 1
    chooser = view.app.page.dialogs[-1]
    assert chooser is not dialog and fields(chooser)[0].value == "3"
    click(chooser, "Cancel")
    assert macro_loops.load(macro_loops.entries()[0].path).loop[0]["ms"] == 100
    assert view.macro.loop == [] and view.path.read_bytes() == before


def test_empty_and_invalid_finish_keep_buffer_and_dialog(view):
    view._start_loop()
    view._finish_loop()
    dialog = view.app.page.dialogs[-1]
    name, count = fields(dialog)
    name.value = "Fight"
    click(dialog, "Save loop")
    assert view.loop_capture == [] and view.app.page.dialogs[-1] is dialog
    click(dialog, "Cancel")
    view._set_recording(True)
    record(view)
    view._finish_loop()
    dialog = view.app.page.dialogs[-1]
    name, count = fields(dialog)
    name.value, count.value = "Fight", "0"
    click(dialog, "Save loop")
    assert len(view.loop_capture) == 1 and macro_loops.entries() == []


def test_capture_guards_all_lifecycle_and_structural_callbacks(view):
    view.macro.loop = [{"press": "B"}, {"repeat": 2, "steps": [{"press": "A"}]}]
    view.save()
    original, path, before = view.macro, view.path, deepcopy(view.macro)
    # Obtain field/add callbacks before capture, so guard checks also cover queued events.
    setter = next(c for c in walk(view._number("Press", 100, lambda v: setattr(original, "press_ms", v)))
                  if isinstance(c, ft.TextField))
    adders = view._adders(original.loop)
    view._start_loop()
    record(view)
    for action in [view.new, lambda: view.open(path), view._delete, view.play,
                   lambda: view._target("setup"), lambda: view._move(original.loop, 1, -1),
                   lambda: view._remove_step(original.loop, 0), lambda: view._edit(original.loop, 0),
                   lambda: view._insert_loop_dialog(),
                   lambda: view._save_loop_dialog(original.loop, 1)]:
        action()
    asyncio.run(view._import(None))  # no file picker is called
    setter.on_blur(SimpleNamespace(control=SimpleNamespace(value="900")))
    adders.controls[0].on_click(None)
    assert view.macro is original and original == before and view.path == path
    assert len(view.loop_capture) == 1 and not view.app.page.dialogs
    assert all(button.disabled for button in view.library_buttons) and view.list.disabled
    assert view.editor.controls[0].disabled
    assert view.note.value == "Finish or cancel loop first."
    view.stop()
    assert view.sent[-1][0] == "stop"


def test_insertion_save_failure_keeps_macro_and_chooser_retry_has_no_duplicate(view, monkeypatch):
    view.macro.setup = [{"press": "HOME"}]
    view.macro.loop = [{"repeat": 2, "steps": [{"press": "B"}, {"wait": 200}]}]
    view.save()
    path = macro_loops.save(view.macro, [{"press": "A"}], "Fight", 2)
    original, before = view.macro, view.path.read_bytes()
    view._insert_loop_dialog(path)
    dialog = view.app.page.dialogs[-1]
    saved, destination, position = fields(dialog, ft.Dropdown)
    assert destination.value == "1" and position.value == "1"  # current root end
    assert destination.options[-1].text == "Loop / Step 1 (repeat)"
    destination.value = "2"
    destination.on_select(SimpleNamespace(control=destination))
    position.value = "1"
    fields(dialog)[0].value = "3"
    real_save = macros.save
    monkeypatch.setattr(macros, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("read only")))
    click(dialog, "Insert loop")
    assert view.macro is original and view.path.read_bytes() == before
    assert view.app.page.dialogs[-1] is dialog and len(macro_loops.entries()) == 1
    monkeypatch.setattr(macros, "save", real_save)
    click(dialog, "Insert loop")
    assert not view.app.page.dialogs and view.macro is not original
    assert view.macro.loop[0]["steps"][1]["repeat"] == 3
    assert view.macro.loop[0]["steps"][1]["note"] == "Fight"
    assert original.loop[0]["steps"] == [{"press": "B"}, {"wait": 200}]
    assert macros.load(view.path) == view.macro and len(macro_loops.entries()) == 1


def test_repeat_save_as_loop_preserves_source_defaults_and_does_not_insert(view):
    view.macro.press_ms, view.macro.gap_ms = 80, 20
    view.macro.loop = [{"repeat": 3, "steps": [{"press": "A"}], "note": "Fight"}]
    before = deepcopy(view.macro)
    action = next(c for row in view._steps(view.macro.loop, 0) for c in walk(row)
                  if isinstance(c, ft.TextButton) and c.content == "Save as loop")
    action.on_click(None)
    dialog = view.app.page.dialogs[-1]
    assert [f.value for f in fields(dialog)] == ["Fight", "3"]
    click(dialog, "Save loop")
    assert not view.app.page.dialogs and view.macro == before
    assert macro_loops.load(macro_loops.entries()[0].path).loop == [{"press": "A", "ms": 80, "after": 20}]


def test_empty_and_corrupt_library_feedback(view):
    view._insert_loop_dialog()
    assert not view.app.page.dialogs and "No saved loops" in view.note.value
    macro_loops.library().mkdir()
    (macro_loops.library() / "broken.pokemacro").write_text("broken")
    original, before = view.macro, view.path.read_bytes()
    view._insert_loop_dialog()
    dialog = view.app.page.dialogs[-1]
    click(dialog, "Insert loop")
    assert view.macro is original and view.path.read_bytes() == before
    assert any("Choose a loop" in (c.value or "") for c in walk(dialog.content) if isinstance(c, ft.Text))


def test_invalid_candidate_retains_chooser_and_inserted_repeat_remains_editable(view):
    path = macro_loops.save(view.macro, [{"press": "A"}], "Fight", 2)
    view.macro.loop = [{"press": "B"}]
    view.save()
    before = view.path.read_bytes()
    view._insert_loop_dialog(path)
    dialog = view.app.page.dialogs[-1]
    fields(dialog)[0].value = "5000"
    click(dialog, "Insert loop")
    assert view.app.page.dialogs[-1] is dialog and view.path.read_bytes() == before
    assert view.macro.loop == [{"press": "B"}]
    fields(dialog)[0].value = "2"
    click(dialog, "Insert loop")
    view._move(view.macro.loop, 1, -1)
    view._edit(view.macro.loop, 0)
    edit = view.app.page.dialogs[-1]
    fields(edit)[0].value = "3"
    click(edit, "Save")
    assert view.macro.loop[0]["repeat"] == 3
    view._remove_step(view.macro.loop[0]["steps"], 0)
    view._remove_step(view.macro.loop, 0)
    assert view.macro.loop == [{"press": "B"}]
    assert macro_loops.load(path).loops == 2
    assert macro_loops.load(path).loop == [{"press": "A", "ms": 100, "after": 150}]


def test_chooser_full_hierarchy_and_beginning_position(view):
    path = macro_loops.save(view.macro, [{"press": "A"}], "Fight", 1)
    view.macro.setup = [{"repeat": 2, "steps": [{"repeat": 2, "steps": [{"press": "B"}], "note": "Inner"}], "note": "Outer"}]
    view.target = "setup"
    view._insert_loop_dialog(path)
    dialog = view.app.page.dialogs[-1]
    _, destination, position = fields(dialog, ft.Dropdown)
    assert destination.value == "0" and position.value == "1"
    assert destination.options[2].text == "Run once / Step 1 (repeat) · Outer / Step 1 (repeat) · Inner"
    destination.value = "2"
    destination.on_select(SimpleNamespace(control=destination))
    assert [option.text for option in position.options] == ["Beginning", "After step 1"]
    position.value = "0"
    click(dialog, "Insert loop")
    assert view.macro.setup[0]["steps"][0]["steps"][0]["note"] == "Fight"
