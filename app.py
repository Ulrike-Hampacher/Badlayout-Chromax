from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Dict, Any
from datetime import datetime

app = FastAPI(title="CHROMAX ST Demo — Bath Layout Editor")

# =========================================================
# IFU-like bath slot schema (positions fixed)
# =========================================================
TOP_ROW = [f"R{i}" for i in range(1, 10)] + [f"W{i}" for i in range(1, 6)] + ["OVEN"]
BOTTOM_ROW = [f"R{i}" for i in range(18, 9, -1)] + ["LOAD"]

SLOT_KIND: Dict[str, str] = {**{f"R{i}": "reagent" for i in range(1, 19)},
                             **{f"W{i}": "water" for i in range(1, 6)},
                             "OVEN": "oven",
                             "LOAD": "load"}

# =========================================================
# Layout data model (editable)
# =========================================================
def _default_layout() -> Dict[str, Dict[str, str]]:
    d = {slot: {"assign": "Empty", "group": ""} for slot in (TOP_ROW + BOTTOM_ROW)}
    for w in [f"W{i}" for i in range(1, 6)]:
        d[w] = {"assign": "Water", "group": "Water"}
    d["OVEN"] = {"assign": "Oven", "group": "Oven"}
    d["LOAD"] = {"assign": "Load", "group": "Load"}
    return d

DEFAULT_LAYOUT = _default_layout()
CURRENT_LAYOUT: Dict[str, Dict[str, str]] = dict(DEFAULT_LAYOUT)

# =========================================================
# Small audit (optional, helps BU testing)
# =========================================================
AUDIT: list[dict[str, Any]] = []

def log(event: str, details: Dict[str, Any]):
    AUDIT.append({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event, "details": details})
    if len(AUDIT) > 400:
        del AUDIT[:100]

log("BOOT", {"slots_top": len(TOP_ROW), "slots_bottom": len(BOTTOM_ROW)})

# =========================================================
# API models
# =========================================================
class LayoutItem(BaseModel):
    assign: str
    group: str = ""

class LayoutSaveReq(BaseModel):
    layout: Dict[str, LayoutItem]

# =========================================================
# API
# =========================================================
@app.get("/api/layout")
def api_get_layout():
    return {"layout": CURRENT_LAYOUT}

@app.post("/api/layout")
def api_save_layout(req: LayoutSaveReq):
    # Validate only known slots
    for slot in req.layout.keys():
        if slot not in CURRENT_LAYOUT:
            return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)

    for slot, item in req.layout.items():
        CURRENT_LAYOUT[slot] = {
            "assign": (item.assign or "").strip(),
            "group": (item.group or "").strip()
        }

    log("SAVE_LAYOUT", {"n": len(req.layout)})
    return {"ok": True, "layout": CURRENT_LAYOUT}

@app.post("/api/layout/reset")
def api_reset_layout():
    CURRENT_LAYOUT.clear()
    CURRENT_LAYOUT.update(_default_layout())
    log("RESET_LAYOUT", {})
    return {"ok": True, "layout": CURRENT_LAYOUT}

@app.get("/api/audit", response_class=JSONResponse)
def api_audit():
    return AUDIT[-300:]

# =========================================================
# Pages
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home():
    tpl = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CHROMAX ST Demo</title>
