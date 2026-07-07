"""
main.py - Chuong trinh chinh: doc sheet, lap lich va thuc hien search Yahoo Japan.

Su dung:
    python main.py               # Chay tuan tu, moi nhom search cach nhau ngau nhien 50-70 phut (tu dong tiep tuc khi restart)
    python main.py --all         # Chay tat ca cac nhom lien tuc (khong doi)
    python main.py --test-sheet  # Chi hien thi du lieu tu sheet, khong search
    python main.py --test-teams  # Gui tin nhan test vao Teams va thoat
    python main.py --test-wifi   # Hien thi WiFi hien tai, doi sang WiFi con lai va thoat
    python main.py --test-network # Test day du: phat hien ethernet, tat/bat + doi WiFi (ethernet-wifi mode)
"""

import os
import sys
import json
import random
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
GROUP_GAP_MINUTES = [50, 55, 60, 65, 70]


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
        # Mac dinh BAT: clone profile tu thu muc mac dinh (hoac EDGE_SOURCE_USER_DATA_DIR)
        # sang mot thu muc rieng o lan dau, de giu session/cookie ma van tranh
        # bi Edge >= 136 chan remote debugging tren thu muc mac dinh.
        # Dat EDGE_CLONE_FROM_DEFAULT=false trong .env neu muon tat.
        "edge_clone_from_default": os.getenv("EDGE_CLONE_FROM_DEFAULT", "true").strip().lower() in ("1", "true", "yes"),
        "edge_source_user_data_dir": os.getenv("EDGE_SOURCE_USER_DATA_DIR", "").strip(),
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

    _normalize_profile_config(config)

    return config


def get_default_clone_target_dir() -> str:
    """Thu muc clone rieng mac dinh (khong phai thu muc mac dinh cua Edge)."""
    local = os.getenv("LOCALAPPDATA", "")
    base = local if local else str(Path.home())
    return os.path.join(base, "EdgeAutomation", "User Data")


def _normalize_profile_config(config: dict) -> None:
    """
    Khi bat clone (mac dinh), tu dong tach NGUON va DICH cho profile:
      - Nguon (source)  = profile that dang co session (thu muc mac dinh cua Edge,
                          hoac EDGE_USER_DATA_DIR neu no tro vao mac dinh,
                          hoac EDGE_SOURCE_USER_DATA_DIR neu duoc chi dinh).
      - Dich (target)   = thu muc clone RIENG (khong mac dinh) de Edge >= 136 khong chan.

    Nho vay nguoi dung khong can sua .env: cu de EDGE_USER_DATA_DIR nhu cu (tro vao
    thu muc mac dinh), tool se tu clone sang thu muc rieng va chay o do.
    """
    if not config.get("edge_clone_from_default"):
        return

    configured = config.get("edge_user_data_dir", "")
    source = config.get("edge_source_user_data_dir", "")

    # Xac dinh nguon: uu tien EDGE_SOURCE_USER_DATA_DIR, roi den configured neu no
    # la thu muc mac dinh, cuoi cung la thu muc mac dinh cua Edge.
    if not source:
        if configured and points_to_default_edge_profile(configured):
            source = configured
        else:
            source = get_default_edge_user_data_dir()

    # Xac dinh dich: neu configured da la thu muc rieng (khong mac dinh) thi giu nguyen,
    # nguoc lai dung thu muc clone rieng mac dinh.
    if configured and not points_to_default_edge_profile(configured):
        target = configured
    else:
        target = get_default_clone_target_dir()

    config["edge_source_user_data_dir"] = source
    config["edge_user_data_dir"] = target

    if points_to_default_edge_profile(target):
        # Truong hop hiem (khong xac dinh duoc thu muc rieng) -> tat clone de tranh vong lap
        print("[CLONE] Khong the chon thu muc clone rieng, tat clone.")
        config["edge_clone_from_default"] = False


def get_cdp_version(cdp_url: str) -> dict | None:
    """
    Doc thong tin phien ban tu endpoint CDP (/json/version).
    Tra ve dict (co 'Browser', 'User-Agent', ...) neu ket noi duoc, None neu khong.
    """
    try:
        url = cdp_url.rstrip("/") + "/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def is_cdp_port_open(cdp_url: str) -> bool:
    """Kiem tra xem trinh duyet co dang chay va lang nghe tren cong CDP khong."""
    return get_cdp_version(cdp_url) is not None


