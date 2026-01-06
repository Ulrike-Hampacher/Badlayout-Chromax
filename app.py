from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

app = FastAPI(title="CHROMAX ST Demo Device")

# ----------------------------
# Helpers
# ----------------------------
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ampel(overall: Optional[str]) -> str:
    return {"OK": "🟢 OK", "WARN": "🟡 WARN", "BLOCK": "🔴 BLOCK"}.get(overall or "", "⚪ (no check)")

# ----------------------------
# Protocol library (Demo)
# ----------------------------
PROTOCOL_LIBRARY = {
    "H&E_STD": {
        "protocol_type": "H&E",
        "steps": [
            ("deparaffinization", 300),
            ("hematoxylin", 180),
            ("rinse", 60),
            ("eosin", 120),
            ("dehydrate", 240),
            ("clear", 180),
        ],
    }
}

# ----------------------------
# Rules with codes
# ----------------------------
RULES: Dict[str, Any] = {
    "rules": [
        {"id": "R-ST-SLIDE-LIMIT", "then": {"code": "E101", "require_max": {"field": "slides_loaded", "max_from_field": "max_slides_supported"}, "level": "BLOCK", "message": "Maximale Objektträgerzahl überschritten."}},
        {"id": "R-ST-SLIDE-HIGH",  "then": {"code": "W201", "warn_if_greater": {"field": "slides_loaded", "threshold": 50}, "level": "WARN", "message": "Hohe Objektträgerzahl: mögliches QC-Risiko."}},
        {"id": "R-ST-TEMP-RANGE",  "then": {"code": "E103", "require_range": {"field": "temperature_c", "min": 15, "max": 30}, "level": "BLOCK", "message": "Betriebstemperatur außerhalb 15–30°C."}},
        {"id": "R-ST-SEQ-HE", "when": {"protocol_type": "H&E"}, "then": {"code": "E201", "must_contain_order": {"sequence": ["deparaffinization","hematoxylin","rinse","eosin","dehydrate","clear"]}, "level": "BLOCK", "message": "Ungültige oder unvollständige H&E-Schrittfolge."}},
        {"id": "R-ST-REQ-RINSE", "then": {"code": "E202", "require_step": {"name": "rinse"}, "level": "BLOCK", "message": "Pflichtschritt fehlt: rinse."}},
        {"id": "R-ST-HEMA-TIME", "when": {"protocol_type": "H&E"}, "then": {"code": "W202", "step_time_range": {"step": "hematoxylin", "min": 120, "max": 300}, "level": "WARN", "message": "Hematoxylin-Zeit außerhalb 120–300s."}},
        {"id": "R-ST-REAGENT-MIN", "then": {"code": "E301", "reagent_minimum": True, "level": "BLOCK", "message": "Mindestens ein Reagenz unter Mindestfüllstand."}},
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
    protocol_id: str
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
        "protocol_id": run.protocol_id,
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
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"field": f, "min": mn, "max": mx, "actual": val}})
                continue

        if "require_max" in then:
            f = then["require_max"]["field"]
            mf = then["require_max"]["max_from_field"]
            val = int(run_dict[f]); mx = int(run_dict[mf])
            if val > mx:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"field": f, "max": mx, "actual": val}})
                continue

        if "warn_if_greater" in then:
            f = then["warn_if_greater"]["field"]
            th = float(then["warn_if_greater"]["threshold"])
            val = float(run_dict[f])
            if val > th:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"field": f, "threshold": th, "actual": val}})
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
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"required_sequence": required, "actual": actual}})
            continue

        if "require_step" in then:
            need = then["require_step"]["name"]
            if need not in steps_list(run_dict):
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"missing_step": need}})
                continue

        if "step_time_range" in then:
            name = then["step_time_range"]["step"]
            mn = int(then["step_time_range"]["min"])
            mx = int(then["step_time_range"]["max"])
            t = next((s["time_sec"] for s in run_dict["steps"] if s["name"] == name), None)
            if t is None or not (mn <= int(t) <= mx):
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"step": name, "min": mn, "max": mx, "actual": t}})
                continue

        if then.get("reagent_minimum"):
            below = []
            for name, r in run_dict["reagents"].items():
                if float(r["volume_ml"]) < float(r["min_required_ml"]):
                    below.append({"reagent": name, "volume_ml": r["volume_ml"], "min_required_ml": r["min_required_ml"]})
            if below:
                findings.append({"rule_id": rid, "code": code, "level": level, "message": msg, "details": {"below_min": below}})
                continue

    overall = "OK"
    for f in findings:
        if SEVERITY[f["level"]] > SEVERITY[overall]:
            overall = f["level"]

    return {"run_id": run.run_id, "protocol_id": run.protocol_id, "overall": overall, "findings": findings}

