Verstanden: W1 und W2 sind nicht immer „fixe Wasserbäder“, sondern können optional als „R-Stationen“ (Reagenzstationen) genutzt werden. Dann dürfen sie nicht mehr WATER-Klasse erzwingen.

Ich ändere die Logik so:
	•	W3–W5 bleiben immer WATER (BLOCK wenn nicht WATER).
	•	W1–W2 sind „Mode-switch“:
	•	Mode = WATER → muss WATER-Klasse enthalten
	•	Mode = REAGENT → darf jede Reagenzklasse enthalten (wie R-Station)
	•	UI: neben dem Layout gibt’s einen kleinen Schalter W1/W2 Mode: WATER/REAGENT
	•	Check-Regeln berücksichtigen das:
	•	Wenn ein Schritt „rinse/wash/water“ auf W1 oder W2 liegt, dann muss W1/W2 Mode = WATER sein, sonst BLOCK.
	•	Wenn W1/W2 Mode = REAGENT, dann gelten für W1/W2 die gleichen Klassenregeln wie bei R-Slots.

Hier ist der Patch als kompletter neuer app.py (damit du nicht wieder in Klammer-Fehler läufst). Du kannst den von der letzten Version 1:1 ersetzen.

⸻

✅ Neuer kompletter app.py (mit W1/W2 umschaltbar)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import json, re

app = FastAPI(title="CHROMAX ST Demo — IFU compatibility + W1/W2 switchable")

DATA_FILE = Path("chromax_demo_data.json")

# =========================================================
# IFU layout order (your screenshot)
# TOP:    R1..R7, W1..W5, OVEN (top-right)
# BOTTOM: R18..R8, LOAD (bottom-right)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 8)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = [f"R{i}" for i in range(18, 7, -1)] + ["LOAD"]
ALL_SLOTS = TOP_ROW + BOTTOM_ROW

TRANSPORT_ORDER = TOP_ROW + BOTTOM_ROW
SLOT_POS = {s: i for i, s in enumerate(TRANSPORT_ORDER)}

# Default physical kinds; W1/W2 can be overridden by STATE["w_mode"]
SLOT_KIND_BASE: Dict[str, str] = {
    **{f"R{i}": "reagent" for i in range(1, 19)},
    **{f"W{i}": "water" for i in range(1, 6)},
    "OVEN": "oven",
    "LOAD": "load",
}

# =========================================================
# helpers
# =========================================================
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def is_valid_id(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9_\-]{2,32}", s or ""))

def clamp_hex(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    return s if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else ""

def safe_read() -> Optional[Dict[str, Any]]:
    try:
        if not DATA_FILE.exists():
            return None
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

def safe_write(data: Dict[str, Any]) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}
def bump(cur: str, new: str) -> str:
    return new if SEVERITY[new] > SEVERITY[cur] else cur

# =========================================================
# predefined classes (colors fixed by class)
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
    "LOAD": {"id":"LOAD","name":"Load","color":"#cbd5e1"},
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
    "LOAD": {"id":"LOAD","name":"Load","class_id":"LOAD","override_color":""},
}

def default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"reagent_id": "EMPTY"} for slot in ALL_SLOTS}
    for w in [f"W{i}" for i in range(1, 6)]:
        d[w] = {"reagent_id": "H2O"}
    d["OVEN"] = {"reagent_id": "OVEN"}
    d["LOAD"] = {"reagent_id": "LOAD"}
    return d

DEFAULT_PROGRAMS: Dict[str, Dict[str, Any]] = {
    "H&E": {
        "steps": [
            {"name":"deparaffinization","slot":"R1","time_sec":300,"exact":True},
            {"name":"hematoxylin","slot":"R2","time_sec":180,"exact":True},
            {"name":"rinse","slot":"W5","time_sec":60,"exact":False},
            {"name":"eosin","slot":"R3","time_sec":120,"exact":True},
            {"name":"dehydrate","slot":"R4","time_sec":240,"exact":False},
            {"name":"clear","slot":"R5","time_sec":180,"exact":False},
        ]
    },
    "PAP": {"steps":[{"name":"custom_step","slot":"R6","time_sec":60,"exact":False}]},
    "CELLPROG": {"steps":[{"name":"custom_step","slot":"R7","time_sec":60,"exact":False}]},
}

STATE: Dict[str, Any] = {
    "classes": dict(DEFAULT_CLASSES),
    "reagents": dict(DEFAULT_REAGENTS),
    "layout": default_layout(),
    "programs": dict(DEFAULT_PROGRAMS),
    "selected_program": "H&E",
    "selected_for_run": ["H&E"],
    "water_flow_l_min": 8.0,
    # NEW: W1/W2 mode
    # "WATER" => behaves as water slot
    # "REAGENT" => behaves like R-station (reagent slot)
    "w_mode": {"W1": "WATER", "W2": "WATER"},
    "last_check": None,
    "audit": [],
}