def get_default_edge_user_data_dir() -> str:
    """Duong dan thu muc User Data mac dinh cua Edge tren Windows."""
    local = os.getenv("LOCALAPPDATA", "")
    if not local:
        return ""
    return os.path.join(local, "Microsoft", "Edge", "User Data")


def points_to_default_edge_profile(user_data_dir: str) -> bool:
    """
    True neu user_data_dir chinh la thu muc User Data mac dinh cua Edge.

    Tu Edge/Chrome 136, --remote-debugging-port bi BO QUA khi chay tren thu muc
    profile mac dinh (bao mat chong trom cookie). Do la ly do cong debug khong mo
    tren cac may da cap nhat Edge moi.
    """
    default = get_default_edge_user_data_dir()
    if not user_data_dir or not default:
        return False
    try:
        return os.path.normcase(os.path.normpath(user_data_dir)) == \
            os.path.normcase(os.path.normpath(default))
    except Exception:
        return False


def count_edge_processes() -> int:
    """Dem so tien trinh msedge.exe dang chay."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/NH"],
            capture_output=True,
            text=True,
        )
        return result.stdout.lower().count("msedge.exe")
    except Exception:
        return 0


def kill_edge() -> None:
    """
    Tat tat ca tien trinh Edge dang chay ngam (ca cay tien trinh con) va
    CHO cho den khi chung that su bien mat.

    Quan trong: neu con bat ky tien trinh msedge.exe nao con giu user-data-dir,
    lenh mo Edge moi voi --remote-debugging-port se chi mo them 1 tab trong
    instance cu roi thoat -> cong debug KHONG duoc mo -> ket noi CDP that bai.
    Vi vay phai dam bao Edge tat han truoc khi mo lai.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "msedge.exe"],
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"[BROWSER] Khong the tat Edge: {e}")
        return

    # Cho toi da 10s cho tien trinh Edge tat han (poll moi 0.5s).
    for _ in range(20):
        if count_edge_processes() == 0:
            print("[BROWSER] Da tat het tien trinh Edge cu.")
            return
        time.sleep(0.5)
    print("[BROWSER] Canh bao: van con tien trinh Edge sau khi taskkill.")


def clone_edge_profile(source_user_data_dir: str, target_user_data_dir: str,
                       profile_dir: str) -> bool:
    """
    Clone profile Edge tu thu muc nguon (thuong la thu muc mac dinh) sang thu muc
    rieng de van giu session/cookie ma tranh bi Edge >= 136 chan remote debugging.

    Copy:
      - <source>/Local State   (chua khoa ma hoa cookie - BAT BUOC de giai ma cookie)
      - <source>/<profile_dir> (cookie, dang nhap, ...), bo qua cache cho nhe.

    Luu y: cookie duoc ma hoa bang DPAPI + App-Bound Encryption, gan voi TAI KHOAN
    Windows + ung dung Edge, KHONG gan voi duong dan. Nen clone chi giai ma duoc khi
    chay cung mot user Windows tren cung may. Copy sang may/user khac se mat session.
    Edge phai DA DONG khi copy (file bi khoa neu Edge dang chay).

    Tra ve True neu clone thanh cong (hoac da co san), False neu that bai.
    """
    import shutil

    src_root = Path(source_user_data_dir)
    dst_root = Path(target_user_data_dir)
    src_profile = src_root / profile_dir
    dst_profile = dst_root / profile_dir

    if not src_profile.exists():
        print(f"[CLONE] Khong tim thay profile nguon: {src_profile}")
        return False

    # Cac thu muc cache khong can copy (nang, tu tao lai)
    ignore_names = {
        "Cache", "Code Cache", "GPUCache", "GraphiteDawnCache", "DawnCache",
        "DawnGraphiteCache", "DawnWebGPUCache", "Service Worker", "CacheStorage",
        "Application Cache", "ShaderCache", "GrShaderCache", "component_crx_cache",
    }
    ignore = shutil.ignore_patterns(*ignore_names)

    try:
        dst_root.mkdir(parents=True, exist_ok=True)

        # 1. Local State (chua khoa ma hoa) - bat buoc
        src_local_state = src_root / "Local State"
        if src_local_state.exists():
            shutil.copy2(src_local_state, dst_root / "Local State")
            print("[CLONE] Da copy Local State (khoa ma hoa cookie).")
        else:
            print("[CLONE] Canh bao: khong thay Local State o nguon, cookie co the khong giai ma duoc.")

        # 2. Thu muc profile
        print(f"[CLONE] Dang copy profile '{profile_dir}' (bo qua cache)...")
        if dst_profile.exists():
            shutil.rmtree(dst_profile, ignore_errors=True)
        shutil.copytree(src_profile, dst_profile, ignore=ignore, dirs_exist_ok=True)

        print(f"[CLONE] Da clone profile sang: {dst_root}")
        return True
    except Exception as e:
        print(f"[CLONE] Loi khi clone profile: {e}")
        print("[CLONE] Dam bao Edge da dong hoan toan truoc khi clone.")
        return False


