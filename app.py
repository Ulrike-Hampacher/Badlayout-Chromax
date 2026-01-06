from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
import re

app = FastAPI(title="CHROMAX ST Demo — IFU Badlayout + Protokolle + Regeln")

DATA_FILE = Path("chromax_demo_data.json")

# =========================================================
# IFU-LAYOUT (wie von dir beschrieben)
# TOP:    R1..R7, W1..W5, OVEN (oben rechts)
# BOTTOM: OUTPUT + UNLOAD (unten links), R18..R8, LOAD (unten rechts)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 8)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = ["OUTPUT", "UNLOAD"] + [f"R{i}" for i in range(18, 7, -1)] + ["LOAD"]
ALL_SLOTS = TOP_ROW + BOTTOM_ROW
SLOT_POS = {s: i for i, s in enumerate(ALL_SLOTS)}

def safe_read() -> Optional[Dict[str, Any]]:
    try:
        if not DATA_FILE.exists():
            return None
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

def safe_write(data: Dict[str, Any]) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def is_valid_id(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9_\-]{2,32}", s or ""))

def clamp_hex(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    return s if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else ""

SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}
def bump(cur: str, new: str) -> str:
    return new if SEVERITY[new] > SEVERITY[cur] else cur

# =========================================================
# Klassen (vordefiniert, feste Farben)
# =========================================================
DEFAULT_CLASSES: Dict[str, Dict[str, str]] = {
    "EMPTY": {"id":"EMPTY","name":"Empty","color":"#94a3b8"},
    "WATER": {"id":"WATER","name":"Water","color":"#60a5fa"},
    "ALCOHOL": {"id":"ALCOHOL","name":"Alcohol","color":"#a78bfa"},
    "XYLENE": {"id":"XYLENE","name":"Xylene","color":"#fbbf24"},
    "CLEARING": {"id":"CLEARING","name":"Clearing","color":"#f59e0b"},
    "HEMATOXYLIN": {"id":"HEMATOXYLIN","name":"Hematoxylin","color":"#22c55e"},
    "EOSIN": {"id":"EOSIN","name":"Eosin","color":"#fb7185"},
    "OTHER": {"id":"OTHER","name":"Other","color":"#38bdf8"},
    "OVEN": {"id":"OVEN","name":"Oven","color":"#f87171"},
    "IO": {"id":"IO","name":"Load/Unload/Output","color":"#cbd5e1"},
}

DEFAULT_REAGENTS: Dict[str, Dict[str, str]] = {
    "EMPTY": {"id":"EMPTY","name":"Empty","class_id":"EMPTY","override_color":""},
    "H2O": {"id":"H2O","name":"H₂O","class_id":"WATER","override_color":""},
    "XYL": {"id":"XYL","name":"Xylene","class_id":"XYLENE","override_color":""},
    "ALC96": {"id":"ALC96","name":"Alcohol 96%","class_id":"ALCOHOL","override_color":""},
    "HEM": {"id":"HEM","name":"Hematoxylin","class_id":"HEMATOXYLIN","override_color":""},
    "EOS": {"id":"EOS","name":"Eosin","class_id":"EOSIN","override_color":""},
    "CLR": {"id":"CLR","name":"Clearing agent","class_id":"CLEARING","override_color":""},
    "OVEN": {"id":"OVEN","name":"Oven","class_id":"OVEN","override_color":""},
    "LOAD": {"id":"LOAD","name":"Load","class_id":"IO","override_color":""},
    "UNLOAD": {"id":"UNLOAD","name":"Unload","class_id":"IO","override_color":""},
    "OUTPUT": {"id":"OUTPUT","name":"Output","class_id":"IO","override_color":""},
}

def default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"reagent_id": "EMPTY"} for slot in ALL_SLOTS}
    for w in ("W1","W2","W3","W4","W5"):
        d[w] = {"reagent_id": "H2O"}
    d["OVEN"] = {"reagent_id": "OVEN"}
    d["LOAD"] = {"reagent_id": "LOAD"}
    d["UNLOAD"] = {"reagent_id": "UNLOAD"}
    d["OUTPUT"] = {"reagent_id": "OUTPUT"}
    return d

DEFAULT_PROGRAMS: Dict[str, Dict[str, Any]] = {
    "H&E": {"steps": [
        {"name":"deparaffinization","slot":"R1","time_sec":300,"exact":True},
        {"name":"hematoxylin","slot":"R2","time_sec":180,"exact":True},
        {"name":"rinse","slot":"W5","time_sec":60,"exact":False},
        {"name":"eosin","slot":"R3","time_sec":120,"exact":True},
        {"name":"dehydrate","slot":"R4","time_sec":240,"exact":False},
        {"name":"clear","slot":"R5","time_sec":180,"exact":False},
    ]},
}

STATE: Dict[str, Any] = {
    "classes": dict(DEFAULT_CLASSES),
    "reagents": dict(DEFAULT_REAGENTS),
    "layout": default_layout(),
    "programs": dict(DEFAULT_PROGRAMS),
    "selected_program": "H&E",
    "selected_for_run": ["H&E"],
    "w_mode": {"W1": "WATER", "W2": "WATER"},  # WATER oder REAGENT
    "water_flow_l_min": 8.0,
    "last_check": None,
}

