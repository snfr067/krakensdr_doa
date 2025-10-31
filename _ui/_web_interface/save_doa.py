# save_doa.py
from datetime import datetime
from pathlib import Path
import threading
import socket

# === 設定區 ===
LOG_DIR = Path("/home/krakenrf/logs")
DEST_HOST = "127.0.0.1"
DEST_PORT = 6677           # UDP 目的地
# =================

_session_start_stamp = None
_log_file = None
_init_lock = threading.Lock()

# 共用一個 UDP socket
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _now_line_ts() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]


def _ensure_logfile():
    global _session_start_stamp, _log_file
    if _log_file is None:
        with _init_lock:
            if _log_file is None:
                _session_start_stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _log_file = LOG_DIR / f"doa_log_{_session_start_stamp}.txt"
                with _log_file.open("a", encoding="utf-8") as f:
                    f.write("time, doa_max_str, result\n")


def _send_udp(line: str):
    data = line.encode("utf-8")
    _udp_sock.sendto(data, (DEST_HOST, DEST_PORT))


def saveDOA(doa, result):
    """
    1) 寫檔 /home/krakenrf/logs/doa_log_*.txt
    2) 用 UDP 丟 "時間, doa" 到 127.0.0.1:6677
    """
    _ensure_logfile()
    ts = _now_line_ts()
    line = f"{ts}, {doa}, {result}"
    send_line = f"{ts}, {doa}"

    # 1) 寫檔
    try:
        with _log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print("[LOG] write error:", e)
        return

    # 2) 丟 UDP
    try:
        _send_udp(send_line)
    except Exception as e:
        print("[UDP] send error:", e)
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(f"{ts}, UDP_SEND_ERROR: {e}\n")
        except Exception:
            pass
