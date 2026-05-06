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


def _get_wlan_interfaces() -> list[str]:
    """
    Lay danh sach ten cac interface WiFi hien co tren may.
    Vi du: ['Wi-Fi', 'Wi-Fi 2', 'Wireless Network Connection']
    """
    interfaces = []
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
            # Tim dong "Name" (khong phai "Profile Name" hay "Network Name")
            if re.match(r"^Name\s*:", stripped, re.IGNORECASE):
                match = re.search(r":\s*(.+)$", stripped)
                if match:
                    interfaces.append(match.group(1).strip())
    except Exception:
        pass
    return interfaces


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


def _run_connect(target_ssid: str, interface: str) -> str:
    """
    Chay lenh netsh wlan connect voi interface cu the.
    Tra ve stdout cua lenh.
    """
    result = subprocess.run(
        ["netsh", "wlan", "connect", f"name={target_ssid}", f"interface={interface}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (result.stdout + result.stderr).strip()


def switch_to_wifi(target_ssid: str, wait_seconds: int = 20) -> bool:
    """
    Doi sang WiFi co ten target_ssid.
    WiFi profile phai da duoc luu tren may.

    Luu y: neu gap loi "not available to connect", ham tu dong:
      1. Detect ten interface WiFi thuc te tren may (khong hard-code "Wi-Fi")
      2. Disconnect truoc roi moi connect lai
      3. Thu toan bo interface neu con nhieu card WiFi

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

    # Lay danh sach interface WiFi thuc te tren may nay
    interfaces = _get_wlan_interfaces()
    if not interfaces:
        # Fallback: thu voi ten pho bien nhat
        interfaces = ["Wi-Fi", "Wireless Network Connection"]
    _log(f"[WIFI] Interface phat hien: {interfaces}")

    def _attempt_connect(iface: str, do_disconnect: bool) -> bool:
        """Thu ket noi tren 1 interface, co the disconnect truoc."""
        if do_disconnect:
            subprocess.run(
                ["netsh", "wlan", "disconnect", f"interface={iface}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            time.sleep(1)

        output = _run_connect(target_ssid, iface)
        if output:
            _log(f"[WIFI] [{iface}] netsh: {output}")
        return "successfully" in output.lower()

    # Lan 1: connect thang (khong disconnect) tren tung interface
    for iface in interfaces:
        _attempt_connect(iface, do_disconnect=False)

    # Kiem tra sau lan 1
    for i in range(min(10, wait_seconds)):
        time.sleep(1)
        current = get_current_wifi()
        if current == target_ssid:
            _log(f"[WIFI] Ket noi thanh cong sau {i + 1}s: {target_ssid}")
            return True

    # Lan 2: disconnect truoc roi connect lai (xu ly loi "not available")
    _log(f"[WIFI] Chua ket noi duoc, thu disconnect truoc roi connect lai...")
    for iface in interfaces:
        _attempt_connect(iface, do_disconnect=True)

    # Doi ket noi duoc thiet lap
    remaining = wait_seconds - 10
    for i in range(max(remaining, 10)):
        time.sleep(1)
        current = get_current_wifi()
        if current == target_ssid:
            _log(f"[WIFI] Ket noi thanh cong sau lan 2 ({i + 1}s): {target_ssid}")
            return True
        if (i + 1) % 5 == 0:
            _log(f"[WIFI] Dang cho... ({i + 1}s), hien tai: '{current}'")

    _log(
        f"[WIFI] Timeout: khong the ket noi vao '{target_ssid}' "
        f"sau {wait_seconds}s (hien tai: '{get_current_wifi()}')"
    )
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