def persist():
    safe_write({
        "classes": STATE["classes"],
        "reagents": STATE["reagents"],
        "layout": STATE["layout"],
        "programs": STATE["programs"],
        "selected_program": STATE["selected_program"],
        "selected_for_run": STATE["selected_for_run"],
        "w_mode": STATE["w_mode"],
        "water_flow_l_min": STATE["water_flow_l_min"],
        "last_check": STATE["last_check"],
    })

def load_persisted():
    data = safe_read()
    if not data:
        return
    if isinstance(data.get("classes"), dict):
        STATE["classes"] = data["classes"]
    if isinstance(data.get("reagents"), dict):
        rg = {}
        for rid, r in data["reagents"].items():
            if not isinstance(r, dict):
                continue
            rid2 = (r.get("id") or rid).upper().strip()
            if not is_valid_id(rid2):
                continue
            cid = (r.get("class_id") or "OTHER").upper().strip()
            if cid not in STATE["classes"]:
                cid = "OTHER"
            rg[rid2] = {
                "id": rid2,
                "name": (r.get("name") or rid2),
                "class_id": cid,
                "override_color": clamp_hex(r.get("override_color") or ""),
            }
        for core_id, core in DEFAULT_REAGENTS.items():
            rg.setdefault(core_id, core)
        STATE["reagents"] = rg
    if isinstance(data.get("layout"), dict):
        lay = default_layout()
        for slot, v in data["layout"].items():
            if slot in lay and isinstance(v, dict):
                rid = (v.get("reagent_id") or "EMPTY").upper()
                if rid not in STATE["reagents"]:
                    rid = "EMPTY"
                lay[slot]["reagent_id"] = rid
        STATE["layout"] = lay
    if isinstance(data.get("programs"), dict):
        progs = {}
        for name, pv in data["programs"].items():
            if not isinstance(pv, dict) or not isinstance(pv.get("steps"), list):
                continue
            steps = []
            for s in pv["steps"]:
                if not isinstance(s, dict):
                    continue
                slot = (s.get("slot") or "").strip()
                if slot and slot not in SLOT_POS:
                    continue
                steps.append({
                    "name": (s.get("name") or "").strip(),
                    "slot": slot,
                    "time_sec": int(s.get("time_sec") or 1),
                    "exact": bool(s.get("exact") or False),
                })
            progs[name] = {"steps": steps}
        if progs:
            STATE["programs"] = progs
    sp = data.get("selected_program")
    if isinstance(sp, str) and sp in STATE["programs"]:
        STATE["selected_program"] = sp
    sel = data.get("selected_for_run")
    if isinstance(sel, list):
        cleaned = [x for x in sel if isinstance(x, str) and x in STATE["programs"]]
        STATE["selected_for_run"] = cleaned[:3] if cleaned else ["H&E"]
    wm = data.get("w_mode")
    if isinstance(wm, dict):
        for k in ("W1","W2"):
            v = (wm.get(k) or "WATER").upper()
            if v not in ("WATER","REAGENT"):
                v = "WATER"
            STATE["w_mode"][k] = v
    try:
        wf = data.get("water_flow_l_min")
        if wf is not None:
            STATE["water_flow_l_min"] = float(wf)
    except Exception:
        pass
    if isinstance(data.get("last_check"), dict):
        STATE["last_check"] = data["last_check"]

load_persisted()

# =========================================================
# Layout helpers
# =========================================================
def reagent_of_slot(slot: str) -> str:
    return (STATE["layout"].get(slot) or {}).get("reagent_id", "EMPTY")

def reagent_class(reagent_id: str) -> str:
    r = STATE["reagents"].get(reagent_id)
    return (r.get("class_id") if r else "OTHER") or "OTHER"

def slot_class(slot: str) -> str:
    return reagent_class(reagent_of_slot(slot))

def slot_kind(slot: str) -> str:
    if slot in ("OVEN",):
        return "oven"
    if slot in ("LOAD","UNLOAD","OUTPUT"):
        return "io"
    if slot in ("W3","W4","W5"):
        return "water"
    if slot in ("W1","W2"):
        mode = (STATE["w_mode"].get(slot) or "WATER").upper()
        return "water" if mode == "WATER" else "reagent"
    if slot.startswith("W"):
        return "water"
    return "reagent"

# =========================================================
# Rules / Checks
# =========================================================
WATER_STEPS = {"rinse", "water", "wash"}
OVEN_STEPS = {"oven", "bake", "dry"}

STEP_ALLOWED_CLASSES: Dict[str, List[str]] = {
    "rinse": ["WATER"],
    "water": ["WATER"],
    "wash": ["WATER"],
    "hematoxylin": ["HEMATOXYLIN", "OTHER"],
    "eosin": ["EOSIN", "OTHER"],
    "dehydrate": ["ALCOHOL", "OTHER"],
    "clear": ["XYLENE", "CLEARING", "OTHER"],
    "deparaffinization": ["XYLENE", "CLEARING", "OTHER"],
    "custom_step": ["OTHER","ALCOHOL","XYLENE","CLEARING","HEMATOXYLIN","EOSIN","WATER","EMPTY"],
}

