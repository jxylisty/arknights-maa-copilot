"""Arknights MAA MCP server — wraps the skill's scripts as MCP tools.

Tools (v1):
  screen          -> adb screenshot + full-screen OCR text
  tap             -> tap with page guard: `expect` must appear on screen first
  swipe / back    -> navigation primitives (same guard available on swipe)
  copilot_search  -> prts job ranking for a stage code, feasibility-checked
  battle_wait     -> OCR-poll until win/fail, structured result
  paradox_run     -> full paradox-simulation loop (job search -> skill popup ->
                     MAA ParadoxCopilot -> retry loop), game must already be on
                     the target operator's paradox detail page
  maa_status      -> MAA window/checkboxes/log tail, read-only
  annihilation_run     -> one weekly-annihilation sweep via in-game 全权委托
                     (instant resolution, 1 常态事务代理卡 + 25 sanity per
                     run; game must be on the annihilation map)
  annihilation_collect -> parse the result screen (jade progress or battle
                     brief) and tap back to the annihilation map

Coordinate constants live here ONLY as flow parameters validated against the
skill docs (references/paradox.md). They assume 1920x1080; see SKILL.md.

Run:  python ark_mcp.py           (stdio MCP server)
      python ark_mcp.py --check   (local sanity check, no stdio)
"""
import asyncio
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adb
import winocr
import copilot_api as CA
import maa_drive as M
import annihilation as AN
from config import COPILOT_DIR, SKILL_DIR

from mcp.server.mcpserver import MCPServer
from PIL import Image

server = MCPServer(
    "arknights-maa",
    instructions=(
        "Arknights + MAA copilot. Coordinates assume a 1920x1080 landscape "
        "emulator. Always `screen` first on an unknown page. `tap(expect=...)` "
        "refuses to click when the expected text is absent -- use it to avoid "
        "wrong-page misclicks. paradox_run requires the game to sit on the "
        "target operator's paradox detail page (the one with 开始模拟)."
    ),
)

# paradox flow coordinates (1920x1080) — documented in references/paradox.md
DETAIL_START = (1765, 990)
SKILL_POS = {1: (900, 255), 2: (900, 545), 3: (900, 830)}
RECOVERY_TAP = (960, 540)

MEM_CACHE = os.path.join(SKILL_DIR, "data", "mem_jobs.json")
MEM_CACHE_TTL = 24 * 3600


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _shot() -> Image.Image:
    return adb.screenshot()


def _ocr(img: Image.Image) -> str:
    try:
        return winocr.norm(winocr.ocr_image(img))
    except Exception as e:  # OCR must never kill a tool call
        return f"<ocr-error: {e}>"


def _screen_text() -> dict:
    img = _shot()
    return {"text": _ocr(img), "size": [img.width, img.height]}


def _page_is(txt: str, kind: str) -> bool:
    if kind == "paradox_detail":
        return ("开始模拟" in txt and "悖论模拟" in txt) or \
               "可开始模拟作战" in txt or "已完成模拟作战" in txt
    if kind == "fail":
        return "任务失败" in txt
    if kind == "win":
        return "行动结束" in txt or "MISSIONRESULTS" in txt.upper().replace(" ", "")
    if kind == "cleared":
        return "已完成模拟作战" in txt
    if kind == "brief":
        return "作战简报" in txt or "已歼灭" in txt
    return False


def _maa():
    w = M.win()
    return w


def _tap(x, y, settle=1.2):
    adb.tap(x, y, settle=settle)


