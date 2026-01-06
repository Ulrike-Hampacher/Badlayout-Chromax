from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime

app = FastAPI(title="CHROMAX ST Demo (Badlayout)")

# =========================================================
# Helpers
# =========================================================
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ampel(overall: Optional[str]) -> str:
    return {"OK": "🟢 OK", "WARN": "🟡 WARN", "BLOCK": "🔴 BLOCK"}.get(overall or "", "⚪ (no check)")

SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

def bump_overall(current: str, new_level: str) -> str:
    return new_level if SEVERITY[new_level] > SEVERITY[current] else current

# =========================================================
# Device-like layout model (schematic, not IFU copy)
# =========================================================
# U-shape:
# Top row:    IN -> OVEN -> S1..S9 -> WATER
# Bottom row: S18..S10 -> OUT
STATIONS_TOP = [{"id": "IN", "label": "IN", "type": "input"},
                {"id": "OVEN", "label": "OVEN", "type": "oven"}] + \
               [{"id": f"S{i}", "label": f"S{i}", "type": "bath"} for i in range(1, 10)] + \
               [{"id": "W1", "label": "WATER", "type": "water"}]

STATIONS_BOTTOM = [{"id": f"S{i}", "label": f"S{i}", "type": "bath"} for i in range(18, 9, -1)] + \
                  [{"id": "OUT", "label": "OUT", "type": "output"}]

# Process path order for evaluation (IN/OUT are transport only)
PATH = ["OVEN"] + [f"S{i}" for i in range(1, 19)] + ["W1"]

STATION_TYPE: Dict[str, str] = {}
for s in STATIONS_TOP + STATIONS_BOTTOM:
    STATION_TYPE[s["id"]] = s["type"]

# =========================================================
# Protocol / step model
# =========================================================
@dataclass
class Step:
    name: str
    time_sec: int
    station_id: str

@dataclass
class Reagent:
    volume_ml: float
    min_required_ml: float

@dataclass
class RunInput:
    run_id: str
    protocol_type: str
    slides_loaded: int
    max_slides_supported: int
    temperature_c: float
    steps: List[Step]
    reagents: Dict[str, Reagent]

def run_to_dict(run: RunInput) -> Dict[str, Any]:
    return {
        "run_id": run.run_id,
        "protocol_type": run.protocol_type,
        "slides_loaded": run.slides_loaded,
        "max_slides_supported": run.max_slides_supported,
        "temperature_c": run.temperature_c,
        "steps": [{"station": s.station_id, "name": s.name, "time_sec": s.time_sec} for s in run.steps],
        "reagents": {k: {"volume_ml": v.volume_ml, "min_required_ml": v.min_required_ml} for k, v in run.reagents.items()},
    }

def steps_list_in_path_order(run_dict: Dict[str, Any]) -> List[str]:
    # Steps already collected in PATH order by builder, so keep order as is.
    return [s["name"] for s in run_dict["steps"]]

# =========================================================
# Rules (device-ish)
# =========================================================
# Step -> required station type (simple compatibility layer)
# You can extend this mapping to match the real device constraints.
STEP_REQUIRED_TYPE = {
    "rinse": "water",
    "water": "water",
    "oven": "oven",
    # Most chemistry steps assumed in "bath"
    "deparaffinization": "bath",
    "hematoxylin": "bath",
    "eosin": "bath",
    "dehydrate": "bath",
    "clear": "bath",
}