def check_layout_rules(findings: List[Dict[str, Any]]) -> str:
    overall = "OK"
    # W3..W5 always water class
    for w in ("W3","W4","W5"):
        if slot_class(w) != "WATER":
            findings.append({
                "code":"E-WATER-FIXED",
                "level":"BLOCK",
                "message": f"{w} muss WATER-Klasse sein (fixes Wasserbad).",
                "details":{"slot":w,"reagent":reagent_of_slot(w),"class":slot_class(w)}
            })
            overall = bump(overall, "BLOCK")
    # W1/W2 mode
    for w in ("W1","W2"):
        mode = (STATE["w_mode"].get(w) or "WATER").upper()
        if mode == "WATER" and slot_class(w) != "WATER":
            findings.append({
                "code":"E-W12-WATERMODE",
                "level":"BLOCK",
                "message": f"{w} ist WATER-Mode und muss WATER-Klasse enthalten.",
                "details":{"slot":w,"mode":mode,"reagent":reagent_of_slot(w),"class":slot_class(w)}
            })
            overall = bump(overall, "BLOCK")
    # flow warn
    if float(STATE.get("water_flow_l_min") or 0) < 8.0:
        findings.append({
            "code":"W-FLOW",
            "level":"WARN",
            "message":"Water flow < 8 L/min: ggf. Wash-Zeit verlängern.",
            "details":{"water_flow_l_min": STATE.get("water_flow_l_min")}
        })
        overall = bump(overall, "WARN")
    return overall

def check_program(name: str) -> Dict[str, Any]:
    p = STATE["programs"].get(name)
    findings: List[Dict[str, Any]] = []
    overall = "OK"
    if not p:
        return {"program": name, "overall": "BLOCK", "findings":[{"code":"E-NOTFOUND","level":"BLOCK","message":"Program not found","details":{}}]}
    steps = p.get("steps") or []
    if not steps:
        findings.append({"code":"W-EMPTY","level":"WARN","message":"Programm hat keine Steps","details":{}})
        overall = bump(overall, "WARN")

    last_pos = -1
    for i, s in enumerate(steps, start=1):
        step_name = (s.get("name") or "").strip()
        slot = (s.get("slot") or "").strip()
        t = int(s.get("time_sec") or 1)

        if not step_name:
            findings.append({"code":"E-STEP-NAME","level":"BLOCK","message":"Leerer Step-Name","details":{"step":i}})
            overall = bump(overall, "BLOCK")
            continue
        if slot not in SLOT_POS:
            findings.append({"code":"E-SLOT","level":"BLOCK","message":"Ungültiger Slot","details":{"step":i,"slot":slot}})
            overall = bump(overall, "BLOCK")
            continue
        if t <= 0:
            findings.append({"code":"W-TIME","level":"WARN","message":"time_sec <= 0","details":{"step":i,"time_sec":t}})
            overall = bump(overall, "WARN")

        pos = SLOT_POS[slot]
        if pos < last_pos:
            findings.append({"code":"E-REVERSE","level":"BLOCK","message":"Rückwärtsbewegung im Protokoll (nicht erlaubt).",
                             "details":{"step":i,"slot":slot,"pos":pos,"previous_pos":last_pos}})
            overall = bump(overall, "BLOCK")
        last_pos = max(last_pos, pos)

        if step_name in WATER_STEPS:
            if slot_kind(slot) != "water":
                findings.append({"code":"E-WATER-KIND","level":"BLOCK",
                                 "message":"Water-Step muss auf Wasserstation liegen (W1/W2 müssen WATER-Mode sein).",
                                 "details":{"step":i,"slot":slot,"slot_kind":slot_kind(slot),"w_mode":STATE.get("w_mode")}})
                overall = bump(overall, "BLOCK")
            if slot_class(slot) != "WATER":
                findings.append({"code":"E-WATER-CLASS","level":"BLOCK",
                                 "message":"Water-Step benötigt WATER-Klasse im Bad.",
                                 "details":{"step":i,"slot":slot,"reagent":reagent_of_slot(slot),"class":slot_class(slot)}})
                overall = bump(overall, "BLOCK")

        if step_name in OVEN_STEPS and slot != "OVEN":
            findings.append({"code":"E-OVEN","level":"BLOCK","message":"Oven-Step muss auf OVEN liegen.","details":{"step":i,"slot":slot}})
            overall = bump(overall, "BLOCK")

        allowed = STEP_ALLOWED_CLASSES.get(step_name)
        if allowed and step_name not in WATER_STEPS:
            sc = slot_class(slot)
            if sc == "EMPTY":
                findings.append({"code":"W-EMPTY-SLOT","level":"WARN","message":"Slot ist EMPTY.","details":{"step":i,"slot":slot}})
                overall = bump(overall, "WARN")
            elif sc not in allowed:
                findings.append({"code":"E-CLASS","level":"BLOCK","message":"Reagenzklasse passt nicht zum Step.",
                                 "details":{"step":i,"name":step_name,"slot":slot,"slot_class":sc,"allowed":allowed}})
                overall = bump(overall, "BLOCK")

    return {"program": name, "overall": overall, "findings": findings}