def persist():
    safe_write({
        "classes": STATE["classes"],
        "reagents": STATE["reagents"],
        "layout": STATE["layout"],
        "programs": STATE["programs"],
        "selected_program": STATE["selected_program"],
        "selected_for_run": STATE["selected_for_run"],
        "water_flow_l_min": STATE["water_flow_l_min"],
        "w_mode": STATE["w_mode"],
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
            rid2 = (r.get("id") or rid).upper()
            if not is_valid_id(rid2):
                continue
            cid = (r.get("class_id") or "OTHER").upper()
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
                steps.append({
                    "name": (s.get("name") or "").strip(),
                    "slot": (s.get("slot") or "").strip(),
                    "time_sec": int(s.get("time_sec") or 0),
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

    wf = data.get("water_flow_l_min")
    try:
        if wf is not None:
            STATE["water_flow_l_min"] = float(wf)
    except Exception:
        pass

    wm = data.get("w_mode")
    if isinstance(wm, dict):
        for k in ("W1", "W2"):
            v = (wm.get(k) or "WATER").upper()
            if v not in ("WATER", "REAGENT"):
                v = "WATER"
            STATE["w_mode"][k] = v

def log(event: str, details: Dict[str, Any]):
    STATE["audit"].append({"t": now(), "event": event, "details": details})
    if len(STATE["audit"]) > 800:
        del STATE["audit"][:300]

load_persisted()
log("BOOT", {"layout": "IFU", "slots": len(ALL_SLOTS), "w_mode": STATE["w_mode"]})

# =========================================================
# slot kind with W1/W2 override
# =========================================================
def slot_kind(slot: str) -> str:
    if slot in ("W1", "W2"):
        mode = (STATE.get("w_mode", {}).get(slot) or "WATER").upper()
        return "water" if mode == "WATER" else "reagent"
    return SLOT_KIND_BASE.get(slot, "reagent")

# =========================================================
# class / reagent helpers
# =========================================================
def reagent_of_slot(slot: str) -> str:
    return (STATE["layout"].get(slot) or {}).get("reagent_id", "EMPTY")

def reagent_class(reagent_id: str) -> str:
    r = STATE["reagents"].get(reagent_id)
    return (r.get("class_id") if r else "OTHER") or "OTHER"

def slot_class(slot: str) -> str:
    return reagent_class(reagent_of_slot(slot))

# =========================================================
# RULES
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

def check_layout_water_rules(findings: List[Dict[str, Any]]) -> str:
    overall = "OK"

    # W3..W5 always water
    for w in ("W3", "W4", "W5"):
        if slot_class(w) != "WATER":
            findings.append({
                "code": "E-WATER-CLASS",
                "level": "BLOCK",
                "message": f"{w} must contain WATER class reagent",
                "details": {"slot": w, "slot_class": slot_class(w), "reagent": reagent_of_slot(w)}
            })
            overall = bump(overall, "BLOCK")

    # W1/W2: depends on mode
    for w in ("W1", "W2"):
        mode = (STATE["w_mode"].get(w) or "WATER").upper()
        if mode == "WATER":
            if slot_class(w) != "WATER":
                findings.append({
                    "code": "E-W12-WATER",
                    "level": "BLOCK",
                    "message": f"{w} is in WATER mode and must contain WATER class reagent",
                    "details": {"slot": w, "mode": mode, "slot_class": slot_class(w), "reagent": reagent_of_slot(w)}
                })
                overall = bump(overall, "BLOCK")
        else:
            # REAGENT mode: no water-class requirement
            pass

    # flow warning
    if float(STATE.get("water_flow_l_min") or 0) < 8.0:
        findings.append({
            "code": "W-WATER-FLOW",
            "level": "WARN",
            "message": "Water flow < 8 L/min: wash time may need extension",
            "details": {"water_flow_l_min": STATE.get("water_flow_l_min")}
        })
        overall = bump(overall, "WARN")

    return overall

def check_program(program_name: str) -> Dict[str, Any]:
    p = STATE["programs"].get(program_name)
    findings: List[Dict[str, Any]] = []
    overall = "OK"

    if not p:
        return {"program": program_name, "overall": "BLOCK",
                "findings":[{"code":"E-NOTFOUND","level":"BLOCK","message":"Program not found","details":{}}]}

    steps = p.get("steps") or []
    if not steps:
        findings.append({"code":"W-EMPTY-PROG","level":"WARN","message":"Program has no steps","details":{}})
        overall = bump(overall, "WARN")

    # not backwards across transport order
    last_pos = -1
    for i, s in enumerate(steps, start=1):
        name = (s.get("name") or "").strip()
        slot = (s.get("slot") or "").strip()
        t = int(s.get("time_sec") or 0)

        if not name:
            findings.append({"code":"E-STEP-NAME","level":"BLOCK","message":"Empty step name","details":{"step": i}})
            overall = bump(overall, "BLOCK")
            continue

        if slot not in SLOT_POS:
            findings.append({"code":"E-SLOT","level":"BLOCK","message":"Unknown slot","details":{"step": i,"slot": slot}})
            overall = bump(overall, "BLOCK")
            continue

        if t <= 0:
            findings.append({"code":"W-TIME","level":"WARN","message":"Time <= 0","details":{"step": i,"slot": slot,"time_sec": t}})
            overall = bump(overall, "WARN")

        # reverse rule
        pos = SLOT_POS[slot]
        if pos < last_pos:
            findings.append({
                "code": "E-REVERSE",
                "level": "BLOCK",
                "message": "Program contains stations in reverse order (not allowed)",
                "details": {"step": i, "slot": slot, "pos": pos, "previous_pos": last_pos}
            })
            overall = bump(overall, "BLOCK")
        last_pos = max(last_pos, pos)

        # water step must be on a slot that is CURRENTLY water-kind
        if name in WATER_STEPS:
            if slot_kind(slot) != "water":
                findings.append({
                    "code":"E-KIND-WATER",
                    "level":"BLOCK",
                    "message":"Water step must be on a water slot (W-mode must be WATER)",
                    "details":{"step":i,"name":name,"slot":slot,"slot_kind":slot_kind(slot),"w_mode":STATE.get("w_mode",{})}
                })
                overall = bump(overall, "BLOCK")
            # and class must be water
            if slot_class(slot) != "WATER":
                findings.append({
                    "code":"E-CLASS-WATER",
                    "level":"BLOCK",
                    "message":"Water step requires WATER class in that station",
                    "details":{"step":i,"slot":slot,"slot_class":slot_class(slot),"reagent":reagent_of_slot(slot)}
                })
                overall = bump(overall, "BLOCK")

        # oven step on OVEN
        if name in OVEN_STEPS and slot != "OVEN":
            findings.append({"code":"E-KIND-OVEN","level":"BLOCK","message":"Oven step must be on OVEN",
                             "details":{"step":i,"name":name,"slot":slot}})
            overall = bump(overall, "BLOCK")

        # general class check
        allowed = STEP_ALLOWED_CLASSES.get(name)
        if allowed and name not in WATER_STEPS:
            sc = slot_class(slot)
            if sc == "EMPTY":
                findings.append({"code":"W-EMPTY-SLOT","level":"WARN","message":"Slot is EMPTY",
                                 "details":{"step":i,"slot":slot}})
                overall = bump(overall, "WARN")
            elif sc not in allowed:
                findings.append({"code":"E-CLASS","level":"BLOCK","message":"Reagent class mismatch",
                                 "details":{"step":i,"name":name,"slot":slot,"slot_class":sc,"allowed":allowed}})
                overall = bump(overall, "BLOCK")

    return {"program": program_name, "overall": overall, "findings": findings}

def exact_station_conflict(p1_steps: List[Dict[str, Any]], p2_steps: List[Dict[str, Any]]) -> Optional[str]:
    p1_exact = set(s.get("slot") for s in p1_steps if s.get("exact") and s.get("slot") in SLOT_POS)
    p2_exact = set(s.get("slot") for s in p2_steps if s.get("exact") and s.get("slot") in SLOT_POS)
    both = p1_exact.intersection(p2_exact)
    return sorted(list(both))[0] if both else None

def reverse_order_conflict(p1_steps: List[Dict[str, Any]], p2_steps: List[Dict[str, Any]]) -> Optional[Tuple[str,str]]:
    p1_order = [s.get("slot") for s in p1_steps if (s.get("slot") in SLOT_POS)]
    p2_order = [s.get("slot") for s in p2_steps if (s.get("slot") in SLOT_POS)]
    common = [s for s in p1_order if s in set(p2_order)]
    for i in range(len(common)):
        for j in range(i+1, len(common)):
            a, b = common[i], common[j]
            if p1_order.index(a) < p1_order.index(b) and p2_order.index(a) > p2_order.index(b):
                return (a,b)
            if p1_order.index(a) > p1_order.index(b) and p2_order.index(a) < p2_order.index(b):
                return (a,b)
    return None

def check_multi(selected: List[str]) -> Dict[str, Any]:
    overall = "OK"
    findings: List[Dict[str, Any]] = []
    per_program: List[Dict[str, Any]] = []

    overall = bump(overall, check_layout_water_rules(findings))

    for p in selected:
        r = check_program(p)
        per_program.append(r)
        overall = bump(overall, r["overall"])
        for f in r["findings"]:
            findings.append({**f, "program": p})

    for i in range(len(selected)):
        for j in range(i+1, len(selected)):
            p1, p2 = selected[i], selected[j]
            s1 = (STATE["programs"].get(p1) or {}).get("steps") or []
            s2 = (STATE["programs"].get(p2) or {}).get("steps") or []

            ex = exact_station_conflict(s1, s2)
            if ex:
                findings.append({
                    "code": "E-EXACT-CONFLICT",
                    "level": "BLOCK",
                    "message": "Exact station conflict between programs",
                    "details": {"program_1": p1, "program_2": p2, "station": ex},
                    "program": f"{p1} + {p2}",
                })
                overall = bump(overall, "BLOCK")

            rev = reverse_order_conflict(s1, s2)
            if rev:
                findings.append({
                    "code": "E-REVERSE-CONFLICT",
                    "level": "BLOCK",
                    "message": "Reverse station order conflict between programs",
                    "details": {"program_1": p1, "program_2": p2, "stations": list(rev)},
                    "program": f"{p1} + {p2}",
                })
                overall = bump(overall, "BLOCK")

    STATE["last_check"] = {"overall": overall, "findings": findings, "per_program": per_program, "selected": selected}
    persist()
    log("CHECK", {"selected": selected, "overall": overall, "n": len(findings)})
    return STATE["last_check"]

# =========================================================
# API models
# =========================================================
class LayoutSaveReq(BaseModel):
    layout: Dict[str, str]

class WaterFlowReq(BaseModel):
    water_flow_l_min: float

class WModeReq(BaseModel):
    W1: str
    W2: str

class ClassUpsertReq(BaseModel):
    class_id: str
    name: str
    color: str

class ClassDeleteReq(BaseModel):
    class_id: str

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

class ProgramRenameReq(BaseModel):
    old_name: str
    new_name: str

class ProgramSelectReq(BaseModel):
    name: str

class RunSelectReq(BaseModel):
    selected: List[str]

class StepModel(BaseModel):
    name: str
    slot: str
    time_sec: int
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
        "water_flow_l_min": STATE["water_flow_l_min"],
        "w_mode": STATE["w_mode"],
        "last_check": STATE["last_check"],
    }

@app.post("/api/waterflow")
def api_waterflow(req: WaterFlowReq):
    try:
        STATE["water_flow_l_min"] = float(req.water_flow_l_min)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid water_flow_l_min"}, status_code=400)
    persist()
    log("WATERFLOW", {"water_flow_l_min": STATE["water_flow_l_min"]})
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
    log("WMODE", {"W1": w1, "W2": w2})
    return {"ok": True}

@app.post("/api/layout/save")
def api_layout_save(req: LayoutSaveReq):
    for slot, rid in req.layout.items():
        if slot not in STATE["layout"]:
            return JSONResponse({"ok": False, "error": f"Unknown slot {slot}"}, status_code=400)
        rid = (rid or "EMPTY").upper()
        if rid not in STATE["reagents"]:
            rid = "EMPTY"
        STATE["layout"][slot]["reagent_id"] = rid
    persist()
    log("SAVE_LAYOUT", {"n": len(req.layout)})
    return {"ok": True}

@app.post("/api/classes/upsert")
def api_class_upsert(req: ClassUpsertReq):
    cid = (req.class_id or "").strip().upper()
    if not is_valid_id(cid):
        return JSONResponse({"ok": False, "error": "Invalid class_id"}, status_code=400)
    color = clamp_hex(req.color) or "#94a3b8"
    STATE["classes"][cid] = {"id": cid, "name": (req.name or cid).strip() or cid, "color": color}
    persist()
    return {"ok": True}

@app.post("/api/classes/delete")
def api_class_delete(req: ClassDeleteReq):
    cid = (req.class_id or "").strip().upper()
    if cid in ("EMPTY", "WATER", "OVEN", "LOAD"):
        return JSONResponse({"ok": False, "error": "Core class cannot be deleted"}, status_code=400)
    if cid not in STATE["classes"]:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    for rid, r in STATE["reagents"].items():
        if r.get("class_id") == cid:
            r["class_id"] = "OTHER"
    del STATE["classes"][cid]
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
    if rid in ("EMPTY", "H2O", "OVEN", "LOAD"):
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
        return JSONResponse({"