def maybe_clone_profile(config: dict) -> None:
    """
    Neu bat EDGE_CLONE_FROM_DEFAULT va thu muc dich chua co profile, thi clone tu
    thu muc nguon (mac dinh Edge hoac EDGE_SOURCE_USER_DATA_DIR) sang.
    Chi clone 1 lan (khi thu muc dich chua ton tai profile).
    """
    if not config.get("edge_clone_from_default"):
        return

    target = config["edge_user_data_dir"]
    profile_dir = config["edge_profile_directory"] or "Default"

    if not target:
        print("[CLONE] Bo qua clone: chua dat EDGE_USER_DATA_DIR (thu muc dich).")
        return

    if points_to_default_edge_profile(target):
        print("[CLONE] Bo qua clone: EDGE_USER_DATA_DIR van la thu muc MAC DINH "
              "(clone khong co tac dung, van bi chan). Hay doi sang thu muc rieng.")
        return

    # Da co profile o dich roi -> khong clone lai (tranh ghi de session moi)
    if (Path(target) / profile_dir).exists():
        return

    source = config.get("edge_source_user_data_dir") or get_default_edge_user_data_dir()
    if not source:
        print("[CLONE] Bo qua clone: khong xac dinh duoc thu muc nguon.")
        return

    print(f"[CLONE] Lan dau: clone profile de giu session.")
    print(f"[CLONE]   Nguon: {source}")
    print(f"[CLONE]   Dich : {target}")

    # Edge phai dong truoc khi copy (file bi khoa neu dang chay)
    kill_edge()
    ok = clone_edge_profile(source, target, profile_dir)
    if not ok:
        print("[CLONE] Clone that bai. Se chay voi profile moi (co the phai dang nhap lai).")


