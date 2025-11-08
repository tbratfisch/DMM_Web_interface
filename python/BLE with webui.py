"""
Enhanced BLE DMM -> Web Dashboard with Modern UI

- Connects to your Bluetooth DMM (bleak)
- Decodes readings (your original logic kept)
- Serves a beautiful modern web UI with live updating via SSE
  * /            -> Enhanced HTML dashboard with widgets & graphs
  * /api/latest  -> latest reading as JSON
  * /stream      -> live Server-Sent Events

Requires: bleak, aiohttp
pip install bleak aiohttp
"""
import asyncio
import json
import logging
import signal
from datetime import datetime

from bleak import BleakClient
from aiohttp import web

# ----------------- Configuration -----------------
TARGET_NAME = "Bluetooth DMM"
TARGET_ADDR_STR = "XX:XX:XX:XX:XX:XX"  # your device's MAC address
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000
POLL_HZ = 3.0  # reads per second
READ_CHAR_HANDLE = 8  # your device's handle as in original script
# -------------------------------------------------

LOG = logging.getLogger("ble_dmm_web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ======= Original decode logic (unchanged) =======

def pre_process(value):
    hex_data = []
    for i in range(0, len(value), 2):
        hex_data.append(int("0x" + value[i] + value[i+1], base=16))

    def hex_to_binary(x):
        return bin(int(x, 16)).lstrip('0b').zfill(8)

    def LSB_TO_MSB(x):
        return x[::-1]

    xorkey = [0x41,0x21,0x73,0x55,0xa2,0xc1,0x32,0x71,0x66,0xaa,0x3b,0xd0,0xe2,0xa8,0x33,0x14,0x20,0x21,0xaa,0xbb]
    fullbinary = ""
    for x in range(len(hex_data)):
        tohex = hex(hex_data[x] ^ xorkey[x])
        tobinary = hex_to_binary(tohex)
        flipped = LSB_TO_MSB(tobinary)
        fullbinary += flipped
    return fullbinary


class type_detecter:
    type_dict = {
        '11000000':'1',
        '01000000':'2',
        '10000000':'3',
        '00100000':'4',
    }

    @classmethod
    def decode(cls, origin_value):
        return pre_process(origin_value)

    @classmethod
    def type(cls, origin_value):
        type_code = ''
        for i in range(16,24,1):
            type_code = type_code + cls.decode(origin_value)[i]
        return cls.type_dict.get(type_code)


class BaseDecoder:
    digit_dict = {
        '1110111':'0','0010010':'1','1011101':'2','1011011':'3','0111010':'4',
        '1101011':'5','1101111':'6','1010010':'7','1111111':'8','1111011':'9',
        '1111110':'A','0000111':'u','0101101':'t','0001111':'o','0100101':'L',
        '1101101':'E','1101100':'F','0001000':'-'
    }

    @classmethod
    def digit(cls, segment, digi):
        signal = segment[3]+segment[2]+segment[7]+segment[6]+segment[1]+segment[5]+segment[4]
        try:
            if digi is not None:
                digi = digi + cls.digit_dict.get(signal, '')
        except Exception:
            digi = digi + ''
        return digi


class decoder_1(BaseDecoder):
    @classmethod
    def decode(cls, origin_value):
        return pre_process(origin_value)

    @classmethod
    def printdigit(cls, prepared):
        digi = ''
        if prepared[28]=='1':
            digi = digi + '-'
        digi = cls.digit(prepared[28:36], digi)
        if prepared[36]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[36:44], digi)
        if prepared[44]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[44:52], digi)
        if prepared[52]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[52:60], digi)
        if digi == None or digi == '':
            digi = '0'
        return digi

    @classmethod
    def printchar(cls, prepared):
        char_function = []
        char_unit = []
        bits_1 = ["∆", "", "BUZ"]
        for i in range(25,28,1):
            if prepared[i]=='1':
                char_function.append(bits_1[i-25])
        bits_2 = ["HOLD","°F","°C","->","MAX","MIN","%","AC",
                  "F","μ","?5","n","Hz","Ω","K","M",
                  "V","m","DC","A","Auto","?7","μ","m",
                  "?8","?9","?10","?11"]
        function = {60,63,64,65,80}
        for i in range(59+len(bits_2),59,-1):
            if i in function:
                if prepared[i]=='1':
                    char_function.append(bits_2[i-60])
            else:
                if prepared[i]=='1':
                    char_unit.append(bits_2[i-60])
        return [char_function, char_unit]


