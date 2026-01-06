from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
import json
import re

app = FastAPI(title="CHROMAX ST Demo — IFU Layout + Rules (v1)")

DATA_FILE = Path("chromax_demo_data.json")

# =========================================================
# IFU bath schema (EXACT as your screenshot)
# Top:    R1..R7, W1..W5, OVEN (top-right)
# Bottom: R18..R8, LOAD (bottom-right)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 8)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = [f"R{i}" for i in range(18, 7, -1)] + ["LOAD"]
ALL_SLOTS = TOP_ROW + BOTTOM_ROW

SLOT_KIND: Dict[str, str] = {
    **{f"R{i}": "reagent" for i in range(1, 19)},
    **{f"W{i}": "water" for i in range(1, 6)},
    "OVEN": "oven",
    "LOAD": "load",
}

# =========================================================
# Helpers
# =========================================================
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def clamp_hex(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "#64748b"
    if not s.startswith("#"):
        s = "#" + s
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s
    return "#64748b"

def safe_write_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False

def safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

# =========================================================
# Defaults (Reagents, Layout, Programs)
# =========================================================
DEFAULT_REAGENTS: Dict[str, Dict[str, Any]] = {
    # id: {name, category, color}
    "EMPTY": {"id": "EMPTY", "name": "Empty", "category": "EMPTY", "color": "#94a3b8"},
    "WATER": {"id": "WATER", "name": "Water", "category": "WATER", "color": "#60a5fa"},
    "XYLENE": {"id": "XYLENE", "name": "Xylene", "category": "XYLENE", "color": "#fbbf24"},
    "ALC96": {"id": "ALC96", "name": "Alcohol 96%", "category": "ALCOHOL", "color": "#a78bfa"},
    "ALC100": {"id": "ALC100", "name": "Alcohol 100%", "category": "ALCOHOL", "color": "#8b5cf6"},
    "HEM": {"id": "HEM", "name": "Hematoxylin", "category": "HEMATOXYLIN", "color": "#22c55e"},
    "EOS": {"id": "EOS", "name": "Eosin", "category": "EOSIN", "color": "#fb7185"},
    "CLEAR": {"id": "CLEAR", "name": "Clearing", "category": "CLEAR", "color": "#f59e0b"},
    "OVEN": {"id": "OVEN", "name": "Oven", "category": "OVEN", "color": "#f87171"},
    "LOAD": {"id": "LOAD", "name": "Load", "category": "LOAD", "color": "#cbd5e1"},
}

def default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"reagent_id": "EMPTY"} for slot in ALL_SLOTS}
    # water stations W1..W5
    for w in [f"W{i}" for i in range(1, 6)]:
        d[w] = {"reagent_id": "WATER"}
    d["OVEN"] = {"reagent_id": "OVEN"}
    d["LOAD"] = {"reagent_id": "LOAD"}
    return d

