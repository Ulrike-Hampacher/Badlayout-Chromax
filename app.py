from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime

app = FastAPI(title="CHROMAX ST Demo Device (Badlayout)")

# ----------------------------
# Helpers
# ----------------------------
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ampel(overall: Optional[str]) -> str:
    return {"OK": "🟢 OK", "WARN": "🟡 WARN", "BLOCK": "🔴 BLOCK"}.get(overall or "", "⚪ (no check)")

# ----------------------------
# Rules with codes
# ----------------------------
RULES: Dict[str, Any] = {
    "rules": [
        {"id": "R-ST-SLIDE-LIMIT",
         "then": {"code": "E101", "require_max": {"field": "slides_loaded", "max_from_field": "max_slides_supported"},
                  "level": "BLOCK", "message": "Maximale Objektträgerzahl überschritten."}},
        {"id": "R-ST-SLIDE-HIGH",
         "then": {"code": "W201", "warn_if_greater": {"field": "slides_loaded", "threshold": 50},
                  "level": "WARN", "message": "Hohe Objektträgerzahl: mögliches QC-Risiko."}},
        {"id": "R-ST-TEMP-RANGE",
         "then": {"code": "E103", "require_range": {"field": "temperature_c", "min": 15, "max": 30},
                  "level": "BLOCK", "message": "Betriebstemperatur außerhalb 15–30°C."}},

        # H&E sequence rule (Demo)
        {"id": "R-ST-SEQ-HE", "when": {"protocol_type": "H&E"},
         "then": {"code": "E201", "must_contain_order": {"sequence": ["deparaffinization","hematoxylin","rinse","eosin","dehydrate","clear"]},
                  "level": "BLOCK", "message": "Ungültige oder unvollständige H&E-Schrittfolge."}},

        {"id": "R-ST-REQ-RINSE",
         "then": {"code": "E202", "require_step": {"name": "rinse"},
                  "level": "BLOCK", "message": "Pflichtschritt fehlt: rinse."}},

        {"id": "R-ST-HEMA-TIME", "when": {"protocol_type": "H&E"},
         "then": {"code": "W202", "step_time_range": {"step": "hematoxylin", "min": 120, "max": 300},
                  "level": "WARN", "message": "Hematoxylin-Zeit außerhalb 120–300s."}},

        {"id": "R-ST-REAGENT-MIN",
         "then": {"code": "E301", "reagent_minimum": True,
                  "level": "BLOCK", "message": "Mindestens ein Reagenz unter Mindestfüllstand."}},
    ]
}
SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

# ----------------------------
# Models
# ----------------------------
@dataclass
class Step:
    name: str
    time_sec: int

@dataclass
class Reagent:
    volume_ml: float
    min_required_ml: float

@dataclass
class RunInput:
    run_id: str
    protocol_type: str
    run_state: str
    slides_loaded: int
    max_slides_supported: int
    temperature_c: float
    steps: List[Step]
    reagents: Dict[str, Reagent]

def run_to_dict(run: RunInput) -> Dict[str, Any]:
    return {
        "run_id": run.run_id,
        "protocol_type": run.protocol_type,
        "run_state": run.run_state,
        "slides_loaded": run.slides_loaded,
        "max_slides_supported": run.max_slides_supported,
        "temperature_c": run.temperature_c,
        "steps": [{"name": s.name, "time_sec": s.time_sec} for s in run.steps],
        "reagents": {k: {"volume_ml": v.volume_ml, "min_required_ml": v.min_required_ml} for k, v in run.reagents.items()},
    }

def steps_list(run_dict: Dict[str, Any]) -> List[str]:
    return [s["name"] for s in run_dict["steps"]]

