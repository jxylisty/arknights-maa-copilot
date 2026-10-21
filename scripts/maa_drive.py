"""Drive the MAA WPF GUI through UI Automation (pywinauto uia backend).

Why UIA instead of editing gui.json: MAA rewrites its config on exit, so live
control has to go through the widgets.

Two traps that cost real time:
  * CheckBox.click_input() often does NOT flip the toggle (the WPF template
    swallows it). Use el.toggle() — it maps to the TogglePattern.
  * SetForegroundWindow raises pywintypes.error(1400) when another window holds
    focus; the UIA actions still work, so swallow it.
"""
import io
import os
import time

import win32con
import win32gui
from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from config import GUI_LOG, MAA_DIR

# checkbox labels seen in the Copilot tab
CB_AUTO_FORMATION = "自动编队"
CB_IGNORE_REQ = "忽略干员属性要求"
CB_SUPPORT = "借助战"
CB_USE_FORMATION = "使用编队"
CB_MULTI = "多作业模式"


def win():
    d = Desktop(backend="uia")
    return d.windows(title_re=".*MAA.*")[0]


def focus(w=None):
    w = w or win()
    h = w.handle
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    time.sleep(0.3)
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:
        pass
    time.sleep(0.8)
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
            try:
                el.iface_selection_item.Select()
            except Exception:
                pass
            time.sleep(0.7)
            if el.is_selected():
                return True
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


def set_path(w, path):
    """Type a copilot json path into the FileTreeBox."""
    box = _path_box(w)
    if box is None:
        raise RuntimeError("FileTreeBox TextBox not found")
    box.click_input()
    time.sleep(0.4)
    send_keys("^a")
    time.sleep(0.2)
    send_keys("{DELETE}")
    time.sleep(0.2)
    box.set_text(path)
    time.sleep(0.5)
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
    """Set a checkbox state. Returns the resulting state, or None if not found."""
    el = _checkbox(w, text)
    if el is None:
        return None
    want = 1 if value else 0
    try:
        if el.get_toggle_state() != want:
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
