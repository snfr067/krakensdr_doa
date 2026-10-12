# save_doa.py
from datetime import datetime
from pathlib import Path
import threading
import socket

# === 設定區 ===
LOG_DIR = Path("/home/krakenrf/logs")
DEST_HOST = "192.168.50.14"
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
    print(f"send {data} to {DEST_HOST}:{DEST_PORT}")


def saveDOA(doa, result):
    """
    1) 寫檔 /home/krakenrf/logs/doa_log_*.txt
    2) 用 UDP 丟 "時間, doa" 到 127.0.0.1:6677
    """
    _ensure_logfile()
    ts = _now_line_ts()
    line = f"{ts}, {doa}, {result}"
    send_line = f"{ts}, {doa}, H, {result}"

    # 1) 寫檔
    """try:
        with _log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print("[LOG] write error:", e)
        return"""

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


def save2DMUSIC(angleH, angleV):
    print(f"(H,V)={angleH}, {angleV}")
    _ensure_logfile()
    ts = _now_line_ts()
    line = f"{ts}, {angleH}, {angleV}"
    send_line_h = f"{ts}, {angleH}, H"
    send_line_v = f"{ts}, {angleV}, V"
    
    try:
        _send_udp(send_line_h)
        _send_udp(send_line_v)
    except Exception as e:
        print("[UDP] send error:", e)
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(f"{ts}, UDP_SEND_ERROR: {e}\n")
        except Exception:
            pass 


def saveDOA_Multi(doa_multi):
    """
    多發射源 2D MUSIC 輸出。

    送出格式改成 JSON，沿用原本:
      DEST_HOST
      DEST_PORT
      _send_udp()

    doa_multi 預期格式:
      [
          {
              "source_index": 1,
              "azimuth": -40.0,
              "elevation": -34.0,
              "score": 1.0,
              "freq": 849550000
          },
          ...
      ]
    """
    import json

    _ensure_logfile()
    ts = _now_line_ts()

    if doa_multi is None:
        doa_multi = []

    sources = []

    for idx, src in enumerate(doa_multi):
        if not isinstance(src, dict):
            continue

        try:
            source_index = int(src.get("source_index", idx + 1))
        except Exception:
            source_index = idx + 1

        try:
            azimuth = float(src.get("azimuth", 0.0))
        except Exception:
            azimuth = 0.0

        try:
            elevation = float(src.get("elevation", 0.0))
        except Exception:
            elevation = 0.0

        try:
            score = float(src.get("score", 0.0))
        except Exception:
            score = 0.0

        try:
            freq = int(src.get("freq", 0))
        except Exception:
            freq = 0

        item = {
            "source_index": source_index,
            "azimuth": azimuth,
            "elevation": elevation,
            "score": score,
            "freq": freq,
        }

        if "raw_score" in src:
            try:
                item["raw_score"] = float(src.get("raw_score", 0.0))
            except Exception:
                item["raw_score"] = 0.0

        sources.append(item)

    payload = {
        "type": "DOA_MULTI",
        "time": ts,
        "timestamp_ms": int(datetime.now().timestamp() * 1000),
        "count": len(sources),
        "sources": sources,
    }

    send_line = json.dumps(payload, ensure_ascii=False)

    try:
        _send_udp(send_line)
    except Exception as e:
        print("[UDP] send error:", e)
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(f"{ts}, UDP_SEND_ERROR: {e}\n")
        except Exception:
            pass
