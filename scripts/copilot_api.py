"""prts.plus copilot API client + box matching.

Matching rules, learned the hard way:

  * Search filter is `levelKeyword`, NOT `search`/`keyword`/`stage_name`.
  * Check `opers`, `groups` AND `actions`. Jobs routinely reference an
    operator only from an action (e.g. a support unit placed mid-battle), and
    missing that produces "MAA says 蕾缪安 not owned" at runtime.
  * Only 5-6* count as a real gap: the OperBox scan is incomplete for low
    rarity operators, and the account owns essentially all of them.
  * At most ONE hard gap is acceptable — MAA's 借助战 borrows a single unit.
  * `requirements.level` is the author's own level, not a hard floor. Level
    shortfalls are reported, not disqualifying.
  * A garrisoned operator (特别战线驻防中) cannot be used in another stage, so
    the game may reject an operator the box says you own. MAA reports those as
    "未拥有"; fix by changing the job, not by editing the box.

KNOWN BLIND SPOT: garrison status is not in OperBoxData.json, so `feasible`
cannot see it. A job can score zero gaps here and still die at formation time
with "以下干员组内的干员均未拥有". When that happens, the operator is almost
certainly garrisoned — pick another job, or ask the user whether that supply is
worth un-garrisoning.
"""
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from config import OPERBOX, RARITY_FILE, STAGE_MAP

API = "https://prts.maa.plus/copilot/query"
GET = "https://prts.maa.plus/copilot/get/{}"
SET_GET = "https://prts.maa.plus/set/get?id={}"
SET_QUERY = "https://prts.maa.plus/set/query"
ACTIVITY = ("https://api.maa.plus/MaaAssistantArknights/api/gui/"
            "StageActivityV2.json")
UA = {"User-Agent": "Mozilla/5.0"}

# names that appear in job files but are devices/groups/summons, not operators
NON_OPER = {"再度绽放", "正义骑士号", "PhonoR-0", "GALLUS²", "U-Official",
            "泰拉大陆调查团", "罗德岛隐秘队", "可爱小车", "Lancet-2",
            "Friston-3", "指挥中心", "风雪之眼", "中继器", "流形", "迎宾踏垫",
            "雷鸣地雷", "共振装置",
            # summons and deployables that actions deploy by name
            "魂灵之影", "基质合成台", "战术装备", "装置", "护盾", "无人机",
            "水炮", "铁腕", "巨像", "白铁的铁钳号"}

_RARITY = None


def rarity_map():
    global _RARITY
    if _RARITY is None:
        try:
            _RARITY = json.load(io.open(RARITY_FILE, encoding="utf-8"))
        except Exception:
            _RARITY = {}
    return _RARITY


def rarity_of(name):
    return rarity_map().get(name, 0)


def api(params, retries=4):
    url = API + "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def fetch(cid):
    req = urllib.request.Request(GET.format(cid), headers=UA)
    d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    return d["data"]


def content_of(meta):
    c = meta["content"]
    return json.loads(c) if isinstance(c, str) else c


def all_operator_names(cop):
    """Every operator name a job can reference, including action-only ones."""
    names = [o.get("name", "") for o in cop.get("opers", [])]
    for g in cop.get("groups", []):
        names += [o.get("name", "") for o in g.get("opers", [])]
    out = list(dict.fromkeys(n for n in names if n))
    for a in cop.get("actions", []):
        n = a.get("name")
        if n and n not in out:
            out.append(n)
    return out


def search_stage(stage_id, limit=40, order="hot"):
    d = api({"levelKeyword": stage_id, "page": 1, "limit": limit,
             "desc": "true", "orderBy": order})
    rows = []
    for it in d["data"]["data"]:
        try:
            c = json.loads(it["content"])
        except Exception:
            continue
        if c.get("stage_name") != stage_id:
            continue
        doc = c.get("doc") or {}
        rows.append({
            "id": it["id"], "views": it["views"], "uploader": it.get("uploader"),
            "title": doc.get("title", ""), "details": doc.get("details", ""),
            "opers": c.get("opers", []), "content": c,
        })
    return rows


