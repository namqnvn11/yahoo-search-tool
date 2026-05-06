"""
teams_notifier.py - Tu dong gui tin nhan vao Microsoft Teams qua UI automation.

Su dung pyautogui + clipboard de nhan tin nhan voi danh nghia chinh ban than.
Yeu cau: Microsoft Teams phai duoc cai dat tren may.
"""

import subprocess
import time
from datetime import datetime
from pathlib import Path

import pyautogui
import pyperclip

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    from pywinauto import Desktop as _UIA_Desktop
    HAS_PYWINAUTO = True
except ImportError:
    HAS_PYWINAUTO = False

LOG_DIR = Path("logs")
TEAMS_LOG_FILE = LOG_DIR / "teams.log"

# Tat failsafe cua pyautogui (mac dinh: di chuot den goc trai se dung lai)
# Giu nguyen True de an toan
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3


def _log(msg: str) -> None:
    """In ra console va ghi vao file log Teams."""
    print(msg)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(TEAMS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _get_teams_uia_window():
    """
    Lay cua so Teams chinh qua pywinauto UIA.
    Neu co nhieu cua so (popup/notification), chon cai co title dai nhat (cua so chinh).
    Tra ve window object hoac None.
    """
    if not HAS_PYWINAUTO:
        return None
    try:
        d = _UIA_Desktop(backend="uia")
        wins = d.windows(title_re=".*Teams.*")
        if not wins:
            return None
        # Chon cua so co title dai nhat = cua so chinh (khong phai popup)
        return max(wins, key=lambda w: len(w.window_text()))
    except Exception:
        return None


def _find_teams_window() -> object | None:
    """Tim cua so Microsoft Teams dang mo (pygetwindow)."""
    if not HAS_PYGETWINDOW:
        return None
    try:
        for title_pattern in ["Microsoft Teams", "Teams"]:
            windows = gw.getWindowsWithTitle(title_pattern)
            if windows:
                return windows[0]
    except Exception:
        pass
    return None


def _open_teams(teams_exe: str = "") -> bool:
    """
    Mo Microsoft Teams neu chua chay.
    Tra ve True neu Teams da san sang, False neu that bai.
    """
    win = _find_teams_window()
    if win:
        _log("[TEAMS] Teams dang chay, bring to front...")
        try:
            win.activate()
            time.sleep(1)
        except Exception:
            pass
        return True

    _log("[TEAMS] Dang mo Microsoft Teams...")

    # Thu cac duong dan pho bien
    candidates = [
        teams_exe,
        r"C:\Users\\" + __import__('os').environ.get('USERNAME', '') + r"\AppData\Local\Microsoft\Teams\Update.exe",
        r"C:\Program Files\WindowsApps\MSTeams_25xxx\ms-teams.exe",
    ]
    # Dung shell de mo qua Start menu (hoat dong voi ca old & new Teams)
    try:
        subprocess.Popen(["explorer.exe", "msteams://"])
        time.sleep(6)
    except Exception:
        pass

    # Kiem tra lai
    for _ in range(10):
        win = _find_teams_window()
        if win:
            _log("[TEAMS] Teams da san sang.")
            return True
        time.sleep(1)

    _log("[TEAMS] Khong tim thay cua so Teams sau khi mo.")
    return False


def _focus_teams() -> bool:
    """
    Focus vao cua so Teams chinh.
    Uu tien pywinauto (xu ly duoc truong hop nhieu cua so).
    Fallback sang pygetwindow neu can.
    """
    # Cach 1: pywinauto set_focus (xu ly duoc nhieu cua so)
    uia_win = _get_teams_uia_window()
    if uia_win:
        try:
            uia_win.set_focus()
            time.sleep(0.8)
            return True
        except Exception as e:
            _log(f"[TEAMS] pywinauto set_focus loi: {e}")

    # Cach 2: pygetwindow activate
    win = _find_teams_window()
    if not win:
        _log("[TEAMS] Khong tim thay cua so Teams.")
        return False
    try:
        win.activate()
        time.sleep(0.8)
        return True
    except Exception as e:
        # "Error code from Windows: 0" = Windows bao thanh cong nhung pygetwindow
        # raise exception nham - xu ly nhu thanh cong
        if "Error code from Windows: 0" in str(e):
            time.sleep(0.8)
            return True
        _log(f"[TEAMS] Khong the focus Teams: {e}")
        return False


def _type_message(text: str) -> None:
    """
    Nhan tin nhan vao o chat dang duoc focus.
    Dung clipboard de ho tro Unicode (tieng Viet, tieng Nhat).
    """
    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)


