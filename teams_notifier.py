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

# Span chua ten cuoc tro chuyen
# - May 1: span[id^="title-chat-list-item_"]  (khong co data-tid)
# - May 2: span[data-tid="chat-list-item-title"] (co them data-tid)
# => dung OR selector, id-prefix la chung nhat
CHAT_TITLE_SPAN = (
    'span[id^="title-chat-list-item_"], '
    'span[data-tid="chat-list-item-title"]'
)

# Ten group dang mo o header (khung tieu de phia tren cuoc tro chuyen).
# id that day du la "chat-header-19:<thread-id>@thread.v2" - phan thread-id thay
# doi theo tung group nen KHONG hardcode. Chi dua vao prefix "chat-header-" (on dinh)
# va lay <span title="..."> trong <h2> - title chua ten group hien thi day du.
CHAT_HEADER_TITLE = (
    'div[id^="chat-header-"] h2 span[title], '
    'div[id^="chat-header-"] h2'
)


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

    Chien luoc chong nhau-phien-ban:
      - Tim tat ca span ten chat bang OR selector (tuong thich ca 2 phien ban HTML)
      - Khi tim thay span khop, chi click ancestor GAN NHAT: div[data-inp=...switch] neu co,
        neu khong thi div[role=treeitem] gan nhat — khong click span tho de tranh mo nham chat
      - Khong phu thuoc vao cac attribute phu (data-testid, data-fui-tree-item-value...)

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

        # Tim tat ca span chua ten chat (OR selector - tuong thich ca 2 phien ban)
        title_spans = page.locator(CHAT_TITLE_SPAN)
        count = await title_spans.count()
        _log(f"[TEAMS] Tim thay {count} span ten chat trong danh sach.")

        if count == 0:
            _log("[TEAMS] Khong co span ten chat. Trang co the chua load xong.")
            return False

        # Chi chap nhan exact match (khop chinh xac 100%, case-insensitive)
        chat_name_exact = chat_name.strip()
        chat_name_lower = chat_name_exact.lower()

        exact_matches: list[tuple[int, str]] = []  # (index, title_text)
        all_titles: list[str] = []

        for i in range(count):
            span = title_spans.nth(i)
            title_text = (await span.inner_text()).strip()
            all_titles.append(title_text)
            if title_text.lower() == chat_name_lower:
                exact_matches.append((i, title_text))

        _log(f"[TEAMS] --- DANH SACH CHAT TIM DUOC ({count}) ---")
        for idx, t in enumerate(all_titles):
            marker = " <== MATCH" if t.lower() == chat_name_lower else ""
            _log(f"[TEAMS]   [{idx + 1:02d}] '{t}'{marker}")
        _log(f"[TEAMS] --- TIM KIEM: '{chat_name_exact}' ---")

        if not exact_matches:
            _log(
                f"[TEAMS] LOI: Khong tim thay chat co ten chinh xac la '{chat_name_exact}' "
                f"trong {count} chat hien thi. Huy gui tin nhan."
            )
            return False

        if len(exact_matches) > 1:
            _log(
                f"[TEAMS] Canh bao: co {len(exact_matches)} chat cung ten '{chat_name_exact}', "
                f"se chon cai dau tien trong danh sach."
            )

        chosen_index, chosen_title = exact_matches[0]
        _log(f"[TEAMS] Tim thay (exact match): '{chosen_title}' (vi tri {chosen_index + 1}/{count})")

        span = title_spans.nth(chosen_index)
        await span.scroll_into_view_if_needed()

        # Chi click phan tu la ANCESTOR GAN NHAT cua dung span ten da match — khong click
        # span tho (de tranh trung/lach) va khong dung .first tren day ancestor (co the map xa).
        # XPath ancestor::*[1] = node gan span nhat theo truc ancestor (thuong la dung hang chat).
        chat_switch = span.locator(
            'xpath=./ancestor::div[@data-inp="simple-collab-chat-switch"][1]'
        )
        row_treeitem = span.locator(
            'xpath=./ancestor::div[@role="treeitem"][1]'
        )

        clicked = False
        for label, loc in (
            ("data-inp=simple-collab-chat-switch (gan span ten)", chat_switch),
            ("role=treeitem gan nhat (hang list)", row_treeitem),
        ):
            try:
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=8000)
                _log(f"[TEAMS] Da click dung hang chat qua: {label}")
                clicked = True
                break
            except Exception as ex:
                _log(f"[TEAMS] Thu click ({label}) that bai: {ex}")

        if not clicked:
            _log(
                "[TEAMS] LOI: Khong the click vao hang chat (switch div hoac treeitem gan span). "
                "Khong thu click span de tranh mo nham chat khac."
            )
            return False

        await page.wait_for_timeout(2000)
        return True

    except Exception as e:
        _log(f"[TEAMS] Loi khi tim chat: {e}")
        return False