def exact_conflict(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> Optional[str]:
    ax = set(s.get("slot") for s in a if s.get("exact") and s.get("slot") in SLOT_POS)
    bx = set(s.get("slot") for s in b if s.get("exact") and s.get("slot") in SLOT_POS)
    both = ax.intersection(bx)
    return sorted(list(both))[0] if both else None

def reverse_conflict(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> Optional[Tuple[str,str]]:
    ao = [s.get("slot") for s in a if s.get("slot") in SLOT_POS]
    bo = [s.get("slot") for s in b if s.get("slot") in SLOT_POS]
    common = [x for x in ao if x in set(bo)]
    for i in range(len(common)):
        for j in range(i+1, len(common)):
            x, y = common[i], common[j]
            if ao.index(x) < ao.index(y) and bo.index(x) > bo.index(y):
                return (x, y)
            if ao.index(x) > ao.index(y) and bo.index(x) < bo.index(y):
                return (x, y)
    return None

def check_multi(selected: List[str]) -> Dict[str, Any]:
    overall = "OK"
    findings: List[Dict[str, Any]] = []
    per_prog: List[Dict[str, Any]] = []

    overall = bump(overall, check_layout_rules(findings))

    for p in selected:
        r = check_program(p)
        per_prog.append(r)
        overall = bump(overall, r["overall"])
        for f in r["findings"]:
            findings.append({**f, "program": p})

    for i in range(len(selected)):
        for j in range(i+1, len(selected)):
            p1, p2 = selected[i], selected[j]
            s1 = (STATE["programs"].get(p1) or {}).get("steps") or []
            s2 = (STATE["programs"].get(p2) or {}).get("steps") or []
            ex = exact_conflict(s1, s2)
            if ex:
                findings.append({"code":"E-EXACT-CONFLICT","level":"BLOCK",
                                 "message":"Exact-Station-Konflikt zwischen Protokollen.",
                                 "details":{"program_1":p1,"program_2":p2,"station":ex},
                                 "program": f"{p1} + {p2}"})
                overall = bump(overall, "BLOCK")
            rv = reverse_conflict(s1, s2)
            if rv:
                findings.append({"code":"E-REVERSE-CONFLICT","level":"BLOCK",
                                 "message":"Reihenfolge-Konflikt (A/B vertauscht) zwischen Protokollen.",
                                 "details":{"program_1":p1,"program_2":p2,"stations":list(rv)},
                                 "program": f"{p1} + {p2}"})
                overall = bump(overall, "BLOCK")

    out = {"overall": overall, "findings": findings, "per_program": per_prog, "selected": selected}
    STATE["last_check"] = out
    persist()
    return out

# =========================================================
# API models
# =========================================================
class LayoutSaveReq(BaseModel):
    layout: Dict[str, str]

class WModeReq(BaseModel):
    W1: str
    W2: str

class WaterFlowReq(BaseModel):
    water_flow_l_min: float

class ReagentUpsertReq(BaseModel):
    reagent_id: str
    name: str
    class_id: str
    override_color: Optional[str] = ""

class ReagentDeleteReq(BaseModel):
    reagent_id: str

class ProgramCreateReq(BaseModel):
    name: str

class ProgramDeleteReq(BaseModel):
    name: str

class ProgramSelectReq(BaseModel):
    name: str

class RunSelectReq(BaseModel):
    selected: List[str]

class StepModel(BaseModel):
    name: str
    slot: str
    time_sec: int = Field(ge=1)
    exact: bool = False

class ProgramSaveReq(BaseModel):
    name: str
    steps: List[StepModel]

# =========================================================
# API endpoints
# =========================================================
@app.get("/api/state")
def api_state():
    return {
        "classes": STATE["classes"],
        "reagents": STATE["reagents"],
        "layout": STATE["layout"],
        "programs": STATE["programs"],
        "selected_program": STATE["selected_program"],
        "selected_for_run": STATE["selected_for_run"],
        "w_mode": STATE["w_mode"],
        "water_flow_l_min": STATE["water_flow_l_min"],
        "last_check": STATE["last_check"],
        "layout_rows": {"top": TOP_ROW, "bottom": BOTTOM_ROW},
    }

@app.post("/api/layout/save")
def api_layout_save(req: LayoutSaveReq):
    for slot, rid in req.layout.items():
        if slot not in STATE["layout"]:
            return JSONResponse({"ok": False, "error": f"Unknown slot {slot}"}, status_code=400)
        rid2 = (rid or "EMPTY").upper()
        if rid2 not in STATE["reagents"]:
            rid2 = "EMPTY"
        STATE["layout"][slot]["reagent_id"] = rid2
    persist()
    return {"ok": True}

@app.post("/api/wmode")
def api_wmode(req: WModeReq):
    w1 = (req.W1 or "WATER").upper()
    w2 = (req.W2 or "WATER").upper()
    if w1 not in ("WATER","REAGENT") or w2 not in ("WATER","REAGENT"):
        return JSONResponse({"ok": False, "error": "W1/W2 must be WATER or REAGENT"}, status_code=400)
    STATE["w_mode"]["W1"] = w1
    STATE["w_mode"]["W2"] = w2
    persist()
    return {"ok": True}

@app.post("/api/waterflow")
def api_waterflow(req: WaterFlowReq):
    try:
        STATE["water_flow_l_min"] = float(req.water_flow_l_min)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid water_flow_l_min"}, status_code=400)
    persist()
    return {"ok": True}

@app.post("/api/reagents/upsert")
def api_reagent_upsert(req: ReagentUpsertReq):
    rid = (req.reagent_id or "").strip().upper()
    if not is_valid_id(rid):
        return JSONResponse({"ok": False, "error": "Invalid reagent_id"}, status_code=400)
    cid = (req.class_id or "OTHER").strip().upper()
    if cid not in STATE["classes"]:
        return JSONResponse({"ok": False, "error": f"Unknown class_id {cid}"}, status_code=400)
    STATE["reagents"][rid] = {
        "id": rid,
        "name": (req.name or rid).strip() or rid,
        "class_id": cid,
        "override_color": clamp_hex(req.override_color or ""),
    }
    persist()
    return {"ok": True}

@app.post("/api/reagents/delete")
def api_reagent_delete(req: ReagentDeleteReq):
    rid = (req.reagent_id or "").strip().upper()
    if rid in ("EMPTY","H2O","OVEN","LOAD","UNLOAD","OUTPUT"):
        return JSONResponse({"ok": False, "error": "Core reagent cannot be deleted"}, status_code=400)
    if rid not in STATE["reagents"]:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    for slot in STATE["layout"]:
        if STATE["layout"][slot]["reagent_id"] == rid:
            STATE["layout"][slot]["reagent_id"] = "EMPTY"
    del STATE["reagents"][rid]
    persist()
    return {"ok": True}

@app.post("/api/program/create")
def api_program_create(req: ProgramCreateReq):
    name = (req.name or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Name required"}, status_code=400)
    if name in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "Already exists"}, status_code=400)
    STATE["programs"][name] = {"steps": []}
    STATE["selected_program"] = name
    persist()
    return {"ok": True}

@app.post("/api/program/delete")
def api_program_delete(req: ProgramDeleteReq):
    name = (req.name or "").strip()
    if name not in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    if len(STATE["programs"]) <= 1:
        return JSONResponse({"ok": False, "error": "Cannot delete last program"}, status_code=400)
    del STATE["programs"][name]
    if STATE["selected_program"] == name:
        STATE["selected_program"] = sorted(STATE["programs"].keys())[0]
    STATE["selected_for_run"] = [x for x in STATE["selected_for_run"] if x != name] or [STATE["selected_program"]]
    persist()
    return {"ok": True}

@app.post("/api/program/select")
def api_program_select(req: ProgramSelectReq):
    name = (req.name or "").strip()
    if name not in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    STATE["selected_program"] = name
    persist()
    return {"ok": True}

@app.post("/api/program/save")
def api_program_save(req: ProgramSaveReq):
    name = (req.name or "").strip()
    if name not in STATE["programs"]:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    STATE["programs"][name] = {"steps": [s.model_dump() for s in req.steps]}
    persist()
    return {"ok": True}

@app.post("/api/run/select")
def api_run_select(req: RunSelectReq):
    selected = [x for x in req.selected if isinstance(x, str) and x in STATE["programs"]][:3]
    if not selected:
        return JSONResponse({"ok": False, "error": "Select at least 1"}, status_code=400)
    STATE["selected_for_run"] = selected
    persist()
    return {"ok": True}

@app.post("/api/check")
def api_check():
    return check_multi(STATE["selected_for_run"])

# =========================================================
# UI
# =========================================================
@app.get("/", response_class=HTMLResponse)
def ui():
    # bewusst ohne f-string, damit nichts aus Versehen "zerbricht"
    html = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CHROMAX ST Demo</title>
<style>
:root{--bg:#0b1220;--text:#eaf0ff;--muted:rgba(234,240,255,.75);--stroke:rgba(255,255,255,.12);--card:rgba(255,255,255,.04);--btn:rgba(255,255,255,.06);--ok:#2ecc71;--warn:#f1c40f;--block:#e74c3c;--tileW:98px;}
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,Arial;margin:0;padding:14px;color:var(--text);
background:radial-gradient(1200px 800px at 20% 0%, rgba(122,162,255,.20), transparent 55%),
radial-gradient(900px 600px at 80% 20%, rgba(46,204,113,.12), transparent 60%),var(--bg);}
.grid{display:grid;grid-template-columns:1.35fr .65fr;gap:12px;}
@media (max-width: 980px){ .grid{grid-template-columns:1fr;} }
.card{border:1px solid var(--stroke);border-radius:16px;background:var(--card);padding:12px;}
.sectionTitle{font-weight:1000;font-size:13px;color:var(--muted);margin-bottom:8px;}
.hint{color:var(--muted);font-size:12px;line-height:1.35;}
.row{display:grid;grid-auto-flow:column;grid-auto-columns:var(--tileW);gap:8px;overflow-x:auto;padding:8px 0;}
.tile{border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:10px;min-height:78px;background:rgba(255,255,255,.03);}
.slot{font-weight:1000;margin-bottom:6px;font-size:12px;}
.sel{width:100%;padding:7px 8px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.18);color:var(--text);outline:none;font-size:12px;}
button{padding:11px 12px;border-radius:14px;border:1px solid var(--stroke);background:var(--btn);color:var(--text);font-weight:1000;font-size:13px;}
button.primary{border-color:rgba(122,162,255,.55);background:rgba(122,162,255,.18);}
.badge{padding:8px 10px;border-radius:999px;border:1px solid var(--stroke);background:rgba(255,255,255,.04);color:var(--muted);font-size:12px;display:inline-block;}
.badge.ok{border-color:rgba(46,204,113,.35);background:rgba(46,204,113,.10);color:var(--ok);}
.badge.warn{border-color:rgba(241,196,15,.35);background:rgba(241,196,15,.10);color:var(--warn);}
.badge.block{border-color:rgba(231,76,60,.35);background:rgba(231,76,60,.12);color:var(--block);}
.box{border:1px solid rgba(255,255,255,.10);border-radius:14px;background:rgba(0,0,0,.12);padding:10px;}
.mono{white-space:pre-wrap;font-family:ui-monospace, Menlo, monospace;font-size:12px;color:var(--muted);}
.formRow{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;}
.formRow input,.formRow select{font-size:12px;padding:7px 8px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);color:var(--text);}
.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.small{font-size:12px;color:var(--muted);}
.table{width:100%;border-collapse:separate;border-spacing:0 8px;}
.table th{font-size:11px;color:var(--muted);text-align:left;}
.table td{padding:0;}
hr{border:none;border-top:1px solid rgba(255,255,255,.10);margin:12px 0;}
</style>
</head>
<body>
<div class="grid">
  <div class="card">
    <div class="sectionTitle">Badlayout (IFU)</div>
    <div class="hint">Oben: R1–R7, W1–W5, OVEN • Unten: OUTPUT/UNLOAD links, R18–R8, LOAD rechts</div>
    <div class="row" id="row_top"></div>
    <div class="row" id="row_bottom"></div>
    <div class="inline" style="margin-top:8px;">
      <button class="primary" onclick="saveLayout()">Save layout</button>
      <button class="primary" onclick="check()">Check compatibility</button>
      <span class="badge" id="badge">⚪ no check</span>
    </div>
    <div class="box mono" id="check_out" style="margin-top:10px;">Ready.</div>
  </div>

  <div class="card">
    <div class="sectionTitle">Reagenz anlegen</div>
    <div class="hint">Reagenz frei, aber Klasse bestimmt die Farbe (optional override_color).</div>
    <div class="formRow">
      <input id="r_id" placeholder="ID z.B. ALC70" />
      <input id="r_name" placeholder="Name z.B. Alcohol 70%" />
      <select id="r_class"></select>
      <input id="r_color" placeholder="override #RRGGBB (optional)" />
      <button class="primary" onclick="saveReagent()">Save</button>
      <button onclick="deleteReagent()">Delete</button>
    </div>

    <hr />

    <div class="sectionTitle">W1/W2 Mode + Water flow</div>
    <div class="formRow">
      <select id="w1_mode"><option value="WATER">W1 = WATER</option><option value="REAGENT">W1 = REAGENT</option></select>
      <select id="w2_mode"><option value="WATER">W2 = WATER</option><option value="REAGENT">W2 = REAGENT</option></select>
      <input id="flow" type="number" step="0.1" />
      <button class="primary" onclick="saveModes()">Save</button>
    </div>

    <hr />

    <div class="sectionTitle">Protocol Editor</div>
    <div class="formRow">
      <select id="p_select"></select>
      <button class="primary" onclick="openProgram()">Open</button>
      <input id="p_new" placeholder="Neues Protokoll (Name)" />
      <button class="primary" onclick="createProgram()">Create</button>
      <button onclick="deleteProgram()">Delete</button>
    </div>

    <div class="box" style="margin-top:10px;">
      <div class="small">Steps</div>
      <table class="table">
        <thead>
          <tr><th style="width:30%;">name</th><th style="width:20%;">slot</th><th style="width:20%;">time_sec</th><th style="width:15%;">exact</th><th style="width:15%;"></th></tr>
        </thead>
        <tbody id="steps_body"></tbody>
      </table>
      <div class="inline">
        <button class="primary" onclick="addStep()">+ Step</button>
        <button class="primary" onclick="saveProgram()">Save protocol</button>
      </div>
    </div>

    <div class="box mono" id="right_out" style="margin-top:10px;">Ready.</div>

    <hr />

    <div class="sectionTitle">Run selection (max 3)</div>
    <div id="run_box" class="small"></div>
    <button class="primary" onclick="saveRun()">Save selection</button>
  </div>
