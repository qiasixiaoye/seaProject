from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ocean_agents_demo import deepseek_client
from ocean_agents_demo.core import run_pipeline
from ocean_agents_demo.nc_data import dataset_summary, list_variables, query_grid


INDEX = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ocean RAG + Elements Demo</title>
<style>
:root{--bg:#eef3f7;--panel:#fff;--line:#d6e0ea;--ink:#13212f;--muted:#64748b;--blue:#0f6f9f;--green:#0b7668}
*{box-sizing:border-box}body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:0;background:var(--bg);color:var(--ink)}header{height:70px;padding:14px 24px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:18px}h1{font-size:21px;margin:0;font-weight:650}.health{font-size:13px;color:var(--muted);text-align:right}main{display:grid;grid-template-columns:340px 1fr;gap:14px;padding:14px;max-width:1800px;margin:0 auto}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px}.stack{display:grid;gap:12px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.label{font-size:12px;color:var(--muted);margin-bottom:5px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;background:#fff}textarea{min-height:136px;resize:vertical;line-height:1.5}button{border:0;border-radius:6px;padding:9px 12px;background:var(--blue);color:#fff;cursor:pointer}button.secondary{background:#edf5f7;color:#0f5666;border:1px solid #cfe4ea}.status{font-size:13px;color:var(--muted);min-height:20px}.scene{display:grid;grid-template-columns:minmax(620px,1.35fr) minmax(360px,.65fr);gap:14px;align-items:stretch}canvas{width:100%;display:block;border-radius:8px;border:1px solid var(--line);background:#071927}.earth{min-height:calc(100vh - 118px);height:760px;max-height:900px}.mapwrap{position:relative}.mapwrap canvas{height:420px}.legend{display:flex;align-items:center;gap:8px;margin-top:8px;color:var(--muted);font-size:12px}.bar{height:10px;flex:1;border-radius:999px;background:linear-gradient(90deg,#1d4ed8,#0891b2,#22c55e,#fde047,#dc2626)}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}.stat{border:1px solid var(--line);border-radius:8px;padding:8px}.stat b{display:block;font-size:18px}.report{white-space:pre-wrap;line-height:1.58;font-size:14px}.doc{border:1px solid var(--line);border-radius:6px;padding:8px;margin-top:8px;font-size:13px}.ok{color:var(--green)}.bad{color:#b45309}@media(max-width:1100px){main,.scene{grid-template-columns:1fr}.earth{height:620px;min-height:520px}header{height:auto;align-items:flex-start;flex-direction:column}.health{text-align:left}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head>
<body><header><h1>海洋要素区域查询 + 海洋知识 RAG 助手</h1><div id="health" class="health">checking...</div></header>
<main>
<aside class="panel stack">
  <div>
    <div class="label">海洋要素数据集</div>
    <select id="variable"></select>
  </div>
  <button onclick="queryOcean()">查询框选区域</button>
  <div id="oceanStatus" class="status">请在右侧大地球或框架地图上拖拽框选区域。</div>
  <hr style="border:0;border-top:1px solid var(--line);width:100%">
  <div>
    <div class="label">海洋知识问题</div>
    <textarea id="q">请结合海表温度和海洋热浪知识，评估目标海域对珊瑚礁和渔业的风险，并形成规划建议</textarea>
  </div>
  <div class="grid2">
    <div><div class="label">RAG 后端</div><select id="backend"><option>auto</option><option>local</option><option>ragflow</option></select></div>
    <div><div class="label">TopK</div><input id="topk" type="number" value="6" min="1" max="12"></div>
  </div>
  <button onclick="ask(false)">生成 RAG 报告</button>
  <button class="secondary" onclick="ask(true)">带 Trace 运行</button>
</aside>
<section class="stack">
  <div class="panel scene">
    <canvas id="globe" class="earth" width="940" height="820"></canvas>
    <div class="mapwrap">
      <canvas id="map" width="820" height="420"></canvas>
      <div class="legend"><span id="vmin">-</span><div class="bar"></div><span id="vmax">-</span></div>
      <div id="stats" class="stats"></div>
    </div>
  </div>
  <div class="panel">
    <div id="status" class="status">等待提问。</div>
    <div id="report" class="report"></div>
    <div id="docs"></div>
  </div>
</section>
</main>
<script>
const G=document.getElementById('globe'), M=document.getElementById('map'), g=G.getContext('2d'), m=M.getContext('2d');
let selection=null, oceanData=null, dragStart=null, dragSurface='map', rot=-150, lockRot=false;
function lonX(lon){return (lon+180)/360*M.width} function latY(lat){return (90-lat)/180*M.height}
function xyLonLat(x,y){return {lon:x/M.width*360-180,lat:90-y/M.height*180}}
function color(t){t=Math.max(0,Math.min(1,t));const stops=[[29,78,216],[8,145,178],[34,197,94],[253,224,71],[220,38,38]];const p=t*(stops.length-1),i=Math.min(stops.length-2,Math.floor(p)),f=p-i;const a=stops[i],b=stops[i+1];return `rgb(${a.map((v,k)=>Math.round(v+(b[k]-v)*f)).join(',')})`}
function drawMap(){m.clearRect(0,0,M.width,M.height);m.fillStyle='#e6f3fb';m.fillRect(0,0,M.width,M.height);m.strokeStyle='#aac5d8';m.lineWidth=1;for(let lon=-180;lon<=180;lon+=30){m.beginPath();m.moveTo(lonX(lon),0);m.lineTo(lonX(lon),M.height);m.stroke()}for(let lat=-60;lat<=60;lat+=30){m.beginPath();m.moveTo(0,latY(lat));m.lineTo(M.width,latY(lat));m.stroke()}m.fillStyle='#d6e4d1';[[-170,15,95,45],[-82,-55,70,68],[-20,-35,60,70],[70,-45,110,75]].forEach(r=>m.fillRect(lonX(r[0]),latY(r[3]),lonX(r[2])-lonX(r[0]),latY(r[1])-latY(r[3])));if(oceanData){const vals=oceanData.values, lats=oceanData.lats, lons=oceanData.lons, mn=oceanData.stats.min, mx=oceanData.stats.max;for(let i=0;i<lats.length;i++){for(let j=0;j<lons.length;j++){const v=vals[i][j];if(v===null)continue;const t=(v-mn)/Math.max(1e-9,mx-mn);m.fillStyle=color(t);const x=lonX(lons[j]), y=latY(lats[i]);const cw=Math.max(2,M.width/Math.max(1,lons.length)), ch=Math.max(2,M.height/Math.max(1,lats.length));m.fillRect(x-cw/2,y-ch/2,cw,ch)}}}drawSelection()}
function drawSelection(){if(!selection)return;const x1=lonX(selection.west),x2=lonX(selection.east),y1=latY(selection.north),y2=latY(selection.south);m.strokeStyle='#111827';m.lineWidth=2;m.setLineDash([6,4]);m.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));m.setLineDash([])}
function project(lon,lat){const R=G.width*.42, cx=G.width/2, cy=G.height/2, la=lat*Math.PI/180, lo=(lon+rot)*Math.PI/180;const c=Math.cos(la)*Math.cos(lo);if(c<0)return null;return [cx+R*Math.cos(la)*Math.sin(lo),cy-R*Math.sin(la)]}
function inverseGlobe(x,y){const R=G.width*.42,cx=G.width/2,cy=G.height/2,dx=(x-cx)/R,dy=(cy-y)/R,r2=dx*dx+dy*dy;if(r2>1)return null;const z=Math.sqrt(1-r2),lat=Math.asin(dy)*180/Math.PI,lon=Math.atan2(dx,z)*180/Math.PI-rot;return {lon:((lon+540)%360)-180,lat}}
function lineSphere(points,style){g.strokeStyle=style;g.beginPath();let open=true;for(const [lon,lat] of points){const p=project(lon,lat);if(!p){open=true;continue}if(open){g.moveTo(p[0],p[1]);open=false}else g.lineTo(p[0],p[1])}g.stroke()}
function drawGlobe(){g.clearRect(0,0,G.width,G.height);const cx=G.width/2,cy=G.height/2,R=G.width*.42,grad=g.createRadialGradient(cx-R*.35,cy-R*.4,R*.1,cx,cy,R);grad.addColorStop(0,'#64e4d6');grad.addColorStop(.5,'#0f6f9f');grad.addColorStop(1,'#031724');g.fillStyle=grad;g.beginPath();g.arc(cx,cy,R,0,Math.PI*2);g.fill();g.save();g.beginPath();g.arc(cx,cy,R,0,Math.PI*2);g.clip();g.lineWidth=1.2;for(let lat=-60;lat<=60;lat+=20)lineSphere(Array.from({length:145},(_,i)=>[-180+i*2.5,lat]),'rgba(255,255,255,.22)');for(let lon=-150;lon<=180;lon+=20)lineSphere(Array.from({length:73},(_,i)=>[lon,-90+i*2.5]),'rgba(255,255,255,.18)');if(oceanData){const vals=oceanData.values,lats=oceanData.lats,lons=oceanData.lons,mn=oceanData.stats.min,mx=oceanData.stats.max;for(let i=0;i<lats.length;i++){for(let j=0;j<lons.length;j++){const v=vals[i][j];if(v===null)continue;const p=project(lons[j],lats[i]);if(!p)continue;g.fillStyle=color((v-mn)/Math.max(1e-9,mx-mn));g.fillRect(p[0]-2,p[1]-2,4,4)}}}if(selection){const b=selection;g.lineWidth=3;lineSphere([[b.west,b.south],[b.east,b.south],[b.east,b.north],[b.west,b.north],[b.west,b.south]],'#fde047')}g.restore();if(!lockRot)rot+=.05;requestAnimationFrame(drawGlobe)}
function setSelection(a,b){if(!a||!b)return;selection={west:Math.min(a.lon,b.lon),east:Math.max(a.lon,b.lon),south:Math.min(a.lat,b.lat),north:Math.max(a.lat,b.lat)};oceanData=null;vmin.textContent='-';vmax.textContent='-';stats.innerHTML='';oceanStatus.textContent=`已框选：${selection.west.toFixed(1)} 至 ${selection.east.toFixed(1)}，${selection.south.toFixed(1)} 至 ${selection.north.toFixed(1)}。点击查询框选区域。`;drawMap()}
M.addEventListener('mousedown',e=>{const r=M.getBoundingClientRect();dragSurface='map';dragStart={x:(e.clientX-r.left)*M.width/r.width,y:(e.clientY-r.top)*M.height/r.height}});
M.addEventListener('mousemove',e=>{if(!dragStart||dragSurface!=='map')return;const r=M.getBoundingClientRect(),x=(e.clientX-r.left)*M.width/r.width,y=(e.clientY-r.top)*M.height/r.height;setSelection(xyLonLat(dragStart.x,dragStart.y),xyLonLat(x,y))});
G.addEventListener('mousedown',e=>{const r=G.getBoundingClientRect(),x=(e.clientX-r.left)*G.width/r.width,y=(e.clientY-r.top)*G.height/r.height;const p=inverseGlobe(x,y);if(!p)return;dragSurface='globe';dragStart=p;lockRot=true});
G.addEventListener('mousemove',e=>{if(!dragStart||dragSurface!=='globe')return;const r=G.getBoundingClientRect(),x=(e.clientX-r.left)*G.width/r.width,y=(e.clientY-r.top)*G.height/r.height;setSelection(dragStart,inverseGlobe(x,y))});
window.addEventListener('mouseup',()=>{dragStart=null;lockRot=false});
async function loadMeta(){const h=await fetch('/health').then(r=>r.json());health.textContent=`Demo=${h.status} | RAGFlow=${h.ragflow.configured} | LLM=${h.llm.configured?'DeepSeek '+h.llm.model:'local fallback'}`;const d=await fetch('/api/ocean/variables').then(r=>r.json());variable.innerHTML=d.variables.map(v=>`<option value="${v.dataset}::${v.name}">${v.dataset} / ${v.name} - ${v.long_name} (${v.origin})</option>`).join('');drawMap()}
async function queryOcean(){if(!selection){oceanStatus.textContent='先在大地球或框架地图上拖拽框选区域。';return}const [dataset,varName]=variable.value.split('::');oceanStatus.textContent='查询海洋要素中...';const r=await fetch('/api/ocean/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset,variable:varName,bounds:selection,max_points:6500})});const d=await r.json();if(!r.ok){oceanData=null;vmin.textContent='-';vmax.textContent='-';stats.innerHTML='';oceanStatus.textContent=d.message||JSON.stringify(d);drawMap();return}oceanData=d;vmin.textContent=`${d.stats.min} ${d.units}`;vmax.textContent=`${d.stats.max} ${d.units}`;stats.innerHTML=`<div class="stat"><span>最小</span><b>${d.stats.min}</b></div><div class="stat"><span>最大</span><b>${d.stats.max}</b></div><div class="stat"><span>均值</span><b>${d.stats.mean}</b></div><div class="stat"><span>格点</span><b>${d.stats.count}</b></div>`;oceanStatus.textContent=`已渲染 ${d.dataset} / ${d.long_name}`;drawMap()}
async function ask(trace){const reportStatus=document.getElementById('status');reportStatus.textContent='运行中...';report.textContent='';docs.innerHTML='';const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q.value,backend:backend.value,top_k:Number(topk.value),trace})});const d=await r.json();if(!r.ok){reportStatus.textContent=JSON.stringify(d);return}reportStatus.textContent='完成';report.textContent=d.report;[...(d.kept_documents||[]).map(x=>['保留',x]),...(d.passed_documents||[]).map(x=>['PASS',x])].forEach(([k,x])=>{const div=document.createElement('div');div.className='doc';div.innerHTML=`<b class="${k==='保留'?'ok':'bad'}">${k}</b> ${x.title}<br>score=${x.decision_score}`;docs.appendChild(div)})}
loadMeta();drawGlobe();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    backend = "auto"

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = INDEX.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            self.send_json(200, {"status": "ok", "backend": self.backend, "ragflow": ragflow_status(), "llm": deepseek_client.status()})
            return
        if path == "/api/ocean/datasets":
            self.send_json(200, dataset_summary())
            return
        if path == "/api/ocean/variables":
            self.send_json(200, list_variables())
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/ocean/query":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                self.send_json(200, query_grid(body))
            except Exception as exc:
                self.send_json(500, {"error": type(exc).__name__, "message": str(exc)})
            return
        if path != "/ask":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = run_pipeline(
                question=str(body.get("question", "")).strip(),
                top_k=int(body.get("top_k", 6)),
                threshold=float(body.get("threshold", 0.22)),
                backend=str(body.get("backend", self.backend)),
                trace=bool(body.get("trace", False)),
            )
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(500, {"error": type(exc).__name__, "message": str(exc)})


def ragflow_status() -> dict:
    dataset_ids = [x for x in os.getenv("RAGFLOW_DATASET_IDS", "").split(",") if x.strip()]
    return {
        "configured": bool(os.getenv("RAGFLOW_BASE_URL") and os.getenv("RAGFLOW_API_KEY") and dataset_ids),
        "base_url": os.getenv("RAGFLOW_BASE_URL", "http://127.0.0.1:9380"),
        "dataset_count": len(dataset_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["auto", "local", "ragflow"], default="auto")
    args = parser.parse_args()
    Handler.backend = args.backend
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