class decoder_2(decoder_1):
    @classmethod
    def printchar(cls, prepared):
        char_function = []
        char_unit = []
        bits_1 = ["HOLD", "Flash", "BUZ"]
        for i in range(25,28,1):
            if prepared[i]=='1':
                char_function.append(bits_1[i-25])
        bits_2 = ["n", "V", "DC", "AC","F", "->","A", "μ",
            "Ω", "k", "m", "M","", "Hz", "°F", "°C"]
        function = {64,69}
        for i in range(63+len(bits_2),63,-1):
            if i in function:
                if prepared[i]=='1':
                    char_function.append(bits_2[i-64])
            else:
                if prepared[i]=='1':
                    char_unit.append(bits_2[i-64])
        return [char_function, char_unit]


class decoder_3(decoder_1):
    pass


class decoder_4(decoder_1):
    pass

# ======= Shared state for web/UI =======

latest = {
    "timestamp": None,
    "value": None,
    "unit": "",
    "functions": "",
    "device_type": None,
    "connected": False,
    "target_name": TARGET_NAME,
    "target_addr": TARGET_ADDR_STR,
}
sse_clients = set()

def make_payload():
    return {
        **latest,
        "timestamp": latest["timestamp"],
        "value": latest["value"],
        "unit": latest["unit"],
        "functions": latest["functions"],
        "connected": latest["connected"],
    }

async def broadcast(payload: dict):
    if not sse_clients:
        return
    data = f"data: {json.dumps(payload)}\n\n"
    dead = []
    for q in sse_clients:
        try:
            await q.put(data)
        except Exception:
            dead.append(q)
    for q in dead:
        sse_clients.discard(q)

# ======= BLE reader task =======

async def ble_reader(stop_event: asyncio.Event):
    address = TARGET_ADDR_STR
    LOG.info("Target name: %s | Target address: %s", TARGET_NAME, address)

    while not stop_event.is_set():
        try:
            async with BleakClient(address) as client:
                latest["connected"] = bool(client.is_connected)
                LOG.info("Connected: %s", client.is_connected)

                try:
                    raw = bytes(await client.read_gatt_char(READ_CHAR_HANDLE, use_cached=1)).hex()
                except Exception as e:
                    LOG.warning("Failed to read initial char: %s", e)
                    latest["connected"] = False
                    await asyncio.sleep(2.0)
                    continue

                dev_type = type_detecter.type(raw)
                latest["device_type"] = dev_type
                LOG.info("Detected type: %s", dev_type)

                if dev_type == '1':
                    dec = decoder_1
                elif dev_type == '2':
                    dec = decoder_2
                elif dev_type == '3':
                    dec = decoder_3
                elif dev_type == '4':
                    dec = decoder_4
                else:
                    LOG.warning("Unknown device type. Using decoder_1 as fallback.")
                    dec = decoder_1

                period = 1.0 / max(POLL_HZ, 0.1)
                while not stop_event.is_set():
                    try:
                        raw = bytes(await client.read_gatt_char(READ_CHAR_HANDLE, use_cached=1)).hex()
                    except Exception as e:
                        LOG.warning("Read failed: %s", e)
                        break

                    try:
                        prepared = dec.decode(raw)
                        digi = dec.printdigit(prepared)
                        char = dec.printchar(prepared)
                        func = ' '.join(char[0]).strip()
                        unit = ' '.join(char[1]).strip()
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

                        latest.update({
                            "timestamp": ts,
                            "value": digi,
                            "unit": unit,
                            "functions": func,
                            "connected": True,
                        })

                        await broadcast(make_payload())
                    except Exception as e:
                        LOG.exception("Decode error: %s", e)

                    await asyncio.sleep(period)

        except Exception as e:
            latest["connected"] = False
            LOG.warning("BLE connection error: %s (retrying in 2s)", e)
            await asyncio.sleep(2.0)

    LOG.info("BLE reader stopped")