def _click_chat_in_list(chat_name: str) -> bool:
    """
    Tim va click truc tiep vao chat trong danh sach ben trai bang pywinauto.
    Moi TreeItem co text dang: "Group chat TEN_CHAT Has pinned... Last message..."
    Tra ve True neu tim thay va click thanh cong.
    """
    if not HAS_PYWINAUTO:
        return False

    try:
        w = _get_teams_uia_window()
        if not w:
            return False
        items = w.descendants(control_type="TreeItem")
        chat_name_lower = chat_name.lower()
        for item in items:
            txt = item.window_text()
            if chat_name_lower in txt.lower():
                _log(f"[TEAMS] Tim thay trong chat list: {txt[:60]}...")
                item.click_input()
                time.sleep(2.0)
                return True
    except Exception as e:
        _log(f"[TEAMS] pywinauto loi: {e}")

    return False


def _click_compose_box() -> bool:
    """
    Focus vao compose box sau khi da mo dung chat.
    Dung phim tat Alt+Shift+C, sau do click vao panel ben phai (khong dung pywinauto
    vi cac chat list item cung co chu 'message' de gay nham lan).
    """
    # Cach 1: Phim tat chinh thuc cua Teams
    pyautogui.hotkey("alt", "shift", "c")
    time.sleep(0.5)

    # Cach 2: Click vao panel ben phai - lay toa do tu pywinauto (chinh xac hon pygetwindow)
    uia_win = _get_teams_uia_window()
    if uia_win:
        try:
            rect = uia_win.rectangle()
            # 60% chieu rong: nam trong vung chat, tranh sidebar ben trai
            cx = rect.left + int((rect.right - rect.left) * 0.60)
            cy = rect.bottom - 60
            pyautogui.click(cx, cy)
            time.sleep(0.4)
            _log(f"[TEAMS] Click compose box tai ({cx}, {cy})")
            return True
        except Exception as e:
            _log(f"[TEAMS] Khong the lay toa do cua so: {e}")

    return False


def send_message(chat_name: str, message: str, teams_exe: str = "") -> bool:
    """
    Tim nhom chat va gui tin nhan.
    Uu tien click truc tiep vao chat list (pywinauto),
    fallback sang Ctrl+E search neu khong tim thay.

    Args:
        chat_name: Ten nhom chat / kenh / nguoi
        message: Noi dung tin nhan (ho tro Unicode)
        teams_exe: Duong dan den Teams exe (tuy chon)

    Returns:
        True neu gui thanh cong, False neu that bai
    """
    _log(f"[TEAMS] Chuan bi gui tin nhan vao '{chat_name}'...")

    # 1. Dam bao Teams dang chay
    if not _open_teams(teams_exe):
        _log("[TEAMS] Khong the mo Teams. Bo qua notification.")
        return False

    # 2. Focus Teams -> tim chat trong list
    if not _focus_teams():
        return False
    time.sleep(0.8)

    found = _click_chat_in_list(chat_name)

    # 3. Fallback: Ctrl+E search neu khong tim thay trong list
    if not found:
        _log(f"[TEAMS] Khong tim thay trong chat list, thu Ctrl+E search...")
        if not _focus_teams():
            return False
        pyautogui.hotkey("ctrl", "e")
        time.sleep(1.2)
        pyautogui.hotkey("ctrl", "a")
        pyperclip.copy(chat_name)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(2.0)
        pyautogui.press("down")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(2.5)

    # 4. Focus Teams -> Escape dong popup neu co
    if not _focus_teams():
        return False
    pyautogui.press("escape")
    time.sleep(0.3)

    # 5. Focus Teams -> click compose box
    if not _focus_teams():
        return False
    _click_compose_box()

    # 6. Focus Teams -> nhan tin nhan va gui
    if not _focus_teams():
        return False
    _type_message(message)
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.5)

    _log(f"[TEAMS] Da gui tin nhan vao '{chat_name}'.")
    return True


def notify_hour_complete(
    hour: int,
    results: list[dict],
    chat_name: str,
    teams_exe: str = "",
) -> None:
    """
    Gui thong bao don gian sau khi hoan thanh nhom task 1 gio.
    Vi du: "10:00 VNT- Done"
    """
    if not chat_name:
        return

    message = f"{hour:02d}:00 VNT- Done"
    _log(f"[TEAMS] Gui: {message}")

    try:
        send_message(chat_name=chat_name, message=message, teams_exe=teams_exe)
    except Exception as e:
        _log(f"[TEAMS] Loi khi gui thong bao: {e}")