</div>

<script>
let ST = null;

function rgba(hex, a){
  hex = (hex||"").replace("#","");
  if(hex.length!==6) return "rgba(148,163,184,"+a+")";
  const r=parseInt(hex.slice(0,2),16), g=parseInt(hex.slice(2,4),16), b=parseInt(hex.slice(4,6),16);
  return "rgba("+r+","+g+","+b+","+a+")";
}

function classColor(classId){
  const c = (ST.classes[classId]||{}).color || "#94a3b8";
  return c;
}

function reagentColor(reagentId){
  const r = ST.reagents[reagentId] || {};
  const oc = (r.override_color||"").trim();
  if(oc && oc.startsWith("#") && oc.length===7) return oc;
  return classColor(r.class_id || "OTHER");
}

function setBadge(overall){
  const b=document.getElementById("badge");
  b.classList.remove("ok","warn","block");
  if(overall==="OK"){ b.textContent="🟢 OK"; b.classList.add("ok"); }
  else if(overall==="WARN"){ b.textContent="🟡 WARN"; b.classList.add("warn"); }
  else if(overall==="BLOCK"){ b.textContent="🔴 BLOCK"; b.classList.add("block"); }
  else { b.textContent="⚪ no check"; }
}

function tileHtml(slot){
  return '<div class="tile" id="tile_'+slot+'"><div class="slot">'+slot+'</div><select class="sel" id="sel_'+slot+'"></select></div>';
}

