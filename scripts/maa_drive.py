"""Drive the MAA WPF GUI through UI Automation (pywinauto uia backend).

Why UIA instead of editing gui.json: MAA rewrites its config on exit, so live
control has to go through the widgets.

Why not PostMessage: MAA is WPF, and WPF does not process WM_LBUTTONDOWN
posted to the window -- it has its own input pipeline. PostMessage clicks on
its checkboxes are silently dropped (verified). UIA is the supported way in.

Focus policy -- the whole point of this module is to drive MAA WITHOUT
bringing it to the foreground, so you can keep working in another window:

  * Read-only operations (element_info, window_text, get_toggle_state,
    iface_value.SetValue) never touch focus.
  * UIA Toggle/Invoke DO steal foreground: the WPF control calls Focus() on
    itself. There is no way around that short of driving the app's model
    directly, so every mutating helper wraps the call in `keep_focus()`,
    which snapshots the foreground window first and hands it back afterwards.
  * `send_keys()` is never used for input: it types into whatever has focus.
    Text goes in through ValuePattern instead.

Two more traps that cost real time:
  * CheckBox.click_input() often does NOT flip the toggle (the WPF template
    swallows it). Use el.toggle() -- it maps to the TogglePattern.
  * SetForegroundWindow raises pywintypes.error(1400) when another window holds
    focus; the UIA actions still work, so swallow it.
"""
import contextlib
import ctypes
import io
import os
import time

import win32api
import win32con
import win32gui
import win32process
from pywinauto import Desktop

from config import GUI_LOG, MAA_DIR

_u32 = ctypes.windll.user32

# checkbox labels seen in the Copilot tab
CB_AUTO_FORMATION = "自动编队"
CB_IGNORE_REQ = "忽略干员属性要求"
CB_SUPPORT = "借助战"
CB_USE_FORMATION = "使用编队"
CB_MULTI = "多作业模式"


def win():
    d = Desktop(backend="uia")
    return d.windows(title_re=".*MAA.*")[0]


# --------------------------------------------------------------------------
# focus handling
# --------------------------------------------------------------------------

def get_foreground():
    return win32gui.GetForegroundWindow()


