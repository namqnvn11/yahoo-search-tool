"""
sheet_reader.py - Doc du lieu tu Google Sheet va tra ve danh sach task search theo gio.

Cau truc sheet (moi nhan vien co 3 dong):
  - Dong 1: Ten nhan vien (cot A) | PC hoac PC(UA:SP) (cac cot du lieu)
  - Dong 2: "trang thai (click hay khong)" | click status
  - Dong 3: "Tu khoa" | keyword text

Header:
  - Dong ngay: chua ngay (vd: 5/5)
  - Dong gio: chua cac khung gio (8:00, 9:00, ... 14:00), moi khung gio 2 cot
"""

import os
import re
from datetime import datetime, date
from dataclasses import dataclass

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


@dataclass
class SearchTask:
    """Mot task search Yahoo."""
    keyword: str
    device_type: str       # "PC" hoac "PC(UA:SP)"
    should_click: bool     # True neu can click vao ket qua
    hour: int              # Khung gio (8, 9, 10, ...)
    column_index: int      # Vi tri cot trong sheet


def get_sheet_client(credentials_file: str) -> gspread.Client:
    """Tao Google Sheets client tu service account credentials."""
    creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return gspread.authorize(creds)


def find_today_column_range(header_rows: list[list[str]], today: date) -> dict[int, list[int]]:
    """
    Tim cac cot tuong ung voi ngay hom nay va map tung khung gio den cac cot.
    Chi lay cac cot nam trong pham vi cua ngay hom nay (khong lay cot cua ngay khac).

    Returns:
        Dict mapping hour (int) -> list of column indices (0-based)
    """
    today_str_patterns = [
        f"{today.month}/{today.day}",
        f"{today.month}/{today.day:02d}",
        f"{today.month:02d}/{today.day}",
        f"{today.month:02d}/{today.day:02d}",
    ]

    date_row_idx = -1
    date_col_start = -1

    for row_idx, row in enumerate(header_rows):
        for col_idx, cell in enumerate(row):
            if str(cell).strip() in today_str_patterns:
                date_row_idx = row_idx
                date_col_start = col_idx
                break
        if date_row_idx >= 0:
            break

    if date_row_idx < 0:
        print(f"[WARN] Khong tim thay ngay hom nay ({today.month}/{today.day}) trong sheet.")
        return {}

    # Tim cot ket thuc cua ngay hom nay (o cot co gia tri khac trong cung dong date)
    date_row = header_rows[date_row_idx]
    date_col_end = len(date_row)
    for col_idx in range(date_col_start + 1, len(date_row)):
        cell = str(date_row[col_idx]).strip()
        if cell and cell not in today_str_patterns:
            date_col_end = col_idx
            break

    # Tim dong chua cac khung gio
    time_row_idx = date_row_idx + 1 if date_row_idx + 1 < len(header_rows) else date_row_idx
    time_row = header_rows[time_row_idx] if time_row_idx < len(header_rows) else []

    # Fallback: gio co the nam tren cung dong voi ngay
    if not any(re.match(r"^\d{1,2}:\d{2}$", str(c).strip()) for c in time_row if c):
        time_row = header_rows[date_row_idx]

    # Chi quet cot trong pham vi ngay hom nay [date_col_start, date_col_end)
    hour_map: dict[int, list[int]] = {}
    current_hour = None
    for col_idx in range(date_col_start, date_col_end):
        cell = str(time_row[col_idx]).strip() if col_idx < len(time_row) else ""
        match = re.match(r"^(\d{1,2}):(\d{2})$", cell)
        if match:
            current_hour = int(match.group(1))

        if current_hour is not None:
            hour_map.setdefault(current_hour, []).append(col_idx)

    return hour_map


def find_user_rows(all_data: list[list[str]], user_name: str) -> tuple[int, int, int] | None:
    """
    Tim 3 dong cua nhan vien trong sheet.
    
    Returns:
        Tuple (device_row, click_row, keyword_row) - 0-based row indices
        None neu khong tim thay
    """
    user_name_upper = user_name.strip().upper()
    
    for row_idx, row in enumerate(all_data):
        if not row:
            continue
        cell_a = str(row[0]).strip().upper()

        # Bo qua o trong (tranh khop nham voi chuoi rong)
        if not cell_a:
            continue

        # Tim ten nhan vien trong cot A
        if user_name_upper in cell_a or cell_a in user_name_upper:
            # Dong nay la dong ten + device type (PC/PC(UA:SP))
            device_row = row_idx
            click_row = row_idx + 1
            keyword_row = row_idx + 2
            
            if keyword_row < len(all_data):
                return (device_row, click_row, keyword_row)
    
    return None


def parse_click_status(cell_value: str) -> bool:
    """Kiem tra cell co yeu cau click hay khong."""
    val = str(cell_value).strip().lower()
    # "co click" hoac "クリック有" -> True
    # "khong click" hoac "クリック無" -> False
    if "co click" in val or "có click" in val or "クリック有" in val:
        if "無" not in val and "khong" not in val and "không" not in val:
            return True
    return False