def evaluate(run: RunInput) -> Dict[str, Any]:
    rd = run_to_dict(run)
    findings: List[Dict[str, Any]] = []
    overall = "OK"

    # ---- Basic capacity / environment checks
    if rd["slides_loaded"] > rd["max_slides_supported"]:
        findings.append({"code": "E101", "level": "BLOCK", "message": "Maximale Objektträgerzahl überschritten.",
                         "details": {"slides_loaded": rd["slides_loaded"], "max_slides_supported": rd["max_slides_supported"]}})
        overall = bump_overall(overall, "BLOCK")

    if rd["slides_loaded"] > 50:
        findings.append({"code": "W201", "level": "WARN", "message": "Hohe Objektträgerzahl: mögliches QC-Risiko.",
                         "details": {"slides_loaded": rd["slides_loaded"], "threshold": 50}})
        overall = bump_overall(overall, "WARN")

    if not (15 <= float(rd["temperature_c"]) <= 30):
        findings.append({"code": "E103", "level": "BLOCK", "message": "Betriebstemperatur außerhalb 15–30°C.",
                         "details": {"temperature_c": rd["temperature_c"], "min": 15, "max": 30}})
        overall = bump_overall(overall, "BLOCK")

    # ---- Reagent minimum
    below = []
    for name, r in rd["reagents"].items():
        if float(r["volume_ml"]) < float(r["min_required_ml"]):
            below.append({"reagent": name, "volume_ml": r["volume_ml"], "min_required_ml": r["min_required_ml"]})
    if below:
        findings.append({"code": "E301", "level": "BLOCK", "message": "Mindestens ein Reagenz unter Mindestfüllstand.",
                         "details": {"below_min": below}})
        overall = bump_overall(overall, "BLOCK")

    # ---- Station type compatibility (device layout vs step)
    for s in rd["steps"]:
        step_name = s["name"]
        station_id = s["station"]
        required = STEP_REQUIRED_TYPE.get(step_name)
        actual_type = STATION_TYPE.get(station_id, "unknown")
        if required and actual_type != required:
            findings.append({
                "code": "E402",
                "level": "BLOCK",
                "message": "Schritt auf falschem Stationstyp (Layout inkompatibel).",
                "details": {"step": step_name, "station": station_id, "station_type": actual_type, "required_type": required}
            })
            overall = bump_overall(overall, "BLOCK")

    # ---- Protocol logic checks (H&E demo)
    step_names = steps_list_in_path_order(rd)

    # must contain rinse somewhere
    if "rinse" not in step_names:
        findings.append({"code": "E202", "level": "BLOCK", "message": "Pflichtschritt fehlt: rinse.",
                         "details": {"missing_step": "rinse"}})
        overall = bump_overall(overall, "BLOCK")

    if rd["protocol_type"] == "H&E":
        required_seq = ["deparaffinization", "hematoxylin", "rinse", "eosin", "dehydrate", "clear"]
        idx = -1
        ok = True
        for step in required_seq:
            if step not in step_names:
                ok = False
                break
            pos = step_names.index(step)
            if pos <= idx:
                ok = False
                break
            idx = pos
        if not ok:
            findings.append({"code": "E201", "level": "BLOCK", "message": "Ungültige oder unvollständige H&E-Schrittfolge.",
                             "details": {"required_sequence": required_seq, "actual": step_names}})
            overall = bump_overall(overall, "BLOCK")

        # hematoxylin time range warning
        hema_time = None
        for st in rd["steps"]:
            if st["name"] == "hematoxylin":
                hema_time = int(st["time_sec"])
                break
        if hema_time is None or not (120 <= hema_time <= 300):
            findings.append({"code": "W202", "level": "WARN", "message": "Hematoxylin-Zeit außerhalb 120–300s.",
                             "details": {"step": "hematoxylin", "min": 120, "max": 300, "actual": hema_time}})
            overall = bump_overall(overall, "WARN")

    return {"run_id": run.run_id, "overall": overall, "findings": findings}

# =========================================================
# Device state (simple)
# =========================================================
class Device:
    def __init__(self):
        self.operator: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.audit: List[Dict[str, Any]] = []
        self.log("BOOT", {})

    def log(self, event: str, details: Dict[str, Any]):
        self.audit.append({"t": now(), "event": event, "details": details})

    def status(self) -> Dict[str, Any]:
        return {
            "operator": self.operator,
            "overall": self.last_result["overall"] if self.last_result else None,
            "findings": len(self.last_result["findings"]) if self.last_result else None,
        }

dev = Device()

# =========================================================
# API models
# =========================================================
class LayoutTestReq(BaseModel):
    protocol_type: str = "H&E"
    slides_loaded: int = 48
    max_slides_supported: int = 60
    temperature_c: float = 22.0
    # baths: list of {station: "S1", step: "hematoxylin", time_sec: 180}
    baths: List[Dict[str, Any]]
    # reagents: {"hematoxylin":{"volume_ml":350,"min_required_ml":300}, ...}
    reagents: Dict[str, Dict[str, float]]

