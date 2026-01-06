from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from datetime import datetime

app = FastAPI(title="CHROMAX ST Demo — IFU-like Layout + Program Editor")

# =========================================================
# IFU-like bath slot schema (positions fixed)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 10)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = [f"R{i}" for i in range(18, 9, -1)] + ["LOAD"]

SLOT_KIND: Dict[str, str] = {**{f"R{i}": "reagent" for i in range(1, 19)},
                             **{f"W{i}": "water" for i in range(1, 6)},
                             "OVEN": "oven",
                             "LOAD": "load"}

def _default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"assign": "Empty", "group": ""} for slot in (TOP_ROW + BOTTOM_ROW)}
    for w in [f"W{i}" for i in range(1, 6)]:
        d[w] = {"assign": "Water", "group": "Water"}
    d["OVEN"] = {"assign": "Oven", "group": "Oven"}
    d["LOAD"] = {"assign": "Load", "group": "Load"}
    return d

CURRENT_LAYOUT: Dict[str, Dict[str, str]] = _default_layout()

# =========================================================
# Programs / Protocols (right panel)
# =========================================================
# A "Program" = list of steps. Each step has: step_name, target_slot(optional), time_sec
DEFAULT_PROGRAMS: Dict[str, Dict[str, Any]] = {
    "H&E Stain": {
        "steps": [
            {"name": "deparaffinization", "slot": "R1", "time_sec": 300},
            {"name": "hematoxylin",       "slot": "R2", "time_sec": 180},
            {"name": "rinse",             "slot": "W5", "time_sec": 60},
            {"name": "eosin",             "slot": "R3", "time_sec": 120},
            {"name": "dehydrate",         "slot": "R4", "time_sec": 240},
            {"name": "clear",             "slot": "R5", "time_sec": 180},
        ]
    },
    "PAP": {"steps": [{"name": "custom_step", "slot": "R6", "time_sec": 60}]},
    "CELLPROG": {"steps": [{"name": "custom_step", "slot": "R7", "time_sec": 60}]},
}

PROGRAMS: Dict[str, Dict[str, Any]] = dict(DEFAULT_PROGRAMS)
SELECTED_PROGRAM: str = "H&E Stain"

# =========================================================
# Simple audit + output handoff
# =========================================================
AUDIT: List[Dict[str, Any]] = []
LAST_CHECK: Optional[Dict[str, Any]] = None
LAST_HANDOFF: Optional[Dict[str, Any]] = None

def log(event: str, details: Dict[str, Any]):
    AUDIT.append({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event, "details": details})
    if len(AUDIT) > 500:
        del AUDIT[:200]

log("BOOT", {"top": len(TOP_ROW), "bottom": len(BOTTOM_ROW)})

# =========================================================
# Compatibility checks (device-ish, based on layout assignment)
# =========================================================
SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

def bump_overall(current: str, new_level: str) -> str:
    return new_level if SEVERITY[new_level] > SEVERITY[current] else current

# minimal rule: rinse must be on water slot; oven step must be on OVEN
STEP_REQUIRED_KIND = {
    "rinse": "water",
    "water": "water",
    "oven": "oven",
}