DEFAULT_PROGRAMS: Dict[str, Dict[str, Any]] = {
    "H&E": {
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

# =========================================================
# In-memory state + persistence
# =========================================================
STATE: Dict[str, Any] = {
    "reagents": dict(DEFAULT_REAGENTS),
    "layout": default_layout(),
    "programs": dict(DEFAULT_PROGRAMS),
    "selected_program": "H&E",
    "last_check": None,
    "audit": [],
}

def persist():
    # best-effort write
    safe_write_json(DATA_FILE, {
        "reagents": STATE["reagents"],
        "layout": STATE["layout"],
        "programs": STATE["programs"],
        "selected_program": STATE["selected_program"],
    })

def load_persisted():
    data = safe_read_json(DATA_FILE)
    if not data:
        return
    # merge safely
    if isinstance(data.get("reagents"), dict):
        STATE["reagents"] = data["reagents"]
    if isinstance(data.get("layout"), dict):
        # only accept known slots
        lay = default_layout()
        for k, v in data["layout"].items():
            if k in lay and isinstance(v, dict) and "reagent_id" in v:
                lay[k]["reagent_id"] = v["reagent_id"]
        STATE["layout"] = lay
    if isinstance(data.get("programs"), dict):
        STATE["programs"] = data["programs"]
    if isinstance(data.get("selected_program"), str) and data["selected_program"] in STATE["programs"]:
        STATE["selected_program"] = data["selected_program"]

def log(event: str, details: Dict[str, Any]):
    STATE["audit"].append({"t": now(), "event": event, "details": details})
    if len(STATE["audit"]) > 600:
        del STATE["audit"][:250]

load_persisted()
log("BOOT", {"slots_top": len(TOP_ROW), "slots_bottom": len(BOTTOM_ROW)})

# =========================================================
# Rules / Compatibility Engine
# =========================================================
SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

def bump(cur: str, new: str) -> str:
    return new if SEVERITY[new] > SEVERITY[cur] else cur

# Step -> required slot kind
STEP_REQUIRED_KIND = {
    "rinse": "water",
    "water": "water",
    "oven": "oven",
}

# Step -> allowed reagent categories
STEP_ALLOWED_CATEGORIES: Dict[str, List[str]] = {
    "deparaffinization": ["XYLENE", "CLEAR", "OTHER"],
    "hematoxylin":       ["HEMATOXYLIN", "OTHER"],
    "eosin":             ["EOSIN", "OTHER"],
    "dehydrate":         ["ALCOHOL", "OTHER"],
    "clear":             ["XYLENE", "CLEAR", "OTHER"],
    "rinse":             ["WATER"],
    "custom_step":       ["OTHER", "EMPTY", "ALCOHOL", "XYLENE", "CLEAR", "HEMATOXYLIN", "EOSIN", "WATER"],
}

def reagent_category(reagent_id: str) -> str:
    r = STATE["reagents"].get(reagent_id)
    if not r:
        return "UNKNOWN"
    return (r.get("category") or "UNKNOWN").upper()

def slot_assigned_category(slot: str) -> str:
    rid = (STATE["layout"].get(slot) or {}).get("reagent_id", "EMPTY")
    return reagent_category(rid)

def run_check(program_name: str) -> Dict[str, Any]:
    programs = STATE["programs"]
    if program_name not in programs:
        return {"program": program_name, "overall": "BLOCK",
                "findings": [{"code": "E900", "level": "BLOCK", "message": "Program not found", "details": {}}]}

    steps = programs[program_name].get("steps", [])
    findings: List[Dict[str, Any]] = []
    overall = "OK"

    # Track order positions for order rules
    positions: Dict[str, int] = {}

    for idx, st in enumerate(steps, start=1):
        name = (st.get("name") or "").strip()
        slot = (st.get("slot") or "").strip()
        t = int(st.get("time_sec") or 0)

        if not name:
            findings.append({"code": "E910", "level": "BLOCK", "message": "Empty step name", "details": {"step_index": idx}})
            overall = bump(overall, "BLOCK")
            continue

        positions.setdefault(name, idx)

        if slot not in STATE["layout"]:
            findings.append({"code": "E401", "level": "BLOCK", "message": "Unknown slot", "details": {"step": name, "slot": slot}})
            overall = bump(overall, "BLOCK")
            continue

        if t <= 0:
            findings.append({"code": "W910", "level": "WARN", "message": "Time <= 0", "details": {"step": name, "slot": slot, "time_sec": t}})
            overall = bump(overall, "WARN")

        # kind rule
        req_kind = STEP_REQUIRED_KIND.get(name)
        if req_kind:
            actual_kind = SLOT_KIND.get(slot, "reagent")
            if actual_kind != req_kind:
                findings.append({"code": "E402", "level": "BLOCK", "message": "Step on wrong slot kind",
                                 "details": {"step": name, "slot": slot, "required_kind": req_kind, "actual_kind": actual_kind}})
                overall = bump(overall, "BLOCK")

        # category rule
        allowed = STEP_ALLOWED_CATEGORIES.get(name)
        if allowed:
            cat = slot_assigned_category(slot)
            if cat == "EMPTY":
                findings.append({"code": "W401", "level": "WARN", "message": "Slot is Empty", "details": {"step": name, "slot": slot}})
                overall = bump(overall, "WARN")
            elif cat not in allowed and "OTHER" not in allowed:
                findings.append({"code": "E403", "level": "BLOCK", "message": "Reagent category mismatch",
                                 "details": {"step": name, "slot": slot, "slot_category": cat, "allowed": allowed}})
                overall = bump(overall, "BLOCK")

    # Program-specific rules (extendable)
    if program_name == "H&E":
        # must contain rinse
        if not any((s.get("name") == "rinse") for s in steps):
            findings.append({"code": "E202", "level": "BLOCK", "message": "H&E requires rinse step", "details": {}})
            overall = bump(overall, "BLOCK")

        # order: hematoxylin before eosin
        if "hematoxylin" in positions and "eosin" in positions:
            if positions["hematoxylin"] > positions["eosin"]:
                findings.append({"code": "E203", "level": "BLOCK", "message": "H&E order invalid (hematoxylin must be before eosin)",
                                 "details": {"hematoxylin_pos": positions["hematoxylin"], "eosin_pos": positions["eosin"]}})
                overall = bump(overall, "BLOCK")

    return {"program": program_name, "overall": overall, "findings": findings}

# =========================================================
# API models
# =========================================================
class LayoutSaveReq(BaseModel):
    layout: Dict[str, str]  # slot -> reagent_id

class ReagentUpsertReq(BaseModel):
    reagent_id: str
    name: str
    category: str
    color: str

class ReagentDeleteReq(BaseModel):
    reagent_id: str

class ProgramSelectReq(BaseModel):
    program: str

class StepModel(BaseModel):
    name: str
    slot: str
    time_sec: int

class ProgramSaveReq(BaseModel):
    program: str
    steps: List[StepModel]

# =========================================================
# APIs
# =========================================================
@app.get("/api/state")
def api_state():
    return {
        "reagents": STATE["reagents"],
        "layout": STATE["layout"],
        "programs": STATE["programs"],
        "selected_program": STATE["selected_program"],
        "last_check": STATE["last_check"],
    }

@app.post("/api/layout/save")
def api_layout_save(req: LayoutSaveReq):
    for slot, rid in req.layout.items():
        if slot not in STATE["layout"]:
            return JSONResponse({"ok": False, "error": f"Unknown slot {slot}"}, status_code=400)
        if rid not in STATE["reagents"]:
            rid = "EMPTY"
        STATE["layout"][slot]["reagent_id"] = rid
    log("SAVE_LAYOUT", {"n": len(req.layout)})
    persist()
    return {"ok": True}

@app.post("/api/reagents/upsert")
def api_reagents_upsert(req: ReagentUpsertReq):
    rid = (req.reagent_id or "").strip().upper()
    if not rid:
        return JSONResponse({"ok": False, "error": "reagent_id required"}, status_code=400)
    if rid in ("OVEN", "LOAD", "WATER", "EMPTY"):
        # allow edit color/name/category for demo, but keep them present
        pass
    STATE["reagents"][rid] = {
        "id": rid,
        "name": (req.name or "").strip() or rid,
        "category": (req.category or "").strip().upper() or "OTHER",
        "color": clamp_hex(req.color),
    }
    log("UPSERT_REAGENT", {"id": rid})
    persist()
    return {"ok": True}

@app.post("/api/reagents/delete")
def api_reagents_delete(req: ReagentDeleteReq):
    rid = (req.reagent_id or "").strip().upper()
    if rid in ("OVEN", "LOAD", "WATER", "EMPTY"):
        return JSONResponse({"ok": False, "error": "core reagent cannot be deleted"}, status_code=400)
    if rid not in STATE["reagents"]:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    # unassign from layout
    for slot in STATE["layout"].keys():
        if STATE["layout"][slot]["reagent_id"] == rid:
            STATE["layout"][slot]["reagent_id"] = "EMPTY"

    del STATE["reagents"][rid]
    log("DELETE_REAGENT", {"id": rid})
    persist()
    return {"ok": True}

@app.post("/api/program/select")
def api_program_select(req: ProgramSelectReq):
    if req.program not in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "program not found"}, status_code=404)
    STATE["selected_program"] = req.program
    log("SELECT_PROGRAM", {"program": req.program})
    persist()
    return {"ok": True}

