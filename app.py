from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from datetime import datetime
import re

app = FastAPI(title="CHROMAX ST Demo — IFU Layout + Programs + Reagents")

# =========================================================
# IFU-like bath slot schema (fixed positions)
# Top:    R1–R9, W1–W5, OVEN
# Bottom: R18–R10, LOAD (bottom-right)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 10)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = [f"R{i}" for i in range(18, 9, -1)] + ["LOAD"]
ALL_SLOTS = TOP_ROW + BOTTOM_ROW

SLOT_KIND: Dict[str, str] = {
    **{f"R{i}": "reagent" for i in range(1, 19)},
    **{f"W{i}": "water" for i in range(1, 6)},
    "OVEN": "oven",
    "LOAD": "load",
}

# =========================================================
# In-memory storage (demo)
# =========================================================
AUDIT: List[Dict[str, Any]] = []
LAST_CHECK: Optional[Dict[str, Any]] = None
LAST_HANDOFF: Optional[Dict[str, Any]] = None

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(event: str, details: Dict[str, Any]):
    AUDIT.append({"t": now(), "event": event, "details": details})
    if len(AUDIT) > 600:
        del AUDIT[:250]

def clamp_hex(color: str) -> str:
    c = (color or "").strip()
    if not c:
        return "#888888"
    if not c.startswith("#"):
        c = "#" + c
    if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        return c
    return "#888888"

# =========================================================
# Reagents catalog (user editable)
# Each reagent has: id, name, category, color
# Categories are also used for compatibility checks.
# =========================================================
DEFAULT_REAGENTS: Dict[str, Dict[str, str]] = {
    "water": {"id": "water", "name": "H₂O", "category": "WATER", "color": "#4aa3ff"},
    "xylene": {"id": "xylene", "name": "Xylene", "category": "XYLENE", "color": "#f5c542"},
    "alcohol96": {"id": "alcohol96", "name": "Alcohol 96%", "category": "ALCOHOL", "color": "#a78bfa"},
    "alcohol100": {"id": "alcohol100", "name": "Alcohol 100%", "category": "ALCOHOL", "color": "#8b5cf6"},
    "hema": {"id": "hema", "name": "Hematoxylin", "category": "HEMATOXYLIN", "color": "#60a5fa"},
    "eosin": {"id": "eosin", "name": "Eosin", "category": "EOSIN", "color": "#fb7185"},
    "clear": {"id": "clear", "name": "Clearing agent", "category": "CLEAR", "color": "#f59e0b"},
    "empty": {"id": "empty", "name": "Empty", "category": "EMPTY", "color": "#64748b"},
    "oven": {"id": "oven", "name": "Oven", "category": "OVEN", "color": "#f87171"},
    "load": {"id": "load", "name": "Load", "category": "LOAD", "color": "#94a3b8"},
}

REAGENTS: Dict[str, Dict[str, str]] = dict(DEFAULT_REAGENTS)

# =========================================================
# Layout assignment per slot (user editable)
# Assign by reagent_id (or free text fallback)
# =========================================================
def default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"reagent_id": "empty", "label": ""} for slot in ALL_SLOTS}
    for w in [f"W{i}" for i in range(1, 6)]:
        d[w] = {"reagent_id": "water", "label": "H₂O"}
    d["OVEN"] = {"reagent_id": "oven", "label": "OVEN"}
    d["LOAD"] = {"reagent_id": "load", "label": "LOAD"}
    return d

LAYOUT: Dict[str, Dict[str, str]] = default_layout()

# =========================================================
# Programs (Protocols)
# Each program: name, steps[]
# Each step: name, slot, time_sec
# =========================================================
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
PROGRAMS: Dict[str, Dict[str, Any]] = dict(DEFAULT_PROGRAMS)
SELECTED_PROGRAM = "H&E"

# =========================================================
# Compatibility rules (extendable)
# - Slot-kind rules: rinse must be on water, oven step on OVEN, etc.
# - Category rules: step expects a reagent category on that slot
# =========================================================
SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

def bump_overall(cur: str, new_level: str) -> str:
    return new_level if SEVERITY[new_level] > SEVERITY[cur] else cur

STEP_REQUIRED_KIND = {
    "rinse": "water",
    "water": "water",
    "oven": "oven",
}