def check_program_vs_layout(program_name: str) -> Dict[str, Any]:
    prog = PROGRAMS.get(program_name)
    if not prog:
        return {"overall": "BLOCK", "findings": [{"code": "E900", "level": "BLOCK", "message": "Programm nicht gefunden.", "details": {"program": program_name}}]}

    overall = "OK"
    findings: List[Dict[str, Any]] = []
    steps = prog.get("steps", [])

    # rule: step must have a slot and slot must exist
    for idx, st in enumerate(steps, start=1):
        slot = (st.get("slot") or "").strip()
        name = (st.get("name") or "").strip()
        t = int(st.get("time_sec") or 0)

        if not name:
            findings.append({"code": "E910", "level": "BLOCK", "message": "Leerer Schrittname.", "details": {"step_index": idx}})
            overall = bump_overall(overall, "BLOCK")
            continue

        if t <= 0:
            findings.append({"code": "W910", "level": "WARN", "message": "Zeit ist 0 oder negativ.", "details": {"step": name, "slot": slot, "time_sec": t}})
            overall = bump_overall(overall, "WARN")

        if not slot:
            findings.append({"code": "E911", "level": "BLOCK", "message": "Schritt hat keinen Ziel-Slot.", "details": {"step": name, "step_index": idx}})
            overall = bump_overall(overall, "BLOCK")
            continue

        if slot not in CURRENT_LAYOUT:
            findings.append({"code": "E401", "level": "BLOCK", "message": "Slot existiert nicht im Layout.", "details": {"step": name, "slot": slot}})
            overall = bump_overall(overall, "BLOCK")
            continue

        # required slot kind for some steps
        req_kind = STEP_REQUIRED_KIND.get(name)
        actual_kind = SLOT_KIND.get(slot, "reagent")
        if req_kind and actual_kind != req_kind:
            findings.append({"code": "E402", "level": "BLOCK", "message": "Schritt auf falschem Slot-Typ.", "details": {"step": name, "slot": slot, "required": req_kind, "actual": actual_kind}})
            overall = bump_overall(overall, "BLOCK")

        # assignment sanity: if layout says Empty, warn
        assign = (CURRENT_LAYOUT[slot].get("assign") or "").strip()
        if assign.lower() in ("", "empty"):
            findings.append({"code": "W401", "level": "WARN", "message": "Slot ist als Empty belegt.", "details": {"slot": slot, "step": name}})
            overall = bump_overall(overall, "WARN")

    # must contain rinse somewhere (demo H&E safety)
    if program_name == "H&E Stain":
        if not any((s.get("name") == "rinse") for s in steps):
            findings.append({"code": "E202", "level": "BLOCK", "message": "Pflichtschritt fehlt: rinse.", "details": {"program": program_name}})
            overall = bump_overall(overall, "BLOCK")

    return {"program": program_name, "overall": overall, "findings": findings}

# =========================================================
# API models
# =========================================================
class LayoutItem(BaseModel):
    assign: str
    group: str = ""

class LayoutSaveReq(BaseModel):
    layout: Dict[str, LayoutItem]

class ProgramStep(BaseModel):
    name: str
    slot: str
    time_sec: int

class ProgramSaveReq(BaseModel):
    program_name: str
    steps: List[ProgramStep]

class SelectProgramReq(BaseModel):
    program_name: str

# =========================================================
# APIs: layout
# =========================================================
@app.get("/api/layout")
def api_get_layout():
    return {"layout": CURRENT_LAYOUT}

@app.post("/api/layout")
def api_save_layout(req: LayoutSaveReq):
    for slot in req.layout.keys():
        if slot not in CURRENT_LAYOUT:
            return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)
    for slot, item in req.layout.items():
        CURRENT_LAYOUT[slot] = {"assign": (item.assign or "").strip(), "group": (item.group or "").strip()}
    log("SAVE_LAYOUT", {"n": len(req.layout)})
    return {"ok": True}

@app.post("/api/layout/reset")
def api_reset_layout():
    CURRENT_LAYOUT.clear()
    CURRENT_LAYOUT.update(_default_layout())
    log("RESET_LAYOUT", {})
    return {"ok": True}

# =========================================================
# APIs: programs
# =========================================================
@app.get("/api/programs")
def api_programs():
    return {"selected": SELECTED_PROGRAM, "programs": sorted(list(PROGRAMS.keys()))}

@app.get("/api/program")
def api_program():
    prog = PROGRAMS.get(SELECTED_PROGRAM, {"steps": []})
    return {"selected": SELECTED_PROGRAM, "program": prog}

@app.post("/api/program/select")
def api_select_program(req: SelectProgramReq):
    global SELECTED_PROGRAM
    if req.program_name not in PROGRAMS:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)
    SELECTED_PROGRAM = req.program_name
    log("SELECT_PROGRAM", {"program": SELECTED_PROGRAM})
    return {"ok": True, "selected": SELECTED_PROGRAM}

@app.post("/api/program/save")
def api_save_program(req: ProgramSaveReq):
    PROGRAMS[req.program_name] = {"steps": [s.model_dump() for s in req.steps]}
    global SELECTED_PROGRAM
    SELECTED_PROGRAM = req.program_name
    log("SAVE_PROGRAM", {"program": req.program_name, "n_steps": len(req.steps)})
    return {"ok": True}

@app.post("/api/program/check")
def api_check_program():
    global LAST_CHECK
    res = check_program_vs_layout(SELECTED_PROGRAM)
    LAST_CHECK = res
    log("CHECK_PROGRAM", {"program": SELECTED_PROGRAM, "overall": res["overall"], "n": len(res["findings"])})
    return res