def load_box():
    d = json.load(io.open(OPERBOX, encoding="utf-8"))
    box = {}
    for o in d["own_opers"]:
        box[o["name"]] = {"elite": o["elite"], "level": o["level"],
                          "potential": o["potential"], "rarity": o["rarity"]}
    return box


def check_oper(box, name, req):
    """Return (status, reason). status in {'ok','low','miss','filler'}"""
    if name in ("", "-", "支援单位", "助战"):
        return "filler", "助战"
    rar = rarity_of(name)
    if name in box:
        b = box[name]
        re_elite = req.get("elite", 0)
        re_level = req.get("level", 0)
        if b["elite"] < re_elite:
            return "low", f"精{b['elite']}<精{re_elite}"
        if b["elite"] == re_elite and b["level"] < re_level:
            return "low", f"L{b['level']}<L{re_level}"
        return "ok", "ok"
    if name in NON_OPER or (rar and rar <= 4):
        return "filler", "低星补齐"
    return "miss", f"缺{rar or '?'}星"


def feasible(box, cop):
    """Return (ok, hard_missing, low, fillers).

    A 干员组 is a pick-one-of list, so it is satisfied when ANY member is
    owned; only a group with no owned member is a hard gap. Treating every
    member as a separate requirement is how an earlier version reported
    25 "missing" operators for a job whose groups were all satisfied.

    ok is True when the job has at most one hard gap (borrowable via 借助战).
    """
    hard, low, fillers = [], [], []
    seen = set()

    def add(name, req):
        if name in seen:
            return
        seen.add(name)
        st, why = check_oper(box, name, req or {})
        if st == "miss":
            hard.append((name, why))
        elif st == "low":
            low.append((name, why))
        elif st == "filler":
            fillers.append(name)

    for o in cop.get("opers", []):
        add(o.get("name", ""), o.get("requirements"))
    for g in cop.get("groups", []):
        members = g.get("opers", [])
        if not members:
            continue
        # A group is satisfied by any member we own at a usable level. Only
        # when NO member is usable is it a real gap; among those, prefer
        # reporting a level shortfall (fixable) over a hard miss.
        best = None
        satisfied = False
        for o in members:
            st, why = check_oper(box, o.get("name", ""), o.get("requirements") or {})
            if st in ("ok", "filler"):
                satisfied = True
                break
            if best is None or (st == "low" and best[0] == "miss"):
                best = (st, why, o.get("name", ""))
        if satisfied:
            continue
        if best is not None:
            if best[0] == "miss":
                hard.append((best[2], f"组[{g.get('name')}]无可用"))
            else:
                low.append((best[2], best[1]))
    # Actions reference operators AND group names ("拦人先锋", "单奶1").
    # A group name is not an operator: it was already resolved above, and
    # treating it as a missing unit is what produced the bogus
    # "missing 25 operators" verdict. Only action names that are neither a
    # known group nor a device get the owned-if-in-box check.
    group_names = {g.get("name") for g in cop.get("groups", [])}
    listed = set(seen)
    for a in cop.get("actions", []):
        n = a.get("name")
        if n and n not in listed and n not in group_names:
            add(n, {})

    return len(hard) <= 1, hard, low, fillers


def rank(box, rows):
    """Order jobs: fewest hard gaps, then fewest level shortfalls, then views."""
    scored = []
    for r in rows:
        ok, hard, low, fillers = feasible(box, r)
        scored.append((len(hard), len(low), -r["views"], r, hard, low, fillers))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return scored


def stage_id_for(code):
    """VEC-SP12 -> act3break_sp08 etc. Falls back to the code itself."""
    try:
        m = json.load(io.open(STAGE_MAP, encoding="utf-8"))
    except Exception:
        return code
    return m.get(code, code)


# --------------------------------------------------------------------------
# 作业集 (copilot sets) and the activity table
#
# A set bundles every stage of an event (normal + 突袭) under one id, which is
# what MAA's 多作业模式 consumes via `prts://s<id>`. Endpoint is /set/get?id=N
# — plain GET, no auth. Searching sets is POST /set/query (GET returns 405).
# --------------------------------------------------------------------------

