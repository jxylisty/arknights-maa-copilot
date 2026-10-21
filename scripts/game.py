"""Game-side navigation, verification, and job patching for MAA copilot runs.

The workflow this supports (the one that actually works when MAA has no
navigation adaptation for an event):

    user/agent puts the game on the stage's 开始行动 screen
    -> MAA runs the copilot (formation + battle)
    -> agent verifies the result on screen
    -> agent advances to the next stage

Key insight: MAA's copilot does not need to know how to navigate to a stage. It
only needs the game to already be sitting on that stage's start screen.
"""
import json
import io
import re
import time

import adb
import winocr
from config import COPILOT_DIR, OPERBOX

# 1920x1080 landscape screencap coordinates
UP = (290, 322)        # stage selector +1
DOWN = (290, 655)      # stage selector -1
START = (1560, 968)    # 开始行动
CONTINUE = (1690, 855)  # 继续 on the reward screen
LEFT_PANEL = (80, 250, 540, 720)
RESULT_HEADER = (40, 230, 760, 480)  # 行动结束 / MISSION RESULTS on the win screen

# 补给页（特别战线）4x4 网格，顺序与 SP 网格一致
SUPPLY_GRID = {
    "急救": (190, 285), "灵便": (390, 285), "蓄能": (590, 285), "凝神": (790, 285),
    "缴械": (190, 490), "迟滞": (390, 490), "催化": (590, 490), "消杀": (790, 490),
    "抵抗": (190, 700), "拮抗": (390, 700), "活血": (590, 700), "净血": (790, 700),
    "冷结": (190, 910), "冻结": (390, 910), "纯化": (590, 910), "极化": (790, 910),
}
SUPPLY_TOGGLE = (1555, 845)   # 补给详情页右下角的启用/停用开关
SUPPLY_ON = (255, 221, 0)     # 已启用时的黄色
SUPPLY_LOCKED = (236, 236, 236)  # 未通关（按钮是「夺取控制」）

# OCR reads VEC as VE0 / VE008 / VE-6P etc. The prefix is kept generic so the
# same helper works on other events (YW-EX-7, etc.) without editing it.
STAGE_CODE_RE = re.compile(r"(VEC|VE0|VE[CG]|YW|EX|S|WD|SV|CB|IW|ZT|RS|DV)"
                           r"[-]?\s*(EX[-]?\s*[0-9I]{1,2}|\d{1,2}|SP\s*\d{1,2}|[A-D])")


def norm_ocr(txt):
    """Uppercase and undo OCR's habit of mangling digits.

    OCR renders 1 as I and 0 as O often enough that stage codes come back as
    YW-EX-I / VE008. Only the code tokens are rewritten, so the raw text stays
    useful for the 任务失败 check in verify_result.
    """
    t = winocr.norm(txt).upper()
    t = t.replace("VE0", "VEC")
    # a lone I/O between dashes or at the end of a code is a digit
    t = re.sub(r"(?<=[-])([IO])(?=$|[-A-Z])", lambda m: "1" if m.group(1) == "I" else "0", t)
    return t


def detect_stage(im=None):
    """Return (code, raw_ocr). code looks like 'VEC-08', 'YW-EX-7'.

    Only used as a sanity check against the stage the user asked for; the
    caller compares the string it gets back to its own argument.
    """
    im = im or adb.screenshot()
    txt = winocr.ocr_region(im, LEFT_PANEL)
    t = norm_ocr(txt)
    m = STAGE_CODE_RE.search(t)
    if not m:
        return None, txt
    prefix = "VEC" if m.group(1) in ("VE0", "VE[CG]") else m.group(1)
    tail = m.group(2).replace(" ", "").replace("-", "")
    if tail.upper().startswith("EX"):
        return f"{prefix}-EX-{tail[2:].lstrip('-')}", txt
    return f"{prefix}-{tail}", txt


def _stage_key(code):
    n = code[4:]
    if n.startswith("SP"):
        return (2, int(n[2:]))
    if n in ("A", "B", "C", "D"):
        return (3, ord(n) - ord("A"))
    return (0, int(n))


def goto_stage(target_code, tries=14):
    """Walk the stage selector to the target code. Stages unlock linearly."""
    for _ in range(tries):
        cur, _txt = detect_stage()
        if cur is None:
            time.sleep(1.0)
            continue
        if cur == target_code:
            return True, cur
        try:
            adb.tap(*(UP if _stage_key(target_code) > _stage_key(cur) else DOWN))
        except Exception:
            adb.tap(*UP)
        time.sleep(1.2)
    return False, detect_stage()[0]


def verify_result(im=None):
    """Return (cleared, ocr_text). Reads the battle-end screen.

    Two different reads are needed, verified against real screenshots:
      * the success headline 行动结束 / MISSION RESULTS only comes through on a
        cropped header region — a full-screen OCR misses it entirely;
      * the failure headline 任务失败 / MISSION FAILED only comes through on a
        full-screen OCR.

    Note the success headline is NOT "MISSION COMPLETED"; an earlier version
    matched that and reported every win as a failure.
    """
    im = im or adb.screenshot()
    full = winocr.norm(winocr.ocr_image(im))
    up = full.upper()
    failed = "任务失败" in full or "MISSIONFAILED" in up

    head = winocr.norm(winocr.ocr_region(im, RESULT_HEADER))
    cleared = ("MISSIONRESULTS" in head.upper() or "行动结束" in head
               or "行动已完成" in head)
    return (cleared and not failed), (head[:80] + " | " + full[:160])