def ensure_edge_running(config: dict) -> None:
    """
    Dam bao Edge san sang cho ket noi CDP.

    - Neu da ket noi duoc (CDP endpoint tra ve OK) thi KHONG kill, dung lai Edge hien tai.
    - Neu khong ket noi duoc thi moi kill Edge cu va mo Edge moi voi cong CDP da cau hinh.
    """
    cdp_url = config["cdp_url"]

    # Neu Edge da dang chay va CDP endpoint dang phan hoi thi dung luon, tranh kill khong can thiet.
    existing = get_cdp_version(cdp_url)
    if existing is not None:
        print(f"[BROWSER] Edge da san sang (CDP OK): {cdp_url}")
        print(f"[BROWSER] Phien ban: {existing.get('Browser', '?')}")
        return

    edge_exe = config["edge_executable"]
    if not edge_exe or not os.path.exists(edge_exe):
        print(f"[ERROR] Khong tim thay Edge: '{edge_exe}'")
        print("Vui long kiem tra EDGE_EXECUTABLE_PATH trong .env")
        sys.exit(1)

    # Neu bat clone: lan dau se copy profile mac dinh sang thu muc rieng de giu session.
    maybe_clone_profile(config)

    user_data_dir = config["edge_user_data_dir"]

    # Canh bao truoc: Edge/Chrome >= 136 BO QUA --remote-debugging-port khi chay
    # tren thu muc profile mac dinh. Day la nguyen nhan "may duoc may khong".
    using_default_dir = points_to_default_edge_profile(user_data_dir)
    if using_default_dir:
        print("[BROWSER] *** CANH BAO ***")
        print(f"[BROWSER] EDGE_USER_DATA_DIR dang tro vao thu muc MAC DINH cua Edge:")
        print(f"[BROWSER]   {user_data_dir}")
        print("[BROWSER] Tu Edge/Chrome 136, remote debugging BI CHAN tren thu muc mac dinh.")
        print("[BROWSER] Neu may nay da cap nhat Edge moi, cong CDP se KHONG mo duoc.")
        print("[BROWSER] Cach sua: doi EDGE_USER_DATA_DIR sang thu muc rieng (khong mac dinh),")
        print("[BROWSER] vi du: C:\\EdgeAutomation\\UserData")

    cmd = [
        edge_exe,
        f"--remote-debugging-port={config['edge_port']}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if user_data_dir:
        cmd.append(f"--user-data-dir={user_data_dir}")
    if config["edge_profile_directory"]:
        cmd.append(f"--profile-directory={config['edge_profile_directory']}")

    print(f"[BROWSER] Lenh mo Edge: {' '.join(cmd)}")

    # Thu toi da 3 lan: dam bao tat het Edge cu -> mo moi -> cho cong CDP mo.
    # Retry vi doi khi Edge moi bi "forward" vao instance cu con sot lai va thoat
    # ngay, khien cong debug khong mo o lan dau.
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        kill_edge()

        print(f"[BROWSER] Dang mo Edge moi tren {cdp_url} (lan {attempt}/{max_attempts})...")
        proc = subprocess.Popen(cmd)

        print("[BROWSER] Dang cho Edge khoi dong", end="", flush=True)
        version = None
        for _ in range(30):
            time.sleep(1)
            print(".", end="", flush=True)
            version = get_cdp_version(cdp_url)
            if version is not None:
                break
            # Neu tien trinh vua mo da thoat som (bi forward vao instance cu)
            # thi khong can cho het 30s - kill lai va thu lan nua.
            if proc.poll() is not None and count_edge_processes() == 0:
                print(" (Edge thoat som)", end="", flush=True)
                break
        print()

        if version is not None:
            print("[BROWSER] Edge da san sang (CDP OK).")
            print(f"[BROWSER] Phien ban: {version.get('Browser', '?')}")
            return

        # Log ro trang thai de chan doan
        edge_alive = count_edge_processes() > 0
        print(f"[BROWSER] Lan {attempt}/{max_attempts}: cong CDP chua mo "
              f"(tien trinh Edge {'CON chay' if edge_alive else 'DA thoat'}).")
        if edge_alive and using_default_dir:
            # Edge dang chay nhung khong mo cong -> gan nhu chac chan la chan boi 136.
            print("[BROWSER] -> Edge chay nhung khong mo cong debug: dung dau hieu bi chan boi Edge >= 136 "
                  "do dung thu muc profile mac dinh.")

    print(f"[ERROR] Edge khong mo duoc cong CDP sau {max_attempts} lan thu.")
    print("[ERROR] Nguyen nhan thuong gap:")
    if using_default_dir:
        print("  - (KHA NANG CAO) Edge >= 136 chan remote debugging tren thu muc profile MAC DINH.")
        print("    => Doi EDGE_USER_DATA_DIR sang thu muc rieng, vi du C:\\EdgeAutomation\\UserData")
    print("  - Con tien trinh Edge cu giu user-data-dir (tat thu cong roi chay lai).")
    print("  - EDGE_USER_DATA_DIR hoac EDGE_EXECUTABLE_PATH sai trong .env.")
    print("  - Cong debug bi ung dung khac chiem (doi EDGE_REMOTE_DEBUGGING_PORT).")
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
        (r["hour"], r["keyword"], r["device_type"], r["should_click"]): r
        for r in results
    }

    for task in data.get("tasks", []):
        key = (task["hour"], task["keyword"], task["device_type"], task["should_click"])
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
    """Doc log, tra ve thoi diem task gan nhat da chay xong (success hoac failed)."""
    log_file = get_log_path()
    if not log_file.exists():
        return None

    with open(log_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    timestamps = [
        datetime.fromisoformat(t["timestamp"])
        for t in data.get("tasks", [])
        if t.get("timestamp")
    ]
    return max(timestamps) if timestamps else None


def execute_task_batch(config: dict, tasks: list[SearchTask]) -> list[dict]:
    """Chay mot batch task va cap nhat log ngay sau khi xong."""
    if not tasks:
        return []

    hour = tasks[0].hour

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

        # Bat buoc phai co WiFi moi chay - cho vo han, retry moi 5 phut
        while session_wifi is None:
            wait_mins = 5
            print(f"[WIFI] WiFi khong co san. Cho {wait_mins} phut roi thu lai...")
            time.sleep(wait_mins * 60)
            session_wifi = ensure_different_from_last(last_network, wifi_1, wifi_2)

        print(f"[WIFI] Nhom nay se dung: '{session_wifi}'")
        ensure_edge_running(config)

    else:
        print("\n[WIFI] Khong cau hinh WiFi rotation (WIFI_1_SSID/WIFI_2_SSID trong .env)")
        ensure_edge_running(config)

    def _run_tasks() -> list[dict]:
        return asyncio.run(execute_tasks(
            cdp_url=config["cdp_url"],
            tasks=tasks,
            mobile_ua=config["mobile_ua"],
            delay_between=5.0,
            session_wifi=session_wifi,
        ))

    try:
        try:
            results = _run_tasks()
        except Exception as e:
            print(f"[BROWSER] Loi ket noi CDP / chay tasks: {e}")
            print("[BROWSER] Kill Edge va mo lai roi thu lai...")
            kill_edge()
            ensure_edge_running(config)
            try:
                results = _run_tasks()
            except Exception as e2:
                print(f"[BROWSER] Van loi sau retry: {e2}")
                print("[BROWSER] Danh dau tat ca task la failed va tiep tuc.")
                results = [{
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "hour": t.hour,
                    "keyword": t.keyword,
                    "device_type": t.device_type,
                    "should_click": t.should_click,
                    "status": "failed",
                    "wifi_ssid": session_wifi,
                } for t in tasks]
    finally:
        # Luon bat lai ethernet sau khi dung WiFi de search
        if disabled_adapters:
            from wifi_manager import enable_ethernet
            print(f"\n[NETWORK] Bat lai ethernet: {disabled_adapters}")
            enable_ethernet(disabled_adapters)

    update_daily_log(results)
    return results


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

    results = execute_task_batch(config, tasks)
    if not results:
        return

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
    Chay tuan tu cac nhom search, moi nhom cach nhau ngau nhien theo moc
    50/55/60/65/70 phut
    tinh tu lan hoan thanh truoc.

    Neu chuong trinh bi ngat va khoi dong lai, doc log de biet lan cuoi chay luc nao
    va tinh thoi gian cho den luot tiep theo.
    """
    groups = sorted(tasks_by_hour.keys())
    total_tasks = sum(len(tasks_by_hour[h]) for h in groups)
    print(f"[INFO] {len(groups)} nhom can chay, {total_tasks} task con lai.")

    for i, hour in enumerate(groups):
        # Kiem tra thoi diem co the chay nhom nay
        last_done = get_last_completion_time()

        if last_done is not None:
            gap_minutes = random.choice(GROUP_GAP_MINUTES)
            next_run_at = last_done + timedelta(minutes=gap_minutes)
            now = datetime.now()
            wait_secs = (next_run_at - now).total_seconds()

            if wait_secs > 0:
                wait_mins = int(wait_secs // 60)
                wait_secs_rem = int(wait_secs % 60)
                print(f"\n[INFO] Lan cuoi hoan thanh luc {last_done.strftime('%H:%M:%S')}.")
                print(f"[INFO] Khoang cach nhom: {gap_minutes} phut (ngau nhien 50/55/60/65/70).")
                print(f"[INFO] Cho {wait_mins} phut {wait_secs_rem} giay den {next_run_at.strftime('%H:%M:%S')}...")
                print(f"[INFO] Cac nhom con lai: {len(groups) - i}")
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
    parser.add_argument(
        "--test-network", action="store_true",
        help="Test ethernet-wifi mode: phat hien adapter, tat ethernet, doi WiFi, bat lai ethernet"
    )

    args = parser.parse_args()
    config = load_config()

    print(f"[INFO] User: {config['user_name']}")
    print(f"[INFO] Sheet ID: {config['sheet_id'][:20]}...")
    print(f"[INFO] CDP URL: {config['cdp_url']}")
    if config.get("edge_clone_from_default"):
        print(f"[INFO] Clone profile: BAT")
        print(f"[INFO]   Nguon (session): {config.get('edge_source_user_data_dir')}")
        print(f"[INFO]   Dich (chay tren): {config.get('edge_user_data_dir')}")
    else:
        print(f"[INFO] Edge user-data-dir: {config.get('edge_user_data_dir')}")
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

    # Che do test ethernet-wifi
    if args.test_network:
        wifi_1 = config.get("wifi_1", "")
        wifi_2 = config.get("wifi_2", "")
        ethernet_adapter = config.get("ethernet_adapter", "")
        network_mode = config.get("network_mode", "wifi")

        print(f"\n[TEST-NETWORK] NETWORK_MODE = '{network_mode}'")
        print(f"[TEST-NETWORK] WIFI_1_SSID  = '{wifi_1}'")
        print(f"[TEST-NETWORK] WIFI_2_SSID  = '{wifi_2}'")
        print(f"[TEST-NETWORK] ETHERNET_ADAPTER = '{ethernet_adapter or '(tu dong phat hien)'}'")

        from wifi_manager import (
            get_current_wifi,
            get_ethernet_interfaces,
            disable_ethernet,
            enable_ethernet,
            switch_to_wifi,
            get_next_wifi,
        )

        # Buoc 1: Hien thi WiFi hien tai
        current_wifi = get_current_wifi()
        print(f"\n[TEST-NETWORK] WiFi hien tai: '{current_wifi}'")

        # Buoc 2: Phat hien adapter ethernet
        if ethernet_adapter:
            detected = [ethernet_adapter]
            print(f"[TEST-NETWORK] Adapter ethernet (tu .env): {detected}")
        else:
            detected = get_ethernet_interfaces()
            print(f"[TEST-NETWORK] Adapter ethernet (tu dong phat hien): {detected}")

        if not detected:
            print("[TEST-NETWORK] Canh bao: khong tim thay ethernet adapter nao!")
            print("[TEST-NETWORK] Kiem tra lai hoac dat ETHERNET_ADAPTER trong .env")
        else:
            # Buoc 3: Tat ethernet
            print(f"\n[TEST-NETWORK] Buoc 1/3: Tat ethernet {detected}...")
            disabled = disable_ethernet(ethernet_adapter)
            print(f"[TEST-NETWORK] Da tat: {disabled}")

            # Buoc 4: Doi WiFi
            if wifi_1 and wifi_2:
                target_wifi = get_next_wifi(current_wifi, wifi_1, wifi_2)
                print(f"\n[TEST-NETWORK] Buoc 2/3: Doi WiFi sang '{target_wifi}'...")
                ok = switch_to_wifi(target_wifi)
                after_wifi = get_current_wifi()
                print(f"[TEST-NETWORK] Ket qua doi WiFi: {'Thanh cong' if ok else 'That bai'} (hien tai: '{after_wifi}')")
            else:
                print("[TEST-NETWORK] Buoc 2/3: Bo qua doi WiFi (chua dat WIFI_1/WIFI_2 trong .env)")

            # Buoc 5: Bat lai ethernet
            print(f"\n[TEST-NETWORK] Buoc 3/3: Bat lai ethernet {disabled}...")
            enable_ethernet(disabled)
            final_wifi = get_current_wifi()
            print(f"[TEST-NETWORK] Hoan thanh. WiFi sau khi bat lai ethernet: '{final_wifi}'")

        return

    # Che do test Teams
    if args.test_teams:
        teams_chat = config.get("teams_chat_name", "")
        if not teams_chat:
            print("[ERROR] Chua dat TEAMS_CHAT_NAME trong .env")
            return
        print(f"[INFO] [DRY-RUN] Click chat '{teams_chat}' va go tin nhan thu (KHONG GUI)...")
        from teams_notifier import send_message
        ok = send_message(
            chat_name=teams_chat,
            message="[TEST] Yahoo Search Tool - kiem tra click chat (khong gui)",
            teams_exe=config.get("teams_exe", ""),
            dry_run=True,
        )
        print(f"[INFO] Ket qua: {'Da click va go tin nhan thanh cong - kiem tra thu cong xem dung chat chua' if ok else 'That bai'}")
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
        # Che do mac dinh: chay tuan tu, moi nhom search cach nhau ngau nhien 50-70 phut
        run_scheduled(config, tasks_by_hour)


if __name__ == "__main__":
    main()