function reagentOptions(selectedId){
  const ids = Object.keys(ST.reagents).sort();
  return ids.map(id=>{
    const r=ST.reagents[id];
    const nm=r.name||id;
    const cid=r.class_id||"OTHER";
    const sel=(id===selectedId)?"selected":"";
    return '<option value="'+id+'" '+sel+'>'+nm+' ('+id+') • '+cid+'</option>';
  }).join("");
}

function applyTileColor(slot, reagentId){
  const col = reagentColor(reagentId);
  const tile = document.getElementById("tile_"+slot);
  tile.style.background = "linear-gradient(180deg,"+rgba(col,0.35)+","+rgba(col,0.12)+")";
}

function renderLayout(){
  const top = ST.layout_rows.top;
  const bottom = ST.layout_rows.bottom;
  document.getElementById("row_top").innerHTML = top.map(tileHtml).join("");
  document.getElementById("row_bottom").innerHTML = bottom.map(tileHtml).join("");

  top.concat(bottom).forEach(slot=>{
    const sel = document.getElementById("sel_"+slot);
    const rid = (ST.layout[slot] && ST.layout[slot].reagent_id) ? ST.layout[slot].reagent_id : "EMPTY";
    sel.innerHTML = reagentOptions(rid);
    sel.onchange = ()=> applyTileColor(slot, sel.value);
    applyTileColor(slot, rid);
  });
}

