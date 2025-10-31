from datetime import datetime
from pathlib import Path
import threading
import asyncio
import ssl
from typing import Optional
import websockets

# === 設定區 ===
LOG_DIR = Path("/home/krakenrf/logs")      # 您的實際路徑
DEST_HOST = "127.0.0.1"
DEST_PORT = 6677
WS_URI = f"wss://{DEST_HOST}:{DEST_PORT}/"   # 一定要 wss，用您的設定

# === 內部狀態 ===
_session_start_stamp = None
_log_file = None
_init_lock = threading.Lock()

# === WebSocket 背景傳送用 ===
_ws_loop = None
_ws_queue = None   


def make_ssl_ctx(url: str, cafile: Optional[str] = None) -> Optional[ssl.SSLContext]:
    if not url.lower().startswith("wss://"):
        return None
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _now_line_ts() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]


def _ensure_logfile():
    """第一次呼叫時決定檔名與路徑"""
    global _session_start_stamp, _log_file
    if _log_file is None:
        with _init_lock:
            if _log_file is None:
                _session_start_stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _log_file = LOG_DIR / f"doa_log_{_session_start_stamp}.txt"
                with _log_file.open("a", encoding="utf-8") as f:
                    f.write("time, doa_max_str, result\n")


async def _ws_sender_main():
    """
    這個函式是在「背景 event loop 那條 thread」裡跑的
    在這裡建 queue，之後 _send() 才能用 call_soon_threadsafe 丟進來
    """
    global _ws_queue
    _ws_queue = asyncio.Queue()
    ssl_ctx = make_ssl_ctx(WS_URI)

    ws = None
    while True:
        msg = await _ws_queue.get()
        while True:
            try:
                if ws is None:
                    ws = await websockets.connect(
                        WS_URI,
                        ssl=ssl_ctx,
                        ping_interval=20,
                        ping_timeout=20,
                    )
                    print(f"[WS] connected -> {WS_URI}")
                await ws.send(msg)
                print(f"[WS] send: {msg}")
                break
            except Exception as e:
                print("[WS] sender error:", e)
                await asyncio.sleep(0.5)
                ws = None


def _ensure_ws_loop():
    """啟動背景 loop，但不在這裡建 asyncio 物件"""
    global _ws_loop
    if _ws_loop is not None:
        return

    loop = asyncio.new_event_loop()

    def _runner(l: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(l)
        # 在「loop」這條 thread 裡面建立 sender task
        l.create_task(_ws_sender_main())
        l.run_forever()

    t = threading.Thread(target=_runner, args=(loop,), daemon=True)
    t.start()

    _ws_loop = loop


def _send(line: str):
    """
    同步界面：把 line 丟給背景的 queue
    注意：queue 其實是在 loop 那邊才建立，所以要 call_soon_threadsafe
    """
    _ensure_ws_loop()
    # 這裡不能直接 _ws_queue.put_nowait(...)，因為 queue 可能還沒建好
    def _put():
        if _ws_queue is not None:
            _ws_queue.put_nowait(line)
        else:
            # queue 還沒建好就晚一點再丟
            # 這裡簡單處理：再排一次
            _ws_loop.call_later(0.1, _put)

    _ws_loop.call_soon_threadsafe(_put)


def saveDOA(doa, result):
    """
    1) 寫檔 /home/krakenrf/logs/doa_log_*.txt
    2) 傳 "時間, doa" 到 wss://127.0.0.1:6677/
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

    # 2) 丟到 WebSocket 背景
    try:
        _send(send_line)
    except Exception as e:
        print("[WS] enqueue error:", e)
        # 寫到檔案方便查
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(f"{ts}, SEND_ERROR: {e}\n")
        except Exception:
            pass