# Step -> allowed reagent categories (you can tune these)
STEP_ALLOWED_CATEGORIES = {
    "deparaffinization": ["XYLENE", "CLEAR", "OTHER"],
    "hematoxylin":       ["HEMATOXYLIN", "OTHER"],
    "eosin":             ["EOSIN", "OTHER"],
    "dehydrate":         ["ALCOHOL", "OTHER"],
    "clear":             ["XYLENE", "CLEAR", "OTHER"],
    "rinse":             ["WATER"],
    "custom_step":       ["OTHER", "EMPTY", "ALCOHOL", "XYLENE", "CLEAR", "HEMATOXYLIN", "EOSIN", "WATER"],
}

def reagent_category_for_slot(slot: str) -> str:
    rid = (LAYOUT.get(slot, {}) or {}).get("reagent_id", "empty")
    r = REAGENTS.get(rid)
    if not r:
        return "UNKNOWN"
    return r.get("category", "UNKNOWN")

def check_program(program_name: str) -> Dict[str, Any]:
    prog = PROGRAMS.get(program_name)
    if not prog:
        return {"program": program_name, "overall": "BLOCK",
                "findings": [{"code": "E900", "level": "BLOCK", "message": "Programm nicht gefunden.", "details": {"program": program_name}}]}

    overall = "OK"
    findings: List[Dict[str, Any]] = []
    steps = prog.get("steps", [])

    for i, st in enumerate(steps, start=1):
        name = (st.get("name") or "").strip()
        slot = (st.get("slot") or "").strip()
        t = int(st.get("time_sec") or 0)

        if not name:
            findings.append({"code": "E910", "level": "BLOCK", "message": "Leerer Schrittname.", "details": {"step_index": i}})
            overall = bump_overall(overall, "BLOCK")
            continue

        if not slot:
            findings.append({"code": "E911", "level": "BLOCK", "message": "Schritt hat keinen Ziel-Slot.", "details": {"step": name, "step_index": i}})
            overall = bump_overall(overall, "BLOCK")
            continue

        if slot not in LAYOUT:
            findings.append({"code": "E401", "level": "BLOCK", "message": "Slot existiert nicht im Layout.", "details": {"step": name, "slot": slot}})
            overall = bump_overall(overall, "BLOCK")
            continue

        if t <= 0:
            findings.append({"code": "W910", "level": "WARN", "message": "Zeit ist 0 oder negativ.", "details": {"step": name, "slot": slot, "time_sec": t}})
            overall = bump_overall(overall, "WARN")

        # Slot kind rule (e.g., rinse must be on water)
        req_kind = STEP_REQUIRED_KIND.get(name)
        actual_kind = SLOT_KIND.get(slot, "reagent")
        if req_kind and actual_kind != req_kind:
            findings.append({"code": "E402", "level": "BLOCK", "message": "Schritt auf falschem Slot-Typ.",
                             "details": {"step": name, "slot": slot, "required_kind": req_kind, "actual_kind": actual_kind}})
            overall = bump_overall(overall, "BLOCK")

        # Category rule (step expects reagent category)
        allowed = STEP_ALLOWED_CATEGORIES.get(name)
        if allowed:
            cat = reagent_category_for_slot(slot)
            if cat == "EMPTY":
                findings.append({"code": "W401", "level": "WARN", "message": "Slot ist als Empty belegt.",
                                 "details": {"slot": slot, "step": name}})
                overall = bump_overall(overall, "WARN")
            elif cat not in allowed:
                findings.append({"code": "E403", "level": "BLOCK", "message": "Reagenz-Kategorie passt nicht zum Schritt.",
                                 "details": {"slot": slot, "step": name, "slot_category": cat, "allowed_categories": allowed}})
                overall = bump_overall(overall, "BLOCK")

    return {"program": program_name, "overall": overall, "findings": findings}

# =========================================================
# API Models
# =========================================================
class LayoutSlotUpdate(BaseModel):
    reagent_id: str
    label: str = ""

class LayoutSaveReq(BaseModel):
    layout: Dict[str, LayoutSlotUpdate]

class ReagentCreateReq(BaseModel):
    id: str
    name: str
    category: str
    color: str

class ReagentDeleteReq(BaseModel):
    id: str

class ProgramStep(BaseModel):
    name: str
    slot: str
    time_sec: int

class ProgramCreateReq(BaseModel):
    name: str

class ProgramRenameReq(BaseModel):
    old_name: str
    new_name: str