async def _find_and_click_chat_with_retry(
    page: Page,
    chat_name: str,
    max_wait_seconds: int = 180,
) -> bool:
    """
    Poll tim+click chat cho den khi thanh cong hoac het thoi gian.

    Teams web la SPA load bang JS, khong the dua vao DOMContentLoaded de biet
    trang da san sang. Tin hieu chac chan duy nhat la khi tim+click duoc chat
    trong sidebar. Voi tab da mo san (lan thu 2 tro di trong ngay), thuong
    thanh cong ngay lan dau. Voi tab moi mo (lan dau trong ngay / user lo
    tay tat) co the can vai vong poll de Teams load xong.
    """
    start = datetime.now()
    attempt = 0

    while True:
        attempt += 1
        elapsed = (datetime.now() - start).total_seconds()
        _log(f"[TEAMS] Lan thu {attempt} (sau {elapsed:.0f}s): tim chat '{chat_name}'...")

        if await _find_and_click_chat(page, chat_name):
            return True

        elapsed = (datetime.now() - start).total_seconds()
        if elapsed >= max_wait_seconds:
            _log(f"[TEAMS] Het {max_wait_seconds}s ma chua tim duoc chat '{chat_name}'.")
            return False

        _log("[TEAMS] Chua tim duoc, cho 3s roi thu lai...")
        await page.wait_for_timeout(3000)


async def _verify_chat_header(page: Page, chat_name: str, timeout_ms: int = 10000) -> bool:
    """
    Kiem tra ten group dang mo o HEADER khop voi chat_name truoc khi gui.

    Day la lop bao ve thu hai (sau buoc click chat o sidebar): du sidebar co
    click nham hang khac, header van phai dung ten group thi moi cho gui. Tranh
    gui nham tin nhan vao group khac.

    Doc ten group theo thu tu uu tien:
      1. Thuoc tinh `title` cua <span> trong <h2> (ten day du, on dinh nhat)
      2. inner_text cua <h2> (fallback neu khong co span[title])

    So khop: exact match, case-insensitive (giong logic tim chat o sidebar).

    Returns:
        True neu header khop chat_name, False neu khong khop / khong doc duoc.
    """
    chat_name_lower = chat_name.strip().lower()

    try:
        header = page.locator(CHAT_HEADER_TITLE).first
        await header.wait_for(state="visible", timeout=timeout_ms)

        # Uu tien thuoc tinh title (ten day du), neu khong co thi dung inner_text
        header_title = await header.get_attribute("title")
        if not header_title:
            header_title = await header.inner_text()
        header_title = (header_title or "").strip()

        _log(f"[TEAMS] Header group hien tai: '{header_title}' (can: '{chat_name.strip()}')")

        if header_title.lower() == chat_name_lower:
            _log("[TEAMS] Header KHOP - dung group, cho phep gui.")
            return True

        _log(
            f"[TEAMS] LOI: Header group '{header_title}' KHONG khop '{chat_name.strip()}'. "
            f"Huy gui de tranh gui nham group."
        )
        return False

    except Exception as e:
        _log(f"[TEAMS] Loi khi doc header group: {e}. Huy gui de an toan.")
        return False


