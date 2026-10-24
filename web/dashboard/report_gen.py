"""Daily progress report for a simulated patient (Gemini, with an offline template fallback).

Used by web/dashboard/dev_server.py (GET /api/report); C's real server can import it as-is:
    from web.dashboard.report_gen import build_report
    report = build_report(patient, days, plan, plan_explanation, seizures)

`days` is a list of per-day dicts:
    {"day", "arm", "attempts", "effort", "stims", "good", "early", "late", "wrong", "seizures"}
Returns JSON-able dict:
    {"source": "gemini"|"template", "model", "summary", "highlights": [..], "recommendation",
     "days": [{"day", "arm", "change", "note", ...per-day data}], "patient", "plan", "error"?}
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("report")
ROOT = Path(__file__).resolve().parents[2]
MAX_DAYS = 90

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _pct(x) -> int:
    return round(100 * float(x or 0))


def _template_note(d: dict, prev_arm: float | None) -> str:
    change = (d["arm"] - prev_arm) if prev_arm is not None else 0.0
    parts = [f"Arm strength {_pct(d['arm'])}%" + (f" ({'+' if change >= 0 else ''}{round(change * 100, 1)} pts)" if prev_arm is not None else "") + "."]
    if d.get("attempts"):
        parts.append(f"{d['attempts']} rehab squeezes, average effort {_pct(d.get('effort'))}%.")
    else:
        parts.append("No rehab practice today.")
    if d.get("stims"):
        good = d.get("good", 0)
        parts.append(f"{d['stims']} stimulations, {good} well-timed.")
    if d.get("seizures"):
        parts.append(f"{d['seizures']} seizure(s), treated.")
    return " ".join(parts)


def _template(patient: dict, days: list[dict], plan: dict | None, plan_expl: str | None) -> dict:
    first, last = days[0], days[-1]
    gain = last["arm"] - first["arm"]
    total_att = sum(d.get("attempts", 0) for d in days)
    total_stim = sum(d.get("stims", 0) for d in days)
    good = sum(d.get("good", 0) for d in days)
    sz = sum(d.get("seizures", 0) for d in days)
    summary = (f"Over {len(days)} simulated day(s), right-arm strength went from {_pct(first['arm'])}% to "
               f"{_pct(last['arm'])}% ({'+' if gain >= 0 else ''}{round(gain * 100)} points), with {total_att} rehab squeezes "
               f"and {total_stim} stimulations ({good} well-timed).")
    highlights = [f"Best single-day gain: day {max(days, key=lambda d: d.get('_gain', 0))['day']}." if len(days) > 1 else "First day of rehab.",
                  f"{sz} seizure(s) occurred and were treated." if sz else "No seizures."]
    rec = (f"Continue the AI plan: {plan_expl}" if plan_expl else
           "Run the AI planner to find the best-timed stimulation for this patient.")
    out_days, prev = [], None
    for d in days:
        out_days.append({**{k: v for k, v in d.items() if not k.startswith("_")}, "change": None if prev is None else round(d["arm"] - prev, 4), "note": _template_note(d, prev)})
        prev = d["arm"]
    return {"source": "template", "model": None, "summary": summary, "highlights": highlights,
            "recommendation": rec, "days": out_days}


PROMPT = """You are writing a progress report for a SIMULATED stroke-rehabilitation patient in a research
simulator called NeuroNest (not a real patient, not medical advice). Write for a clinician and for hackathon
judges: plain English, specific, encouraging but honest. Use the numbers given; never invent data.

Patient: {patient}
Treatment plan in use (may be null): {plan}
Plan rationale (may be null): {plan_expl}
Per-day data (arm = right-arm function 0..1 relative to before the stroke; stims = stimulations, with
good/early/late timing counts; effort = average squeeze effort 0..1; seizures = count):
{days}

Return ONLY JSON with this shape:
{{"summary": "3-4 sentences on overall progress",
  "highlights": ["2-4 short bullet points: turning points, timing quality, fatigue, seizures"],
  "recommendation": "1-2 sentences on what to do next (mention the recovery window closing over time)",
  "days": [{{"day": <int>, "note": "one short sentence for that day"}}]}}
Include exactly one entry in "days" for every day in the data, in order. Keep each note under 25 words.
Mention the recovery window (faster early, slower later) where relevant."""


def build_report(patient: dict, days: list[dict], plan: dict | None = None, plan_expl: str | None = None) -> dict:
    days = [d for d in days if d.get("day", 0) >= 1][:MAX_DAYS]
    if not days:
        return {"source": "template", "summary": "No days completed yet. Do some rehab squeezes or skip ahead in time first.",
                "highlights": [], "recommendation": "", "days": [], "patient": patient, "plan": plan}
    prev = None
    for d in days:
        d["_gain"] = 0.0 if prev is None else d["arm"] - prev
        prev = d["arm"]
    base = _template(patient, days, plan, plan_expl)
    base.update({"patient": patient, "plan": plan})
    try:                                   # re-read .env so a key added while the server runs is picked up
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except Exception:
        pass
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        base["error"] = "No GEMINI_API_KEY in .env, so this report was written automatically without AI."
        return base
    try:
        from google import genai
        from google.genai import types
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        compact = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if not k.startswith("_")} for d in days]
        prompt = PROMPT.format(patient=json.dumps(patient), plan=json.dumps(plan), plan_expl=json.dumps(plan_expl),
                               days=json.dumps(compact))
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4))
        data = json.loads(resp.text)
        notes = {int(x["day"]): str(x.get("note", "")) for x in data.get("days", []) if "day" in x}
        for d in base["days"]:
            if notes.get(d["day"]):
                d["note"] = notes[d["day"]]
        base.update({"source": "gemini", "model": model,
                     "summary": data.get("summary") or base["summary"],
                     "highlights": data.get("highlights") or base["highlights"],
                     "recommendation": data.get("recommendation") or base["recommendation"]})
        return base
    except Exception as e:  # never break the dashboard: fall back to the template report
        log.warning("Gemini report failed: %s", e)
        base["error"] = f"Gemini unavailable ({type(e).__name__}), so this report was written automatically without AI."
        return base