class ProgramDeleteReq(BaseModel):
    name: str

class ProgramSaveReq(BaseModel):
    name: str
    steps: List[ProgramStep]

class ProgramSelectReq(BaseModel):
    name: str

# =========================================================
# APIs — Layout
# =========================================================
@app.get("/api/layout")
def api_get_layout():
    return {"layout": LAYOUT}

@app.post("/api/layout/save")
def api_save_layout(req: LayoutSaveReq):
    for slot in req.layout.keys():
        if slot not in LAYOUT:
            return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)
    for slot, item in req.layout.items():
        rid = (item.reagent_id or "").strip()
        if rid not in REAGENTS:
            rid = "empty"
        LAYOUT[slot] = {"reagent_id": rid, "label": (item.label or "").strip()}
    log("SAVE_LAYOUT", {"n": len(req.layout)})
    return {"ok": True}

@app.post("/api/layout/reset")
def api_layout_reset():
    LAYOUT.clear()
    LAYOUT.update(default_layout())
    log("RESET_LAYOUT", {})
    return {"ok": True}

# =========================================================
# APIs — Reagents
# =========================================================
@app.get("/api/reagents")
def api_get_reagents():
    # return sorted by name
    items = sorted(REAGENTS.values(), key=lambda x: x.get("name", ""))
    return {"reagents": items}

@app.post("/api/reagents/create")
def api_create_reagent(req: ReagentCreateReq):
    rid = (req.id or "").strip()
    if not rid:
        return JSONResponse({"ok": False, "error": "id required"}, status_code=400)
    if rid in ("water", "oven", "load"):
        return JSONResponse({"ok": False, "error": "reserved id"}, status_code=400)

    REAGENTS[rid] = {
        "id": rid,
        "name": (req.name or "").strip() or rid,
        "category": (req.category or "").strip().upper() or "OTHER",
        "color": clamp_hex(req.color),
    }
    log("CREATE_REAGENT", {"id": rid})
    return {"ok": True, "reagent": REAGENTS[rid]}

@app.post("/api/reagents/delete")
def api_delete_reagent(req: ReagentDeleteReq):
    rid = (req.id or "").strip()
    if rid in ("water", "oven", "load", "empty"):
        return JSONResponse({"ok": False, "error": "cannot delete core reagent"}, status_code=400)
    if rid not in REAGENTS:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    # unassign from layout
    for slot in list(LAYOUT.keys()):
        if LAYOUT[slot].get("reagent_id") == rid:
            LAYOUT[slot]["reagent_id"] = "empty"
            LAYOUT[slot]["label"] = ""

    del REAGENTS[rid]
    log("DELETE_REAGENT", {"id": rid})
    return {"ok": True}

# =========================================================
# APIs — Programs
# =========================================================
@app.get("/api/programs")
def api_programs():
    return {"selected": SELECTED_PROGRAM, "programs": sorted(list(PROGRAMS.keys()))}

@app.get("/api/program")
def api_program():
    return {"selected": SELECTED_PROGRAM, "program": PROGRAMS.get(SELECTED_PROGRAM, {"steps": []})}

@app.post("/api/program/select")
def api_program_select(req: ProgramSelectReq):
    global SELECTED_PROGRAM
    if req.name not in PROGRAMS:
        return JSONResponse({"ok": False, "error": "program not found"}, status_code=404)
    SELECTED_PROGRAM = req.name
    log("SELECT_PROGRAM", {"name": SELECTED_PROGRAM})
    return {"ok": True, "selected": SELECTED_PROGRAM}

