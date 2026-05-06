"""
main.py - Chuong trinh chinh: doc sheet, lap lich va thuc hien search Yahoo Japan.

Su dung:
    python main.py              # Chay tuan tu, moi nhom cach nhau 1 tieng (tu dong tiep tuc khi restart)
    python main.py --all        # Chay tat ca cac nhom lien tuc (khong doi)
    python main.py --test-sheet # Chi hien thi du lieu tu sheet, khong search
    python main.py --test-teams # Gui tin nhan test vao Teams va thoat
    python main.py --test-wifi # Hien thi WiFi hien tai, doi sang WiFi con lai va thoat
"""

import os
import sys
import json
import time
import asyncio
import argparse
import subprocess
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from sheet_reader import get_search_tasks, SearchTask
from searcher import execute_tasks
from teams_notifier import notify_hour_complete

LOG_DIR = Path("logs")


def load_config() -> dict:
    """Load cau hinh tu .env file."""
    load_dotenv()
    
    port = os.getenv("EDGE_REMOTE_DEBUGGING_PORT", "9222")
    config = {
        "credentials_file": os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        "sheet_id": os.getenv("SHEET_ID", ""),
        "sheet_name": os.getenv("SHEET_NAME", "Sheet1"),
        "user_name": os.getenv("USER_NAME", ""),
        "cdp_url": os.getenv("EDGE_CDP_URL", f"http://localhost:{port}"),
        "mobile_ua": os.getenv("MOBILE_USER_AGENT", ""),
        "edge_executable": os.getenv("EDGE_EXECUTABLE_PATH", ""),
        "edge_user_data_dir": os.getenv("EDGE_USER_DATA_DIR", ""),
        "edge_profile_directory": os.getenv("EDGE_PROFILE_DIRECTORY", "Default"),
        "edge_port": port,
        "wifi_1": os.getenv("WIFI_1_SSID", "").strip(),
        "wifi_2": os.getenv("WIFI_2_SSID", "").strip(),
        "network_mode": os.getenv("NETWORK_MODE", "wifi").strip().lower(),
        "ethernet_adapter": os.getenv("ETHERNET_ADAPTER", "").strip(),
        "teams_chat_name": os.getenv("TEAMS_CHAT_NAME", "").strip(),
        "teams_exe": os.getenv("TEAMS_EXE_PATH", "").strip(),
    }

    # Kiem tra cau hinh bat buoc
    missing = []
    if not config["sheet_id"]:
        missing.append("SHEET_ID")
    if not config["user_name"]:
        missing.append("USER_NAME")
    if not config["mobile_ua"]:
        missing.append("MOBILE_USER_AGENT")
    
    if missing:
        print(f"[ERROR] Thieu cau hinh trong .env: {', '.join(missing)}")
        print("Vui long copy .env.example thanh .env va dien day du thong tin.")
        sys.exit(1)
    
    if not os.path.exists(config["credentials_file"]):
        print(f"[ERROR] Khong tim thay file credentials: {config['credentials_file']}")
        print("Vui long dat file Google Service Account credentials JSON vao thu muc goc.")
        sys.exit(1)
    
    return config


def is_cdp_port_open(cdp_url: str) -> bool:
    """Kiem tra xem trinh duyet co dang chay va lang nghe tren cong CDP khong."""
    try:
        url = cdp_url.rstrip("/") + "/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def kill_edge() -> None:
    """
    Tat tat ca tien trinh Edge dang chay ngam.
    Goi truoc khi mo Edge moi de tranh xung dot cong CDP.
    """
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "msedge.exe"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[BROWSER] Da tat cac tien trinh Edge cu.")
            time.sleep(1)
        # returncode 128 = khong tim thay process (Edge chua chay) -> bo qua
    except Exception as e:
        print(f"[BROWSER] Khong the tat Edge: {e}")