# ======= Web server (aiohttp) =======

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Multimeter</title>
  <style>
    :root{
      --bg:#1a1d29;
      --fg:#ffffff;
      --mut:#94a3b8;
      --card:#2d3748;
      --grid:#4a5568;
      --accent:#3b82f6;
      --voltage:#00d4ff;
      --current:#ff6b35;
      --power:#10b981;
      --status:#f59e0b;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{
      background:var(--bg);
      color:var(--fg);
      font-family:'JetBrains Mono', 'Consolas', monospace;
      margin:20px;
      font-weight:500;
    }
    .wrap{max-width:1200px;margin:0 auto}

    .header{ text-align:center; margin-bottom:30px; }
    .header h1{
      color:var(--fg);
      font-size:2.5rem;
      font-weight:800;
      margin-bottom:10px;
      text-shadow:0 2px 4px rgba(0,0,0,0.3);
      letter-spacing:2px;
    }

    .main-metrics{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
      gap:20px;
      margin-bottom:24px;
    }

    .metric-card{
      background:var(--card);
      border-radius:12px;
      padding:24px;
      text-align:center;
      border:2px solid transparent;
      transition:all 0.3s ease;
      position:relative;
      overflow:hidden;
    }
    .metric-card::before{
      content:'';
      position:absolute;
      top:0;left:0;right:0;
      height:4px;
      background:var(--accent);
      opacity:0.6;
    }
    .metric-card.readout::before{ background:var(--power); }

    .metric-card.readout{ padding:20px 16px; }
    .readout-box{
      height: clamp(80px, 22vh, 200px);
      display:flex; align-items:center; justify-content:center;
    }
    .metric-value{
      font-size: clamp(7rem, 12vw, 10rem);
      font-weight:900; line-height:.9;
      text-shadow:0 4px 8px rgba(0,0,0,.5);
      white-space:nowrap;
      display:flex; align-items:baseline; justify-content:center;
      width:100%;
    }
    .metric-card.readout .metric-value{ color:var(--power); }
    #unit{ font-size:.28em; margin-left:.25em; color:var(--mut); }

    .chart-container{
      background:var(--card);
      border-radius:16px;
      padding:24px;
      margin-bottom:20px;
      border:1px solid var(--grid);
      position:relative;
      overflow:hidden;
    }
    .chart-container::before{
      content:'';
      position:absolute; top:0; left:0; right:0;
      height:4px; background:var(--voltage); opacity:.8;
    }
    .chart-header{
      display:flex; justify-content:center; align-items:center; margin-bottom:20px;
    }
    .legend{ display:flex; gap:24px; align-items:center; }
    .legend-item{ display:flex; align-items:center; gap:8px; font-size:1.1rem; font-weight:600; }
    .dot{ width:14px; height:14px; border-radius:50%; box-shadow:0 0 8px currentColor; }
    .dot.measurement{ background:var(--voltage); color:var(--voltage); }

    #chart{
      width:100%; height:400px; background:var(--bg);
      border-radius:12px; display:block; border:2px solid var(--grid);
    }

    .status-bar{
      background:var(--card);
      border-radius:8px;
      padding:12px 20px;
      font-size:0.9rem;
      color:var(--mut);
      text-align:center;
      border:1px solid var(--grid);
    }

    .pill{
      display:inline-block; background:var(--card); color:var(--fg);
      border:2px solid var(--grid); padding:.3rem .8rem; border-radius:8px;
      font-weight:500; font-size:.9rem; margin:0 4px 4px 0; transition:all .2s;
    }
    .pill:hover{ border-color:var(--accent); background:var(--accent); color:var(--bg); }

    @media (max-width:768px){
      .main-metrics{ grid-template-columns:repeat(2,1fr); }
      .readout-box{ height: clamp(40px, 24vh, 100px); }
    }
  </style>
</head>
<body>
<div class="wrap">

  <header class="header">
    <h1>MULTIMETER</h1>
  </header>

  <div class="main-metrics">
    <div class="metric-card readout" style="grid-column: 1 / -1;">
      <div class="readout-box">
        <div class="metric-value">
          <span id="value">—</span>
          <span id="unit"></span>
        </div>
      </div>
    </div>
  </div>

  <div class="chart-container">
    <div class="chart-header">
      <div class="legend">
        <div class="legend-item">
          <span class="dot measurement"></span>
          <span>Live Measurement</span>
        </div>
        <span id="ymax" style="margin-left:auto; font-size:12px; color:var(--mut)"></span>
      </div>
    </div>
    <canvas id="chart"></canvas>
  </div>

  <!-- Minimal hidden placeholders so JS doesn't crash; no layout change -->
  <div id="badges" style="display:none"></div>
  <div id="meta" style="display:none"></div>

</div>