@app.post("/api/program/create")
def api_program_create(req: ProgramCreateReq):
    name = (req.name or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if name in PROGRAMS:
        return JSONResponse({"ok": False, "error": "already exists"}, status_code=400)
    PROGRAMS[name] = {"steps": []}
    log("CREATE_PROGRAM", {"name": name})
    return {"ok": True}

@app.post("/api/program/rename")
def api_program_rename(req: ProgramRenameReq):
    global SELECTED_PROGRAM
    old = (req.old_name or "").strip()
    new = (req.new_name or "").strip()
    if old not in PROGRAMS:
        return JSONResponse({"ok": False, "error": "old program not found"}, status_code=404)
    if not new:
        return JSONResponse({"ok": False, "error": "new name required"}, status_code=400)
    if new in PROGRAMS:
        return JSONResponse({"ok": False, "error": "new name already exists"}, status_code=400)
    PROGRAMS[new] = PROGRAMS.pop(old)
    if SELECTED_PROGRAM == old:
        SELECTED_PROGRAM = new
    log("RENAME_PROGRAM", {"old": old, "new": new})
    return {"ok": True, "selected": SELECTED_PROGRAM}

@app.post("/api/program/delete")
def api_program_delete(req: ProgramDeleteReq):
    global SELECTED_PROGRAM
    name = (req.name or "").strip()
    if name not in PROGRAMS:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if len(PROGRAMS) <= 1:
        return JSONResponse({"ok": False, "error": "cannot delete last program"}, status_code=400)
    del PROGRAMS[name]
    if SELECTED_PROGRAM == name:
        SELECTED_PROGRAM = sorted(list(PROGRAMS.keys()))[0]
    log("DELETE_PROGRAM", {"name": name})
    return {"ok": True, "selected": SELECTED_PROGRAM}

@app.post("/api/program/save")
def api_program_save(req: ProgramSaveReq):
    name = (req.name or "").strip()
    if name not in PROGRAMS:
        return JSONResponse({"ok": False, "error": "program not found"}, status_code=404)
    PROGRAMS[name] = {"steps": [s.model_dump() for s in req.steps]}
    log("SAVE_PROGRAM", {"name": name, "n_steps": len(req.steps)})
    return {"ok": True}

@app.post("/api/program/check")
def api_program_check():
    global LAST_CHECK
    res = check_program(SELECTED_PROGRAM)
    LAST_CHECK = res
    log("CHECK", {"program": SELECTED_PROGRAM, "overall": res["overall"], "n": len(res["findings"])})
    return res

# =========================================================
# APIs — Output / handoff
# =========================================================
@app.post("/api/handoff")
def api_handoff():
    global LAST_HANDOFF
    LAST_HANDOFF = {"t": now(), "to": "coverslipper", "program": SELECTED_PROGRAM, "overall": (LAST_CHECK or {}).get("overall")}
    log("HANDOFF_TO_COVERSLIPPER", LAST_HANDOFF)
    return {"ok": True, "handoff": LAST_HANDOFF}

# =========================================================
# Audit
# =========================================================
@app.get("/audit", response_class=PlainTextResponse)
def audit_page():
    lines = ["AUDIT (last 300)"]
    for e in AUDIT[-300:]:
        lines.append(f"{e['t']} | {e['event']} | {e['details']}")
    return PlainTextResponse("\n".join(lines))

# =========================================================
# Main UI (single page like IFU: baths center, program panel right)
# - tiles are narrower
# - bath tile background uses assigned reagent color (tinted)
# =========================================================
@app.get("/", response_class=HTMLResponse)
def ui():
    def tile(slot: str) -> str:
        kind = SLOT_KIND.get(slot, "reagent")
        # placeholder: JS will set background tint via inline style after loading layout+reagents
        return (
            f"<div class='tile {kind}' id='tile_{slot}'>"
            f"  <div class='slot'>{slot}</div>"
            f"  <select class='sel' id='rid_{slot}'></select>"
            f"  <input class='inp' id='lbl_{slot}' placeholder='Label (optional)' />"
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
  --bg:#0b1220;
  --text:#eaf0ff;
  --muted:rgba(234,240,255,.76);
  --stroke:rgba(255,255,255,.12);
  --card:rgba(255,255,255,.04);
  --btn:rgba(255,255,255,.06);
  --accent:#7aa2ff;
  --ok:#2ecc71; --warn:#f1c40f; --block:#e74c3c;
}
*{box-sizing:border-box}
body{
  font-family:-apple-system,system-ui,Arial;margin:0;padding:14px;color:var(--text);
  background:
    radial-gradient(1200px 800px at 20% 0%, rgba(122,162,255,.20), transparent 55%),
    radial-gradient(900px 600px at 80% 20%, rgba(46,204,113,.12), transparent 60%),
    var(--bg);
}
a{color:var(--accent);text-decoration:none}

/* top bar */
.topbar{
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding:10px 12px; border-radius:16px;
  border:1px solid var(--stroke); background:rgba(255,255,255,.04);
}
.tab{
  padding:10px 12px; border-radius:14px;
  border:1px solid var(--stroke); background:rgba(255,255,255,.05);
  font-weight:1000; font-size:13px;
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
  grid-template-columns: 1.35fr .65fr;
  gap:12px;
}
@media (max-width: 980px){ .grid{grid-template-columns:1fr;} }

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
  grid-auto-columns: 118px;   /* narrower tiles */
  gap:8px;
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
.sel{
  width:100%;
  padding:8px 10px;
  border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.05);
  color:var(--text);
  font-size:12px;
  outline:none;
}
.inp{
  width:100%;
  padding:8px 10px;
  border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.05);
  color:var(--text);
  outline:none;
  font-size:12px;
  margin-top:7px;
}
.sel:focus,.inp:focus{ border-color:rgba(122,162,255,.55); box-shadow:0 0 0 4px rgba(122,162,255,.12); }