function renderClasses(){
  const sel = document.getElementById("r_class");
  const ids = Object.keys(ST.classes).sort();
  sel.innerHTML = ids.map(id=>{
    const c=ST.classes[id];
    return '<option value="'+id+'">'+c.name+' ('+id+')</option>';
  }).join("");
}

function renderPrograms(){
  const ps = document.getElementById("p_select");
  const names = Object.keys(ST.programs).sort();
  ps.innerHTML = names.map(n=>{
    const sel = (n===ST.selected_program)?"selected":"";
    return '<option value="'+n+'" '+sel+'>'+n+'</option>';
  }).join("");
}

function renderRunBox(){
  const box = document.getElementById("run_box");
  const names = Object.keys(ST.programs).sort();
  const selected = new Set(ST.selected_for_run||[]);
  box.innerHTML = names.map(n=>{
    const checked = selected.has(n) ? "checked" : "";
    return '<label style="display:block;margin:6px 0;"><input type="checkbox" class="run_cb" value="'+n+'" '+checked+'/> '+n+'</label>';
  }).join("");
}

function slotOptions(selected){
  const all = Object.keys(ST.layout);
  return all.map(s=>{
    const sel = (s===selected)?"selected":"";
    return '<option value="'+s+'" '+sel+'>'+s+'</option>';
  }).join("");
}

function renderSteps(){
  const body = document.getElementById("steps_body");
  const p = ST.programs[ST.selected_program] || {steps:[]};
  const steps = p.steps || [];
  body.innerHTML = steps.map((s, i)=>{
    const nm = (s.name||"").replaceAll('"','&quot;');
    const sl = s.slot || "R1";
    const ts = s.time_sec || 60;
    const ex = !!s.exact;
    return '<tr>'
      + '<td><input value="'+nm+'" data-i="'+i+'" data-k="name"/></td>'
      + '<td><select data-i="'+i+'" data-k="slot">'+slotOptions(sl)+'</select></td>'
      + '<td><input type="number" min="1" value="'+ts+'" data-i="'+i+'" data-k="time_sec"/></td>'
      + '<td><select data-i="'+i+'" data-k="exact">'
      + '<option value="false" '+(ex?"":"selected")+'>false</option>'
      + '<option value="true" '+(ex?"selected":"")+'>true</option>'
      + '</select></td>'
      + '<td><button onclick="removeStep('+i+')">Remove</button></td>'
      + '</tr>';
  }).join("");

  Array.from(body.querySelectorAll("input,select")).forEach(el=>{
    el.onchange = ()=>{
      const i = parseInt(el.getAttribute("data-i"));
      const k = el.getAttribute("data-k");
      const prog = ST.programs[ST.selected_program];
      if(!prog.steps) prog.steps=[];
      if(k==="time_sec") prog.steps[i][k] = parseInt(el.value||"1");
      else if(k==="exact") prog.steps[i][k] = (el.value==="true");
      else prog.steps[i][k] = el.value;
    };
  });
}

async function loadState(){
  const r = await fetch("/api/state");
  ST = await r.json();
  document.getElementById("w1_mode").value = (ST.w_mode && ST.w_mode.W1) ? ST.w_mode.W1 : "WATER";
  document.getElementById("w2_mode").value = (ST.w_mode && ST.w_mode.W2) ? ST.w_mode.W2 : "WATER";
  document.getElementById("flow").value = (ST.water_flow_l_min!=null) ? ST.water_flow_l_min : 8.0;
  renderLayout();
  renderClasses();
  renderPrograms();
  renderRunBox();
  renderSteps();
}