def evaluate(run: RunInput) -> Dict[str, Any]:
    run_dict = run_to_dict(run)
    findings: List[Dict[str, Any]] = []

    for rule in RULES["rules"]:
        rid = rule["id"]
        when = rule.get("when", {})
        then = rule["then"]

        if any(run_dict.get(k) != v for k, v in when.items()):
            continue

        code = then.get("code", "X000")
        level = then["level"]
        msg = then["message"]

        if "require_range" in then:
            f = then["require_range"]["field"]
            mn = float(then["require_range"]["min"])
            mx = float(then["require_range"]["max"])
            val = float(run_dict[f])
            if not (mn <= val <= mx):
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"field": f, "min": mn, "max": mx, "actual": val}})
                continue

        if "require_max" in then:
            f = then["require_max"]["field"]
            mf = then["require_max"]["max_from_field"]
            val = int(run_dict[f]); mx = int(run_dict[mf])
            if val > mx:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"field": f, "max": mx, "actual": val}})
                continue

        if "warn_if_greater" in then:
            f = then["warn_if_greater"]["field"]
            th = float(then["warn_if_greater"]["threshold"])
            val = float(run_dict[f])
            if val > th:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"field": f, "threshold": th, "actual": val}})
                continue

        if "must_contain_order" in then:
            required = then["must_contain_order"]["sequence"]
            actual = steps_list(run_dict)
            idx = -1
            ok = True
            for step in required:
                if step not in actual or actual.index(step) <= idx:
                    ok = False
                    break
                idx = actual.index(step)
            if not ok:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"required_sequence": required, "actual": actual}})
            continue

        if "require_step" in then:
            need = then["require_step"]["name"]
            if need not in steps_list(run_dict):
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"missing_step": need}})
                continue

        if "step_time_range" in then:
            name = then["step_time_range"]["step"]
            mn = int(then["step_time_range"]["min"])
            mx = int(then["step_time_range"]["max"])
            t = next((s["time_sec"] for s in run_dict["steps"] if s["name"] == name), None)
            if t is None or not (mn <= int(t) <= mx):
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"step": name, "min": mn, "max": mx, "actual": t}})
                continue

        if then.get("reagent_minimum"):
            below = []
            for name, r in run_dict["reagents"].items():
                if float(r["volume_ml"]) < float(r["min_required_ml"]):
                    below.append({"reagent": name, "volume_ml": r["volume_ml"], "min_required_ml": r["min_required_ml"]})
            if below:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg,
                                 "details": {"below_min": below}})
                continue

    overall = "OK"
    for f in findings:
        if SEVERITY[f["level"]] > SEVERITY[overall]:
            overall = f["level"]

    return {"run_id": run.run_id, "overall": overall, "findings": findings}

# ----------------------------
# Device singleton (super simple)
# ----------------------------
class Device:
    def __init__(self):
        self.state = "READY"
        self.operator: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.audit: List[Dict[str, Any]] = []

    def log(self, event, details=None):
        self.audit.append({"t": now(), "event": event, "details": details or {}, "operator": self.operator, "state": self.state})

    def status(self):
        return {
            "state": self.state,
            "operator": self.operator,
            "overall": self.last_result["overall"] if self.last_result else None,
            "findings": len(self.last_result["findings"]) if self.last_result else None,
        }

device = Device()
device.log("BOOT")

# ----------------------------
# API payload for Badlayout test
# ----------------------------
class LayoutTestReq(BaseModel):
    protocol_type: str = "H&E"
    slides_loaded: int = 48
    max_slides_supported: int = 60
    temperature_c: float = 22.0
    baths: List[Dict[str, Any]]  # [{station:1, step:"hematoxylin", time_sec:180}, ...]
    reagents: Dict[str, Dict[str, float]]  # {"hematoxylin":{"volume_ml":350,"min_required_ml":300}, ...}

@app.post("/api/test_layout")
def api_test_layout(req: LayoutTestReq):
    steps = []
    for b in req.baths:
        name = (b.get("step") or "").strip()
        t = int(b.get("time_sec") or 0)
        if name and name != "none" and t > 0:
            steps.append(Step(name=name, time_sec=t))

    reagents = {}
    for k, v in req.reagents.items():
        reagents[k] = Reagent(volume_ml=float(v.get("volume_ml", 0)), min_required_ml=float(v.get("min_required_ml", 0)))

    run = RunInput(
        run_id="RUN-BADLAYOUT",
        protocol_type=req.protocol_type,
        run_state="READY",
        slides_loaded=int(req.slides_loaded),
        max_slides_supported=int(req.max_slides_supported),
        temperature_c=float(req.temperature_c),
        steps=steps,
        reagents=reagents
    )
    result = evaluate(run)
    device.last_result = result
    device.log("TEST_LAYOUT", {"overall": result["overall"], "n": len(result["findings"])})
    return result