def _set_foreground(hwnd):
    """SetForegroundWindow, working around the OS foreground lock.

    A plain SetForegroundWindow fails with error 1400 when the calling process
    does not own the current foreground window. Attaching our thread to both
    the foreground and target threads lifts that restriction.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    me = win32api.GetCurrentThreadId()
    others = set()
    for h in (win32gui.GetForegroundWindow(), hwnd):
        try:
            t, _ = win32process.GetWindowThreadProcessId(h)
            if t and t != me:
                others.add(t)
        except Exception:
            pass
    attached = []
    for t in others:
        try:
            if _u32.AttachThreadInput(me, t, True):
                attached.append(t)
        except Exception:
            pass
    try:
        _u32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
    finally:
        for t in attached:
            try:
                _u32.AttachThreadInput(me, t, False)
            except Exception:
                pass
    return win32gui.GetForegroundWindow() == hwnd


@contextlib.contextmanager
def keep_focus():
    """Run a block that may steal focus, then give it back.

    Every mutating UIA call is wrapped in this so the user's active window
    comes back. It deliberately does not raise if the restore fails -- losing
    the focus race is annoying, not fatal, and must not abort a game run.
    """
    prev = get_foreground()
    try:
        yield
    finally:
        time.sleep(0.05)
        if prev and win32gui.GetForegroundWindow() != prev:
            _set_foreground(prev)


def focus(w=None):
    """Bring MAA to the front. Only for interactive debugging.

    The automation path does not need this -- use keep_focus() instead.
    """
    w = w or win()
    h = w.handle
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    time.sleep(0.3)
    _set_foreground(h)
    time.sleep(0.5)
    return w


def find(w, control_type=None, text=None, aid=None, cls=None):
    for el in w.descendants():
        try:
            i = el.element_info
            if control_type and i.control_type != control_type:
                continue
            if text is not None and text not in (el.window_text() or ""):
                continue
            if aid is not None and i.automation_id != aid:
                continue
            if cls is not None and i.class_name != cls:
                continue
            return el
        except Exception:
            pass
    return None


def tab_click(w, name, verify=True):
    """Select a TabItem by visible text.

    The tab headers read 一键长草 / 自动战斗 / 小工具 / 设置. The copilot page is
    called 自动战斗 in the UI even though the feature is 作业, so accepting
    either alias saves a silent no-op.

    select() alone is not enough on this WPF TabControl: it can return without
    raising while leaving the previous tab selected. Try the SelectionItem
    pattern, then a real click, and confirm by reading is_selected().
    """
    aliases = {"作业": ("自动战斗",), "自动战斗": ("作业",)}.get(name, ())
    wanted = (name,) + aliases
    for el in w.descendants():
        try:
            if el.element_info.control_type != "TabItem":
                continue
            texts = [el.window_text() or ""]
            texts += [(c.window_text() or "") for c in el.descendants()]
            if not any(w in t for w in wanted for t in texts):
                continue
            if el.is_selected():
                return True
            # el.select() is not usable here: pywinauto's wrapper returns
            # without raising but leaves the previous tab selected, so it
            # never falls through to the pattern that actually works.
            with keep_focus():
                try:
                    el.iface_selection_item.Select()
                except Exception:
                    pass
            time.sleep(0.7)
            if el.is_selected():
                return True
            with keep_focus():
                try:
                    el.click_input()
                except Exception:
                    pass
            time.sleep(0.7)
            return el.is_selected()
        except Exception:
            pass
    return False


def _path_box(w, timeout=6.0):
    """Wait for the copilot page's path box.

    The tab can report selected while its content is still being built, so
    callers must not assume the controls exist the instant the tab flips.
    """
    t0 = time.time()
    while True:
        for el in w.descendants():
            try:
                if el.element_info.automation_id == "FileTreeBox":
                    for c in el.descendants():
                        if c.element_info.class_name == "TextBox":
                            return c
            except Exception:
                pass
        if time.time() - t0 >= timeout:
            return None
        time.sleep(0.4)


def set_path(w, path, settle=0.0):
    """Put a path (or prts:// code) into the FileTreeBox.

    Uses ValuePattern, not keyboard input: send_keys() types into whatever
    window has focus, so it would hijack the user's typing and also need MAA
    raised. SetValue writes the field without touching focus, and MAA's
    TextChanged handler fires from it (verified: the set loads).

    `settle` is how long MAA needs to finish reacting. Passing prts:// makes
    MAA fetch the set and, as a side effect, activate its own window -- that
    is inside MAA, not something we can suppress, so callers that care about
    the foreground should pass settle>0 and let keep_focus() restore it after
    the fetch settles.
    """
    box = _path_box(w)
    if box is None:
        raise RuntimeError("FileTreeBox TextBox not found")
    if box.window_text() == path:
        return True
    try:
        box.iface_value.SetValue(path)
    except Exception:
        # fallback for a future MAA that drops ValuePattern from this control
        with keep_focus():
            box.click_input()
            time.sleep(0.3)
            from pywinauto.keyboard import send_keys
            send_keys("^a")
            send_keys("{DELETE}")
            box.set_text(path)
    if settle:
        time.sleep(settle)
    return True


def get_path(w):
    box = _path_box(w)
    return box.window_text() if box else None


def _checkbox(w, text):
    for el in w.descendants():
        try:
            i = el.element_info
            if i.control_type == "CheckBox" and (el.window_text() or "").strip() == text:
                return el
        except Exception:
            pass
    return None


def set_checkbox(w, text, value=True):
    """Set a checkbox state. Returns the resulting state, or None if not found.

    toggle() raises the MAA window (the WPF control focuses itself), so it is
    wrapped in keep_focus() to hand the foreground back afterwards.
    """
    el = _checkbox(w, text)
    if el is None:
        return None
    want = 1 if value else 0
    try:
        if el.get_toggle_state() != want:
            with keep_focus():
                el.toggle()
            time.sleep(0.6)
        return el.get_toggle_state()
    except Exception:
        return None


def checkbox_state(w, text):
    el = _checkbox(w, text)
    if el is None:
        return None
    try:
        return el.get_toggle_state()
    except Exception:
        return None


def list_checkboxes(w):
    out = []
    for el in w.descendants():
        try:
            i = el.element_info
            if i.control_type == "CheckBox":
                out.append(((el.window_text() or "").strip(), el.get_toggle_state()))
        except Exception:
            pass
    return out


def click_start(w):
    """Press 开始.

    Uses UIA Invoke rather than click_input: click_input moves the real mouse
    to the button's screen position, so when the emulator window covers MAA
    the click lands on the emulator instead and MAA never starts. Invoke goes
    straight through the accessibility tree and works while occluded.
    """
    for el in w.descendants():
        try:
            if el.element_info.control_type == "Button" and (el.window_text() or "").strip() == "开始":
                with keep_focus():
                    try:
                        el.invoke()
                    except Exception:
                        el.click_input()
                return True
        except Exception:
            pass
    return False


def click_stop(w):
    """Press 停止. Same focus policy as click_start."""
    for el in w.descendants():
        try:
            if el.element_info.control_type == "Button" and (el.window_text() or "").strip() == "停止":
                with keep_focus():
                    try:
                        el.invoke()
                    except Exception:
                        el.click_input()
                return True
        except Exception:
            pass
    return False


def log_size():
    try:
        return os.path.getsize(GUI_LOG)
    except OSError:
        return 0


def log_since(offset):
    with io.open(GUI_LOG, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(offset)
        return f.read()


def wait_done(off, timeout=480, poll=4):
    """Wait for the copilot task chain to finish.

    Returns (ok, text). ok is True on a clean chain completion — note that a
    clean chain does NOT mean the battle was won; always verify on screen.

    Error markers are checked FIRST because MAA emits them and then still
    writes "完成任务: 自动战斗". A failure therefore looks exactly like a
    success if you only look for the completion line, which is how an earlier
    version reported every lost battle as a clean finish.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        txt = log_since(off)
        if ("任务出错" in txt or "战斗出错" in txt
                or "CopilotStartFailed" in txt or "CopilotError" in txt):
            return False, txt
        if "Completed Task Chain: Copilot" in txt or "完成任务: 自动战斗" in txt:
            return True, txt
        time.sleep(poll)
    return False, log_since(off)


if __name__ == "__main__":
    w = focus()
    print("window:", w.window_text())
    print("checkboxes:", list_checkboxes(w))
    print("path:", get_path(w))
