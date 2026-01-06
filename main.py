from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

app = FastAPI(title="Chromax ST Demo - Protocol Validator")

RULES = {
    "rules": [
        {
            "id": "R-ST-SLIDE-LIMIT",
            "then": {
                "require_max": {"field": "slides_loaded", "max_from_field": "max_slides_supported"},
                "level": "BLOCK",
                "message": "Maximale Objektträgerzahl überschritten."
            }
        },
        {
            "id": "R-ST-TEMP-RANGE",
            "then": {
                "require_range": {"field": "temperature_c", "min": 15, "max": 30},
                "level": "BLOCK",
                "message": "Betriebstemperatur außerhalb 15–30°C."
            }
        },
        {
            "id": "R-ST-STEP-SEQUENCE-HE",
            "when": {"protocol_type": "H&E"},
            "then": {
                "must_contain_order": {
                    "sequence": [
                        "deparaffinization",
                        "hematoxylin",
                        "rinse",
                        "eosin",
                        "dehydrate",
                        "clear"
                    ]
                },
                "level": "BLOCK",
                "message": "Ungültige oder unvollständige H&E-Schrittfolge."
            }
        },
        {
            "id": "R-ST-REAGENT-MIN",
            "then": {
                "reagent_minimum": True,
                "level": "BLOCK",
                "message": "Mindestens ein Reagenz unter Mindestfüllstand."
            }
        }
    ]
}

SEVERITY = {"OK": 1, "WARN": 2, "BLOCK": 3}

class Step(BaseModel):
    name: str
    time_sec: int = Field(ge=0)

class Reagent(BaseModel):
    volume_ml: float = Field(ge=0)
    min_required_ml: float = Field(ge=0)

class RunInput(BaseModel):
    run_id: str
    protocol_type: str
    run_state: str = "READY"
    slides_loaded: int = Field(ge=0)
    max_slides_supported: int = Field(gt=0)
    temperature_c: float
    steps: List[Step]
    reagents: Dict[str, Reagent]

def evaluate(run: RunInput):
    findings = []
    run_dict = run.model_dump()

    for rule in RULES["rules"]:
        then = rule["then"]
        when = rule.get("when")

        if when:
            for k, v in when.items():
                if run_dict.get(k) != v:
                    break
            else:
                pass
            if run_dict.get(list(when.keys())[0]) != list(when.values())[0]:
                continue

        level = then["level"]
        msg = then["message"]

        if "require_range" in then:
            f = then["require_range"]["field"]
            mn = then["require_range"]["min"]
            mx = then["require_range"]["max"]
            if not mn <= run_dict[f] <= mx:
                findings.append({"rule": rule["id"], "level": level, "message": msg})

        if "require_max" in then:
            f = then["require_max"]["field"]
            mf = then["require_max"]["max_from_field"]
            if run_dict[f] > run_dict[mf]:
                findings.append({"rule": rule["id"], "level": level, "message": msg})

        if "must_contain_order" in then:
            required = then["must_contain_order"]["sequence"]
            actual = [s["name"] for s in run_dict["steps"]]
            idx = -1
            for step in required:
                if step not in actual or actual.index(step) <= idx:
                    findings.append({"rule": rule["id"], "level": level, "message": msg})
                    break
                idx = actual.index(step)

        if "reagent_minimum" in then:
            for r in run_dict["reagents"].values():
                if r["volume_ml"] < r["min_required_ml"]:
                    findings.append({"rule": rule["id"], "level": level, "message": msg})
                    break

    overall = "OK"
    for f in findings:
        if SEVERITY[f["level"]] > SEVERITY[overall]:
            overall = f["level"]

    return {"run_id": run.run_id, "overall": overall, "findings": findings}

@app.post("/validate")
def validate(run: RunInput):
    return evaluate(run)
