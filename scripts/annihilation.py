"""Annihilation sweep via the in-game 全权委托 (full delegation) — the game
resolves the run instantly against your best no-support clear record. No MAA,
no battle playback, ~10 seconds end to end.

UI semantics on the annihilation map page (verified 2026-10, 灰石剧场,
1920x1080) — this cost a pile of misclicks to figure out, trust it:

  * Default: two toggle rows sit bottom-right — 全权委托 (checkbox ~1365,888)
    and 代理指挥 (checkbox ~1600,888), both empty, plus the blue 开始行动
    button at (1725,985).
  * Tapping 全权委托 switches to DELEGATION mode: the 全权委托 row hides
    (a PRTS badge replaces it) and the 代理指挥 checkbox renders CHECKED.
    That checked box is the mode indicator — do not "fix" it.
  * Tapping the checked 代理指挥 box unticks and restores the default rows.
  * Ticking plain 代理指挥 instead gives replay mode: the game watches the
    recorded ~25 min battle for the same fixed result. Pointless for sweeps.

Delegation costs 1 常态事务代理卡 per run (the game burns the
soonest-expiring one) plus 25 sanity, and pays 合成玉 for the best
no-support kill count (runs that used a borrowed support don't count —
that's why the record can sit at 395 while the week's clear was 400).

Flow: on map -> tap 全权委托 -> tap 开始行动 -> confirmation dialog
(use 1 card) -> instant MISSION RESULTS -> tap through back to the map.
"""
import re
import time

import adb
import winocr

# 1920x1080, annihilation map page (verified 2026-10)
ANNIH_START = (1725, 985)     # blue 开始行动 button
FULL_CB = (1365, 888)         # 全权委托 checkbox (default layout)
PROXY_CB = (1600, 888)        # 代理指挥 checkbox; CHECKED = delegation mode on
CONFIRM_OK = (1710, 990)      # 确认使用 on the delegation confirm dialog
CONFIRM_CANCEL = (1430, 990)  # 取消 on the same dialog
CENTER = (960, 540)           # generic advance / result screens

FAIL_RE = ("任务失败", "MISSION FAILED")
RESULT_RE = ("MISSION RESULTS", "行动结束", "已歼灭", "作战简报")


def _ocr(im):
    return winocr.norm(winocr.ocr_image(im))


def shot():
    return adb.screenshot()


def screen_text(im=None):
    im = im or shot()
    return _ocr(im)


def _pixel(im, xy):
    return im.convert("RGB").getpixel(xy)


def is_on_map(im=None):
    """The annihilation map page: 剿灭委托 text OCRs reliably (the stylized
    开始行动/代理指挥 buttons never do). Result screens also carry other
    markers, so exclude them."""
    txt = _ocr(im or shot())
    return ("剿灭委托" in txt
            and not any(r in txt for r in RESULT_RE)
            and "任务失败" not in txt)


def delegation_mode(im=None):
    """Delegation mode is ON iff the 代理指挥 checkbox reads bright white."""
    return _pixel(im or shot(), PROXY_CB)[0] > 200


def set_delegation(want=True, tries=3):
    """Switch delegation mode on/off and verify by the checkbox pixel.

    ON: tap the 全权委托 checkbox (only present in the default layout).
    OFF: tap the checked 代理指挥 checkbox.
    """
    for _ in range(tries):
        if delegation_mode() == want:
            return True
        adb.tap(*(FULL_CB if want else PROXY_CB), settle=1.8)
    return delegation_mode() == want


def read_dialog(im=None):
    """Parse the delegation confirm dialog: best no-support kills and the
    合成玉 payout. Returns {} when the dialog is not up."""
    txt = _ocr(im or shot())
    if "确认使用" not in txt or "代理卡" not in txt:
        return {}
    d = {"raw": txt[:220]}
    m = re.search(r"歼灭数\s*(\d+)", txt)
    if m:
        d["kills"] = int(m.group(1))
    m = re.search(r"[×x]\s*(\d+)", txt)
    if m:
        d["jade"] = int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*1800", txt)
    if m:
        d["weekly_jade"] = [int(m.group(1)), 1800]
    return d


def run_delegation(confirm=True, tries=3):
    """One delegated sweep, from the map page back to the map page.

    confirm=False stops at the confirmation dialog and reports the spend
    preview without executing (leaves the dialog up).

    Sanity/card errors surface as a missing dialog — the caller gets the
    screen text to diagnose.
    """
    if not is_on_map():
        return {"ok": False, "error": "not on the annihilation map",
                "screen": screen_text()[:150]}
    if not set_delegation(True):
        return {"ok": False, "error": "could not switch on 全权委托",
                "screen": screen_text()[:150]}
    adb.tap(*ANNIH_START, settle=2.5)
    dlg = {}
    for _ in range(6):                      # dialog may lag a beat
        dlg = read_dialog()
        if dlg:
            break
        time.sleep(1.5)
    if not dlg:
        set_delegation(False)
        return {"ok": False,
                "error": "confirm dialog did not appear (sanity/card short? "
                         "or the week has no eligible clear)",
                "screen": screen_text()[:150]}
    if not confirm:
        adb.tap(*CONFIRM_CANCEL, settle=2)   # leave the game in a clean state
        return {"ok": True, "executed": False, "dialog": dlg}
    adb.tap(*CONFIRM_OK, settle=3)
    res = collect_results()
    back = advance_to_map()
    return {"ok": True, "executed": True, "dialog": dlg,
            "result": res, "back_on_map": back}


def collect_results(timeout=90):
    """Wait for the (instant) MISSION RESULTS screen and parse the weekly
    jade progress from it. Non-delegation battles finish too late for this
    short timeout by design — use mcp battle_wait for those."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        txt = screen_text()
        if any(r in txt for r in FAIL_RE):
            return {"failed": True, "raw": txt[:160]}
        if "MISSION RESULTS" in txt.upper().replace(" ", "") or "行动结束" in txt:
            out = {"raw": txt[:160]}
            m = re.search(r"(\d+)\s*/\s*1800", txt)
            if m:
                out["weekly_jade"] = [int(m.group(1)), 1800]
            return out
        time.sleep(3)
    return {"raw": "timeout", "screen": screen_text()[:120]}


def advance_to_map(tries=8):
    """Tap through result screens until the annihilation map is back."""
    for _ in range(tries):
        if is_on_map():
            return True
        adb.tap(*CENTER, settle=2.2)
    return is_on_map()
