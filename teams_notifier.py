"""
teams_notifier.py - Tu dong gui tin nhan vao Microsoft Teams qua Playwright web.

Su dung Playwright de dieu khien Teams web (https://teams.live.com/v2/)
trong mot tab rieng biet cua browser hien tai, cho phep nguoi dung tiep tuc
lam viec song song ma khong bi gian doan.

Yeu cau: Edge phai dang chay voi remote debugging (CDP) va da dang nhap Teams web.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, Page

LOG_DIR = Path("logs")
TEAMS_LOG_FILE = LOG_DIR / "teams.log"
TEAMS_URL = "https://teams.live.com/v2/"

# ============================================================
# Selectors - dua tren cau truc HTML cua Teams web
# ============================================================

# Cay danh sach chat ben trai
CHAT_LIST_TREE = 'div[role="tree"]'

# Moi item la 1 cuoc tro chuyen trong danh sach
CHAT_ITEM = 'div[role="treeitem"][data-testid="list-item"]'

# Span chua ten cuoc tro chuyen ben trong moi CHAT_ITEM
CHAT_TITLE = 'span[id^="title-chat-list-item_"]'


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


async def _get_or_create_teams_page(browser: Browser) -> Page:
    """
    Tim tab Teams web da mo trong browser hien tai va tra ve no.
    Neu chua co, tao tab moi va dieu huong den Teams web.
    """
    context = browser.contexts[0]

    # Tim tab Teams dang mo
    for page in context.pages:
        if "teams.live.com" in page.url:
            _log(f"[TEAMS] Dung lai tab Teams da mo: {page.url}")
            return page

    # Tao tab moi
    _log("[TEAMS] Chua co tab Teams, dang mo tab moi...")
    page = await context.new_page()
    await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=30000)
    # Cho trang khoi dong xong
    await page.wait_for_timeout(3000)
    return page


async def _find_and_click_chat(page: Page, chat_name: str) -> bool:
    """
    Tim va click vao cuoc tro chuyen khop voi chat_name trong danh sach ben trai.

    Selector path:
        div[role="tree"]
            div[data-fui-tree-item-value*="RecentChats"]
                div[role="group"]
                    div[role="treeitem"][data-testid="list-item"]  <- moi chat 1 div
                        span[id^="title-chat-list-item_"]          <- ten chat

    Args:
        page: Playwright Page dang hien thi Teams web
        chat_name: Ten cuoc tro chuyen can tim (lay tu TEAMS_CHAT_NAME trong .env)

    Returns:
        True neu tim thay va click thanh cong, False neu that bai
    """
    _log(f"[TEAMS] Tim chat '{chat_name}' trong danh sach...")

    try:
        # Doi danh sach chat hien thi
        await page.wait_for_selector(CHAT_LIST_TREE, timeout=15000)
        await page.wait_for_timeout(500)

        # Lay tat ca cac chat item
        chat_items = page.locator(CHAT_ITEM)
        count = await chat_items.count()
        _log(f"[TEAMS] Danh sach co {count} cuoc tro chuyen.")

        if count == 0:
            _log("[TEAMS] Danh sach chat trong. Trang co the chua load xong.")
            return False

        # Duyet qua tung item, tim cai co ten khop (case-insensitive)
        chat_name_lower = chat_name.lower().strip()
        for i in range(count):
            item = chat_items.nth(i)
            title_span = item.locator(CHAT_TITLE)

            if await title_span.count() == 0:
                continue

            title_text = (await title_span.inner_text()).strip()
            if chat_name_lower in title_text.lower():
                _log(f"[TEAMS] Tim thay: '{title_text}' (vi tri {i + 1}/{count})")
                await item.click()
                await page.wait_for_timeout(1500)
                return True

        _log(
            f"[TEAMS] Khong tim thay '{chat_name}' "
            f"trong {count} cuoc tro chuyen hien thi."
        )
        return False

    except Exception as e:
        _log(f"[TEAMS] Loi khi tim chat: {e}")
        return False


async def _type_and_send_message(page: Page, message: str) -> bool:
    """
    Nhap tin nhan vao CKEditor compose box va bam nut Send.

    Selectors:
        - Compose box : [data-tid="ckeditor"]                       (data-tid - on dinh)
        - Send button : button[data-tid="newMessageCommands-send"]  (data-tid - on dinh)

    Teams web dung CKEditor 5 (contenteditable div voi class "ck ck-content ...").
    execCommand("insertText") khong tuong thich voi CKEditor 5, nen dung
    clipboard paste (navigator.clipboard.writeText + Ctrl+V) de dam bao
    Unicode (tieng Viet, tieng Nhat) duoc nhan vao chinh xac.
    """
    try:
        # data-tid="ckeditor" la selector duy nhat va on dinh nhat cho compose box
        compose = page.locator('[data-tid="ckeditor"]')
        await compose.wait_for(state="visible", timeout=10000)
        await compose.click()
        await page.wait_for_timeout(300)

        # Xoa noi dung cu (neu co) truoc khi nhap moi
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(100)

        # Ghi noi dung vao clipboard qua JS, sau do paste bang Ctrl+V
        # Cach nay ho tro day du Unicode ma khong can cai dat them gi
        await page.evaluate("(text) => navigator.clipboard.writeText(text)", message)
        await page.keyboard.press("Control+v")
        await page.wait_for_timeout(400)

        _log(f"[TEAMS] Da nhap: {message[:40]}{'...' if len(message) > 40 else ''}")

        # Fallback giua 2 data-tid cua nut Send (Teams thay doi theo phien ban/che do)
        send_btn = page.locator(
            'button[data-tid="newMessageCommands-send"]'
        ).or_(
            page.locator('button[data-tid="sendMessageCommands-send"]')
        )

        try:
            await send_btn.first.wait_for(state="visible", timeout=5000)
            await send_btn.first.click()
            _log("[TEAMS] Da bam nut Send.")
        except Exception:
            # Fallback cuoi: Ctrl+Enter (Teams luon ho tro phim tat nay)
            _log("[TEAMS] Khong tim thay nut Send, thu Ctrl+Enter...")
            await page.keyboard.press("Control+Enter")

        await page.wait_for_timeout(500)
        return True

    except Exception as e:
        _log(f"[TEAMS] Loi khi nhap/gui tin nhan: {e}")
        return False


async def _async_send_message(cdp_url: str, chat_name: str, message: str) -> bool:
    """
    Ket noi den browser hien tai qua CDP, mo tab Teams web (hoac dung lai tab cu),
    tim chat theo ten va gui tin nhan.

    Args:
        cdp_url: CDP endpoint (vi du: http://localhost:9222)
        chat_name: Ten cuoc tro chuyen
        message: Noi dung tin nhan

    Returns:
        True neu thanh cong, False neu that bai
    """
    async with async_playwright() as p:
        try:
            # Ket noi den browser dang chay qua CDP
            browser = await p.chromium.connect_over_cdp(cdp_url)
            _log("[TEAMS] Da ket noi den browser qua CDP.")

            # Cap quyen clipboard de co the dung navigator.clipboard.writeText + Ctrl+V
            await browser.contexts[0].grant_permissions(
                ["clipboard-read", "clipboard-write"]
            )

            # Tim hoac tao tab Teams
            page = await _get_or_create_teams_page(browser)

            # Neu tab khong con o trang Teams (vd: da di chuyen), navigate lai
            if "teams.live.com" not in page.url:
                _log("[TEAMS] Tab khong con o Teams, dang navigate lai...")
                await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

            # Buoc 1: Tim va click vao chat
            found = await _find_and_click_chat(page, chat_name)
            if not found:
                return False

            # Buoc 2: Nhap tin nhan va bam Send
            sent = await _type_and_send_message(page, message)
            if not sent:
                return False

            _log(f"[TEAMS] Da gui tin nhan thanh cong vao '{chat_name}'.")
            return True

        except Exception as e:
            _log(f"[TEAMS] Loi CDP / Playwright: {e}")
            return False


def send_message(chat_name: str, message: str, teams_exe: str = "") -> bool:
    """
    Tim nhom chat va gui tin nhan qua Teams web bang Playwright.

    Args:
        chat_name: Ten nhom chat / kenh / nguoi (phai khop voi ten hien thi tren Teams)
        message: Noi dung tin nhan (ho tro Unicode: tieng Viet, tieng Nhat...)
        teams_exe: Khong con su dung (giu lai de khong break code cu)

    Returns:
        True neu gui thanh cong, False neu that bai
    """
    load_dotenv()
    port = os.getenv("EDGE_REMOTE_DEBUGGING_PORT", "9222")
    cdp_url = os.getenv("EDGE_CDP_URL", f"http://localhost:{port}")

    _log(f"[TEAMS] Chuan bi gui tin nhan vao '{chat_name}'...")

    try:
        return asyncio.run(_async_send_message(cdp_url, chat_name, message))
    except Exception as e:
        _log(f"[TEAMS] Loi: {e}")
        return False


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
