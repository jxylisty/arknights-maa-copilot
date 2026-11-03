"""Shared config for the Arknights/MAA copilot skill.

Paths default to the user's current install; override with env vars so the
scripts stay usable if MAA or the emulator moves.
"""
import os

MAA_DIR = os.environ.get(
    "MAA_DIR", r"E:\C盘搬家\工具包\MAA-v5.12.3-win-x64")
ADB = os.environ.get(
    "MAA_ADB", os.path.join(MAA_DIR, "adb", "platform-tools", "adb.exe"))
DEFAULT_SERIAL = os.environ.get("MAA_SERIAL", "127.0.0.1:16384")


def _detect_serial():
    """Pick the first adb device; config's default may go stale when the
    emulator (MuMu 12) exposes emulator-5554 instead of 127.0.0.1:16384."""
    import subprocess
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, timeout=10)
        for line in r.stdout.decode(errors="ignore").splitlines()[1:]:
            p = line.split()
            if len(p) == 2 and p[1] == "device":
                return p[0]
    except Exception:
        pass
    return DEFAULT_SERIAL


SERIAL = _detect_serial() if _detect_serial() else DEFAULT_SERIAL

COPILOT_DIR = os.path.join(MAA_DIR, "config", "copilot")
GUI_LOG = os.path.join(MAA_DIR, "debug", "gui.log")
ASST_LOG = os.path.join(MAA_DIR, "debug", "asst.log")
OPERBOX = os.path.join(MAA_DIR, "data", "OperBoxData.json")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RARITY_FILE = os.path.join(SKILL_DIR, "data", "rarity.json")
STAGE_MAP = os.path.join(SKILL_DIR, "data", "stage_map.json")