@app.post("/api/test_layout")
def api_test_layout(req: LayoutTestReq):
    steps: List[Step] = []
    for b in req.baths:
        station = (b.get("station") or "").strip()
        step = (b.get("step") or "").strip()
        time_sec = int(b.get("time_sec") or 0)
        if station in STATION_TYPE and step and step != "none" and time_sec > 0:
            steps.append(Step(name=step, time_sec=time_sec, station_id=station))

    reagents: Dict[str, Reagent] = {}
    for name, rv in req.reagents.items():
        reagents[name] = Reagent(volume_ml=float(rv.get("volume_ml", 0.0)),
                                 min_required_ml=float(rv.get("min_required_ml", 0.0)))

    run = RunInput(
        run_id="RUN-BADLAYOUT",
        protocol_type=req.protocol_type,
        slides_loaded=int(req.slides_loaded),
        max_slides_supported=int(req.max_slides_supported),
        temperature_c=float(req.temperature_c),
        steps=steps,
        reagents=reagents,
    )

    result = evaluate(run)
    dev.last_result = result
    dev.log("TEST_LAYOUT", {"overall": result["overall"], "n": len(result["findings"])})
    return result

@app.get("/api/audit", response_class=JSONResponse)
def api_audit():
    return dev.audit[-300:]