<script>
  const valueEl=document.getElementById('value');
  const unitEl=document.getElementById('unit');
  const badgesEl=document.getElementById('badges'); // may be hidden
  const chart=document.getElementById('chart');
  const ymax=document.getElementById('ymax');
  const meta=document.getElementById('meta');       // may be hidden

  function setBadges(txt){
    if(!badgesEl) return;
    badgesEl.innerHTML="";
    const parts=(txt||"").split(/\s+/).filter(Boolean);
    if(!parts.length){
      const s=document.createElement('span');
      s.style.color='var(--mut)';
      s.textContent='—';
      s.style.fontSize='1.2rem';
      badgesEl.appendChild(s);
      return;
    }
    for(const t of parts){
      const s=document.createElement('span');
      s.className='pill';
      s.textContent=t;
      badgesEl.appendChild(s);
    }
  }

  async function loadLatest(){
    try{
      const r=await fetch('/api/latest',{cache:'no-store'});
      if(!r.ok) return;
      render(await r.json());
    }catch(e){}
  }

  let data=[];    // {t,y}
  let YMAX=1;
  const SMOOTH=0.18;

  function fit(){
    const dpr=window.devicePixelRatio||1;
    const r=chart.getBoundingClientRect();
    chart.width=Math.floor(r.width*dpr);
    chart.height=Math.floor(r.height*dpr);
  }
  addEventListener('resize',fit);
  fit();

  function draw(){
    const dpr=window.devicePixelRatio||1;
    const ctx=chart.getContext('2d');
    const W=chart.width, H=chart.height;

    ctx.fillStyle='#1a1d29';
    ctx.fillRect(0,0,W,H);

    const L=60*dpr, R=60*dpr, T=20*dpr, B=40*dpr;
    const w=W-L-R, h=H-T-B;

    ctx.save();
    ctx.translate(L,T);

    // Grid
    ctx.strokeStyle='#374151';
    ctx.lineWidth=1*dpr;
    ctx.beginPath();
    for(let gx=0; gx<=10; gx++){ const x=w*gx/10; ctx.moveTo(x,0); ctx.lineTo(x,h); }
    for(let gy=0; gy<=6; gy++){ const y=h*gy/6; ctx.moveTo(0,y); ctx.lineTo(w,y); }
    ctx.stroke();

    // Y labels
    ctx.fillStyle='#e2e8f0';
    ctx.font=`bold ${16*dpr}px JetBrains Mono, Consolas, monospace`;
    ctx.textAlign='right';
    for(let gy=0; gy<=6; gy++){
      const y=h*gy/6;
      const vLab=(YMAX*(1-gy/6)).toFixed(1);
      ctx.fillStyle='#00d4ff';
      ctx.fillText(vLab, -12*dpr, y+6*dpr);
    }

    const now=Date.now()/1000, win=60, t0=now-win;
    const mapX=t=> (t-t0)/win * w;
    const mapY=y=> (1 - Math.min(1, Math.max(0, y/YMAX))) * h;

    // Line
    const LWmain = Math.max(4*dpr, 4);
    ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.strokeStyle='#00d4ff';
    ctx.lineWidth=LWmain;
    ctx.beginPath();
    let started=false;
    for(const p of data){
      const x=mapX(p.t);
      if(x<0) continue;
      const y=mapY(p.y);
      if(!started){ ctx.moveTo(x,y); started=true; } else { ctx.lineTo(x,y); }
    }
    ctx.stroke();

    ctx.restore();
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);

  function render(j){
    valueEl.textContent = j.value ?? '—';
    unitEl.textContent  = j.unit || '';
    setBadges(j.functions);

    const y=parseFloat(j.value);
    if(!isNaN(y)){
      const now=Date.now()/1000, win=60, cut=now-win;
      data.push({t:now, y});
      while(data.length && data[0].t < cut) data.shift();

      let m=0; for(const p of data){ if(p.y>m) m=p.y; }
      const target=Math.max(1, Math.ceil(m*1.1*100)/100);
      YMAX += (target - YMAX) * SMOOTH;

      if(ymax) ymax.textContent = `Max: ${YMAX.toFixed(2)} ${j.unit||''}`;
    }

    if(meta) meta.textContent = `Live multimeter • ${data.length} samples • Auto-scaling`;
    document.title = (j.value ? `${j.value}${j.unit?' '+j.unit:''} – ` : '') + 'Multimeter';
  }

  loadLatest();
  const evt=new EventSource('/stream');
  evt.onmessage = ev => { try{ render(JSON.parse(ev.data)); }catch(_){} };
  evt.onerror = ()=>{};
</script>
</body>
</html>
""".replace("{poll_hz}", str(POLL_HZ))


async def handle_index(_req):
    return web.Response(text=DASHBOARD_HTML, content_type="text/html", charset="utf-8")

async def handle_latest(_req):
    return web.json_response(make_payload())

async def handle_stream(request):
    q: asyncio.Queue[str] = asyncio.Queue()
    sse_clients.add(q)
    await q.put(f"data: {json.dumps(make_payload())}\n\n")

    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    try:
        while True:
            data = await q.get()
            await resp.write(data.encode("utf-8"))
            await resp.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        sse_clients.discard(q)
        try:
            await resp.write_eof()
        except Exception:
            pass
    return resp

def make_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/latest", handle_latest)
    app.router.add_get("/stream", handle_stream)
    return app

# ======= Main runner =======

async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    ble_task = asyncio.create_task(ble_reader(stop_event))

    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    LOG.info("Starting web server at http://%s:%d", HTTP_HOST, HTTP_PORT)
    await site.start()

    await stop_event.wait()
    LOG.info("Shutting down...")

    ble_task.cancel()
    try:
        await ble_task
    except asyncio.CancelledError:
        pass

    await runner.cleanup()
    LOG.info("Bye")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