# ----------------------------
# Pages
# ----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    st = device.status()
    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>CHROMAX ST Demo</title>
      <style>
        body {{ font-family: -apple-system, Arial; padding: 16px; }}
        .card {{ border:1px solid #ddd; border-radius: 14px; padding: 14px; margin: 12px 0; }}
        a.button {{ display:inline-block; padding:10px 12px; border:1px solid #ccc; border-radius:12px; text-decoration:none; margin-right:8px; }}
        .mono {{ font-family: ui-monospace, Menlo, monospace; white-space: pre-wrap; }}
      </style>
    </head>
    <body>
      <h2>CHROMAX ST (DEMO)</h2>
      <div class="card">
        <div><b>STATE</b>: {st["state"]}</div>
        <div><b>OPERATOR</b>: {st["operator"] or "-"}</div>
        <div><b>CHECK</b>: {ampel(st["overall"])}</div>
        <div><b>FINDINGS</b>: {st["findings"] if st["findings"] is not None else "-"}</div>
      </div>

      <div class="card">
        <h3>Badlayout</h3>
        <a class="button" href="/layout">Open Badlayout Screen</a>
        <a class="button" href="/audit">Audit</a>
        <a class="button" href="/last">Last Result</a>
      </div>

      <div class="card">
        <div class="mono">
Tipp: Im Badlayout Screen kannst du pro Station Schritt + Zeit einstellen,
Reagenzien füllen und dann CHECK drücken.
        </div>
      </div>
    </body></html>
    """
    return html

@app.get("/last", response_class=HTMLResponse)
def last():
    res = device.last_result or {"overall": None, "findings": []}
    return HTMLResponse(f"<pre>{res}</pre>")

@app.get("/audit", response_class=PlainTextResponse)
def audit():
    lines = []
    for e in device.audit[-200:]:
        lines.append(f"{e['t']} | {e['event']} | state={e['state']} | {e['details']}")
    return "\n".join(lines)

@app.get("/layout", response_class=HTMLResponse)
def layout():
    # 8 Stationen als Demo-Badlayout
    stations = 8
    step_options = ["none","deparaffinization","hematoxylin","rinse","eosin","dehydrate","clear"]

    opts_html = "".join([f"<option value='{s}'>{s}</option>" for s in step_options])

    # Default Reagents
    default_reagents = {
        "hematoxylin": {"volume_ml": 350, "min_required_ml": 300},
        "eosin": {"volume_ml": 450, "min_required_ml": 300},
    }

    reagent_rows = ""
    for name, r in default_reagents.items():
        reagent_rows += f"""
        <div class="reagent-row">
          <div><b>{name}</b></div>
          <div>vol <input type="number" id="r_{name}_vol" value="{r['volume_ml']}" style="width:90px;"> ml</div>
          <div>min <input type="number" id="r_{name}_min" value="{r['min_required_ml']}" style="width:90px;"> ml</div>
        </div>
        """

    station_cards = ""
    for i in range(1, stations+1):
        station_cards += f"""
        <div class="bath">
          <div class="bath-title">Station {i}</div>
          <div>
            <select id="step_{i}" style="width:100%; padding:10px; border-radius:10px;">
              {opts_html}
            </select>
          </div>
          <div style="margin-top:8px;">
            Zeit (s):
            <input type="number" id="time_{i}" value="0" style="width:100%; padding:10px; border-radius:10px;">
          </div>
        </div>
        """

    html = f"""
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Badlayout</title>
      <style>
        body {{ font-family:-apple-system, Arial; padding: 14px; }}
        .top {{ display:flex; gap:10px; flex-wrap:wrap; }}
        .card {{ border:1px solid #ddd; border-radius:14px; padding:14px; margin:12px 0; }}
        .grid {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:12px; }}
        .bath {{ border:1px solid #e5e5e5; border-radius:14px; padding:12px; }}
        .bath-title {{ font-weight:700; margin-bottom:8px; }}
        button {{ padding:12px 14px; border-radius:12px; border:1px solid #ccc; }}
        .out {{ white-space:pre-wrap; font-family: ui-monospace, Menlo, monospace; }}
        .reagent-row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; padding:10px 0; border-bottom:1px solid #eee; }}
        .badge {{ display:inline-block; padding:6px 10px; border-radius:999px; border:1px solid #ddd; }}
      </style>
    </head>
    <body>
      <h2>Badlayout Screen</h2>

      <div class="card top">
        <div>
          Protocol:
          <select id="protocol_type" style="padding:10px; border-radius:10px;">
            <option value="H&E">H&E</option>
            <option value="Custom">Custom</option>
          </select>
        </div>
        <div>Slides: <input id="slides" type="number" value="48" style="width:90px; padding:10px; border-radius:10px;"></div>
        <div>Max Slides: <input id="max_slides" type="number" value="60" style="width:90px; padding:10px; border-radius:10px;"></div>
        <div>Temp °C: <input id="temp" type="number" value="22" style="width:90px; padding:10px; border-radius:10px;"></div>
        <div><a href="/" class="badge">Home</a></div>
      </div>

      <div class="card">
        <h3>Stations / Baths</h3>
        <div class="grid">
          {station_cards}
        </div>
      </div>

      <div class="card">
        <h3>Reagents</h3>
        {reagent_rows}
      </div>

      <div class="card">
        <button onclick="preset_he()">Preset H&E</button>
        <button onclick="preset_warn()">Preset WARN</button>
        <button onclick="preset_block()">Preset BLOCK</button>
        <button onclick="check_layout()">CHECK Layout</button>
      </div>

      <div class="card">
        <h3>Result</h3>
        <div id="result_badge" class="badge">⚪ (no check)</div>
        <div id="out" class="out"></div>
      </div>

      <script>
        function getBaths() {{
          const baths = [];
          for(let i=1;i<={stations};i++) {{
            const step = document.getElementById('step_'+i).value;
            const time = parseInt(document.getElementById('time_'+i).value || '0');
            baths.push({{station:i, step:step, time_sec:time}});
          }}
          return baths;
        }}

        function getReagents() {{
          return {{
            "hematoxylin": {{
              volume_ml: parseFloat(document.getElementById('r_hematoxylin_vol').value || '0'),
              min_required_ml: parseFloat(document.getElementById('r_hematoxylin_min').value || '0')
            }},
            "eosin": {{
              volume_ml: parseFloat(document.getElementById('r_eosin_vol').value || '0'),
              min_required_ml: parseFloat(document.getElementById('r_eosin_min').value || '0')
            }}
          }};
        }}

        function setOverallBadge(overall) {{
          const badge = document.getElementById('result_badge');
          if(overall === "OK") badge.textContent = "🟢 OK";
          else if(overall === "WARN") badge.textContent = "🟡 WARN";
          else if(overall === "BLOCK") badge.textContent = "🔴 BLOCK";
          else badge.textContent = "⚪ (no check)";
        }}

        async function check_layout() {{
          const payload = {{
            protocol_type: document.getElementById('protocol_type').value,
            slides_loaded: parseInt(document.getElementById('slides').value || '0'),
            max_slides_supported: parseInt(document.getElementById('max_slides').value || '0'),
            temperature_c: parseFloat(document.getElementById('temp').value || '0'),
            baths: getBaths(),
            reagents: getReagents()
          }};
          const r = await fetch('/api/test_layout', {{
            method: 'POST',
            headers: {{'Content-Type':'application/json'}},
            body: JSON.stringify(payload)
          }});
          const data = await r.json();
          setOverallBadge(data.overall);
          document.getElementById('out').textContent = JSON.stringify(data, null, 2);
        }}

        function clearAll() {{
          for(let i=1;i<={stations};i++) {{
            document.getElementById('step_'+i).value = "none";
            document.getElementById('time_'+i).value = "0";
          }}
        }}

        function preset_he() {{
          document.getElementById('protocol_type').value = "H&E";
          document.getElementById('slides').value = "48";
          document.getElementById('max_slides').value = "60";
          document.getElementById('temp').value = "22";
          document.getElementById('r_hematoxylin_vol').value = "350";
          document.getElementById('r_hematoxylin_min').value = "300";
          document.getElementById('r_eosin_vol').value = "450";
          document.getElementById('r_eosin_min').value = "300";

          clearAll();
          // Sequence on stations
          const seq = [
            ["deparaffinization",300],
            ["hematoxylin",180],
            ["rinse",60],
            ["eosin",120],
            ["dehydrate",240],
            ["clear",180]
          ];
          for(let i=0;i<seq.length;i++) {{
            document.getElementById('step_'+(i+1)).value = seq[i][0];
            document.getElementById('time_'+(i+1)).value = seq[i][1];
          }}
        }}

        function preset_warn() {{
          preset_he();
          document.getElementById('slides').value = "55";     // W201
          document.getElementById('time_2').value = "90";     // W202 hematoxylin time
        }}

        function preset_block() {{
          preset_he();
          document.getElementById('slides').value = "70";     // E101
          document.getElementById('r_hematoxylin_vol').value = "200"; // E301
          // remove rinse
          document.getElementById('step_3').value = "none";   // E202 + E201
          document.getElementById('time_3').value = "0";
        }}
      </script>
    </body></html>
    """
    return html