# =========================================================
# API: output handoff
# =========================================================
@app.post("/api/handoff")
def api_handoff():
    global LAST_HANDOFF
    LAST_HANDOFF = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "to": "coverslipper", "program": SELECTED_PROGRAM, "check": (LAST_CHECK or {}).get("overall")}
    log("HANDOFF_TO_COVERSLIPPER", LAST_HANDOFF)
    return {"ok": True, "handoff": LAST_HANDOFF}

# =========================================================
# Audit endpoints
# =========================================================
@app.get("/api/audit", response_class=JSONResponse)
def api_audit():
    return AUDIT[-300:]

@app.get("/audit", response_class=PlainTextResponse)
def audit_page():
    lines = ["AUDIT (last 300)"]
    for e in AUDIT[-300:]:
        lines.append(f"{e['t']} | {e['event']} | {e['details']}")
    return PlainTextResponse("\n".join(lines))

# =========================================================
# Main UI (IFU-like: baths center, program list right, output bottom-left)
# NOTE: template+replace to avoid any `{}` syntax issues.
# =========================================================
@app.get("/", response_class=HTMLResponse)
def main_ui():
    def bath_tile(slot: str) -> str:
        kind = SLOT_KIND.get(slot, "reagent")
        # smaller fields requested: compact inputs
        return (
            f"<div class='tile {kind}' id='tile_{slot}'>"
            f"  <div class='slot'>{slot}</div>"
            f"  <input class='inp' id='a_{slot}' placeholder='Assign' />"
            f"  <input class='inp inp2' id='g_{slot}' placeholder='Group' />"
            f"</div>"
        )

    top_html = "".join([bath_tile(s) for s in TOP_ROW])
    bottom_html = "".join([bath_tile(s) for s in BOTTOM_ROW])

    top_js = "[" + ",".join([f"'{s}'" for s in TOP_ROW]) + "]"
    bottom_js = "[" + ",".join([f"'{s}'" for s in BOTTOM_ROW]) + "]"

    tpl = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CHROMAX ST Demo</title>
<style>
:root{
  --bg:#0b1220;
  --text:#eaf0ff;
  --muted:rgba(234,240,255,.75);
  --stroke:rgba(255,255,255,.12);
  --card:rgba(255,255,255,.04);
  --btn:rgba(255,255,255,.06);
  --accent:#7aa2ff;
  --ok:#2ecc71; --warn:#f1c40f; --block:#e74c3c;
}
*{box-sizing:border-box}
body{
  font-family:-apple-system,system-ui,Arial;
  margin:0;padding:14px;color:var(--text);
  background:
    radial-gradient(1200px 800px at 20% 0%, rgba(122,162,255,.20), transparent 55%),
    radial-gradient(900px 600px at 80% 20%, rgba(46,204,113,.12), transparent 60%),
    var(--bg);
}

/* top menu bar mimic */
.topbar{
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding:10px 12px; border-radius:16px;
  border:1px solid var(--stroke); background:rgba(255,255,255,.04);
}
.tab{
  padding:10px 12px; border-radius:14px;
  border:1px solid var(--stroke); background:rgba(255,255,255,.05);
  font-weight:900; font-size:13px;
}
.tab.active{ background:rgba(122,162,255,.18); border-color:rgba(122,162,255,.55); }
.rightinfo{ margin-left:auto; display:flex; gap:10px; align-items:center; }
.badge{ padding:8px 10px; border-radius:999px; border:1px solid var(--stroke); background:rgba(255,255,255,.04); color:var(--muted); font-size:12px; }
.badge.ok{ border-color:rgba(46,204,113,.35); background:rgba(46,204,113,.10); color:var(--ok); }
.badge.warn{ border-color:rgba(241,196,15,.35); background:rgba(241,196,15,.10); color:var(--warn); }
.badge.block{ border-color:rgba(231,76,60,.35); background:rgba(231,76,60,.12); color:var(--block); }

.grid{
  margin-top:12px;
  display:grid;
  grid-template-columns: 1.25fr .75fr;
  gap:12px;
}
@media (max-width: 980px){
  .grid{ grid-template-columns:1fr; }
}