def _mem_jobs() -> list:
    """mem_ paradox jobs with a 24h disk cache (5 API pages otherwise)."""
    if os.path.exists(MEM_CACHE):
        try:
            d = json.load(open(MEM_CACHE, encoding="utf-8"))
            if time.time() - d.get("fetched_at", 0) < MEM_CACHE_TTL:
                return d["jobs"]
        except Exception:
            pass
    import urllib.request
    import urllib.parse
    rows = []
    for page in range(1, 6):
        url = ("https://prts.maa.plus/copilot/query?"
               + urllib.parse.urlencode(
                   {"levelKeyword": "mem_", "page": page, "limit": 100,
                    "desc": "true", "orderBy": "views"}))
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
        r = d.get("data", {}).get("data", [])
        if not r:
            break
        rows += r
        time.sleep(0.4)
    jobs = []
    for it in rows:
        try:
            c = json.loads(it["content"])
        except Exception:
            continue
        sn = c.get("stage_name", "")
        if not sn.startswith("mem_"):
            continue
        jobs.append({
            "id": it["id"], "views": it["views"], "stage": sn,
            "title": (c.get("doc") or {}).get("title", ""),
            "skill": next((o.get("skill") for o in c.get("opers", [])
                           if o.get("skill")), None),
        })
    json.dump({"fetched_at": time.time(), "jobs": jobs},
              open(MEM_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return jobs


def _save_job(cid: int) -> str:
    meta = CA.fetch(cid)
    c = CA.content_of(meta)
    fn = os.path.join(COPILOT_DIR, f"mcp_job_{cid}.json")
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    return fn, c


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@server.tool()
def screen() -> dict:
    """Screenshot the emulator and return full-screen OCR text."""
    return _screen_text()


@server.tool()
def tap(x: int, y: int, expect: str = "", settle: float = 1.2) -> dict:
    """Tap the emulator at (x, y). If `expect` is given, the screen must
    contain that text first, else the tap is REFUSED and the current screen
    text is returned so the caller can re-orient. Returns the screen text
    after tapping."""
    if expect:
        cur = _screen_text()
        if expect not in cur["text"]:
            return {"ok": False, "refused": True,
                    "reason": f"expect '{expect}' not on screen",
                    "screen": cur}
    _tap(x, y, settle=settle)
    return {"ok": True, "screen": _screen_text()}


@server.tool()
def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 400) -> dict:
    """Swipe on the emulator (ms = duration in milliseconds)."""
    adb.swipe(x1, y1, x2, y2, ms=ms)
    return {"ok": True, "screen": _screen_text()}


@server.tool()
def back() -> dict:
    """Press the Android back key."""
    adb.back()
    return {"ok": True, "screen": _screen_text()}


@server.tool()
def copilot_search(stage: str) -> dict:
    """Search prts jobs for a stage code (e.g. camp_r_34, mem_lin_1),
    ranked by feasibility against the local operator box."""
    box = CA.load_box()
    rows = CA.search_stage(stage)
    ranked = []
    for nhard, nlow, nv, r, hard, low, fillers in CA.rank(box, rows):
        ranked.append({
            "id": r["id"], "views": r["views"], "title": r["title"][:60],
            "hard_missing": [h[0] for h in hard],
            "level_short": [l[0] for l in low],
            "ok": nhard <= 1,
        })
    return {"stage": stage, "count": len(ranked), "jobs": ranked}


@server.tool()
def battle_wait(timeout: int = 300) -> dict:
    """OCR-poll the emulator until the battle shows a result.
    Returns status: win | fail | paradox_cleared | brief | timeout.
    `brief` = the annihilation 作战简报 overlay is up — follow with
    annihilation_collect."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(6)
        txt = _screen_text()["text"]
        if _page_is(txt, "fail"):
            return {"status": "fail", "text": txt[:200]}
        if _page_is(txt, "brief"):
            return {"status": "brief", "text": txt[:200]}
        if _page_is(txt, "cleared"):
            return {"status": "paradox_cleared", "text": txt[:200]}
        if _page_is(txt, "win"):
            return {"status": "win", "text": txt[:200]}
    return {"status": "timeout"}


@server.tool()
def annihilation_run(confirm: bool = True) -> dict:
    """One weekly-annihilation sweep via the in-game 全权委托 (full
    delegation): the game instantly resolves the run against the best
    no-support clear record — no MAA, no ~25-min battle playback.
    REQUIREMENT: the game is on the annihilation map (1920x1080), this week
    already has a clear, and there is >=1 常态事务代理卡 + 25 sanity. Each run
    consumes 1 proxy card and pays 合成玉 for the recorded kills.
    confirm=False only opens the confirmation dialog and returns the spend
    preview (kills / jade / weekly progress) without executing."""
    try:
        return AN.run_delegation(confirm=confirm)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@server.tool()
def annihilation_collect(advance: bool = True) -> dict:
    """Parse whatever result screen is currently up: the delegation instant
    result (每周报酬进度 N/1800) or a real battle's 作战简报 (已歼灭 /
    已攻入敌方数), then tap through back to the annihilation map. Only call
    this when a result screen is actually showing."""
    txt = _screen_text()["text"]
    import re
    out = {"text": txt[:200]}
    m = re.search(r"已歼灭\s*(\d+)", txt)
    if m:
        out["kills"] = int(m.group(1))
    m = re.search(r"已攻入敌方数\s*(\d+)", txt)
    if m:
        out["leaks"] = int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*1800", txt)
    if m:
        out["weekly_jade"] = [int(m.group(1)), 1800]
    if advance and ("作战简报" in txt or "行动结束" in txt
                    or "MISSION RESULTS" in txt.upper()):
        out["back_on_map"] = AN.advance_to_map()
    return out


@server.tool()
def maa_status() -> dict:
    """Report MAA state: window found, title, copilot-tab checkboxes, current
    copilot path, tail of gui.log. Read-only — no focus stealing. Call before
    flows that need MAA (paradox_run, copilot battles)."""
    out = {"running": False}
    try:
        w = _maa()
    except Exception as e:
        out["error"] = f"MAA window not found: {e}"
        return out
    out["running"] = True
    try:
        out["title"] = w.window_text()
    except Exception:
        pass
    try:
        out["checkboxes"] = dict(M.list_checkboxes(w))
    except Exception:
        pass
    try:
        out["copilot_path"] = M.get_path(w)
    except Exception:
        pass
    try:
        out["log_tail"] = M.log_since(max(0, M.log_size() - 4000))[-1200:]
    except Exception:
        pass
    return out


@server.tool()
def paradox_run(oper: str, job_id: int = 0, retries: int = 3) -> dict:
    """Run one paradox simulation end-to-end. REQUIREMENT: the game is on the
    target operator's paradox detail page (has the 开始模拟 button). `oper` is
    the operator's Chinese name used to find a job on prts (or pass job_id).
    Handles: job lookup, skill selection, MAA takeover, OCR verification and
    up to `retries` attempts. MAA must be running and connected."""
    # 0. precondition: on paradox detail page
    cur = _screen_text()
    if not (_page_is(cur["text"], "paradox_detail")):
        return {"ok": False,
                "error": "game is not on a paradox detail page — navigate there "
                         "first (tap 开始模拟 lives at 1765,990 on 1920x1080)",
                "screen": cur}
    try:
        _maa()
    except Exception as e:
        return {"ok": False, "error": f"MAA window not found: {e}"}

    # 1. job
    detail = {"attempts": []}
    try:
        if job_id:
            cid = job_id
        else:
            jobs = [j for j in _mem_jobs()
                    if oper in j["title"] or oper in j["stage"]]
            if not jobs:
                return {"ok": False,
                        "error": f"no prts job found for operator '{oper}'"}
            jobs.sort(key=lambda j: -j["views"])
            cid = jobs[0]["id"]
        fn, c = _save_job(cid)
        skill = next((o.get("skill") for o in c.get("opers", [])
                      if o.get("skill")), 1)
        detail["job_id"] = cid
        detail["skill"] = skill
    except Exception as e:
        return {"ok": False, "error": f"job lookup failed: {e}"}

    w = _maa()
    with M.keep_focus():
        M.set_path(w, fn, settle=2.0)
    M.set_checkbox(w, "自动编队", False)   # paradox squad is fixed
    M.set_checkbox(w, "多作业模式", False)

    pos = SKILL_POS.get(skill, SKILL_POS[1])
    cleared = False
    for attempt in range(1, retries + 1):
        _tap(*DETAIL_START, settle=3.0)          # 开始模拟 -> skill popup
        _tap(*pos, settle=2.0)                   # required skill
        off = M.log_size()
        started = M.click_start(w)               # MAA taps 开始模拟 itself
        M.wait_done(off, timeout=120, poll=5)
        res = battle_wait(timeout=300)
        detail["attempts"].append(res["status"])
        if res["status"] in ("paradox_cleared", "win"):
            cleared = True
            break
        # recover: tap through fail/result screens back to the paradox page
        recovered = False
        for _ in range(10):
            _tap(*RECOVERY_TAP, settle=2.2)
            txt = _screen_text()["text"]
            if _page_is(txt, "paradox_detail"):
                recovered = True
                break
        if not recovered:
            detail["recovery"] = "lost — screen unknown, manual check needed"
            break
    detail["attempts"] = detail["attempts"]
    return {"ok": True, "cleared": cleared, "detail": detail}


# --------------------------------------------------------------------------

def _check():
    """Local sanity check without stdio: adb, OCR, MAA window, prts cache."""
    print("adb:", adb.size())
    print("screen ocr:", _screen_text()["text"][:80])
    try:
        print("maa:", _maa().window_text())
    except Exception as e:
        print("maa: NOT FOUND:", e)
    print("mem jobs cached:", len(_mem_jobs()))
    print("tools:", [t.name for t in asyncio.run(server.list_tools())])


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    else:
        server.run()  # stdio