@app.post("/api/program/save")
def api_program_save(req: ProgramSaveReq):
    if req.program not in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "program not found"}, status_code=404)
    STATE["programs"][req.program] = {"steps": [s.model_dump() for s in req.steps]}
    log("SAVE_PROGRAM", {"program": req.program, "n_steps": len(req.steps)})
    persist()
    return {"ok": True}

@app.post("/api/check")
def api_check():
    prog = STATE["selected_program"]
    res = run_check(prog)
    STATE["last_check"] = res
    log("CHECK", {"program": prog, "overall": res["overall"], "n": len(res["findings"])})
    persist()
    return res

@app.get("/audit", response_class=PlainTextResponse)
def audit():
    lines = ["AUDIT (last 300)"]
    for e in STATE["audit"][-300:]:
        lines.append(f"{e['t']} | {e['event']} | {e['details']}")
    return PlainTextResponse("\n".join(lines))

# =========================================================
# UI — IFU-like: center baths, right programs panel
# =========================================================
@app.get("/", response_class=HTMLResponse)
def ui():
    def tile(slot: str) -> str:
        return (
            f"<div class='tile' id='tile_{slot}'>"
            f"  <div class='slot'>{slot}</div>"
            f"  <select class='sel' id='sel_{slot}'></select>"
            f"</div>"
        )

    top_html = "".join(tile(s) for s in TOP_ROW)
    bottom_html = "".join(tile(s) for s in BOTTOM_ROW)
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
  --bg:#0b1220; --text:#eaf0ff; --muted:rgba(234,240,255,.75);
  --stroke:rgba(255,255,255,.12); --card:rgba(255,255,255,.04);
  --btn:rgba(255,255,255,.06); --accent:#7aa2ff;
  --ok:#2ecc71; --warn:#f1c40f; --block:#e74c3c;
  --tileW: 102px; /* make narrower here */
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
a{color:var(--accent);text-decoration:none}

