"""adb helper for a MuMu/other emulator running Arknights."""
import io
import subprocess
import time

from PIL import Image

from config import ADB, SERIAL


def sh(*args, timeout=30):
    return subprocess.run([ADB, "-s", SERIAL, "shell", *args],
                          capture_output=True, timeout=timeout)


def screenshot(path=None):
    r = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=30)
    im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
    if path:
        im.save(path)
    return im


def tap(x, y, settle=0.4):
    sh("input", "tap", str(int(x)), str(int(y)))
    time.sleep(settle)


def swipe(x1, y1, x2, y2, ms=300):
    sh("input", "swipe", str(int(x1)), str(int(y1)),
       str(int(x2)), str(int(y2)), str(int(ms)))
    time.sleep(0.5)


def back():
    sh("input", "keyevent", "4")
    time.sleep(0.6)


def size():
    return sh("wm", "size").stdout.decode(errors="ignore").strip()


if __name__ == "__main__":
    print(size())