def build_run(run_id: str, protocol_id: str, slides_loaded: int, max_slides_supported: int, temperature_c: float,
              reagent_state: Dict[str, tuple], step_overrides: Optional[Dict[str, int]] = None) -> RunInput:
    proto = PROTOCOL_LIBRARY[protocol_id]
    steps = []
    for name, t in proto["steps"]:
        if step_overrides and name in step_overrides:
            steps.append(Step(name, int(step_overrides[name])))
        else:
            steps.append(Step(name, int(t)))

    reagents = {k: Reagent(float(v[0]), float(v[1])) for k, v in reagent_state.items()}
    return RunInput(
        run_id=run_id,
        protocol_id=protocol_id,
        protocol_type=proto["protocol_type"],
        run_state="READY",
        slides_loaded=int(slides_loaded),
        max_slides_supported=int(max_slides_supported),
        temperature_c=float(temperature_c),
        steps=steps,
        reagents=reagents
    )

# ----------------------------
# Device state (server-side singleton demo)
# ----------------------------
class Device:
    def __init__(self):
        self.mode = "NORMAL"   # NORMAL / MAINTENANCE
        self.state = "READY"   # READY / RUNNING / COMPLETE
        self.operator: Optional[str] = None
        self.current_run: Optional[RunInput] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.audit: List[Dict[str, Any]] = []
        self._audit("BOOT", {})

    def _audit(self, event, details):
        self.audit.append({"t": now(), "event": event, "details": details, "operator": self.operator, "mode": self.mode, "state": self.state})

    def status(self):
        return {
            "mode": self.mode,
            "state": self.state,
            "operator": self.operator,
            "run_id": self.current_run.run_id if self.current_run else None,
            "protocol_id": self.current_run.protocol_id if self.current_run else None,
            "overall": self.last_result["overall"] if self.last_result else None,
            "findings": len(self.last_result["findings"]) if self.last_result else None,
        }

device = Device()

# ----------------------------
# API models
# ----------------------------
class LoginReq(BaseModel):
    name: str
    pin: str