def parse_device_type(cell_value: str) -> str:
    """Tra ve device type tu cell value."""
    val = str(cell_value).strip().upper()
    if "UA:SP" in val or "UA：SP" in val:
        return "PC(UA:SP)"
    if "PC" in val:
        return "PC"
    return ""


def get_search_tasks(
    credentials_file: str,
    sheet_id: str,
    sheet_name: str,
    user_name: str,
    target_date: date | None = None,
    debug: bool = False,
) -> dict[int, list[SearchTask]]:
    """
    Doc sheet va tra ve danh sach task theo tung khung gio.
    
    Returns:
        Dict mapping hour (int) -> list[SearchTask]
    """
    if target_date is None:
        target_date = date.today()
    
    client = get_sheet_client(credentials_file)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except Exception:
        available = [ws.title for ws in spreadsheet.worksheets()]
        print("[ERROR] Khong tim thay tab trong sheet. Kiem tra SHEET_NAME trong .env")
        for t in available:
            try:
                print(f"  - {t}")
            except UnicodeEncodeError:
                print(f"  - {t.encode('utf-8', errors='replace')}")
        return {}

    all_data = worksheet.get_all_values()

    if not all_data:
        print("[ERROR] Sheet rong!")
        return {}

    # Lay header rows (5 dong dau)
    header_rows = all_data[:5]

    if debug:
        print("\n[DEBUG] === 5 DONG DAU SHEET ===")
        for i, row in enumerate(header_rows):
            # In toi da 20 cot dau
            preview = [str(c)[:20] for c in row[:20]]
            print(f"  Row {i}: {preview}")

    # Tim mapping gio -> cot
    hour_map = find_today_column_range(header_rows, target_date)

    if debug:
        print(f"\n[DEBUG] hour_map = {hour_map}")

    if not hour_map:
        print("[ERROR] Khong tim thay khung gio cho ngay hom nay.")
        print("[INFO] Thu kiem tra lai:")
        print(f"       - Ngay hom nay: {target_date.month}/{target_date.day}")
        print(f"       - Cac gia tri trong 5 dong dau:")
        for i, row in enumerate(header_rows):
            cells = [str(c).strip() for c in row if str(c).strip()]
            if cells:
                print(f"         Row {i}: {cells[:15]}")
        return {}

    print(f"[INFO] Tim thay cac khung gio: {sorted(hour_map.keys())}")
    
    # Tim dong cua nhan vien
    user_rows = find_user_rows(all_data, user_name)
    if user_rows is None:
        print(f"[ERROR] Khong tim thay nhan vien: {user_name}")
        return {}
    
    device_row_idx, click_row_idx, keyword_row_idx = user_rows
    print(f"[INFO] Tim thay nhan vien '{user_name}' tai dong {device_row_idx + 1}")

    device_row = all_data[device_row_idx] if device_row_idx < len(all_data) else []
    click_row = all_data[click_row_idx] if click_row_idx < len(all_data) else []
    keyword_row = all_data[keyword_row_idx] if keyword_row_idx < len(all_data) else []

    if debug:
        print(f"\n[DEBUG] === DU LIEU CUA '{user_name}' ===")
        print(f"  device_row  (row {device_row_idx + 1}): {[str(c)[:15] for c in device_row[:20]]}")
        print(f"  click_row   (row {click_row_idx + 1}): {[str(c)[:15] for c in click_row[:20]]}")
        print(f"  keyword_row (row {keyword_row_idx + 1}): {[str(c)[:20] for c in keyword_row[:20]]}")
    
    # Tao task cho tung khung gio
    tasks_by_hour: dict[int, list[SearchTask]] = {}
    
    for hour, col_indices in sorted(hour_map.items()):
        tasks = []
        for col_idx in col_indices:
            # Lay du lieu tu cac dong
            device = parse_device_type(
                device_row[col_idx] if col_idx < len(device_row) else ""
            )
            should_click = parse_click_status(
                click_row[col_idx] if col_idx < len(click_row) else ""
            )
            keyword = (
                str(keyword_row[col_idx]).strip()
                if col_idx < len(keyword_row)
                else ""
            )
            
            if not keyword or not device:
                continue
            
            tasks.append(SearchTask(
                keyword=keyword,
                device_type=device,
                should_click=should_click,
                hour=hour,
                column_index=col_idx,
            ))
        
        if tasks:
            tasks_by_hour[hour] = tasks
    
    return tasks_by_hour


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    creds = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    sid = os.getenv("SHEET_ID", "")
    sname = os.getenv("SHEET_NAME", "Sheet1")
    uname = os.getenv("USER_NAME", "")
    
    tasks = get_search_tasks(creds, sid, sname, uname)
    
    for hour, task_list in sorted(tasks.items()):
        print(f"\n=== {hour}:00 ===")
        for t in task_list:
            click_str = "CLICK" if t.should_click else "NO CLICK"
            print(f"  [{t.device_type}] [{click_str}] {t.keyword}")
