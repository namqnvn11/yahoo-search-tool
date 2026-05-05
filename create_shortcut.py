"""
create_shortcut.py - Tao shortcut tren Desktop de chay Yahoo Search Tool.

Chay 1 lan:
    python create_shortcut.py
"""

import os
import sys
import subprocess
from pathlib import Path


def create_desktop_shortcut():
    project_dir = Path(__file__).parent.resolve()
    python_exe = Path(sys.executable)
    main_py = project_dir / "main.py"
    desktop = Path.home() / "Desktop"
    shortcut_path = desktop / "Yahoo Search Tool.lnk"

    # Icon: dung icon cua python.exe
    icon_path = python_exe

    ps_script = f"""
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
$Shortcut.TargetPath   = '{python_exe}'
$Shortcut.Arguments    = '"{main_py}"'
$Shortcut.WorkingDirectory = '{project_dir}'
$Shortcut.WindowStyle  = 1
$Shortcut.IconLocation = '{icon_path}, 0'
$Shortcut.Description  = 'Yahoo Japan Search Tool'
$Shortcut.Save()
Write-Host 'OK'
"""

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0 and "OK" in result.stdout:
        print(f"[OK] Da tao shortcut: {shortcut_path}")
    else:
        print(f"[ERROR] Khong tao duoc shortcut.")
        if result.stderr:
            print(result.stderr.strip())


if __name__ == "__main__":
    create_desktop_shortcut()