async function saveLayout(){
  const payload = {layout:{}};
  Object.keys(ST.layout).forEach(slot=>{
    payload.layout[slot] = document.getElementById("sel_"+slot).value;
  });
  const r = await fetch("/api/layout/save", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  const d = await r.json();
  document.getElementById("check_out").textContent = d.ok ? "Layout saved ✅" : ("Save failed ❌ " + JSON.stringify(d,null,2));
  if(d.ok) await loadState();
}

async function saveModes(){
  const W1 = document.getElementById("w1_mode").value;
  const W2 = document.getElementById("w2_mode").value;
  const water_flow_l_min = parseFloat(document.getElementById("flow").value||"8");
  const r1 = await fetch("/api/wmode", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({W1:W1, W2:W2})});
  const d1 = await r1.json();
  const r2 = await fetch("/api/waterflow", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({water_flow_l_min:water_flow_l_min})});
  const d2 = await r2.json();
  document.getElementById("right_out").textContent = (d1.ok && d2.ok) ? "Saved ✅" : ("Failed ❌ " + JSON.stringify({d1:d1, d2:d2},null,2));
  await loadState();
}

async function saveReagent(){
  const reagent_id = (document.getElementById("r_id").value||"").trim().toUpperCase();
  const name = (document.getElementById("r_name").value||"").trim();
  const class_id = document.getElementById("r_class").value;
  const override_color = (document.getElementById("r_color").value||"").trim();
  const r = await fetch("/api/reagents/upsert", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({reagent_id:reagent_id, name:name, class_id:class_id, override_color:override_color})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Reagent saved ✅" : ("Save failed ❌ " + JSON.stringify(d,null,2));
  if(d.ok){
    document.getElementById("r_id").value="";
    document.getElementById("r_name").value="";
    document.getElementById("r_color").value="";
    await loadState();
  }
}

async function deleteReagent(){
  const reagent_id = (document.getElementById("r_id").value||"").trim().toUpperCase();
  const r = await fetch("/api/reagents/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({reagent_id:reagent_id})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Deleted ✅" : ("Delete failed ❌ " + JSON.stringify(d,null,2));
  if(d.ok) await loadState();
}

async function openProgram(){
  const name = document.getElementById("p_select").value;
  const r = await fetch("/api/program/select", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({name:name})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok===false ? ("Open failed ❌ "+JSON.stringify(d,null,2)) : "Opened ✅";
  await loadState();
}

async function createProgram(){
  const name = (document.getElementById("p_new").value||"").trim();
  const r = await fetch("/api/program/create", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({name:name})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Created ✅" : ("Create failed ❌ "+JSON.stringify(d,null,2));
  if(d.ok) document.getElementById("p_new").value="";
  await loadState();
}

async function deleteProgram(){
  const name = document.getElementById("p_select").value;
  const r = await fetch("/api/program/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({name:name})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Deleted ✅" : ("Delete failed ❌ "+JSON.stringify(d,null,2));
  await loadState();
}

function addStep(){
  const prog = ST.programs[ST.selected_program];
  if(!prog.steps) prog.steps=[];
  prog.steps.push({name:"custom_step", slot:"R1", time_sec:60, exact:false});
  renderSteps();
}

function removeStep(i){
  const prog = ST.programs[ST.selected_program];
  prog.steps.splice(i,1);
  renderSteps();
}

async function saveProgram(){
  const prog = ST.programs[ST.selected_program];
  const payload = {name: ST.selected_program, steps: (prog.steps||[]).map(s=>({
    name: (s.name||"").trim(),
    slot: (s.slot||"").trim(),
    time_sec: parseInt(s.time_sec||1),
    exact: !!s.exact
  }))};
  const r = await fetch("/api/program/save", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Saved ✅" : ("Save failed ❌ "+JSON.stringify(d,null,2));
  await loadState();
}

async function saveRun(){
  const cbs = Array.from(document.querySelectorAll(".run_cb"));
  const selected = cbs.filter(x=>x.checked).map(x=>x.value).slice(0,3);
  const r = await fetch("/api/run/select", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({selected:selected})});
  const d = await r.json();
  document.getElementById("right_out").textContent = d.ok ? "Selection saved ✅" : ("Failed ❌ "+JSON.stringify(d,null,2));
  await loadState();
}

async function check(){
  const r = await fetch("/api/check", {method:"POST"});
  const d = await r.json();
  setBadge(d.overall);
  let txt = "Selected: " + (d.selected||[]).join(", ") + "\\n";
  txt += "OVERALL: " + d.overall + "\\n\\n";
  (d.findings||[]).forEach(f=>{
    txt += (f.program||"") + " | " + f.code + " | " + f.level + " | " + f.message + " | " + JSON.stringify(f.details||{}) + "\\n";
  });
  if((d.findings||[]).length===0) txt += "No findings.";
  document.getElementById("check_out").textContent = txt;
}

loadState();
</script>
</body>
</html>"""
    return HTMLResponse(html)

# wichtig: persist nach Boot, damit Datei existiert
persist()