<style>
  body{font-family:-apple-system,system-ui,Arial;margin:0;padding:16px;background:#0b1220;color:#eaf0ff;}
  .card{border:1px solid rgba(255,255,255,.12);border-radius:16px;background:rgba(255,255,255,.04);padding:14px;margin:12px 0;}
  .pill{display:inline-flex;align-items:center;gap:10px;padding:10px 12px;border-radius:999px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);}
  a{color:#7aa2ff;text-decoration:none;}
  .btn{display:inline-block;padding:12px 14px;border-radius:14px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);color:#eaf0ff;font-weight:800;margin-right:10px;}
  .muted{opacity:.75;line-height:1.5;}
</style>
</head>
<body>
  <div class="card">
    <div class="pill"><b>CHROMAX ST</b> <span style="opacity:.7">(DEMO)</span></div>
    <div class="muted" style="margin-top:10px;">
      Bath Layout Editor (IFU-Schema): oben R1–R9 / W1–W5 / OVEN, unten R18–R10 / LOAD
    </div>
  </div>

  <div class="card">
    <a class="btn" href="/baths">Open Bath Layout</a>
    <a class="btn" href="/audit">Audit</a>
  </div>

  <div class="card muted">
    Nächster Schritt (wenn Layout passt): Protokoll-Editor + Kompatibilitäts-Check auf diesem Layout.
  </div>
</body>
</html>
"""
    return HTMLResponse(tpl)

@app.get("/audit", response_class=PlainTextResponse)
def audit_page():
    lines = ["AUDIT (last 300)"]
    for e in AUDIT[-300:]:
        lines.append(f"{e['t']} | {e['event']} | {e['details']}")
    return PlainTextResponse("\n".join(lines))

@app.get("/baths", response_class=HTMLResponse)
def baths_page():
    # Build tiles (safe: only simple f-strings, no CSS/JS braces in them)
    def tile(slot: str) -> str:
        kind = SLOT_KIND.get(slot, "reagent")
        return (
            f"<div class='tile {kind}' id='tile_{slot}'>"
            f"  <div class='slot'>{slot}</div>"
            f"  <input class='inp' id='a_{slot}' placeholder='Assign (z.B. Xylene, Alcohol 96%, Hematoxylin...)' />"
            f"  <input class='inp small' id='g_{slot}' placeholder='Group (optional)' />"
            f"</div>"
        )

    top_html = "".join([tile(s) for s in TOP_ROW])
    bottom_html = "".join([tile(s) for s in BOTTOM_ROW])

    # JS arrays
    top_js = "[" + ",".join([f"'{s}'" for s in TOP_ROW]) + "]"
    bottom_js = "[" + ",".join([f"'{s}'" for s in BOTTOM_ROW]) + "]"

    tpl = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Bath Layout</title>
<style>
  :root{
    --bg:#0b1220;
    --text:#eaf0ff;
    --muted:rgba(234,240,255,.75);
    --stroke:rgba(255,255,255,.12);
    --card:rgba(255,255,255,.04);
    --btn:rgba(255,255,255,.06);
    --accent:#7aa2ff;
  }
  body{
    font-family:-apple-system,system-ui,Arial;
    margin:0;padding:16px;background:var(--bg);color:var(--text);
    background:
      radial-gradient(1200px 800px at 20% 0%, rgba(122,162,255,.20), transparent 55%),
      radial-gradient(900px 600px at 80% 20%, rgba(46,204,113,.12), transparent 60%),
      var(--bg);
  }
  a{color:var(--accent);text-decoration:none;}
  .bar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;}
  .pill{padding:10px 12px;border-radius:999px;border:1px solid var(--stroke);background:rgba(255,255,255,.05);}
  .panel{border:1px solid var(--stroke);border-radius:16px;background:var(--card);padding:12px;margin:12px 0;}
  .hint{color:var(--muted);font-size:13px;line-height:1.5;}
  button{
    padding:12px 14px;border-radius:14px;border:1px solid var(--stroke);
    background:var(--btn);color:var(--text);font-weight:900;
  }
  button.primary{border-color:rgba(122,162,255,.55);background:rgba(122,162,255,.18);}
  button.danger{border-color:rgba(231,76,60,.55);background:rgba(231,76,60,.14);}

  .row{
    display:grid;
    grid-auto-flow:column;
    grid-auto-columns:160px;
    gap:10px;
    overflow-x:auto;
    padding:10px 0;
  }
  .tile{
    border:1px solid rgba(255,255,255,.12);
    border-radius:16px;
    padding:10px;
    min-height:125px;
    background:rgba(255,255,255,.03);
  }
  .slot{font-weight:1000;margin-bottom:8px;letter-spacing:.4px;}
  .inp{
    width:100%;
    padding:10px;
    border-radius:12px;
    border:1px solid rgba(255,255,255,.12);
    background:rgba(255,255,255,.05);
    color:var(--text);
    outline:none;
  }
  .inp.small{margin-top:8px;opacity:.95;}
  .inp:focus{border-color:rgba(122,162,255,.55);box-shadow:0 0 0 4px rgba(122,162,255,.12);}

  /* Neutral tints (not IFU copy) */
  .water{background:rgba(120,190,255,.10);}
  .oven{background:rgba(255,120,120,.10);}
  .load{background:rgba(255,255,255,.02);}

  .msg{
    border:1px solid rgba(255,255,255,.12);
    border-radius:14px;
    padding:10px;
    background:rgba(0,0,0,.12);
    color:var(--muted);
    white-space:pre-wrap;
  }
</style>
</head>
<body>
  <div class="bar">
    <div class="pill"><b>Bath Layout</b> — IFU Schema (Bäder)</div>
    <div class="pill"><a href="/">Home</a></div>
  </div>

  <div class="panel">
    <div class="hint">
      <b>Belegung frei anpassbar:</b> trage pro Slot ein, was in dem Bad ist (Assign). Group ist optional (z.B. „Xylene“, „Alcohol“, „Dye“).<br/>
      Beispiele: <b>Xylene</b>, <b>Alcohol 96%</b>, <b>Hematoxylin</b>, <b>Eosin</b>, <b>Water</b>, <b>Empty</b>.
    </div>
    <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;">
      <button onclick="loadLayout()">Reload</button>
      <button class="primary" onclick="saveLayout()">Save</button>
      <button class="danger" onclick="resetLayout()">Reset Default</button>
    </div>
  </div>

  <div class="panel">
    <div class="hint"><b>Top row:</b> R1–R9, W1–W5, OVEN</div>
    <div class="row">__TOP__</div>
  </div>

  <div class="panel">
    <div class="hint"><b>Bottom row:</b> R18–R10, LOAD</div>
    <div class="row">__BOTTOM__</div>
  </div>

  <div class="panel">
    <div class="msg" id="msg">Status: ready.</div>
  </div>

<script>
const TOP = __TOPSLOTS__;
const BOTTOM = __BOTTOMSLOTS__;
const ALL = TOP.concat(BOTTOM);

function setMsg(t){
  document.getElementById('msg').textContent = t;
}

async function loadLayout(){
  setMsg("Loading layout...");
  const r = await fetch('/api/layout');
  const data = await r.json();
  const layout = data.layout || {};
  ALL.forEach(slot=>{
    const a = document.getElementById('a_'+slot);
    const g = document.getElementById('g_'+slot);
    if(a) a.value = (layout[slot] && layout[slot].assign) ? layout[slot].assign : "";
    if(g) g.value = (layout[slot] && layout[slot].group) ? layout[slot].group : "";
  });
  setMsg("Loaded.");
}

async function saveLayout(){
  setMsg("Saving...");
  const payload = { layout: {} };
  ALL.forEach(slot=>{
    payload.layout[slot] = {
      assign: (document.getElementById('a_'+slot).value || "").trim(),
      group: (document.getElementById('g_'+slot).value || "").trim()
    };
  });

  const r = await fetch('/api/layout', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if(data.ok){
    setMsg("Saved ✅");
  }else{
    setMsg("Save failed ❌\\n" + JSON.stringify(data, null, 2));
  }
}

async function resetLayout(){
  if(!confirm("Reset to default layout?")) return;
  setMsg("Resetting...");
  const r = await fetch('/api/layout/reset', { method: 'POST' });
  const data = await r.json();
  if(data.ok){
    await loadLayout();
    setMsg("Reset ✅");
  }else{
    setMsg("Reset failed ❌\\n" + JSON.stringify(data, null, 2));
  }
}

loadLayout();
</script>

</body>
</html>
"""

    html = tpl.replace("__TOP__", top_html) \
              .replace("__BOTTOM__", bottom_html) \
              .replace("__TOPSLOTS__", top_js) \
              .replace("__BOTTOMSLOTS__", bottom_js)

    return HTMLResponse(html)