.card{
  border:1px solid var(--stroke);
  border-radius:16px;
  background:var(--card);
  padding:12px;
}

.hint{ color:var(--muted); font-size:12px; line-height:1.4; }
.sectionTitle{ font-weight:1000; letter-spacing:.3px; font-size:13px; color:var(--muted); margin-bottom:8px; }

.row{
  display:grid;
  grid-auto-flow:column;
  grid-auto-columns: 140px;
  gap:10px;
  overflow-x:auto;
  padding:8px 0;
}

/* bath tiles */
.tile{
  border:1px solid rgba(255,255,255,.12);
  border-radius:16px;
  padding:10px;
  min-height:108px;
  background:rgba(255,255,255,.03);
}
.slot{ font-weight:1000; margin-bottom:6px; letter-spacing:.3px; }
.inp{
  width:100%;
  padding:8px 10px;
  border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.05);
  color:var(--text);
  outline:none;
  font-size:12px;       /* smaller fields requested */
}
.inp2{ margin-top:7px; opacity:.95; }
.inp:focus{ border-color:rgba(122,162,255,.55); box-shadow:0 0 0 4px rgba(122,162,255,.12); }

/* neutral tint by kind */
.water{ background:rgba(120,190,255,.10); }
.oven{ background:rgba(255,120,120,.10); }
.load{ background:rgba(255,255,255,.02); }

/* buttons */
button{
  padding:11px 12px;
  border-radius:14px;
  border:1px solid var(--stroke);
  background:var(--btn);
  color:var(--text);
  font-weight:1000;
  font-size:13px;
}
button.primary{ border-color:rgba(122,162,255,.55); background:rgba(122,162,255,.18); }
button.danger{ border-color:rgba(231,76,60,.55); background:rgba(231,76,60,.14); }

/* right panel */
.list{
  display:flex; flex-direction:column; gap:8px;
}
.item{
  display:flex; justify-content:space-between; align-items:center; gap:10px;
  padding:10px 10px;
  border-radius:14px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.04);
  font-size:13px;
}
.item.active{ border-color:rgba(122,162,255,.55); background:rgba(122,162,255,.14); }
.smallbtn{ padding:8px 10px; border-radius:12px; font-size:12px; font-weight:900; }

.editorRow{
  display:grid;
  grid-template-columns: 1.2fr .8fr .6fr .3fr;
  gap:8px;
  align-items:center;
}
.editorRow input, .editorRow select{ font-size:12px; padding:8px 10px; border-radius:12px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.05); color:var(--text);}
.editorRow .del{ cursor:pointer; text-align:center; border:1px solid rgba(255,255,255,.12); border-radius:12px; padding:8px 0; background:rgba(231,76,60,.10); }

.finding{
  margin-top:8px;
  border:1px solid rgba(255,255,255,.10);
  border-radius:14px;
  background:rgba(0,0,0,.12);
  padding:10px;
  white-space:pre-wrap;
  font-family:ui-monospace, Menlo, monospace;
  font-size:12px;
  color:var(--muted);
}

/* bottom left output panel */
.outputBox{
  display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap;
  padding:10px; border-radius:16px;
  border:1px solid rgba(255,255,255,.10); background:rgba(255,255,255,.04);
}
</style>
</head>
<body>

  <div class="topbar">
    <div class="tab active">Status</div>
    <div class="tab">Programs</div>
    <div class="tab">Reagents</div>
    <div class="tab">Settings</div>
    <div class="tab">Users</div>

    <div class="rightinfo">
      <div class="badge" id="badge_check">⚪ (no check)</div>
      <div class="badge">Time: <span id="t_now"></span></div>
      <a class="badge" style="text-decoration:none;color:var(--accent);" href="/audit">Audit</a>
    </div>
  </div>

  <div class="grid">

    <!-- LEFT: Baths + Output -->
    <div class="card">
      <div class="sectionTitle">Bath layout (IFU schema) — editable assignment</div>
      <div class="hint">Oben: R1–R9, W1–W5, OVEN • Unten: R18–R10, LOAD</div>

      <div class="row" id="row_top">__TOP__</div>
      <div class="row" id="row_bottom">__BOTTOM__</div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
        <button onclick="loadLayout()">Reload layout</button>
        <button class="primary" onclick="saveLayout()">Save layout</button>
        <button class="danger" onclick="resetLayout()">Reset default</button>
      </div>

      <div style="height:12px"></div>

      <div class="sectionTitle">Output / handoff (bottom-left)</div>
      <div class="outputBox">
        <div>
          <div style="font-weight:1000;">Ausgabe & Übergabe zum Eindecker</div>
          <div class="hint" id="handoff_state">Status: bereit.</div>
        </div>
        <button class="primary" onclick="handoff()">Übergabe</button>
      </div>
      <div class="finding" id="msg_left">Ready.</div>
    </div>

    <!-- RIGHT: Program/Protocol Editor like IFU panel -->
    <div class="card">
      <div class="sectionTitle">Programs / Protocol editor (right panel)</div>

      <div class="list" id="program_list"></div>

      <div style="height:10px"></div>

      <div class="sectionTitle">Steps (editable)</div>
      <div class="hint">Felder klein gehalten (iPad). Slot z.B. R1, W5, OVEN. Zeit in Sekunden.</div>

      <div id="step_editor"></div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
        <button onclick="addStep()">+ Step</button>
        <button class="primary" onclick="saveProgram()">Save program</button>
        <button class="primary" onclick="checkProgram()">Check compatibility</button>
      </div>

      <div class="finding" id="msg_right">Ready.</div>
    </div>

  </div>

