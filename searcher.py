"""
searcher.py - Thuc hien search Yahoo Japan bang Playwright.

Ket noi vao trinh duyet Edge dang chay qua CDP.
Ho tro che do PC (binh thuong) va PC(UA:SP) (mobile UA).
Ho tro click vao ket qua dau tien (khong phai quang cao) hoac chi cuon trang.

Selectors duoc tham khao tu project production taisaku-kun-python.
"""

import asyncio
import random
from datetime import datetime

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from wifi_manager import get_current_wifi


YAHOO_URL = "https://www.yahoo.co.jp/"

# Search input - thu theo thu tu uu tien
SEARCH_INPUT_SELECTORS = [
    'input[name="p"]',
    'input[type="search"]',
    'input[aria-label*="検索"]',
    'input[placeholder*="検索"]',
    'input.search',
]

# Search button - thu theo thu tu uu tien, fallback Enter key
SEARCH_BUTTON_SELECTORS = [
    'button[type="submit"]',
    'button._63Ie6douiF2dG_ihlFTen',
    'input[type="submit"]',
    'button[aria-label*="検索"]',
    '.search-button',
]

# Ket qua organic (khong phai quang cao): card container
RESULT_CARD_SELECTOR = "div.sw-CardBase div.sw-Card.Algo"

# Link trong ket qua - thu theo thu tu uu tien
RESULT_LINK_SELECTORS = [
    "a.sw-Card__titleInner",
    "div.sw-Card__title a[href]",
    "h3 a[href]",
    "a[href]:not([href='#'])",
]

# Anti-detection init script (inject vao moi page moi)
ANTI_DETECTION_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'language', { get: () => 'ja-JP' });
    Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
