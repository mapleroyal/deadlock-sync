#!/usr/bin/env python3
"""Exercise real widget, right-click menu, and quit action without live units."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent / "plugin"
SHELL = Path("/usr/share/omarchy/shell")
with tempfile.TemporaryDirectory(prefix="deadlock-qml-") as temporary:
    root = Path(temporary)
    shutil.copy2(HERE / "shell.qml", root / "shell.qml")
    (root / "Plugin").symlink_to(PLUGIN, target_is_directory=True)
    (root / "Commons").symlink_to(SHELL / "Commons", target_is_directory=True)
    (root / "Ui").mkdir()
    for source in (SHELL / "Ui").iterdir():
        if source.name != "KeyboardPanel.qml":
            (root / "Ui" / source.name).symlink_to(source)
    shutil.copy2(HERE / "KeyboardPanel.qml", root / "Ui/KeyboardPanel.qml")
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(("QT_", "QML", "DBUS_", "HYPRLAND_", "WAYLAND_", "LD_")) or key in {"DISPLAY", "XAUTHORITY"}:
            environment.pop(key, None)
    for key, name in (("HOME", "home"), ("XDG_RUNTIME_DIR", "runtime"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        path = root / name
        path.mkdir(mode=0o700)
        environment[key] = str(path)
    environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QPA_PLATFORMTHEME="", QT_QUICK_CONTROLS_STYLE="Basic")
    try:
        result = subprocess.run(["quickshell", "-p", str(root), "--no-color"], env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=8)
    except subprocess.TimeoutExpired as exc:
        print(exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout, flush=True)
        raise
    print(result.stdout, end="")
    assert result.returncode == 0 and 'DEADLOCK_UI_RESULTS {"failures":0}' in result.stdout
    assert not re.search(r"ReferenceError|TypeError|Unable to assign|Cannot assign|is not a type", result.stdout)
