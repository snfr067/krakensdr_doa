# udp_radar_web.py
import socket
import threading
import queue
import json
from flask import Flask, Response, make_response

HOST_UDP = "127.0.0.1"
PORT_UDP = 2222

HOST_HTTP = "0.0.0.0"
PORT_HTTP = 6688

app = Flask(__name__)

# === 簡單的訂閱者佇列管理（每個瀏覽器連線一個 Queue） ===
_subscribers = set()
_sub_lock = threading.Lock()

def _broadcast(msg: dict):
    dead = []
    with _sub_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _subscribers.discard(q)

# === UDP 監聽執行緒 ===
def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST_UDP, PORT_UDP))
    print(f"[UDP] Listening on {HOST_UDP}:{PORT_UDP}")
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            print(data)
        except Exception:
            continue
        msg = data.decode("utf-8", errors="ignore")
        # 一個 datagram 可能含多行，逐行解析
        for line in msg.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            time_str = parts[0].strip()
            try:
                doa = float(parts[1].strip())
            except ValueError:
                continue

            _broadcast({"ts": time_str, "doa": doa})

# === HTTP: 首頁（內嵌 HTML/CSS/JS） ===
INDEX_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOA Radar (UDP → Web)</title>
<style>
  :root { --bg: #0f1216; --fg: #e8eef5; --muted: #94a3b8; --line: #2b3340; }
  body { margin: 0; background: var(--bg); color: var(--fg); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Arial, "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif; }
  .wrap { max-width: 760px; margin: 32px auto; padding: 0 16px; text-align: center; }
  .title { font-size: 20px; margin-bottom: 8px; color: var(--muted); letter-spacing: .5px; }
  .readout { font-size: 22px; margin: 8px 0 18px; font-weight: 600; }
  .card { background: #141a22; border: 1px solid var(--line); border-radius: 16px; padding: 18px 12px 26px; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
  canvas { display: block; margin: 0 auto; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="title">DOA Radar</div>
    <div id="readout" class="readout">等待資料…</div>
    <div class="card">
      <canvas id="radar" width="600" height="620"></canvas>
    </div>
  </div>

<script>
(function(){
  const canvas = document.getElementById('radar');
  const ctx = canvas.getContext('2d');

  const CANVAS_W = canvas.width;
  const CANVAS_H = canvas.height;
  const CX = CANVAS_W / 2;
  const CY = CANVAS_H / 2 + 20;     // 下移，避免上方文字擠壓
  const R  = 250;

  const readout = document.getElementById('readout');

  function polarToXY(deg, r){
    // 定義：0°在正上方，順時針遞增
    const theta = (Math.PI/180) * (90 - (deg % 360));
    const x = CX + r * Math.cos(theta);
    const y = CY - r * Math.sin(theta);
    return {x, y};
  }

  function drawBackground(){
    ctx.clearRect(0,0,CANVAS_W,CANVAS_H);

    // 圓盤背景
    ctx.save();
    // 外圈
    ctx.beginPath();
    ctx.arc(CX, CY, R, 0, Math.PI*2);
    ctx.strokeStyle = '#3b4758';
    ctx.lineWidth = 2;
    ctx.stroke();

    // 同心圓
    const rings = [0.25, 0.5, 0.75];
    ctx.setLineDash([4,6]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = '#2b3340';
    rings.forEach(fr=>{
      ctx.beginPath();
      ctx.arc(CX, CY, R*fr, 0, Math.PI*2);
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // 十字刻度
    const ticks = [0, 90, 180, 270];
    ctx.setLineDash([6,8]);
    ticks.forEach(d=>{
      const {x, y} = polarToXY(d, R);
      ctx.beginPath();
      ctx.moveTo(CX, CY);
      ctx.lineTo(x, y);
      ctx.strokeStyle = '#253041';
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // 刻度文字
    ctx.fillStyle = '#8aa0b8';
    ctx.font = '12px sans-serif';
    ticks.forEach(d=>{
      const t = polarToXY(d, R + 18);
      ctx.fillText(d + '°', t.x - 10, t.y + 4);
    });

    // N/E/S/W
    ctx.fillStyle = '#c2d1e6';
    ctx.font = 'bold 13px sans-serif';
    [['N',0],['E',90],['S',180],['W',270]].forEach(([label, d])=>{
      const t = polarToXY(d, R + 34);
      ctx.fillText(label, t.x - 6, t.y + 5);
    });

    ctx.restore();
  }

  function drawPointer(deg){
    // 指針
    const tip = polarToXY(deg, R - 10);
    ctx.save();
    ctx.strokeStyle = '#6fb8ff';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(CX, CY);
    ctx.lineTo(tip.x, tip.y);
    ctx.stroke();

    // 中心點
    ctx.beginPath();
    ctx.arc(CX, CY, 5, 0, Math.PI*2);
    ctx.fillStyle = '#6fb8ff';
    ctx.fill();
    ctx.restore();
  }

  function render(deg){
    drawBackground();
    drawPointer(deg);
  }

  render(0); // 初始渲染

  // 以 SSE 連線 /events，接收形如 {ts: "...", doa: 123.45}
  const es = new EventSource('/events');
  es.onmessage = (ev)=>{
    if(!ev.data) return;
    try{
      const obj = JSON.parse(ev.data);
      if(obj && typeof obj.doa === 'number'){
        const deg = ((obj.doa % 360) + 360) % 360;
        render(deg);
        readout.textContent = `${obj.ts}  角度: ${deg.toFixed(2)}°`;
      }
    }catch(e){}
  };
  es.onerror = ()=>{ /* 保持沉默，瀏覽器會自動重連 */ };

})();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    resp = make_response(INDEX_HTML)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/events")
def sse_stream():
    q = queue.Queue()
    with _sub_lock:
        _subscribers.add(q)

    def gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=15)  # 有資料就送
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # keep-alive，避免連線被中間節點關閉
                    yield "data: {}\n\n"
        finally:
            with _sub_lock:
                _subscribers.discard(q)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(gen(), headers=headers)

def main():
    t = threading.Thread(target=udp_listener, daemon=True)
    t.start()
    print(f"[HTTP] Serving on http://{HOST_HTTP}:{PORT_HTTP}")
    app.run(host=HOST_HTTP, port=PORT_HTTP, debug=False, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