<script>
const TOP = __TOPSLOTS__;
const BOTTOM = __BOTTOMSLOTS__;
const ALL = TOP.concat(BOTTOM);

function setTime(){
  const d = new Date();
  document.getElementById('t_now').textContent = d.toLocaleTimeString();
}
setInterval(setTime, 1000); setTime();

function setBadge(overall){
  const b = document.getElementById('badge_check');
  b.classList.remove('ok','warn','block');
  if(overall === "OK"){ b.textContent = "🟢 OK"; b.classList.add('ok'); }
  else if(overall === "WARN"){ b.textContent = "🟡 WARN"; b.classList.add('warn'); }
  else if(overall === "BLOCK"){ b.textContent = "🔴 BLOCK"; b.classList.add('block'); }
  else { b.textContent = "⚪ (no check)"; }
}

// ---------------- Layout
async function loadLayout(){
  document.getElementById('msg_left').textContent = "Loading layout...";
  const r = await fetch('/api/layout');
  const data = await r.json();
  const layout = data.layout || {};
  ALL.forEach(slot=>{
    const a = document.getElementById('a_'+slot);
    const g = document.getElementById('g_'+slot);
    if(a) a.value = (layout[slot] && layout[slot].assign) ? layout[slot].assign : "";
    if(g) g.value = (layout[slot] && layout[slot].group) ? layout[slot].group : "";
  });
  document.getElementById('msg_left').textContent = "Loaded.";
}

async function saveLayout(){
  document.getElementById('msg_left').textContent = "Saving layout...";
  const payload = { layout: {} };
  ALL.forEach(slot=>{
    payload.layout[slot] = {
      assign: (document.getElementById('a_'+slot).value || "").trim(),
      group: (document.getElementById('g_'+slot).value || "").trim()
    };
  });

  const r = await fetch('/api/layout', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_left').textContent = "Saved ✅";
  }else{
    document.getElementById('msg_left').textContent = "Save failed ❌\\n" + JSON.stringify(data, null, 2);
  }
}

async function resetLayout(){
  if(!confirm("Reset layout to default?")) return;
  document.getElementById('msg_left').textContent = "Resetting layout...";
  const r = await fetch('/api/layout/reset', {method:'POST'});
  const data = await r.json();
  if(data.ok){
    await loadLayout();
    document.getElementById('msg_left').textContent = "Reset ✅";
  }else{
    document.getElementById('msg_left').textContent = "Reset failed ❌\\n" + JSON.stringify(data, null, 2);
  }
}

// ---------------- Programs list
let programs = [];
let selected = null;
let currentProgram = null;

async function loadPrograms(){
  const r = await fetch('/api/programs');
  const data = await r.json();
  programs = data.programs || [];
  selected = data.selected;
  renderProgramList();
  await loadSelectedProgram();
}

function renderProgramList(){
  const wrap = document.getElementById('program_list');
  wrap.innerHTML = "";
  programs.forEach(name=>{
    const cls = (name === selected) ? "item active" : "item";
    const html = `
      <div class="${cls}">
        <div><b>${name}</b></div>
        <button class="smallbtn" onclick="selectProgram('${name.replace(/'/g,"\\'")}')">Open</button>
      </div>
    `;
    wrap.insertAdjacentHTML('beforeend', html);
  });
}