.grid{
  display:grid;
  grid-template-columns: 1.35fr .65fr;
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
.sectionTitle{ font-weight:1000; letter-spacing:.3px; font-size:13px; color:var(--muted); margin-bottom:8px; }
.hint{ color:var(--muted); font-size:12px; line-height:1.4; }

.row{
  display:grid;
  grid-auto-flow:column;
  grid-auto-columns: var(--tileW);
  gap:8px;
  overflow-x:auto;
  padding:8px 0;
}
.tile{
  border:1px solid rgba(255,255,255,.12);
  border-radius:14px;
  padding:10px;
  min-height:78px;
  background:rgba(255,255,255,.03);
}
.slot{ font-weight:1000; margin-bottom:6px; font-size:12px; }
.sel{
  width:100%;
  padding:7px 8px;
  border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(0,0,0,.18);
  color:var(--text);
  outline:none;
  font-size:12px;
}
.sel:focus{ border-color:rgba(122,162,255,.55); box-shadow:0 0 0 4px rgba(122,162,255,.12); }

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

.badge{ padding:8px 10px; border-radius:999px; border:1px solid var(--stroke); background:rgba(255,255,255,.04); color:var(--muted); font-size:12px; display:inline-block; }
.badge.ok{ border-color:rgba(46,204,113,.35); background:rgba(46,204,113,.10); color:var(--ok); }
.badge.warn{ border-color:rgba(241,196,15,.35); background:rgba(241,196,15,.10); color:var(--warn); }
.badge.block{ border-color:rgba(231,76,60,.35); background:rgba(231,76,60,.12); color:var(--block); }

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

.item{
  display:flex; justify-content:space-between; align-items:center; gap:10px;
  padding:10px 10px; border-radius:14px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.04);
  font-size:13px;
  margin-bottom:8px;
}
.item.active{ border-color:rgba(122,162,255,.55); background:rgba(122,162,255,.14); }
.smallbtn{ padding:8px 10px; border-radius:12px; font-size:12px; font-weight:900; }

.editorRow{
  display:grid;
  grid-template-columns: 1.15fr .65fr .55fr .25fr;
  gap:8px;
  align-items:center;
  margin-bottom:8px;
}
.editorRow input{
  font-size:12px; padding:7px 8px; border-radius:12px;
  border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.05); color:var(--text);
}
.editorRow .del{
  cursor:pointer; text-align:center;
  border:1px solid rgba(255,255,255,.12);
  border-radius:12px;
  padding:7px 0;
  background:rgba(231,76,60,.10);
}

.formRow{
  display:grid;
  grid-template-columns: 1fr 1fr;
  gap:8px;
  margin-top:10px;
}
.formRow input{
  font-size:12px; padding:7px 8px; border-radius:12px;
  border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.05); color:var(--text);
}
</style>
</head>
<body>