def _get_json(url, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=30)
                              .read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _post_json(url, body, retries=4):
    last = None
    data = json.dumps(body).encode("utf-8")
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data,
                headers={"User-Agent": "Mozilla/5.0",
                         "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=30)
                              .read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def fetch_set(set_id):
    """Return one 作业集: {id, name, description, copilot_ids, ...}."""
    d = _get_json(SET_GET.format(set_id))
    if d.get("status_code") != 200:
        raise RuntimeError(f"set {set_id}: {d.get('message')}")
    return d["data"]


def search_sets(keyword="", limit=20, page=1):
    """Search 作业集 by keyword (event name, stage prefix, author...)."""
    d = _post_json(SET_QUERY, {"page": page, "limit": limit,
                               "keyword": keyword})
    if d.get("status_code") != 200:
        raise RuntimeError(f"set query: {d.get('message')}")
    return d["data"]["data"]


def set_stages(set_id):
    """Expand a 作业集 into [(copilot_id, stage_name, title), ...].

    This is what tells you which stages a set actually covers before you hand
    it to MAA — useful for spotting a set that skips 突袭 or a stage.
    """
    s = fetch_set(set_id)
    out = []
    for cid in s.get("copilot_ids") or []:
        try:
            meta = fetch(cid)
            c = content_of(meta)
        except Exception:
            continue
        out.append((cid, c.get("stage_name"),
                    (c.get("doc") or {}).get("title", "")))
    return s, out


def activities(server="Official"):
    """MAA's activity table: current/recent events with stage codes.

    Returns a list of dicts sorted by start time (newest first):
      {key, tip, stage_name, start, expire, stages: [display names]}
    This is how you find a new event's stage prefix and whether it is still
    open, without guessing stage ids.
    """
    d = _get_json(ACTIVITY)
    root = d.get(server) or {}
    out = []
    for key, block in (root.get("sideStoryStage") or {}).items():
        act = block.get("Activity") or {}
        stages = [s.get("Display") for s in block.get("Stages") or []
                  if s.get("Display")]
        out.append({
            "key": key,
            "tip": act.get("Tip", ""),
            "stage_name": act.get("StageName", ""),
            "start": act.get("UtcStartTime", ""),
            "expire": act.get("UtcExpireTime", ""),
            "stages": stages,
        })
    out.sort(key=lambda a: a["start"], reverse=True)
    return out


def current_activities(server="Official", now=None):
    """Only the events whose window contains `now` (default: system time)."""
    import datetime
    now = now or datetime.datetime.now()
    live = []
    for a in activities(server):
        try:
            end = datetime.datetime.strptime(a["expire"], "%Y/%m/%d %H:%M:%S")
        except Exception:
            continue
        if end >= now:
            live.append(a)
    return live


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--acts", "--activities"):
        for a in current_activities():
            print(f"{a['key']:8s} {a['tip'][:24]:26s} "
                  f"{a['start']} ~ {a['expire']}")
            print(f"         stages: {', '.join(a['stages'][:12])}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--sets":
        kw = sys.argv[2] if len(sys.argv) > 2 else ""
        for s in search_sets(kw):
            print(f"s{s['id']:<7} {len(s.get('copilot_ids') or []):2d}关 "
                  f"{s['name'][:52]}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--set":
        sid = int(sys.argv[2])
        s, rows = set_stages(sid)
        print(f"s{sid}: {s['name']}")
        print(f"  {s.get('description','')[:120]}")
        for cid, stage, title in rows:
            print(f"  {cid} {stage:20s} {title[:40]}")
        sys.exit(0)

    stage = stage_id_for(sys.argv[1]) if len(sys.argv) > 1 else "act3break_08"
    box = load_box()
    rows = search_stage(stage)
    print(f"stage {stage}: {len(rows)} copilots | box {len(box)}")
    for nhard, nlow, nv, r, hard, low, fillers in rank(box, rows):
        mark = "OK " if nhard <= 1 else f"{nhard}!"
        print(f"{mark} {r['id']} v{r['views']:<6} "
              f"hard={[h[0] for h in hard]} low={[l[0] for l in low]}")
        print(f"     {r['title'][:56]}")