"""


async def connect_to_browser(cdp_url: str) -> Browser:
    """Ket noi vao trinh duyet Edge dang chay qua CDP."""
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    return browser


async def scroll_to_bottom(page: Page, steps: int = 5, delay: float = 0.5):
    """Cuon tu tu xuong cuoi trang de simulate nguoi that."""
    for _ in range(steps):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        await asyncio.sleep(delay + random.uniform(0.2, 0.5))

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(random.uniform(0.8, 1.5))


async def find_search_input(page: Page):
    """Tim o input search, thu lan luot cac selector."""
    for selector in SEARCH_INPUT_SELECTORS:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                await loc.wait_for(state="visible", timeout=3000)
                return loc
        except Exception:
            continue
    return None


async def find_and_click_search_button(page: Page, search_input) -> bool:
    """
    Thu click nut search theo tung selector.
    Fallback: Enter key neu khong tim thay nut.
    """
    for selector in SEARCH_BUTTON_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await asyncio.sleep(random.uniform(0.3, 0.6))
                await btn.click()
                return True
        except Exception:
            continue

    # Fallback: Enter key
    try:
        await search_input.press("Enter")
        return True
    except Exception:
        return False


async def find_first_organic_link(page: Page):
    """
    Tim link ket qua dau tien khong phai quang cao.
    Duyet qua cac card div.sw-Card.Algo, bo qua card co class 'ad' hoac 'sponsor'.
    """
    try:
        cards = page.locator(RESULT_CARD_SELECTOR)
        count = await cards.count()

        for i in range(count):
            card = cards.nth(i)

            # Kiem tra card co phai ad khong qua class cua phan tu cha
            try:
                parent_class = await card.evaluate(
                    "el => el.closest('.sw-CardBase')?.className || ''"
                )
                if any(w in parent_class.lower() for w in ["ad", "sponsor", "pr"]):
                    continue
            except Exception:
                pass

            # Tim link trong card theo thu tu selector uu tien
            for link_sel in RESULT_LINK_SELECTORS:
                try:
                    link = card.locator(link_sel).first
                    if await link.count() > 0 and await link.is_visible():
                        return link, i + 1
                except Exception:
                    continue

    except Exception:
        pass

    return None, 0


async def perform_search(
    browser: Browser,
    keyword: str,
    device_type: str,
    should_click: bool,
    mobile_ua: str,
) -> bool:
    """
    Thuc hien 1 luot search Yahoo Japan.

    Args:
        browser: Browser instance (da ket noi qua CDP)
        keyword: Tu khoa can search
        device_type: "PC" hoac "PC(UA:SP)"
        should_click: True neu can click vao ket qua dau tien (non-ad)
        mobile_ua: Mobile user agent string (dung cho PC(UA:SP))

    Returns:
        True neu thanh cong, False neu that bai
    """
    is_sp = device_type == "PC(UA:SP)"
    page = None
    cdp_session = None

    try:
        # Luon dung context hien tai cua browser (mo tab trong cua so dang chay)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()

        page = await context.new_page()

        # Inject anti-detection vao moi page moi
        await page.add_init_script(ANTI_DETECTION_SCRIPT)

        # Voi SP mode: override User-Agent qua CDP session cho tab nay
        if is_sp and mobile_ua:
            cdp_session = await context.new_cdp_session(page)
            await cdp_session.send("Network.setUserAgentOverride", {
                "userAgent": mobile_ua,
                "platform": "iPhone",
                "acceptLanguage": "ja-JP,ja;q=0.9",
            })
            await page.set_viewport_size({"width": 390, "height": 844})

        click_str = "CLICK" if should_click else "NO CLICK"
        print(f"  [{device_type}] [{click_str}] Dang search: {keyword}")

        # 1. Truy cap Yahoo Japan
        await page.goto(YAHOO_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # 2. Tim va click vao o search
        search_input = await find_search_input(page)
        if not search_input:
            print(f"  [ERROR] Khong tim thay o search tren Yahoo")
            return False

        await search_input.click()
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # 3. Nhap tu khoa kieu nguoi that (gai tu tung ky tu)
        await search_input.fill("")
        await asyncio.sleep(random.uniform(0.2, 0.4))
        for char in keyword:
            await search_input.press_sequentially(char, delay=random.randint(60, 160))
            await asyncio.sleep(random.uniform(0.04, 0.12))

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # 4. Click nut search hoac Enter
        clicked_btn = await find_and_click_search_button(page, search_input)
        if not clicked_btn:
            print(f"  [WARN] Khong tim thay nut search, da thu Enter")

        # 5. Cho trang ket qua load
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2.0, 3.5))

        if should_click:
            # 6a. Tim ket qua dau tien khong phai quang cao va click
            link, position = await find_first_organic_link(page)

            if link:
                print(f"    -> Click vao ket qua #{position}")
                await link.click()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=20000)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(2.0, 3.0))
                await scroll_to_bottom(page)
                print(f"    -> Da cuon xuong cuoi trang ket qua.")
            else:
                print(f"    [WARN] Khong tim thay ket qua de click. Chi cuon trang search.")
                await scroll_to_bottom(page)
        else:
            # 6b. Chi cuon xuong cuoi trang ket qua
            await scroll_to_bottom(page)
            print(f"    -> Da cuon xuong cuoi trang ket qua.")

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # Dong CDP session (neu co) va dong tab
        if cdp_session:
            await cdp_session.detach()
        await page.close()

        print(f"  -> Hoan thanh: {keyword}")
        return True

    except Exception as e:
        print(f"  [ERROR] Loi khi search '{keyword}': {e}")
        try:
            if cdp_session:
                await cdp_session.detach()
            if page and not page.is_closed():
                await page.close()
        except Exception:
            pass
        return False


async def execute_tasks(
    cdp_url: str,
    tasks: list,
    mobile_ua: str,
    delay_between: float = 5.0,
    session_wifi: str | None = None,
) -> list[dict]:
    """
    Thuc hien danh sach cac task search.

    Args:
        cdp_url: CDP URL cua trinh duyet Edge
        tasks: Danh sach SearchTask
        mobile_ua: Mobile user agent
        delay_between: Thoi gian cho giua cac task (giay)
        session_wifi: SSID WiFi da chon cho toan bo nhom task nay (None = khong quan ly)

    Returns:
        Danh sach ket qua, moi phan tu chua thong tin task va trang thai.
    """
    if not tasks:
        print("[INFO] Khong co task nao de thuc hien.")
        return []

    # Neu khong truyen session_wifi, ghi nhan WiFi hien tai de logging
    wifi_ssid = session_wifi if session_wifi else get_current_wifi()

    browser = await connect_to_browser(cdp_url)
    results = []

    try:
        for i, task in enumerate(tasks):
            print(f"\n--- Task {i + 1}/{len(tasks)} ---")

            success = await perform_search(
                browser=browser,
                keyword=task.keyword,
                device_type=task.device_type,
                should_click=task.should_click,
                mobile_ua=mobile_ua,
            )

            if not success:
                print(f"  [RETRY] Thu lai lan 1...")
                await asyncio.sleep(3)
                success = await perform_search(
                    browser=browser,
                    keyword=task.keyword,
                    device_type=task.device_type,
                    should_click=task.should_click,
                    mobile_ua=mobile_ua,
                )

            results.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "hour": task.hour,
                "keyword": task.keyword,
                "device_type": task.device_type,
                "should_click": task.should_click,
                "status": "success" if success else "failed",
                "wifi_ssid": wifi_ssid,
            })

            # Doi giua cac task
            if i < len(tasks) - 1:
                wait_time = delay_between + random.uniform(1, 3)
                print(f"  Cho {wait_time:.1f}s truoc task tiep theo...")
                await asyncio.sleep(wait_time)

    finally:
        await browser.close()

    return results