<div class="grid">

  <!-- LEFT: Baths -->
  <div class="card">
    <div class="sectionTitle">IFU Bath layout</div>
    <div class="hint">Top: R1–R7, W1–W5, OVEN • Bottom: R18–R8, LOAD</div>

    <div class="row" id="row_top">__TOP__</div>
    <div class="row" id="row_bottom">__BOTTOM__</div>

    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
      <button onclick="saveLayout()" class="primary">Save layout</button>
      <button onclick="check()" class="primary">Check compatibility</button>
      <a class="badge" href="/audit">Audit</a>
      <span class="badge" id="badge">⚪ no check</span>
    </div>

    <div class="finding" id="msg_left">Ready.</div>
  </div>

  <!-- RIGHT: Programs + Reagents -->
  <div class="card">
    <div class="sectionTitle">Programs (right panel)</div>
    <div id="program_list"></div>

    <div class="sectionTitle">Protocol editor</div>
    <div id="step_editor"></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <button onclick="addStep()">+ Step</button>
      <button onclick="saveProgram()" class="primary">Save program</button>
    </div>

    <div style="height:12px"></div>

    <div class="sectionTitle">Reagents</div>
    <div class="hint">Reagent ID (z.B. ALC70), Name, Kategorie (ALCOHOL/XYLENE/...), Farbe (#RRGGBB)</div>

    <div class="formRow">
      <input id="rg_id" placeholder="Reagent ID (e.g. ALC70)" />
      <input id="rg_name" placeholder="Name" />
      <input id="rg_cat" placeholder="Category (e.g. ALCOHOL)" />
      <input id="rg_col" placeholder="Color (e.g. #a78bfa)" />
    </div>

    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
      <button onclick="upsertReagent()" class="primary">Add/Update reagent</button>
      <button onclick="deleteReagent()">Delete reagent</button>
    </div>

    <div class="finding" id="msg_right">Ready.</div>
  </div>

</div>

<script>
const TOP = __TOPSLOTS__;
const BOTTOM = __BOTTOMSLOTS__;
const ALL = TOP.concat(BOTTOM);

let STATE = null;

function hexToRgba(hex, a){
  const h = (hex||"").replace("#","");
  if(h.length !== 6) return "rgba(148,163,184,"+a+")";
  const r = parseInt(h.slice(0,2),16);
  const g = parseInt(h.slice(2,4),16);
  const b = parseInt(h.slice(4,6),16);
  return `rgba(${r},${g},${b},${a})`;
}

function setBadge(overall){
  const b = document.getElementById('badge');
  b.classList.remove('ok','warn','block');
  if(overall==="OK"){ b.textContent="🟢 OK"; b.classList.add('ok'); }
  else if(overall==="WARN"){ b.textContent="🟡 WARN"; b.classList.add('warn'); }
  else if(overall==="BLOCK"){ b.textContent="🔴 BLOCK"; b.classList.add('block'); }
  else { b.textContent="⚪ no check"; }
}

async function loadState(){
  const r = await fetch('/api/state');
  STATE = await r.json();
  renderBaths();
  renderPrograms();
  renderSteps();
}

function reagentOptionsHtml(selectedId){
  const reag = STATE.reagents;
  const ids = Object.keys(reag).sort();
  return ids.map(id=>{
    const nm = reag[id].name || id;
    const sel = (id===selectedId) ? "selected" : "";
    return `<option value="${id}" ${sel}>${nm} (${id})</option>`;
  }).join("");
}

function applyTileColor(slot, reagentId){
  const tile = document.getElementById('tile_'+slot);
  const r = STATE.reagents[reagentId];
  const col = r ? r.color : "#94a3b8";
  tile.style.background = `linear-gradient(180deg, ${hexToRgba(col,0.30)}, ${hexToRgba(col,0.10)})`;
}

function renderBaths(){
  // fill selects + bind onchange to update color immediately
  ALL.forEach(slot=>{
    const sel = document.getElementById('sel_'+slot);
    const rid = (STATE.layout[slot] && STATE.layout[slot].reagent_id) ? STATE.layout[slot].reagent_id : "EMPTY";
    sel.innerHTML = reagentOptionsHtml(rid);
    applyTileColor(slot, rid);
    sel.onchange = ()=> {
      applyTileColor(slot, sel.value);
      document.getElementById('msg_left').textContent = "Changed " + slot + " -> " + sel.value + " (not saved yet)";
    };
  });
}

function renderPrograms(){
  const wrap = document.getElementById('program_list');
  const names = Object.keys(STATE.programs).sort();
  wrap.innerHTML = "";
  names.forEach(n=>{
    const cls = (n===STATE.selected_program) ? "item active" : "item";
    wrap.insertAdjacentHTML('beforeend', `
      <div class="${cls}">
        <div><b>${n}</b></div>
        <button class="smallbtn" onclick="selectProgram('${n.replace(/'/g,"\\'")}')">Open</button>
      </div>
    `);
  });
}

async function selectProgram(name){
  await fetch('/api/program/select', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({program:name})
  });
  await loadState();
  document.getElementById('msg_right').textContent = "Selected program: " + STATE.selected_program;
}