# =========================================================
# Pages (NO f-strings with curly braces)
# Use placeholders + replace to avoid SyntaxError from { } in CSS/JS.
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home():
    st = dev.status()
    tpl = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CHROMAX ST Demo</title>
  <style>
    body{font-family:-apple-system,system-ui,Arial;margin:0;padding:18px;background:#0b1220;color:#eaf0ff;}
    .card{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);border-radius:16px;padding:14px;margin:12px 0;}
    .pill{display:inline-flex;gap:10px;align-items:center;padding:10px 12px;border-radius:999px;border:1px solid rgba(255,255,255,.10);background:rgba(255,255,255,.04);}
    a{color:#7aa2ff;text-decoration:none;}
    .btn{display:inline-block;padding:12px 14px;border-radius:14px;border:1px solid rgba(255,255,255,.10);background:rgba(255,255,255,.06);color:#eaf0ff;font-weight:700}
  </style>
</head>
<body>
  <div class="card">
    <div class="pill"><b>CHROMAX ST</b> <span style="opacity:.7">(DEMO)</span></div>
    <div style="margin-top:10px;opacity:.85">Operator: <b>__OP__</b></div>
    <div style="margin-top:6px;opacity:.85">Check: <b>__CHECK__</b> &nbsp;&nbsp; Findings: <b>__N__</b></div>
  </div>

  <div class="card">
    <a class="btn" href="/layout">Open Badlayout Screen</a>
    <a class="btn" href="/audit">Audit</a>
    <a class="btn" href="/last">Last Result</a>
  </div>

  <div class="card" style="opacity:.8">
    Tipp: Im Badlayout Screen Stationen belegen (OVEN/S1..S18/WATER) und dann CHECK drücken.
  </div>
</body>
</html>
"""
    html = tpl.replace("__OP__", st["operator"] or "-") \
              .replace("__CHECK__", ampel(st["overall"])) \
              .replace("__N__", str(st["findings"]) if st["findings"] is not None else "-")
    return HTMLResponse(html)

@app.get("/last", response_class=PlainTextResponse)
def last():
    return PlainTextResponse(str(dev.last_result or {"overall": None, "findings": []}))

@app.get("/audit", response_class=PlainTextResponse)
def audit():
    lines = ["AUDIT (last 300)"]
    for e in dev.audit[-300:]:
        lines.append(f"{e['t']} | {e['event']} | {e['details']}")
    return PlainTextResponse("\n".join(lines))

@app.get("/layout", response_class=HTMLResponse)
def layout():
    # Build station tiles (HTML chunks)
    step_options = [
        "none",
        "deparaffinization",
        "hematoxylin",
        "rinse",
        "eosin",
        "dehydrate",
        "clear",
        "custom_step",
        "oven",
        "water",
    ]
    opts = "".join([f"<option value='{s}'>{s}</option>" for s in step_options])

    def tile(st: Dict[str, str]) -> str:
        sid = st["id"]
        typ = st["type"]
        label = st["label"]
        # transport I/O no inputs
        if typ in ("input", "output"):
            return (
                f"<div class='tile {typ}' id='tile_{sid}'>"
                f"<div class='title'>{label}</div>"
                f"<div class='sub'>I/O</div>"
                f"</div>"
            )
        # process stations with step + time
        return (
            f"<div class='tile {typ}' id='tile_{sid}'>"
            f"<div class='title'>{label}</div>"
            f"<select id='step_{sid}' class='sel'>{opts}</select>"
            f"<div class='row'><span class='muted'>Zeit (s)</span>"
            f"<input id='time_{sid}' class='num wide' type='number' value='0'></div>"
            f"</div>"
        )

    top_html = "".join([tile(s) for s in STATIONS_TOP])
    bottom_html = "".join([tile(s) for s in STATIONS_BOTTOM])

    # PATH JS literal
    path_js = "[" + ",".join([f"'{p}'" for p in PATH]) + "]"

    tpl = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Badlayout</title>
  <style>
  :root{
    --bg:#0b1220;
    --card:rgba(255,255,255,.05);
    --stroke:rgba(255,255,255,.10);
    --text:#eaf0ff;
    --muted:#9fb0d0;
    --accent:#7aa2ff;
    --ok:#2ecc71;
    --warn:#f1c40f;
    --block:#e74c3c;
    --shadow:0 10px 30px rgba(0,0,0,.35);
    --radius:16px;
  }
  *{box-sizing:border-box;}
  body{
    margin:0;padding:18px;color:var(--text);
    font-family:-apple-system,system-ui,Segoe UI,Roboto,Arial;
    background:
      radial-gradient(1200px 800px at 20% 0%, rgba(122,162,255,.20), transparent 55%),
      radial-gradient(1000px 700px at 80% 20%, rgba(46,204,113,.12), transparent 60%),
      var(--bg);
  }
  a{color:var(--accent);text-decoration:none;}
  .header{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:12px;}
  .h-title{font-weight:900;letter-spacing:.4px;font-size:18px;}
  .pill{display:inline-flex;align-items:center;gap:10px;padding:10px 12px;border-radius:999px;border:1px solid var(--stroke);background:rgba(255,255,255,.04);}
  .badge{display:inline-flex;align-items:center;gap:8px;padding:8px 10px;border-radius:999px;border:1px solid var(--stroke);background:rgba(255,255,255,.04);color:var(--muted);}
  .status-ok{border-color:rgba(46,204,113,.35);background:rgba(46,204,113,.10);color:var(--ok);}
  .status-warn{border-color:rgba(241,196,15,.35);background:rgba(241,196,15,.10);color:var(--warn);}
  .status-block{border-color:rgba(231,76,60,.35);background:rgba(231,76,60,.10);color:var(--block);}

  .grid{display:grid;grid-template-columns:1fr;gap:12px;}
  @media (min-width: 860px){ .grid{grid-template-columns: 1.2fr .8fr;} }

  .card{background:var(--card);border:1px solid var(--stroke);border-radius:var(--radius);box-shadow:var(--shadow);padding:14px;}
  h3{margin:0 0 10px;font-size:14px;color:var(--muted);font-weight:800;letter-spacing:.3px;}

  .controls{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;}
  .field{min-width:130px;}
  label{color:var(--muted);font-size:12px;display:block;margin-bottom:6px;}
  input,select{
    width:100%;padding:12px 12px;border-radius:14px;border:1px solid var(--stroke);
    background:rgba(255,255,255,.05);color:var(--text);outline:none;
  }
  input:focus,select:focus{border-color:rgba(122,162,255,.55);box-shadow:0 0 0 4px rgba(122,162,255,.12);}

  .btnbar{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;}
  button{
    border:1px solid var(--stroke);background:rgba(255,255,255,.06);color:var(--text);
    padding:12px 14px;border-radius:14px;font-weight:800;
  }
  button.primary{border-color:rgba(122,162,255,.55);background:rgba(122,162,255,.18);}
  button.danger{border-color:rgba(231,76,60,.55);background:rgba(231,76,60,.14);}

  .layout{overflow-x:auto;}
  .rowgrid{
    display:grid;
    grid-auto-flow:column;
    grid-auto-columns: 150px;
    gap:10px;
    padding: 10px 0;
    align-items:start;
  }
  .tile{
    border:1px solid rgba(255,255,255,.12);
    border-radius:16px;
    padding:12px;
    min-height: 150px;
    background:rgba(255,255,255,.03);
  }
  .tile .title{font-weight:900;margin-bottom:8px;letter-spacing:.2px;}
  .tile .sub{opacity:.7;}
  .row{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px;}
  .muted{color:var(--muted);font-size:12px;}
  .num{padding:12px;border-radius:14px;border:1px solid var(--stroke);background:rgba(255,255,255,.05);color:var(--text);width:92px;}
  .num.wide{width:100%;}
  .sel{width:100%;}

  /* station type tint (neutral, not IFU colors) */
  .input{background:rgba(255,255,255,.02);}
  .output{background:rgba(255,255,255,.02);}
  .oven{background:rgba(255,120,120,.10);}
  .water{background:rgba(120,190,255,.10);}
  .bath{background:rgba(255,255,255,.03);}

  /* per-tile highlight */
  .ok{outline:2px solid rgba(46,204,113,.8);}
  .warn{outline:2px solid rgba(241,196,15,.8);}
  .block{outline:2px solid rgba(231,76,60,.85);}

  .finding{
    border:1px solid rgba(255,255,255,.10);
    border-radius:14px;
    padding:10px;
    margin:8px 0;
    background:rgba(0,0,0,.12);
  }
  .finding .code{font-weight:900;letter-spacing:.3px;}
  .finding .lvl{opacity:.85;}
  .finding .msg{margin-top:6px;opacity:.9;}
  .finding .det{margin-top:8px;opacity:.75;font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:pre-wrap;}
  .two{display:grid;grid-template-columns:1fr;gap:10px;}
  @media (min-width: 520px){ .two{grid-template-columns:1fr 1fr;} }
  </style>
</head>
<body>
  <div class="header">
    <div class="pill"><span class="h-title">Badlayout – CHROMAX ST Demo</span></div>
    <div class="pill"><a href="/">Home</a></div>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Layout</h3>
      <div class="badge">Top: IN → OVEN → S1…S9 → WATER</div>
      <div class="layout">
        <div class="rowgrid" id="toprow">__TOP__</div>
      </div>

      <div class="badge">Bottom: S18…S10 → OUT</div>
      <div class="layout">
        <div class="rowgrid" id="bottomrow">__BOTTOM__</div>
      </div>
    </div>

    <div class="card">
      <h3>Run Settings</h3>
      <div class="controls">
        <div class="field">
          <label>Protocol Type</label>
          <select id="protocol_type">
            <option value="H&E">H&E</option>
            <option value="Custom">Custom</option>
          </select>
        </div>
        <div class="field">
          <label>Slides Loaded</label>
          <input id="slides" type="number" value="48" />
        </div>
        <div class="field">
          <label>Max Slides Supported</label>
          <input id="max_slides" type="number" value="60" />
        </div>
        <div class="field">
          <label>Temperature (°C)</label>
          <input id="temp" type="number" value="22" />
        </div>
      </div>

      <div style="height:12px"></div>

      <h3>Reagents</h3>
      <div class="two">
        <div>
          <label>Hematoxylin volume (ml)</label>
          <input id="r_hematoxylin_vol" type="number" value="350" />
          <label style="margin-top:8px">Hematoxylin min (ml)</label>
          <input id="r_hematoxylin_min" type="number" value="300" />
        </div>
        <div>
          <label>Eosin volume (ml)</label>
          <input id="r_eosin_vol" type="number" value="450" />
          <label style="margin-top:8px">Eosin min (ml)</label>
          <input id="r_eosin_min" type="number" value="300" />
        </div>
      </div>

      <div class="btnbar">
        <button onclick="preset_he()">Preset H&E</button>
        <button onclick="preset_warn()">Preset WARN</button>
        <button class="danger" onclick="preset_block()">Preset BLOCK</button>
        <button class="primary" onclick="check_layout()">CHECK Layout</button>
        <button onclick="reset_all()">Reset</button>
      </div>

      <div style="height:12px"></div>

      <h3>Result</h3>
      <div id="result_badge" class="badge">⚪ (no check)</div>
      <div id="findings"></div>
      <div id="raw" class="finding" style="display:none">
        <div class="det" id="rawjson"></div>
      </div>
    </div>
  </div>

<script>
const PATH = __PATH__;

function setBadge(overall){
  const b = document.getElementById('result_badge');
  b.classList.remove('status-ok','status-warn','status-block');
  if(overall === "OK"){ b.textContent = "🟢 OK"; b.classList.add('status-ok'); }
  else if(overall === "WARN"){ b.textContent = "🟡 WARN"; b.classList.add('status-warn'); }
  else if(overall === "BLOCK"){ b.textContent = "🔴 BLOCK"; b.classList.add('status-block'); }
  else { b.textContent = "⚪ (no check)"; }
}

function clearHighlights(){
  document.querySelectorAll('.tile').forEach(t=>{
    t.classList.remove('ok','warn','block');
  });
}

function getBaths(){
  const baths = [];
  for(const id of PATH){
    const stepEl = document.getElementById('step_'+id);
    const timeEl = document.getElementById('time_'+id);
    if(!stepEl || !timeEl) continue;
    baths.push({ station:id, step:stepEl.value, time_sec: parseInt(timeEl.value || "0") });
  }
  return baths;
}

function getReagents(){
  return {
    hematoxylin: {
      volume_ml: parseFloat(document.getElementById('r_hematoxylin_vol').value || "0"),
      min_required_ml: parseFloat(document.getElementById('r_hematoxylin_min').value || "0")
    },
    eosin: {
      volume_ml: parseFloat(document.getElementById('r_eosin_vol').value || "0"),
      min_required_ml: parseFloat(document.getElementById('r_eosin_min').value || "0")
    }
  };
}

function renderFindings(data){
  const wrap = document.getElementById('findings');
  wrap.innerHTML = "";
  if(!data.findings || data.findings.length === 0){
    wrap.innerHTML = "<div class='finding'><div class='code'>OK</div><div class='msg'>Keine Findings.</div></div>";
    return;
  }
  data.findings.forEach(f=>{
    const det = f.details ? JSON.stringify(f.details, null, 2) : "";
    const html = `
      <div class="finding">
        <div><span class="code">${f.code}</span> &nbsp; <span class="lvl">${f.level}</span></div>
        <div class="msg">${f.message}</div>
        ${det ? `<div class="det">${det}</div>` : ""}
      </div>
    `;
    wrap.insertAdjacentHTML("beforeend", html);
  });
}

async function check_layout(){
  clearHighlights();

  const payload = {
    protocol_type: document.getElementById('protocol_type').value,
    slides_loaded: parseInt(document.getElementById('slides').value || "0"),
    max_slides_supported: parseInt(document.getElementById('max_slides').value || "0"),
    temperature_c: parseFloat(document.getElementById('temp').value || "0"),
    baths: getBaths(),
    reagents: getReagents()
  };

  const r = await fetch('/api/test_layout', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  setBadge(data.overall);
  renderFindings(data);

  // simple highlight for used stations by overall severity
  const cls = (data.overall === "OK") ? "ok" : (data.overall === "WARN") ? "warn" : "block";
  payload.baths.forEach(b=>{
    if(b.step && b.step !== "none" && b.time_sec > 0){
      const tile = document.getElementById('tile_'+b.station);
      if(tile) tile.classList.add(cls);
    }
  });

  // raw json (hidden by default, easy to enable)
  document.getElementById('rawjson').textContent = JSON.stringify(data, null, 2);
}

function reset_all(){
  for(const id of PATH){
    const s = document.getElementById('step_'+id);
    const t = document.getElementById('time_'+id);
    if(s) s.value = "none";
    if(t) t.value = "0";
  }
  setBadge(null);
  document.getElementById('findings').innerHTML = "";
  clearHighlights();
}

function preset_he(){
  reset_all();
  document.getElementById('protocol_type').value = "H&E";
  document.getElementById('slides').value = "48";
  document.getElementById('max_slides').value = "60";
  document.getElementById('temp').value = "22";
  document.getElementById('r_hematoxylin_vol').value = "350";
  document.getElementById('r_hematoxylin_min').value = "300";
  document.getElementById('r_eosin_vol').value = "450";
  document.getElementById('r_eosin_min').value = "300";

  // device-ish: use OVEN optionally
  document.getElementById('step_OVEN').value = "oven";
  document.getElementById('time_OVEN').value = "180";

  // put sequence early along baths
  const seq = [
    ["S1","deparaffinization",300],
    ["S2","hematoxylin",180],
    ["W1","rinse",60],           // rinse on WATER to satisfy type check
    ["S3","eosin",120],
    ["S4","dehydrate",240],
    ["S5","clear",180]
  ];
  seq.forEach(([sid, step, t])=>{
    const s = document.getElementById('step_'+sid);
    const tt = document.getElementById('time_'+sid);
    if(s) s.value = step;
    if(tt) tt.value = String(t);
  });
}

function preset_warn(){
  preset_he();
  document.getElementById('slides').value = "55";   // W201
  document.getElementById('time_S2').value = "90";  // W202 hematoxylin time
}

function preset_block(){
  preset_he();
  document.getElementById('slides').value = "70";                 // E101
  document.getElementById('r_hematoxylin_vol').value = "200";     // E301
  // make rinse invalid by removing it
  document.getElementById('step_W1').value = "none";              // E202 + E201
  document.getElementById('time_W1').value = "0";
}
</script>

</body>
</html>
"""

    html = tpl.replace("__TOP__", top_html) \
              .replace("__BOTTOM__", bottom_html) \
              .replace("__PATH__", path_js)
    return HTMLResponse(html)