# ----------------------------
# Web UI
# ----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    st = device.status()
    html = f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>CHROMAX ST Demo</title>
      <style>
        body {{ font-family: -apple-system, Arial; padding: 16px; }}
        .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 12px; margin: 10px 0; }}
        button {{ padding: 10px 12px; margin: 6px 6px 6px 0; border-radius: 10px; border: 1px solid #ccc; }}
        .mono {{ font-family: ui-monospace, Menlo, monospace; white-space: pre-wrap; }}
      </style>
    </head>
    <body>
      <h2>CHROMAX ST (DEMO DEVICE)</h2>

      <div class="card">
        <div><b>MODE</b>: {st["mode"]}</div>
        <div><b>STATE</b>: {st["state"]}</div>
        <div><b>OPERATOR</b>: {st["operator"] or "-"}</div>
        <div><b>RUN</b>: {st["run_id"] or "-"}</div>
        <div><b>PROTOCOL</b>: {st["protocol_id"] or "-"}</div>
        <div><b>CHECK</b>: {ampel(st["overall"])}</div>
        <div><b>FINDINGS</b>: {st["findings"] if st["findings"] is not None else "-"}</div>
      </div>

      <div class="card">
        <h3>Actions</h3>

        <div>
          <button onclick="api('/api/load/ok')">Load OK</button>
          <button onclick="api('/api/load/warn')">Load WARN</button>
          <button onclick="api('/api/load/block')">Load BLOCK</button>
        </div>

        <div>
          <button onclick="api('/api/check')">CHECK</button>
          <button onclick="api('/api/start')">START</button>
          <button onclick="api('/api/stop')">STOP</button>
        </div>

        <div>
          <button onclick="api('/api/report','GET')">REPORT</button>
          <button onclick="api('/api/audit','GET')">AUDIT</button>
        </div>

        <div style="margin-top:10px;">
          <input id="name" placeholder="Operator name" />
          <input id="pin" placeholder="PIN" />
          <button onclick="login()">Login</button>
          <button onclick="api('/api/logout')">Logout</button>
        </div>

        <div class="mono" id="out"></div>
      </div>

      <script>
        async function api(path, method='POST', body=null) {{
          const opts = {{ method }};
          if(body) {{
            opts.headers = {{'Content-Type':'application/json'}};
            opts.body = JSON.stringify(body);
          }}
          const r = await fetch(path, opts);
          const t = await r.text();
          document.getElementById('out').textContent = t;
        }}

        async function login() {{
          const name = document.getElementById('name').value || 'Operator';
          const pin = document.getElementById('pin').value || '1234';
          await api('/api/login','POST',{{name,pin}});
        }}
      </script>
    </body>
    </html>
    """
    return html

# ----------------------------
# API endpoints
# ----------------------------
@app.post("/api/login")
def api_login(req: LoginReq):
    if req.pin != "1234":
        device._audit("LOGIN_FAIL", {"name": req.name})
        return JSONResponse({"ok": False, "msg": "PIN falsch (Demo: 1234)."}, status_code=401)
    device.operator = req.name
    device._audit("LOGIN_OK", {"name": req.name})
    return {"ok": True, "msg": f"Logged in: {req.name}"}

@app.post("/api/logout")
def api_logout():
    device._audit("LOGOUT", {"name": device.operator})
    device.operator = None
    return {"ok": True, "msg": "Logged out"}

@app.post("/api/load/{which}")
def api_load(which: str):
    if which == "ok":
        run = build_run("RUN-OK", "H&E_STD", 48, 60, 22, {"hematoxylin": (350, 300), "eosin": (450, 300)})
    elif which == "warn":
        run = build_run("RUN-WARN", "H&E_STD", 55, 60, 22, {"hematoxylin": (330, 300), "eosin": (450, 300)}, step_overrides={"hematoxylin": 90})
    elif which == "block":
        run = build_run("RUN-BLOCK", "H&E_STD", 70, 60, 22, {"hematoxylin": (200, 300), "eosin": (450, 300)})
    else:
        return JSONResponse({"ok": False, "msg": "unknown preset"}, status_code=400)

    if device.state == "RUNNING":
        return JSONResponse({"ok": False, "msg": "RUNNING – cannot load"}, status_code=409)

    device.current_run = run
    device.last_result = None
    device.state = "READY"
    device._audit("RUN_LOADED", {"run_id": run.run_id, "protocol": run.protocol_id})
    return {"ok": True, "msg": f"Loaded {run.run_id}"}

@app.post("/api/check")
def api_check():
    if not device.current_run:
        return JSONResponse({"ok": False, "msg": "no run loaded"}, status_code=409)
    device.last_result = evaluate(device.current_run)
    device._audit("CHECK", {"overall": device.last_result["overall"], "n": len(device.last_result["findings"])})
    return device.last_result

@app.post("/api/start")
def api_start():
    if not device.operator:
        return JSONResponse({"ok": False, "msg": "login required"}, status_code=401)
    if not device.current_run:
        return JSONResponse({"ok": False, "msg": "no run loaded"}, status_code=409)

    device.last_result = evaluate(device.current_run)
    overall = device.last_result["overall"]
    device._audit("CHECK", {"overall": overall, "n": len(device.last_result["findings"])})

    if overall == "BLOCK":
        device.state = "READY"
        device._audit("START_BLOCKED", {})
        return JSONResponse({"ok": False, "msg": "START blocked (BLOCK)", "result": device.last_result}, status_code=409)

    device.state = "RUNNING"
    device._audit("START", {"overall": overall})
    return {"ok": True, "msg": f"START ok ({overall})", "result": device.last_result}

@app.post("/api/stop")
def api_stop():
    if device.state != "RUNNING":
        return JSONResponse({"ok": False, "msg": "not running"}, status_code=409)
    device.state = "COMPLETE"
    device._audit("STOP", {})
    return {"ok": True, "msg": "STOP ok"}

@app.get("/api/report", response_class=PlainTextResponse)
def api_report():
    st = device.status()
    lines = []
    lines.append("CHROMAX ST DEMO – RUN REPORT")
    lines.append("--------------------------------")
    lines.append(f"Time     : {now()}")
    lines.append(f"Mode     : {st['mode']}")
    lines.append(f"State    : {st['state']}")
    lines.append(f"Operator : {st['operator'] or '-'}")
    lines.append(f"Run      : {st['run_id'] or '-'}")
    lines.append(f"Protocol : {st['protocol_id'] or '-'}")
    lines.append(f"Overall  : {st['overall'] or '(none)'}")
    lines.append("--------------------------------")

    if device.current_run:
        lines.append("Steps:")
        for s in device.current_run.steps:
            lines.append(f" - {s.name}: {s.time_sec}s")
        lines.append("Reagents:")
        for name, r in device.current_run.reagents.items():
            lines.append(f" - {name}: {r.volume_ml}ml (min {r.min_required_ml}ml)")

    if device.last_result:
        lines.append("--------------------------------")
        lines.append("Findings:")
        if not device.last_result["findings"]:
            lines.append(" - none")
        else:
            for f in device.last_result["findings"]:
                lines.append(f" - {f['level']} {f['code']}: {f['message']}")
    return "\n".join(lines)

@app.get("/api/audit")
def api_audit():
    return device.audit