function renderSteps(){
  const wrap = document.getElementById('step_editor');
  const steps = (STATE.programs[STATE.selected_program] || {steps:[]}).steps || [];
  wrap.innerHTML = "";
  if(steps.length===0){
    wrap.innerHTML = "<div class='hint'>No steps yet. Add a step.</div>";
    return;
  }
  steps.forEach((s, idx)=>{
    wrap.insertAdjacentHTML('beforeend', `
      <div class="editorRow">
        <input id="st_name_${idx}" value="${(s.name||"")}" placeholder="step name"/>
        <input id="st_slot_${idx}" value="${(s.slot||"")}" placeholder="slot (R1/W5/OVEN)"/>
        <input id="st_time_${idx}" type="number" value="${(s.time_sec||0)}" placeholder="sec"/>
        <div class="del" onclick="delStep(${idx})">✕</div>
      </div>
    `);
  });
}

function addStep(){
  const prog = STATE.programs[STATE.selected_program];
  prog.steps = prog.steps || [];
  prog.steps.push({name:"custom_step", slot:"R1", time_sec:60});
  renderSteps();
}

function delStep(idx){
  const prog = STATE.programs[STATE.selected_program];
  prog.steps.splice(idx,1);
  renderSteps();
}

function collectSteps(){
  const prog = STATE.programs[STATE.selected_program];
  const steps = prog.steps || [];
  const out = [];
  for(let i=0;i<steps.length;i++){
    out.push({
      name: (document.getElementById('st_name_'+i).value||"").trim(),
      slot: (document.getElementById('st_slot_'+i).value||"").trim(),
      time_sec: parseInt(document.getElementById('st_time_'+i).value||"0")
    });
  }
  return out;
}

async function saveProgram(){
  const steps = collectSteps();
  const r = await fetch('/api/program/save', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({program: STATE.selected_program, steps: steps})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Program saved ✅";
    await loadState();
  }else{
    document.getElementById('msg_right').textContent = "Save failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function saveLayout(){
  const payload = {layout:{}};
  ALL.forEach(slot=>{
    payload.layout[slot] = document.getElementById('sel_'+slot).value;
  });
  const r = await fetch('/api/layout/save', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_left').textContent = "Layout saved ✅";
    await loadState();
  }else{
    document.getElementById('msg_left').textContent = "Save failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function check(){
  const r = await fetch('/api/check', {method:'POST'});
  const data = await r.json();
  setBadge(data.overall);
  let txt = `${data.program} => ${data.overall}\\n`;
  (data.findings||[]).forEach(f=>{
    txt += `${f.code} | ${f.level} | ${f.message} | ${JSON.stringify(f.details||{})}\\n`;
  });
  if((data.findings||[]).length===0) txt += "No findings.";
  document.getElementById('msg_left').textContent = txt;
}

async function upsertReagent(){
  const id = (document.getElementById('rg_id').value||"").trim().toUpperCase();
  const name = (document.getElementById('rg_name').value||"").trim();
  const category = (document.getElementById('rg_cat').value||"OTHER").trim().toUpperCase();
  const color = (document.getElementById('rg_col').value||"#64748b").trim();
  if(!id){ alert("Reagent ID required"); return; }

  const r = await fetch('/api/reagents/upsert', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({reagent_id:id, name:name, category:category, color:color})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Reagent saved ✅";
    await loadState();
  }else{
    document.getElementById('msg_right').textContent = "Reagent failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function deleteReagent(){
  const id = (document.getElementById('rg_id').value||"").trim().toUpperCase();
  if(!id){ alert("Enter Reagent ID"); return; }
  const r = await fetch('/api/reagents/delete', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({reagent_id:id})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Reagent deleted ✅";
    await loadState();
  }else{
    document.getElementById('msg_right').textContent = "Delete failed ❌ " + JSON.stringify(data, null, 2);
  }
}

loadState();
</script>

</body>
</html>
"""
    html = tpl.replace("__TOP__", top_html).replace("__BOTTOM__", bottom_html).replace("__TOPSLOTS__", top_js).replace("__BOTTOMSLOTS__", bottom_js)
    return HTMLResponse(html)

# Pre-rendered bath HTML (safe)
def bath_tile(slot: str) -> str:
    return (
        f"<div class='tile' id='tile_{slot}'>"
        f"  <div class='slot'>{slot}</div>"
        f"  <select class='sel' id='sel_{slot}'></select>"
        f"</div>"
    )

top_html = "".join(bath_tile(s) for s in TOP_ROW)
bottom_html = "".join(bath_tile(s) for s in BOTTOM_ROW)
