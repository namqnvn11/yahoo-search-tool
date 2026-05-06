"""
wifi_manager.py - Quan ly WiFi: lay SSID hien tai va doi WiFi.

Su dung netsh (co san tren Windows) de ket noi den WiFi profile da luu.
Yeu cau: ca 2 WiFi phai da duoc luu profile tren may (da tung ket noi truoc).
"""

import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")
WIFI_LOG_FILE = LOG_DIR / "wifi.log"


def _log(msg: str) -> None:
    """In ra console va ghi vao file log WiFi."""
    print(msg)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(WIFI_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def get_current_wifi() -> str | None:
    """
    Lay SSID cua WiFi dang ket noi.
    Tra ve None neu khong ket noi WiFi hoac gap loi.
    """
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("SSID") and "BSSID" not in stripped:
                match = re.search(r":\s*(.+)$", stripped)
                if match:
                    return match.group(1).strip()
    except Exception:
        pass
    return None


def switch_to_wifi(target_ssid: str, wait_seconds: int = 20) -> bool:
    """
    Doi sang WiFi co ten target_ssid.
    WiFi profile phai da duoc luu tren may.

    Args:
        target_ssid: Ten WiFi can ket noi (phai trung voi ten profile da luu)
        wait_seconds: So giay cho doi ket noi thanh cong

    Returns:
        True neu ket noi thanh cong, False neu that bai
    """
    current = get_current_wifi()
    if current == target_ssid:
        _log(f"[WIFI] Da ket noi san: {target_ssid}")
        return True

    _log(f"[WIFI] Dang chuyen tu '{current}' sang '{target_ssid}'...")

    try:
        result = subprocess.run(
            ["netsh", "wlan", "connect", f"name={target_ssid}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # netsh thuong tra ve thong bao o stdout (khong dung returncode de kiem tra)
        # "Connection request was completed successfully." -> thanh cong
        # Cac thong bao loi khac -> that bai
        if stdout:
            _log(f"[WIFI] netsh: {stdout}")
        if stderr:
            _log(f"[WIFI] netsh stderr: {stderr}")

        # Neu co bao loi ro rang (khong phai thong bao thanh cong), thoat som
        stdout_lower = stdout.lower()
        if stdout and "successfully" not in stdout_lower and (
            "error" in stdout_lower
            or "not found" in stdout_lower
            or "cannot" in stdout_lower
            or "failed" in stdout_lower
            or "khong" in stdout_lower
        ):
            _log(f"[WIFI] Lenh ket noi bao loi, se tiep tuc doi ket noi...")

    except Exception as e:
        _log(f"[WIFI] Loi khi chay lenh netsh: {e}")
        return False

    # Doi ket noi duoc thiet lap (bat ke returncode, chi tin vao ket qua thuc te)
    for i in range(wait_seconds):
        time.sleep(1)
        current = get_current_wifi()
        if current == target_ssid:
            _log(f"[WIFI] Ket noi thanh cong sau {i + 1}s: {target_ssid}")
            return True
        if (i + 1) % 5 == 0:
            _log(f"[WIFI] Dang cho ket noi... ({i + 1}s/{wait_seconds}s), hien tai: '{current}'")

    _log(f"[WIFI] Timeout: khong the ket noi vao '{target_ssid}' sau {wait_seconds}s (hien tai: '{get_current_wifi()}')")
    return False


def get_next_wifi(current_ssid: str | None, wifi_1: str, wifi_2: str) -> str:
    """Tra ve WiFi con lai (khong phai WiFi dang dung)."""
    if current_ssid == wifi_1:
        return wifi_2
    return wifi_1


def ensure_different_from_last(
    last_run_wifi: str | None,
    wifi_1: str,
    wifi_2: str,
) -> str | None:
    """
    Dam bao WiFi hien tai khac voi WiFi cua lan chay truoc.

    Neu WiFi hien tai trung voi last_run_wifi -> doi sang WiFi kia.
    Goi ham nay 1 lan truoc khi bat dau chay ca nhom task.

    Args:
        last_run_wifi: WiFi da dung cho nhom task truoc do (lay tu log)
        wifi_1: SSID WiFi thu nhat
        wifi_2: SSID WiFi thu hai

    Returns:
        SSID cua WiFi se dung cho nhom task nay (sau khi doi neu can)
    """
    current = get_current_wifi()

    if not last_run_wifi:
        _log(f"[WIFI] Chua co lich su chay truoc, dung WiFi hien tai: '{current}'")
        return current

    if current == last_run_wifi:
        target = get_next_wifi(current, wifi_1, wifi_2)
        _log(f"[WIFI] Nhom truoc dung '{last_run_wifi}', hien tai cung la '{current}' -> doi sang '{target}'")
        success = switch_to_wifi(target)
        if success:
            current = get_current_wifi()
        else:
            _log(f"[WIFI] Khong the doi WiFi, tiep tuc voi '{current}'")
    else:
        _log(f"[WIFI] Nhom truoc dung '{last_run_wifi}', hien tai la '{current}' -> khong can doi")

    return current
