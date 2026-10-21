"""Get the game onto a stage's 开始行动 screen (MAA has no nav for many events).

    python nav.py VEC-09            # walk the core-breakthrough stack
    python nav.py VEC-SP12 --sp     # special-front grid (see coords below)
    python nav.py --where           # just report what's on screen
    python nav.py --back            # one screen back

Special-front grid coordinates are for 1920x1080 and are listed in the skill's
SKILL.md; they are stable within an event but do not generalize across events.
"""
import argparse
import time

import adb
import game


def where():
    im = adb.screenshot()
    code, txt = game.detect_stage(im)
    print("stage:", code)
    print("ocr:", txt.replace("\n", " ")[:120])
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?")
    ap.add_argument("--sp", action="store_true", help="navigate inside special front")
    ap.add_argument("--where", action="store_true")
    ap.add_argument("--back", action="store_true")
    ap.add_argument("--coords", help="x,y to tap then stop")
    args = ap.parse_args()

    if args.where:
        where()
        return
    if args.back:
        adb.back()
        return
    if args.coords:
        x, y = (int(v) for v in args.coords.split(","))
        adb.tap(x, y)
        time.sleep(1.0)
        where()
        return
    if not args.stage:
        ap.error("stage required")

    if args.sp:
        print("special-front navigation needs explicit taps; "
              "use --coords or the grid table in SKILL.md")
        return

    ok, cur = game.goto_stage(args.stage)
    print("reached:", cur, "ok" if ok else "FAILED")


if __name__ == "__main__":
    main()