/* neutral tint by kind (extra) */
.water{ box-shadow: inset 0 0 0 9999px rgba(120,190,255,.06); }
.oven{ box-shadow: inset 0 0 0 9999px rgba(255,120,120,.06); }
.load{ box-shadow: inset 0 0 0 9999px rgba(255,255,255,.02); }

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

/* program panel */
.list{ display:flex; flex-direction:column; gap:8px; }
.item{
  display:flex; justify-content:space-between; align-items:center; gap:10px;
  padding:10px 10px; border-radius:14px;
  border:1px solid rgba(255,255,255,.10);
  background:rgba(255,255,255,.04);
  font-size:13px;
}
.item.active{ border-color:rgba(122,162,255,.55); background:rgba(122,162,255,.14); }
.smallbtn{ padding:8px 10px; border-radius:12px; font-size:12px; font-weight:900; }

.editorRow{
  display:grid;
  grid-template-columns: 1.15fr .75fr .55fr .28fr;
  gap:8px;
  align-items:center;
}
.editorRow input{
  font-size:12px; padding:8px 10px; border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.05); color:var(--text); outline:none;
}
.editorRow .del{
  cursor:pointer; text-align:center;
  border:1px solid rgba(255,255,255,.12);
  border-radius:12px; padding:8px 0;
  background:rgba(231,76,60,.10);
}

/* output panel bottom-left */
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
      <a class="badge" href="/audit">Audit</a>
    </div>
  </div>

  <div class="grid">

    <!-- LEFT: Baths + Output -->
    <div class="card">
      <div class="sectionTitle">Bath layout (IFU schema) — reagents + colors</div>
      <div class="hint">Oben: R1–R9, W1–W5, OVEN • Unten: R18–R10, LOAD • Farbe kommt vom Reagenz (editierbar)</div>

      <div class="row" id="row_top">__TOP__</div>
      <div class="row" id="row_bottom">__BOTTOM__</div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
        <button onclick="loadAll()">Reload</button>
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

    <!-- RIGHT: Programs + Reagents -->
    <div class="card">
      <div class="sectionTitle">Programs / Protocol editor (right panel)</div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
        <input id="new_prog_name" placeholder="New program name" style="flex:1; min-width:180px;" />
        <button onclick="createProgram()">Create</button>
      </div>

      <div class="list" id="program_list"></div>

      <div style="height:10px"></div>

      <div class="sectionTitle">Steps (editable)</div>
      <div class="hint">Slot z.B. R1, W5, OVEN. Zeit in Sekunden.</div>
      <div id="step_editor"></div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
        <button onclick="addStep()">+ Step</button>
        <button class="primary" onclick="saveProgram()">Save program</button>
        <button class="primary" onclick="checkProgram()">Check compatibility</button>
      </div>

      <div style="height:14px"></div>

      <div class="sectionTitle">Reagents (create / delete)</div>
      <div class="hint">Lege Reagenzien an (Name, Kategorie, Farbe). Farbe als Hex z.B. #ff3366.</div>

      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px;">
        <input id="r_id" placeholder="id (z.B. alc70)" />
        <input id="r_name" placeholder="name (z.B. Alcohol 70%)" />
        <input id="r_cat" placeholder="category (ALCOHOL, XYLENE, ...)" />
        <input id="r_col" placeholder="color (#RRGGBB)" />
      </div>
      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
        <button class="primary" onclick="createReagent()">Create reagent</button>
        <button class="danger" onclick="deleteReagent()">Delete by id</button>
      </div>

      <div class="finding" id="msg_right">Ready.</div>
    </div>

  </div>

