"""FastAPI app over the engine's thread-safe snapshot (spec §8). Reads only; never blocks the engine."""
from __future__ import annotations

import json
import queue
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from runtime.engine import Engine

UI_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Straight Rows watchdog</title>
<style>
body{font-family:system-ui,sans-serif;margin:16px;background:#0f1419;color:#e6e6e6}
h1{font-size:18px;margin:0 0 8px} h2{font-size:14px;margin:16px 0 6px;color:#9ad}
table{border-collapse:collapse;width:100%;font-size:13px} th,td{border:1px solid #333;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#1b2430} .WORKING{color:#7c7} .PARKED{color:#999}
.ALARM{color:#f66;font-weight:bold} .WATCH{color:#fc6} .CONFIRMED{color:#6f6;font-weight:bold} .RETRACTED{color:#888}
#feed li{font-family:ui-monospace,monospace;font-size:12px;margin:2px 0} .bar{height:8px;background:#234;border-radius:4px;overflow:hidden}
.bar>div{height:100%;background:#39f} small{color:#9aa}
</style></head><body>
<h1>Straight Rows &middot; GNSS degradation watchdog <small id="hdr"></small></h1>
<div class="bar"><div id="prog" style="width:0%"></div></div>
<h2>Tractors</h2><table><thead><tr><th>Tractor</th><th>Gate</th><th>Present findings (severity)</th><th>Explanation</th><th>Top Bayes factors</th></tr></thead><tbody id="trs"></tbody></table>
<h2>Alarms</h2><table><thead><tr><th>ID</th><th>Class</th><th>State</th><th>Start</th><th>End</th><th>Tractors</th><th>Conf</th><th>Reason</th></tr></thead><tbody id="alarms"></tbody></table>
<h2>Recent changes</h2><ul id="feed"></ul>
<script>
const POLL=__POLL__;
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function tick(){
 try{
  const st=await (await fetch('/status')).json();
  document.getElementById('hdr').textContent=` data time ${st.data_time} · ${(st.progress*100).toFixed(1)}% · speed ${st.speed} · ${st.done?'replay finished':'replaying'}`+(st.last_watchdog?` · last watchdog ${st.last_watchdog.t_data} (${st.last_watchdog.n_touched} alarms)`:'');
  document.getElementById('prog').style.width=(st.progress*100)+'%';
  document.getElementById('trs').innerHTML=Object.entries(st.tractors).map(([t,v])=>`<tr><td>${t}</td><td class="${v.working?'WORKING':'PARKED'}">${v.working?'WORKING':'PARKED'}</td><td>${Object.entries(v.present).map(([f,s])=>`${f}(${s})`).join(' ')||'<small>none</small>'}${v.missing.length?` <small>missing: ${v.missing.join(',')}</small>`:''}</td><td>${v.explanation.join(', ')||'{}'}</td><td>${v.top_bf.map(b=>`${b.cause} ${b.bf}`).join(' · ')}</td></tr>`).join('');
  const al=await (await fetch('/alarms')).json();
  document.getElementById('alarms').innerHTML=al.slice().reverse().slice(0,40).map(a=>`<tr><td>${a.alarm_id}</td><td>${a.class}</td><td class="${a.state}">${a.state}</td><td>${a.start}</td><td>${a.end||'<i>open</i>'}</td><td>${a.tractors.join(',')}</td><td>${a.confidence}</td><td>${esc(a.reason||'')}</td></tr>`).join('');
  const fd=await (await fetch('/feed')).json();
  document.getElementById('feed').innerHTML=fd.slice(-25).reverse().map(e=>`<li>${esc(e.t_data_iso)} <span class="${e.to||''}">${esc(e.kind==='watchdog'?'WATCHDOG':e.to)}</span> ${esc(e.kind==='watchdog'?(e.actions||[]).join('; ')||(e.n_touched+' alarms touched'):`${e.cls} ${(e.tractors||[]).join(',')} ${e.alarm_id} ${e.reason||''} ${e.note||''}`)}</li>`).join('');
 }catch(e){document.getElementById('hdr').textContent=' (poll failed: '+e+')'}
}
tick(); setInterval(tick,POLL);
</script></body></html>"""


def create_app(eng: Engine) -> FastAPI:
    app = FastAPI(title="Straight Rows watchdog", version="1.0")

    @app.get("/health")
    def health():
        s = eng.snapshot()
        return {"ok": eng.error is None, "mode": s["mode"], "data_time": s["data_time"], "progress": s["progress"],
                "done": s["done"]}

    @app.get("/status")
    def status():
        return eng.snapshot()

    @app.get("/alarms")
    def alarms(state: Optional[str] = None, cls: Optional[str] = None, tractor: Optional[str] = None,
               include_suppressed: int = 0):
        return eng.alarms(state, cls, tractor, bool(include_suppressed))

    @app.get("/alarms/{alarm_id}")
    def alarm(alarm_id: str):
        a = eng.alarm(alarm_id)
        if a is None:
            raise HTTPException(status_code=404, detail="unknown alarm id")
        return a

    @app.get("/watchdog")
    def watchdog():
        return eng.watchdog_runs(int(eng.cfg.get("api", {}).get("watchdog_history", 20)))

    @app.get("/submission")
    def submission():
        return eng.submission_now()

    @app.get("/feed")
    def feed():
        with eng.sink.lock:
            return list(eng.sink.feed[-100:])

    @app.get("/stream")
    def stream():
        q: queue.Queue = queue.Queue(maxsize=1000)
        eng.sink.subscribe(q)

        def gen():
            try:
                yield "event: hello\ndata: {}\n\n"
                last = time.time()
                while not eng.stop_event.is_set():
                    try:
                        rec = q.get(timeout=1.0)
                    except queue.Empty:
                        if time.time() - last > 15:
                            yield ": keepalive\n\n"
                            last = time.time()
                        continue
                    yield f"event: {rec.get('kind', 'alarm')}\ndata: {json.dumps(rec, default=str)}\n\n"
                    last = time.time()
            finally:
                eng.sink.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/webui/meta")                     # webui hook: static facts for the React UI (services/webui)
    def webui_meta():
        from runtime import webui
        return webui.meta(eng.cfg, eng.tele, eng.sda, eng.out_dir)

    @app.post("/webui/control")                 # webui hook: playback speed / skip ahead (LIVE only; never rewinds)
    def webui_control(body: dict):
        from runtime import webui
        return webui.control(eng, body.get("speed"), body.get("seek"))

    @app.get("/ui", response_class=HTMLResponse)
    def ui():
        return UI_HTML.replace("__POLL__", str(int(eng.cfg.get("api", {}).get("ui_poll_ms", 2000))))

    return app