async function selectProgram(name){
  const r = await fetch('/api/program/select', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({program_name:name})
  });
  const data = await r.json();
  if(data.ok){
    selected = data.selected;
    renderProgramList();
    await loadSelectedProgram();
    document.getElementById('msg_right').textContent = "Selected: " + selected;
  }else{
    document.getElementById('msg_right').textContent = "Select failed ❌";
  }
}

async function loadSelectedProgram(){
  const r = await fetch('/api/program');
  const data = await r.json();
  selected = data.selected;
  currentProgram = data.program || {steps:[]};
  renderStepEditor();
}

// ---------------- Step editor
function renderStepEditor(){
  const wrap = document.getElementById('step_editor');
  wrap.innerHTML = "";
  const steps = currentProgram.steps || [];
  steps.forEach((s, idx)=>{
    const html = `
      <div class="editorRow" style="margin-bottom:8px;">
        <input id="st_name_${idx}" value="${(s.name||'')}" placeholder="step name" />
        <input id="st_slot_${idx}" value="${(s.slot||'')}" placeholder="slot (R1, W5, OVEN)" />
        <input id="st_time_${idx}" type="number" value="${(s.time_sec||0)}" placeholder="time (s)" />
        <div class="del" onclick="delStep(${idx})">✕</div>
      </div>
    `;
    wrap.insertAdjacentHTML('beforeend', html);
  });

  if(steps.length === 0){
    wrap.innerHTML = "<div class='hint'>No steps yet. Click + Step.</div>";
  }
}

function addStep(){
  currentProgram.steps = currentProgram.steps || [];
  currentProgram.steps.push({name:"custom_step", slot:"R1", time_sec:60});
  renderStepEditor();
}

function delStep(idx){
  currentProgram.steps.splice(idx,1);
  renderStepEditor();
}

function collectStepsFromUI(){
  const steps = currentProgram.steps || [];
  const out = [];
  for(let idx=0; idx<steps.length; idx++){
    out.push({
      name: (document.getElementById('st_name_'+idx).value || "").trim(),
      slot: (document.getElementById('st_slot_'+idx).value || "").trim(),
      time_sec: parseInt(document.getElementById('st_time_'+idx).value || "0")
    });
  }
  return out;
}

async function saveProgram(){
  document.getElementById('msg_right').textContent = "Saving program...";
  const steps = collectStepsFromUI();
  const payload = { program_name: selected, steps: steps };
  const r = await fetch('/api/program/save', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Saved ✅";
  }else{
    document.getElementById('msg_right').textContent = "Save failed ❌\\n" + JSON.stringify(data, null, 2);
  }
}

async function checkProgram(){
  document.getElementById('msg_right').textContent = "Checking...";
  const r = await fetch('/api/program/check', {method:'POST'});
  const data = await r.json();
  setBadge(data.overall);

  // show condensed result
  let txt = data.program + " => " + data.overall + "\\n";
  (data.findings||[]).forEach(f=>{
    txt += `${f.code} | ${f.level} | ${f.message} | ${JSON.stringify(f.details||{})}\\n`;
  });
  if((data.findings||[]).length === 0) txt += "No findings.";
  document.getElementById('msg_right').textContent = txt;
}

// ---------------- Output / coverslipper handoff
async function handoff(){
  document.getElementById('handoff_state').textContent = "Übergabe läuft...";
  const r = await fetch('/api/handoff', {method:'POST'});
  const data = await r.json();
  if(data.ok){
    document.getElementById('handoff_state').textContent = "Übergeben ✅ (" + data.handoff.t + ")";
    document.getElementById('msg_left').textContent = "Handoff: " + JSON.stringify(data.handoff, null, 2);
  }else{
    document.getElementById('handoff_state').textContent = "Übergabe fehlgeschlagen ❌";
  }
}

loadLayout();
loadPrograms();
</script>

</body>
</html>
"""

    html = tpl.replace("__TOP__", top_html) \
              .replace("__BOTTOM__", bottom_html) \
              .replace("__TOPSLOTS__", top_js) \
              .replace("__BOTTOMSLOTS__", bottom_js)

    return HTMLResponse(html)