def elite_conflicts(cop):
    """Operators whose requirements.elite is below their real elite.

    MAA refuses to load such a job with CopilotFileReadError, and the only
    clue is a line in asst.log. Catching it here turns a confusing "读取文件失败"
    into a named list before MAA is even touched.

    Returns [(name, required_elite, actual_elite), ...].
    """
    import os
    box = {}
    try:
        with io.open(OPERBOX, encoding="utf-8") as f:
            for o in json.load(f).get("own_opers", []):
                box[o["name"]] = o.get("elite", 0)
    except Exception:
        return []

    def scan(opers):
        out = []
        for o in opers:
            n = o.get("name")
            req = o.get("requirements") or {}
            need = req.get("elite")
            if need is None or n not in box:
                continue
            if box[n] > need:
                out.append((n, need, box[n]))
        return out

    bad = scan(cop.get("opers", []))
    for g in cop.get("groups", []):
        bad += scan(g.get("opers", []))
    return bad


def raise_elite(cop, names=None):
    """Set requirements.elite to the operator's real elite for `names`.

    Both the top-level `elite` and `requirements.elite` are written: MAA
    validates the latter, but jobs in the wild are inconsistent about which
    one they populate, and leaving them contradictory invites the next bug.

    names=None means "only the ones elite_conflicts() flags", which is the
    safe default — blanket-raising every member breaks low-rarity fillers.
    """
    box = {}
    try:
        with io.open(OPERBOX, encoding="utf-8") as f:
            for o in json.load(f).get("own_opers", []):
                box[o["name"]] = o.get("elite", 0)
    except Exception:
        return []

    if names is None:
        names = {n for n, _need, _act in elite_conflicts(cop)}

    changed = []

    def fix(o):
        n = o.get("name")
        if n not in names or n not in box:
            return
        real = box[n]
        o["elite"] = real
        o.setdefault("requirements", {})["elite"] = real
        changed.append(n)

    for o in cop.get("opers", []):
        fix(o)
    for g in cop.get("groups", []):
        for o in g.get("opers", []):
            fix(o)
    return changed


def save_job(cop, name):
    """Write a copilot dict into MAA's copilot dir and return the full path."""
    import os
    os.makedirs(COPILOT_DIR, exist_ok=True)
    path = os.path.join(COPILOT_DIR, name + ".json")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(cop, f, ensure_ascii=False, indent=2)
    return path


def rename_oper(cop, old, new):
    """Rename an operator everywhere in a job.

    MAA matches operator names by OCR against the name shown in game, so a job
    naming an alter's full title (凯尔希·思衡托) fails with `unknown oper 凯尔希`
    even though the operator is owned. Rename both opers and actions — the
    deploy steps reference the same string.
    """
    for o in cop.get("opers", []):
        if o.get("name") == old:
            o["name"] = new
    for g in cop.get("groups", []):
        for o in g.get("opers", []):
            if o.get("name") == old:
                o["name"] = new
    for a in cop.get("actions", []):
        if a.get("name") == old:
            a["name"] = new
    return cop


def supply_state(name, im=None):
    """Read one supply's state from its detail page.

    Returns 'on', 'off' or 'locked'. Use the switch pixel, not OCR: OCR of
    that button is unreliable and frequently returns empty.
    """
    x, y = SUPPLY_GRID[name]
    adb.tap(x, y)
    time.sleep(1.2)
    im = im or adb.screenshot()
    px = im.convert("RGB").getpixel(SUPPLY_TOGGLE)
    if px == SUPPLY_ON or (px[0] > 200 and px[1] > 180 and px[2] < 80):
        return "on"
    if px == SUPPLY_LOCKED or (px[0] > 200 and px[1] > 200 and px[2] > 200):
        return "locked"
    return "off"


def set_supply(name, want):
    """Enable or disable a supply. Returns the state after the click."""
    cur = supply_state(name)
    if cur == "locked":
        raise RuntimeError(f"supply {name} is locked (its SP stage is not cleared)")
    if (cur == "on") != bool(want):
        adb.tap(*SUPPLY_TOGGLE)
        time.sleep(1.8)
        return supply_state(name)
    return cur


def apply_supplies(names):
    """Turn exactly `names` on and everything else off.

    Only the six slots are configurable at once, so callers pass the six the
    job's doc asks for; anything already on but not listed gets disabled.
    """
    for name in names:
        set_supply(name, True)
    for name in SUPPLY_GRID:
        if name not in names:
            st = supply_state(name)
            if st == "on":
                set_supply(name, False)
    return {n: supply_state(n) for n in SUPPLY_GRID}


def patch_job(cop, opers=None, groups=None, drop_actions=None):
    """Relax a job so MAA will accept it on this account.

    opers/groups map a name -> new requirements dict. This is the escape hatch
    for the two failures seen in practice:
      * a group lists only operators MAA refuses (garrisoned / unscanned low
        rarity) — replace the group members with ones you know you own;
      * requirements.elite is below the operator's actual elite — MAA's parser
        rejects that outright with CopilotFileReadError, so never lower elite,
        only level/skill_level.
    """
    for o in cop.get("opers", []):
        if opers and o.get("name") in opers:
            o["requirements"] = opers[o["name"]]
    for g in cop.get("groups", []):
        if groups and g.get("name") in groups:
            spec = groups[g["name"]]
            if isinstance(spec, list):
                g["opers"] = spec
            elif isinstance(spec, dict):
                g["opers"] = spec.get("opers", g.get("opers", []))
    return cop
