"""
wifi_manager.py - Quan ly mang: WiFi rotation va ethernet on/off.

Che do hoat dong (NETWORK_MODE trong .env):
  wifi           - Chi doi WiFi, ethernet khong bi anh huong (mac dinh)
  ethernet-wifi  - Tat ethernet truoc khi search (de buoc dung WiFi),
                   bat lai ethernet sau khi search xong

Su dung netsh (co san tren Windows).
Yeu cau WiFi: ca 2 SSID phai da duoc luu profile tren may.
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
    Neu doi that bai va WiFi mat ket noi (None) -> thu ket noi lai WiFi cu.
    Tra ve None neu ca 2 WiFi deu khong the ket noi duoc (can cho truoc khi chay).

    Args:
        last_run_wifi: WiFi da dung cho nhom task truoc do (lay tu log)
        wifi_1: SSID WiFi thu nhat
        wifi_2: SSID WiFi thu hai

    Returns:
        SSID cua WiFi se dung cho nhom task nay, hoac None neu khong co WiFi nao kha dung.
    """
    current = get_current_wifi()

    if not last_run_wifi:
        _log(f"[WIFI] Chua co lich su chay truoc, dung WiFi hien tai: '{current}'")
        return current

    # WiFi dang None (mat ket noi) - thu ket noi lai truoc khi quyet dinh
    if current is None:
        target = get_next_wifi(last_run_wifi, wifi_1, wifi_2)
        _log(f"[WIFI] Nhom truoc dung '{last_run_wifi}', hien tai la 'None'. Thu ket noi '{target}'...")
        if switch_to_wifi(target):
            current = get_current_wifi()
            _log(f"[WIFI] Ket noi thanh cong: '{current}'")
            return current
        _log(f"[WIFI] Khong the ket noi '{target}', thu ket noi lai '{last_run_wifi}'...")
        if switch_to_wifi(last_run_wifi):
            current = get_current_wifi()
            _log(f"[WIFI] Ket noi lai '{last_run_wifi}' thanh cong")
            return current
        _log(f"[WIFI] Khong the ket noi WiFi nao. Can cho truoc khi chay.")
        return None

    if current == last_run_wifi:
        target = get_next_wifi(current, wifi_1, wifi_2)
        _log(f"[WIFI] Nhom truoc dung '{last_run_wifi}', hien tai cung la '{current}' -> doi sang '{target}'")
        if switch_to_wifi(target):
            return get_current_wifi()
        # Switch that bai - kiem tra WiFi thuc su hien tai
        actual = get_current_wifi()
        if actual is not None:
            # Van con ket noi mot WiFi nao do (thuong la WiFi cu), tiep tuc voi no
            _log(f"[WIFI] Khong the doi sang '{target}', tiep tuc voi '{actual}'")
            return actual
        # WiFi mat ket noi (None) sau khi thu chuyen - ket noi lai WiFi cu
        _log(
            f"[WIFI] Khong the doi sang '{target}', WiFi hien tai: 'None'. "
            f"Thu ket noi lai '{last_run_wifi}'..."
        )
        if switch_to_wifi(last_run_wifi):
            actual = get_current_wifi()
            _log(f"[WIFI] Ket noi lai '{last_run_wifi}' thanh cong")
            return actual
        _log(f"[WIFI] Khong the ket noi ca '{target}' lan '{last_run_wifi}'. Can cho truoc khi chay.")
        return None
    else:
        _log(f"[WIFI] Nhom truoc dung '{last_run_wifi}', hien tai la '{current}' -> khong can doi")
        return current


# ============================================================
# Ethernet management (dung cho NETWORK_MODE=ethernet-wifi)
# ============================================================

def get_ethernet_interfaces() -> list[str]:
    """
    Tu dong phat hien cac adapter ethernet (khong phai WiFi/virtual) tren may.
    Su dung: netsh interface show interface

    Returns:
        Danh sach ten adapter, vd: ['Ethernet', 'Ethernet 2']
    """
    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        ifaces = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            # Chi xu ly cac dong co trang thai Enabled hoac Disabled
            if not stripped.startswith(("Enabled", "Disabled")):
                continue
            # Format: "Enabled   Connected   Dedicated   Interface Name"
            # Dung split 2+ khoang trang de giu nguyen ten co space
            parts = re.split(r"\s{2,}", stripped)
            if len(parts) < 4:
                continue
            iface_name = parts[3].strip()
            # Loai tru WiFi, bluetooth va adapter ao
            skip_keywords = [
                "wi-fi", "wifi", "wireless", "wlan",
                "bluetooth", "loopback", "vethernet",
                "vmware", "virtualbox", "teredo", "isatap",
            ]
            if any(kw in iface_name.lower() for kw in skip_keywords):
                continue
            ifaces.append(iface_name)
        return ifaces
    except Exception as e:
        _log(f"[ETHERNET] Loi khi lay danh sach interface: {e}")
        return []


def _set_ethernet_admin(adapters: list[str], enable: bool) -> None:
    """Bat (enable=True) hoac tat (enable=False) cac adapter cho truoc."""
    action = "enable" if enable else "disable"
    for adapter in adapters:
        try:
            result = subprocess.run(
                ["netsh", "interface", "set", "interface", adapter, f"admin={action}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            output = (result.stdout + result.stderr).strip()
            label = "Bat" if enable else "Tat"
            if output:
                _log(f"[ETHERNET] {label} '{adapter}': {output}")
            else:
                _log(f"[ETHERNET] {label} '{adapter}': OK")
        except Exception as e:
            _log(f"[ETHERNET] Loi khi {'bat' if enable else 'tat'} '{adapter}': {e}")


def disable_ethernet(ethernet_adapter: str = "") -> list[str]:
    """
    Tat ethernet truoc khi chay search (buoc traffic qua WiFi).

    Args:
        ethernet_adapter: Ten adapter cu the (lay tu ETHERNET_ADAPTER trong .env).
                          De trong de tu dong phat hien.

    Returns:
        Danh sach adapter da tat (dung de bat lai sau nay).
    """
    if ethernet_adapter:
        adapters = [ethernet_adapter]
    else:
        adapters = get_ethernet_interfaces()

    if not adapters:
        _log("[ETHERNET] Khong tim thay ethernet adapter nao de tat.")
        return []

    _log(f"[ETHERNET] Dang tat ethernet: {adapters}")
    _set_ethernet_admin(adapters, enable=False)
    time.sleep(1)
    return adapters


def enable_ethernet(adapters: list[str]) -> None:
    """
    Bat lai cac ethernet adapter sau khi search xong.
    Ham nay LUON duoc goi trong khoi finally de dam bao ethernet khong bi tat mai mai.

    Args:
        adapters: Danh sach ten adapter can bat lai (tra ve tu disable_ethernet).
    """
    if not adapters:
        return
    _log(f"[ETHERNET] Dang bat lai ethernet: {adapters}")
    _set_ethernet_admin(adapters, enable=True)
    time.sleep(2)
    _log("[ETHERNET] Ethernet da duoc bat lai.")