<script>
const TOP = __TOPSLOTS__;
const BOTTOM = __BOTTOMSLOTS__;
const ALL = TOP.concat(BOTTOM);

let reagents = [];
let reagMap = {};   // id -> reagent
let programs = [];
let selected = null;
let currentProgram = null;

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

function tintTile(slot, reagentId){
  const tile = document.getElementById('tile_'+slot);
  if(!tile) return;
  const r = reagMap[reagentId];
  const col = r ? r.color : "#64748b";
  // subtle tint using box-shadow overlay
  tile.style.boxShadow = "inset 0 0 0 9999px " + hexToRgba(col, 0.12);
}

function hexToRgba(hex, a){
  const h = (hex||"").replace("#","");
  if(h.length !== 6) return "rgba(100,116,139," + a + ")";
  const r = parseInt(h.slice(0,2),16);
  const g = parseInt(h.slice(2,4),16);
  const b = parseInt(h.slice(4,6),16);
  return `rgba(${r},${g},${b},${a})`;
}

async function loadReagents(){
  const r = await fetch('/api/reagents');
  const data = await r.json();
  reagents = data.reagents || [];
  reagMap = {};
  reagents.forEach(x => { reagMap[x.id] = x; });

  // fill all selects
  ALL.forEach(slot=>{
    const sel = document.getElementById('rid_'+slot);
    if(!sel) return;
    sel.innerHTML = "";
    reagents.forEach(rg=>{
      const opt = document.createElement("option");
      opt.value = rg.id;
      opt.textContent = rg.name;
      sel.appendChild(opt);
    });
  });
}

async function loadLayout(){
  const r = await fetch('/api/layout');
  const data = await r.json();
  const layout = data.layout || {};
  ALL.forEach(slot=>{
    const sel = document.getElementById('rid_'+slot);
    const lbl = document.getElementById('lbl_'+slot);
    const item = layout[slot] || {reagent_id:"empty", label:""};
    if(sel) sel.value = item.reagent_id || "empty";
    if(lbl) lbl.value = item.label || "";
    tintTile(slot, item.reagent_id || "empty");
  });
}

async function loadPrograms(){
  const r = await fetch('/api/programs');
  const data = await r.json();
  programs = data.programs || [];
  selected = data.selected;
  renderProgramList();
  await loadSelectedProgram();
}

async function loadSelectedProgram(){
  const r = await fetch('/api/program');
  const data = await r.json();
  selected = data.selected;
  currentProgram = data.program || {steps:[]};
  renderProgramList();
  renderStepEditor();
}

function renderProgramList(){
  const wrap = document.getElementById('program_list');
  wrap.innerHTML = "";
  programs.forEach(name=>{
    const cls = (name === selected) ? "item active" : "item";
    const html = `
      <div class="${cls}">
        <div><b>${name}</b></div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;">
          <button class="smallbtn" onclick="selectProgram('${name.replace(/'/g,"\\'")}')">Open</button>
          <button class="smallbtn" onclick="renameProgramPrompt('${name.replace(/'/g,"\\'")}')">Rename</button>
          <button class="smallbtn" onclick="deleteProgram('${name.replace(/'/g,"\\'")}')">Delete</button>
        </div>
      </div>
    `;
    wrap.insertAdjacentHTML('beforeend', html);
  });
}

async function selectProgram(name){
  const r = await fetch('/api/program/select', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:name})
  });
  const data = await r.json();
  if(data.ok){
    selected = data.selected;
    await loadPrograms();
    document.getElementById('msg_right').textContent = "Selected: " + selected;
  }else{
    document.getElementById('msg_right').textContent = "Select failed ❌ " + JSON.stringify(data);
  }
}

async function createProgram(){
  const name = (document.getElementById('new_prog_name').value||"").trim();
  if(!name){ alert("Please enter a name"); return; }
  const r = await fetch('/api/program/create', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:name})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('new_prog_name').value = "";
    await loadPrograms();
    document.getElementById('msg_right').textContent = "Program created ✅";
  }else{
    document.getElementById('msg_right').textContent = "Create failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function renameProgramPrompt(oldName){
  const newName = prompt("New name for program:", oldName);
  if(!newName) return;
  const r = await fetch('/api/program/rename', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({old_name: oldName, new_name: newName})
  });
  const data = await r.json();
  if(data.ok){
    await loadPrograms();
    document.getElementById('msg_right').textContent = "Renamed ✅";
  }else{
    document.getElementById('msg_right').textContent = "Rename failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function deleteProgram(name){
  if(!confirm("Delete program '" + name + "'?")) return;
  const r = await fetch('/api/program/delete', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:name})
  });
  const data = await r.json();
  if(data.ok){
    await loadPrograms();
    document.getElementById('msg_right').textContent = "Deleted ✅";
  }else{
    document.getElementById('msg_right').textContent = "Delete failed ❌ " + JSON.stringify(data, null, 2);
  }
}

