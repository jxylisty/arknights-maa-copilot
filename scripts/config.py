"""Shared config for the Arknights/MAA copilot skill.

Paths default to the user's current install; override with env vars so the
scripts stay usable if MAA or the emulator moves.
"""
import os

MAA_DIR = os.environ.get(
    "MAA_DIR", r"E:\C盘搬家\工具包\MAA-v5.12.3-win-x64")
ADB = os.environ.get(
    "MAA_ADB", os.path.join(MAA_DIR, "adb", "platform-tools", "adb.exe"))
SERIAL = os.environ.get("MAA_SERIAL", "127.0.0.1:16384")

COPILOT_DIR = os.path.join(MAA_DIR, "config", "copilot")
GUI_LOG = os.path.join(MAA_DIR, "debug", "gui.log")
ASST_LOG = os.path.join(MAA_DIR, "debug", "asst.log")
OPERBOX = os.path.join(MAA_DIR, "data", "OperBoxData.json")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RARITY_FILE = os.path.join(SKILL_DIR, "data", "rarity.json")
STAGE_MAP = os.path.join(SKILL_DIR, "data", "stage_map.json")