async def _type_and_send_message(page: Page, message: str, dry_run: bool = False) -> bool:
    """
    Nhap tin nhan vao CKEditor compose box va bam nut Send.

    Selectors:
        - Compose box : [data-tid="ckeditor"]                       (data-tid - on dinh)
        - Send button : button[data-tid="newMessageCommands-send"]  (data-tid - on dinh)

    Teams web dung CKEditor 5 (contenteditable div voi class "ck ck-content ...").
    execCommand("insertText") khong tuong thich voi CKEditor 5, nen dung
    clipboard paste (navigator.clipboard.writeText + Ctrl+V) de dam bao
    Unicode (tieng Viet, tieng Nhat) duoc nhan vao chinh xac.

    Args:
        dry_run: Neu True, chi go tin nhan vao o soạn thảo, KHONG bam nut Send.
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

        if dry_run:
            _log("[TEAMS] [DRY-RUN] Khong bam Send. Kiem tra thu cong xem da vao dung chat chua.")
            return True

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


async def _async_send_message(cdp_url: str, chat_name: str, message: str, dry_run: bool = False) -> bool:
    """
    Ket noi den browser hien tai qua CDP, mo tab Teams web (hoac dung lai tab cu),
    tim chat theo ten va gui tin nhan.

    Args:
        cdp_url: CDP endpoint (vi du: http://localhost:9222)
        chat_name: Ten cuoc tro chuyen
        message: Noi dung tin nhan
        dry_run: Neu True, chi click chat va go tin nhan, KHONG bam Send.

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

            # Buoc 1: Tim+click chat va XAC MINH header, lap toi da 3 lan.
            # Neu header sai (vao nham group / chua load xong) -> quay lai tim chat.
            MAX_VERIFY_ATTEMPTS = 3
            verified = False
            for attempt in range(1, MAX_VERIFY_ATTEMPTS + 1):
                _log(f"[TEAMS] === Lan tim+xac minh group {attempt}/{MAX_VERIFY_ATTEMPTS} ===")

                # Lan 1: cho lau (180s) de Teams load lan dau. Cac lan retry chi
                # can 30s vi list conversation da load san.
                wait_seconds = 180 if attempt == 1 else 30
                found = await _find_and_click_chat_with_retry(
                    page, chat_name, max_wait_seconds=wait_seconds
                )
                if not found:
                    if attempt < MAX_VERIFY_ATTEMPTS:
                        _log("[TEAMS] Khong tim duoc chat, thu lai...")
                        await page.wait_for_timeout(1500)
                        continue
                    return False

                # Xac minh ten group o header truoc khi gui (chong gui nham group)
                if await _verify_chat_header(page, chat_name):
                    verified = True
                    break

                if attempt < MAX_VERIFY_ATTEMPTS:
                    _log("[TEAMS] Header sai, quay lai buoc tim group chat...")
                    await page.wait_for_timeout(1500)

            if not verified:
                _log(
                    f"[TEAMS] LOI: Sau {MAX_VERIFY_ATTEMPTS} lan van khong xac minh dung group "
                    f"'{chat_name}'. Huy gui tin nhan."
                )
                return False

            # Buoc 2: Nhap tin nhan (co the khong gui neu dry_run)
            sent = await _type_and_send_message(page, message, dry_run=dry_run)
            if not sent:
                return False

            if dry_run:
                _log(f"[TEAMS] [DRY-RUN] Da click chat '{chat_name}' va go tin nhan. Khong gui.")
            else:
                _log(f"[TEAMS] Da gui tin nhan thanh cong vao '{chat_name}'.")
            return True

        except Exception as e:
            _log(f"[TEAMS] Loi CDP / Playwright: {e}")
            return False


def send_message(chat_name: str, message: str, teams_exe: str = "", dry_run: bool = False) -> bool:
    """
    Tim nhom chat va gui tin nhan qua Teams web bang Playwright.

    Args:
        chat_name: Ten nhom chat / kenh / nguoi (phai khop voi ten hien thi tren Teams)
        message: Noi dung tin nhan (ho tro Unicode: tieng Viet, tieng Nhat...)
        teams_exe: Khong con su dung (giu lai de khong break code cu)
        dry_run: Neu True, chi click chat va go tin nhan, KHONG bam Send.

    Returns:
        True neu thanh cong, False neu that bai
    """
    load_dotenv()
    port = os.getenv("EDGE_REMOTE_DEBUGGING_PORT", "9222")
    cdp_url = os.getenv("EDGE_CDP_URL", f"http://localhost:{port}")

    if dry_run:
        _log(f"[TEAMS] [DRY-RUN] Click chat '{chat_name}' va go tin nhan (khong gui)...")
    else:
        _log(f"[TEAMS] Chuan bi gui tin nhan vao '{chat_name}'...")

    try:
        return asyncio.run(_async_send_message(cdp_url, chat_name, message, dry_run=dry_run))
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