def ensure_edge_running(config: dict) -> None:
    """
    Kill Edge cu, sau do mo Edge moi voi cong CDP da cau hinh.
    Luon kill truoc de dam bao khong co tien trinh ngam nao can duong.
    """
    cdp_url = config["cdp_url"]

    kill_edge()

    print(f"[BROWSER] Dang mo Edge moi tren {cdp_url}...")

    edge_exe = config["edge_executable"]
    if not edge_exe or not os.path.exists(edge_exe):
        print(f"[ERROR] Khong tim thay Edge: '{edge_exe}'")
        print("Vui long kiem tra EDGE_EXECUTABLE_PATH trong .env")
        sys.exit(1)

    cmd = [
        edge_exe,
        f"--remote-debugging-port={config['edge_port']}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if config["edge_user_data_dir"]:
        cmd.append(f"--user-data-dir={config['edge_user_data_dir']}")
    if config["edge_profile_directory"]:
        cmd.append(f"--profile-directory={config['edge_profile_directory']}")

    subprocess.Popen(cmd)

    print("[BROWSER] Dang cho Edge khoi dong", end="", flush=True)
    for _ in range(15):
        time.sleep(1)
        print(".", end="", flush=True)
        if is_cdp_port_open(cdp_url):
            print(" San sang!")
            return

    print()
    print(f"[ERROR] Edge khong phan hoi sau 15 giay. Kiem tra lai cau hinh.")
    sys.exit(1)


def get_log_path(target_date: date | None = None) -> Path:
    """Tra ve duong dan file log theo ngay."""
    if target_date is None:
        target_date = date.today()
    return LOG_DIR / f"{target_date.isoformat()}.json"


def init_daily_log(tasks_by_hour: dict) -> None:
    """Tao file log moi cho ngay hom nay, tat ca task duoc danh dau 'pending'."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = get_log_path()

    tasks = []
    for hour, task_list in sorted(tasks_by_hour.items()):
        for task in task_list:
            tasks.append({
                "hour": task.hour,
                "keyword": task.keyword,
                "device_type": task.device_type,
                "should_click": task.should_click,
                "status": "pending",
                "timestamp": None,
                "wifi_ssid": None,
            })

    data = {"date": date.today().isoformat(), "tasks": tasks}

    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[LOG] Da tao file log: {log_file} ({len(tasks)} tasks)")


def load_tasks_from_log() -> dict[int, list[SearchTask]]:
    """
    Doc tasks tu file log cua ngay hom nay.
    Chi tra ve cac task chua chay thanh cong (pending / failed).
    """
    log_file = get_log_path()
    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks_by_hour: dict[int, list[SearchTask]] = {}
    skipped = 0

    for t in data.get("tasks", []):
        if t["status"] == "success":
            skipped += 1
            continue
        hour = t["hour"]
        task = SearchTask(
            keyword=t["keyword"],
            device_type=t["device_type"],
            should_click=t["should_click"],
            hour=hour,
            column_index=0,
        )
        tasks_by_hour.setdefault(hour, []).append(task)

    total_remaining = sum(len(v) for v in tasks_by_hour.values())
    if skipped:
        print(f"[LOG] Bo qua {skipped} task da hoan thanh truoc do.")
    print(f"[LOG] Con lai {total_remaining} task chua chay.")
    return tasks_by_hour


def update_daily_log(results: list[dict]) -> None:
    """Cap nhat trang thai cac task trong file log sau khi chay xong."""
    if not results:
        return

    log_file = get_log_path()
    if not log_file.exists():
        return

    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    result_map = {
        (r["hour"], r["keyword"], r["device_type"]): r
        for r in results
    }

    for task in data.get("tasks", []):
        key = (task["hour"], task["keyword"], task["device_type"])
        if key in result_map:
            r = result_map[key]
            task["status"] = r["status"]
            task["timestamp"] = r["timestamp"]
            task["wifi_ssid"] = r.get("wifi_ssid")

    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    success = sum(1 for r in results if r["status"] == "success")
    failed = len(results) - success
    print(f"[LOG] Cap nhat log: {log_file} (OK: {success}, FAIL: {failed})")


_ETHERNET_MARKER = "ethernet"


def load_last_run_wifi_from_log() -> str | None:
    """
    Doc mang da dung cho nhom task gan nhat tu file log hom nay.
    Tra ve SSID WiFi, '_ETHERNET_MARKER', hoac None neu chua co lich su.
    """
    log_file = get_log_path()
    if not log_file.exists():
        return None

    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    last_wifi = None
    last_ts = None
    for t in data.get("tasks", []):
        if t.get("status") == "success" and t.get("wifi_ssid") and t.get("timestamp"):
            ts = datetime.fromisoformat(t["timestamp"])
            if last_ts is None or ts > last_ts:
                last_ts = ts
                last_wifi = t["wifi_ssid"]

    return last_wifi


def load_last_wifi_ssid_from_log() -> str | None:
    """
    Doc WiFi SSID thuc su da dung lan cuoi (bo qua cac lan dung ethernet).
    Dung de xoay vong WiFi trong che do ethernet-wifi.
    """
    log_file = get_log_path()
    if not log_file.exists():
        return None

    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    last_wifi = None
    last_ts = None
    for t in data.get("tasks", []):
        ssid = t.get("wifi_ssid", "")
        if (t.get("status") == "success"
                and ssid
                and ssid != _ETHERNET_MARKER
                and t.get("timestamp")):
            ts = datetime.fromisoformat(t["timestamp"])
            if last_ts is None or ts > last_ts:
                last_ts = ts
                last_wifi = ssid

    return last_wifi


def get_last_completion_time() -> datetime | None:
    """Doc log, tra ve thoi diem hoan thanh task gan nhat (timestamp moi nhat trong cac task success)."""
    log_file = get_log_path()
    if not log_file.exists():
        return None

    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    timestamps = [
        datetime.fromisoformat(t["timestamp"])
        for t in data.get("tasks", [])
        if t.get("status") == "success" and t.get("timestamp")
    ]
    return max(timestamps) if timestamps else None


def run_hour_tasks(config: dict, tasks_by_hour: dict, hour: int):
    """Chay cac task cho mot nhom (khung gio cu the)."""
    if hour not in tasks_by_hour:
        print(f"[INFO] Khong co task nao cho nhom {hour}:00")
        return

    tasks = tasks_by_hour[hour]
    print(f"\n{'='*60}")
    print(f"  NHOM {hour}:00 - {len(tasks)} task(s)")
    print(f"{'='*60}")

    for t in tasks:
        click_str = "CLICK" if t.should_click else "NO CLICK"
        print(f"  - [{t.device_type}] [{click_str}] {t.keyword}")

    wifi_1 = config.get("wifi_1", "")
    wifi_2 = config.get("wifi_2", "")
    network_mode = config.get("network_mode", "wifi")
    ethernet_adapter = config.get("ethernet_adapter", "")

    last_network = load_last_run_wifi_from_log()
    disabled_adapters: list[str] = []
    session_wifi: str | None = None

    if network_mode == "ethernet-wifi" and wifi_1 and wifi_2:
        # Xoay vong: ethernet -> wifi -> ethernet -> wifi ...
        # last_network la "ethernet", WiFi SSID, hoac None (lan dau)
        print(f"\n[NETWORK] Che do ethernet-wifi | WiFi: '{wifi_1}' / '{wifi_2}'")
        if last_network:
            print(f"[NETWORK] Nhom truoc da dung: '{last_network}'")

        if last_network == _ETHERNET_MARKER:
            # Lan truoc dung ethernet -> lan nay dung WiFi
            last_wifi_ssid = load_last_wifi_ssid_from_log()
            from wifi_manager import get_next_wifi, switch_to_wifi
            target_wifi = get_next_wifi(last_wifi_ssid, wifi_1, wifi_2)
            print(f"[NETWORK] Nhom nay: WiFi ('{target_wifi}')")

            ensure_edge_running(config)

            from wifi_manager import disable_ethernet
            disabled_adapters = disable_ethernet(ethernet_adapter)
            if disabled_adapters:
                print(f"[NETWORK] Da tat ethernet: {disabled_adapters}")
            else:
                print("[NETWORK] Canh bao: khong tim thay ethernet adapter nao de tat.")

            switched = switch_to_wifi(target_wifi)
            if switched:
                session_wifi = target_wifi
            else:
                from wifi_manager import get_current_wifi
                session_wifi = get_current_wifi() or target_wifi
                print(f"[NETWORK] Canh bao: khong doi duoc WiFi, dang dung: '{session_wifi}'")
        else:
            # Lan truoc dung WiFi (hoac lan dau) -> lan nay dung Ethernet
            print(f"[NETWORK] Nhom nay: Ethernet")
            session_wifi = _ETHERNET_MARKER
            ensure_edge_running(config)

    elif wifi_1 and wifi_2:
        # wifi mode: chi xoay vong WiFi, ethernet khong bi anh huong
        print(f"\n[WIFI] Che do doi WiFi: '{wifi_1}' <-> '{wifi_2}'")
        if last_network:
            print(f"[WIFI] Nhom truoc da dung: '{last_network}'")
        from wifi_manager import ensure_different_from_last
        session_wifi = ensure_different_from_last(last_network, wifi_1, wifi_2)
        print(f"[WIFI] Nhom nay se dung: '{session_wifi}'")
        ensure_edge_running(config)

    else:
        print("\n[WIFI] Khong cau hinh WiFi rotation (WIFI_1_SSID/WIFI_2_SSID trong .env)")
        ensure_edge_running(config)

    try:
        results = asyncio.run(execute_tasks(
            cdp_url=config["cdp_url"],
            tasks=tasks,
            mobile_ua=config["mobile_ua"],
            delay_between=5.0,
            session_wifi=session_wifi,
        ))
    finally:
        # Luon bat lai ethernet sau khi dung WiFi de search
        if disabled_adapters:
            from wifi_manager import enable_ethernet
            print(f"\n[NETWORK] Bat lai ethernet: {disabled_adapters}")
            enable_ethernet(disabled_adapters)

    update_daily_log(results)
    print(f"\n[OK] Hoan thanh tat ca task cho khung gio {hour}:00")

    # Gui thong bao Teams (neu da cau hinh)
    teams_chat = config.get("teams_chat_name", "")
    if teams_chat:
        notify_hour_complete(
            hour=hour,
            results=results,
            chat_name=teams_chat,
            teams_exe=config.get("teams_exe", ""),
        )


def run_scheduled(config: dict, tasks_by_hour: dict):
    """
    Chay tuan tu cac nhom task, moi nhom cach nhau 1 tieng tinh tu lan hoan thanh truoc.

    Neu chuong trinh bi ngat va khoi dong lai, doc log de biet lan cuoi chay luc nao
    va tinh thoi gian cho den luot tiep theo.
    """
    groups = sorted(tasks_by_hour.keys())  # danh sach hour con pending, theo thu tu
    total_tasks = sum(len(tasks_by_hour[h]) for h in groups)
    print(f"[INFO] {len(groups)} nhom can chay, {total_tasks} task con lai.")

    for i, hour in enumerate(groups):
        # Kiem tra thoi diem co the chay nhom nay
        last_done = get_last_completion_time()

        if last_done is not None:
            next_run_at = last_done + timedelta(hours=1)
            now = datetime.now()
            wait_secs = (next_run_at - now).total_seconds()

            if wait_secs > 0:
                wait_mins = int(wait_secs // 60)
                wait_secs_rem = int(wait_secs % 60)
                remaining_groups = groups[i:]
                print(f"\n[INFO] Lan cuoi hoan thanh luc {last_done.strftime('%H:%M:%S')}.")
                print(f"[INFO] Cho {wait_mins} phut {wait_secs_rem} giay den {next_run_at.strftime('%H:%M:%S')}...")
                print(f"[INFO] Cac nhom con lai: {len(remaining_groups)} nhom")
                time.sleep(wait_secs)

        run_hour_tasks(config, tasks_by_hour, hour)

    print(f"\n{'='*60}")
    print("  DA HOAN THANH TAT CA TASK TRONG NGAY!")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Yahoo Japan Search Tool - Tu dong search tu Google Sheet"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Chay tat ca cac nhom lien tuc, khong doi giua cac nhom"
    )
    parser.add_argument(
        "--test-sheet", action="store_true",
        help="Chi doc sheet va hien thi du lieu, khong search"
    )
    parser.add_argument(
        "--test-teams", action="store_true",
        help="Gui tin nhan test vao Teams va thoat"
    )
    parser.add_argument(
        "--test-wifi", action="store_true",
        help="Hien thi WiFi hien tai, doi sang WiFi con lai va thoat"
    )

    args = parser.parse_args()
    config = load_config()

    print(f"[INFO] User: {config['user_name']}")
    print(f"[INFO] Sheet ID: {config['sheet_id'][:20]}...")
    print(f"[INFO] CDP URL: {config['cdp_url']}")
    print(f"[INFO] Ngay: {date.today().strftime('%d/%m/%Y')}")

    # Che do test WiFi
    if args.test_wifi:
        wifi_1 = config.get("wifi_1", "")
        wifi_2 = config.get("wifi_2", "")
        if not wifi_1 or not wifi_2:
            print("[ERROR] Chua dat WIFI_1_SSID / WIFI_2_SSID trong .env")
            return
        from wifi_manager import get_current_wifi, get_next_wifi, switch_to_wifi
        current = get_current_wifi()
        print(f"[WIFI] Hien tai : '{current}'")
        target = get_next_wifi(current, wifi_1, wifi_2)
        print(f"[WIFI] Se doi sang: '{target}'")
        ok = switch_to_wifi(target)
        after = get_current_wifi()
        print(f"[WIFI] Ket qua : {'Thanh cong' if ok else 'That bai'} (hien tai: '{after}')")
        return

    # Che do test Teams
    if args.test_teams:
        teams_chat = config.get("teams_chat_name", "")
        if not teams_chat:
            print("[ERROR] Chua dat TEAMS_CHAT_NAME trong .env")
            return
        print(f"[INFO] Dang gui tin nhan test vao Teams: '{teams_chat}'")
        from teams_notifier import send_message
        ok = send_message(
            chat_name=teams_chat,
            message="[TEST] Yahoo Search Tool - Teams notification hoat dong!",
            teams_exe=config.get("teams_exe", ""),
        )
        print(f"[INFO] Ket qua: {'Thanh cong' if ok else 'That bai'}")
        return

    # Kiem tra file log ngay hom nay
    log_file = get_log_path()
    if log_file.exists():
        print(f"\n[LOG] Tim thay file log hom nay: {log_file}")
        print("[LOG] Su dung du lieu tu log, khong can doc lai sheet.")
        tasks_by_hour = load_tasks_from_log()
    else:
        print("\n[INFO] Chua co file log hom nay. Doc du lieu tu Google Sheet...")
        tasks_by_hour = get_search_tasks(
            credentials_file=config["credentials_file"],
            sheet_id=config["sheet_id"],
            sheet_name=config["sheet_name"],
            user_name=config["user_name"],
            debug=args.test_sheet,
        )
        if tasks_by_hour:
            init_daily_log(tasks_by_hour)

    if not tasks_by_hour:
        print("[INFO] Khong co task nao cho hom nay. Thoat.")
        return
    
    # Hien thi tong quan
    print(f"\n{'='*60}")
    print(f"  TONG QUAN TASK TRONG NGAY")
    print(f"{'='*60}")
    for hour in sorted(tasks_by_hour.keys()):
        tasks = tasks_by_hour[hour]
        print(f"\n  [{hour}:00] - {len(tasks)} task(s):")
        for t in tasks:
            click_str = "CLICK" if t.should_click else "NO CLICK"
            print(f"    - [{t.device_type}] [{click_str}] {t.keyword}")
    
    total = sum(len(t) for t in tasks_by_hour.values())
    print(f"\n  Tong: {total} task(s) trong {len(tasks_by_hour)} khung gio")
    print(f"{'='*60}")
    
    if args.test_sheet:
        print("\n[INFO] Che do test-sheet: chi hien thi du lieu, khong search.")
        return

    if args.all:
        # Chay tat ca nhom lien tuc, khong cho
        for hour in sorted(tasks_by_hour.keys()):
            run_hour_tasks(config, tasks_by_hour, hour)
        print(f"\n{'='*60}")
        print("  DA HOAN THANH TAT CA TASK!")
        print(f"{'='*60}")
    else:
        # Che do mac dinh: chay tuan tu, moi nhom cach nhau 1 tieng
        run_scheduled(config, tasks_by_hour)


if __name__ == "__main__":
    main()