function renderStepEditor(){
  const wrap = document.getElementById('step_editor');
  wrap.innerHTML = "";
  const steps = (currentProgram && currentProgram.steps) ? currentProgram.steps : [];
  if(steps.length === 0){
    wrap.innerHTML = "<div class='hint'>No steps yet. Click + Step.</div>";
    return;
  }
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

function collectSteps(){
  const steps = (currentProgram && currentProgram.steps) ? currentProgram.steps : [];
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
    body: JSON.stringify({name:selected, steps:steps})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Program saved ✅";
    await loadSelectedProgram();
  }else{
    document.getElementById('msg_right').textContent = "Save failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function checkProgram(){
  const r = await fetch('/api/program/check', {method:'POST'});
  const data = await r.json();
  setBadge(data.overall);
  let txt = data.program + " => " + data.overall + "\\n";
  (data.findings||[]).forEach(f=>{
    txt += `${f.code} | ${f.level} | ${f.message} | ${JSON.stringify(f.details||{})}\\n`;
  });
  if((data.findings||[]).length===0) txt += "No findings.";
  document.getElementById('msg_right').textContent = txt;
}

async function saveLayout(){
  document.getElementById('msg_left').textContent = "Saving layout...";
  const payload = {layout:{}};
  ALL.forEach(slot=>{
    payload.layout[slot] = {
      reagent_id: (document.getElementById('rid_'+slot).value || "empty"),
      label: (document.getElementById('lbl_'+slot).value || "").trim()
    };
  });
  const r = await fetch('/api/layout/save', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_left').textContent = "Layout saved ✅";
    await loadLayout();
  }else{
    document.getElementById('msg_left').textContent = "Save failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function resetLayout(){
  if(!confirm("Reset layout to default?")) return;
  const r = await fetch('/api/layout/reset', {method:'POST'});
  const data = await r.json();
  if(data.ok){
    await loadLayout();
    document.getElementById('msg_left').textContent = "Reset ✅";
  }
}

async function createReagent(){
  const id = (document.getElementById('r_id').value||"").trim();
  const name = (document.getElementById('r_name').value||"").trim();
  const cat = (document.getElementById('r_cat').value||"OTHER").trim();
  const col = (document.getElementById('r_col').value||"#888888").trim();
  if(!id){ alert("id required"); return; }
  const r = await fetch('/api/reagents/create', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id:id, name:name, category:cat, color:col})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Reagent created ✅";
    await loadAll();
  }else{
    document.getElementById('msg_right').textContent = "Create failed ❌ " + JSON.stringify(data, null, 2);
  }
}

async function deleteReagent(){
  const id = (document.getElementById('r_id').value||"").trim();
  if(!id){ alert("Enter id"); return; }
  if(!confirm("Delete reagent '" + id + "'?")) return;
  const r = await fetch('/api/reagents/delete', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id:id})
  });
  const data = await r.json();
  if(data.ok){
    document.getElementById('msg_right').textContent = "Reagent deleted ✅";
    await loadAll();
  }else{
    document.getElementById('msg_right').textContent = "Delete failed ❌ " + JSON.stringify(data, null, 2);
  }
}

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

async function loadAll(){
  document.getElementById('msg_left').textContent = "Loading...";
  await loadReagents();
  await loadLayout();
  await loadPrograms();
  document.getElementById('msg_left').textContent = "Ready.";
}

// initial
loadAll();
</script>

</body>
</html>
"""
    html = tpl.replace("__TOP__", top_html) \
              .replace("__BOTTOM__", bottom_html) \
              .replace("__TOPSLOTS__", top_js) \
              .replace("__BOTTOMSLOTS__", bottom_js)
    return HTMLResponse(html)

# Boot audit
log("READY", {})
