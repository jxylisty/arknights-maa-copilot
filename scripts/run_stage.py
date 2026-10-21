"""One-command copilot run for an event stage MAA can't navigate to.

Usage:
    python run_stage.py VEC-09                 # rank jobs, run the best
    python run_stage.py VEC-09 --job 104031    # force a specific job id
    python run_stage.py VEC-09 --dry           # only rank, do not run
    python run_stage.py --list                 # show the stage map

Assumes the game is already sitting on that stage's 开始行动 screen. Use
`python nav.py VEC-09` to get there first.
"""
import argparse
import json
import sys
import time

import adb
import copilot_api as CA
import game
import maa_drive as M


def pick_job(stage_id, box, min_views=0):
    rows = CA.search_stage(stage_id, limit=40)
    ranked = CA.rank(box, rows)
    for nhard, nlow, _nv, r, hard, low, _fill in ranked:
        if r["views"] < min_views:
            continue
        if nhard <= 1:
            return r, hard, low, ranked
    return None, None, None, ranked


def run(job_id, name, verify=True, timeout=480, borrow=False, renames=None,
        auto_elite=True):
    meta = CA.fetch(job_id)
    cop = CA.content_of(meta)
    for pair in renames or []:
        old, _, new = pair.partition("=")
        if old and new:
            game.rename_oper(cop, old, new)
            print(f"  renamed {old} -> {new}")

    bad = game.elite_conflicts(cop)
    if bad:
        if not auto_elite:
            print("  elite conflicts (job will fail to load):", bad)
        else:
            fixed = game.raise_elite(cop)
            print(f"  raised elite to real value for: {fixed}")
    path = game.save_job(cop, name)

    w = M.focus()
    if not M.tab_click(w, "作业"):
        return False, "copilot tab not found"
    time.sleep(0.8)
    # 多作业模式 silently rejects a single job with
    # "正在使用｢多作业模式｣, 但未添加任何作业" — turn it off before running one.
    if M.checkbox_state(w, M.CB_MULTI):
        M.set_checkbox(w, M.CB_MULTI, False)
        print("  multi-job mode was on; turned it off for a single run")
    M.set_path(w, path)
    time.sleep(0.8)
    M.set_checkbox(w, M.CB_AUTO_FORMATION, True)
    M.set_checkbox(w, M.CB_IGNORE_REQ, True)
    M.set_checkbox(w, M.CB_SUPPORT, bool(borrow))

    off = M.log_size()
    if not M.click_start(w):
        return False, "开始 button not found"
    ok, txt = M.wait_done(off, timeout=timeout)
    if not verify:
        return ok, txt[-600:]

    # Verify on screen even when the log says the chain failed. MAA reports
    # 战斗出错 for a result-screen network hiccup ("战斗记录未能成功同步到
    # 神经网络") on a battle that was actually a 3-star clear, so the log
    # alone is not authoritative in either direction.
    screen = ""
    for _ in range(10):
        time.sleep(4)
        cleared, screen = game.verify_result()
        if cleared or "任务失败" in screen or "MISSIONFAILED" in screen.upper():
            return cleared, screen
    if ok:
        return False, screen
    return False, (txt[-800:] + "\n--- screen ---\n" + screen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?", help="VEC-09 / VEC-SP12 / act3break_08")
    ap.add_argument("--job", type=int, help="force a copilot id")
    ap.add_argument("--dry", action="store_true", help="rank only")
    ap.add_argument("--list", action="store_true", help="print stage map")
    ap.add_argument("--timeout", type=int, default=480)
    ap.add_argument("--supplies", help="comma-separated 补给 to enable, "
                    "e.g. 极化,净血,拮抗,消杀,缴械,催化 (game must be on the "
                    "special-front page); everything else is turned off")
    ap.add_argument("--borrow", action="store_true", help="tick 借助战")
    ap.add_argument("--rename", action="append", default=[],
                    metavar="OLD=NEW",
                    help="rename an operator in the job (fixes 异格 name "
                         "mismatches such as 凯尔希·思衡托=凯尔希)")
    ap.add_argument("--no-auto-elite", action="store_true",
                    help="do not auto-raise requirements.elite to the "
                         "operator's real elite")
    args = ap.parse_args()

    if args.list:
        print(json.dumps(json.load(open(CA.STAGE_MAP, encoding="utf-8")),
                         ensure_ascii=False, indent=1))
        return

    if not args.stage:
        ap.error("stage is required (or use --list)")

    stage_id = CA.stage_id_for(args.stage)
    box = CA.load_box()

    if args.job:
        job_id = args.job
        title = f"{args.stage}_{job_id}"
    else:
        r, hard, low, ranked = pick_job(stage_id, box)
        if r is None:
            print(f"no feasible job for {stage_id}; closest:")
            for nhard, nlow, _nv, rr, h, l, _f in ranked[:6]:
                print(f"  {nhard} miss {rr['id']} {rr['title'][:40]} "
                      f"missing={[x[0] for x in h]}")
            return
        job_id = r["id"]
        title = f"{args.stage}_{job_id}"
        print(f"pick {job_id} v{r['views']} {r['title'][:50]}")
        if hard:
            print(f"  borrow needed: {[h[0] for h in hard]}")
        if low:
            print(f"  level shortfall: {[l[0] for l in low]}")

    if args.supplies:
        names = [s.strip() for s in args.supplies.split(",") if s.strip()]
        bad = [n for n in names if n not in game.SUPPLY_GRID]
        if bad:
            ap.error(f"unknown supply names: {bad}")
        print(f"applying supplies: {names}")
        try:
            state = game.apply_supplies(names)
        except RuntimeError as e:
            print(f"SUPPLY ERROR: {e}")
            return
        on = [n for n, s in state.items() if s == "on"]
        print(f"  now on: {on}")

    if args.dry:
        return

    cur, _txt = game.detect_stage()
    print(f"game is on: {cur} (expected {args.stage})")
    if cur != args.stage:
        print("WARNING: stage mismatch — run nav.py first")

    ok, info = run(job_id, title, timeout=args.timeout,
                   borrow=args.borrow, renames=args.rename,
                   auto_elite=not args.no_auto_elite)
    print("RESULT:", "CLEARED" if ok else "FAILED/NOT CLEARED")
    print(info)
    if not ok and "均未拥有" in info:
        print("\nHINT: an operator the box owns was rejected at formation time.")
        print("      Causes, in order of likelihood:")
        print("        1. the job's name differs from the in-game name "
              "(check asst.log for 'unknown oper X');")
        print("        -- use --rename OLD=NEW to fix it")
        print("        2. the operator is 驻防中 (garrisoned on a supply).")
        print("      Pick another job, or ask the user before un-garrisoning.")


if __name__ == "__main__":
    main()
