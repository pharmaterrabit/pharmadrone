"""Deterministic and optional tool-grounded chat orchestration."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from pharmadrone.pipeline import ai_bd_service, case_study_mvp


SYSTEM_PROMPT = """You are PharmaDrone AI, a business-development assistant for pharmaceutical opportunity discovery.

You must answer using PharmaDrone tool outputs only for factual claims.

You must not invent evidence.

You must distinguish:
- retained internal evidence;
- human-reviewed canonical evidence;
- live/direct discovery;
- generated external search routes;
- unavailable or missing evidence.

You must not provide legal, freedom-to-operate, patent-validity, patent-enforceability, regulatory, investment or commercial advice as final conclusions.

Use cautious wording:
- possible opportunity;
- potential fit;
- may justify analyst review;
- requires validation.

Always include limitations and source links when available.
If evidence requested by the user is unavailable, say: I do not have retained PharmaDrone evidence for that yet.
"""


STARTER_PROMPTS = (
    "Generate 10 BD leads for particle properties",
    "Build a Pfizer poor-solubility pitch report",
    "Find Novartis particle-properties opportunities",
    "Show AstraZeneca dissolution innovation evidence",
    "Give me outreach angles for amorphous solid dispersion",
    "Which companies should I target for bioavailability enhancement?",
)

_THEME_ALIASES = {
    "poor-solubility": "Poor solubility",
    "particle-properties": "Particle properties",
    "amorphous solid dispersions": "Amorphous solid dispersion",
    "asd": "Amorphous solid dispersion",
    "nanocrystals": "Nanocrystals / nanosuspensions",
    "nanosuspensions": "Nanocrystals / nanosuspensions",
    "cocrystals": "Cocrystals / salt forms",
    "salt forms": "Cocrystals / salt forms",
    "lipid formulations": "Lipid-based formulations",
    "hme": "Hot-melt extrusion",
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def detect_theme(prompt: str) -> str | None:
    normalized = _normalized(prompt)
    options = [(theme, theme) for theme in case_study_mvp.THEMES]
    options.extend(_THEME_ALIASES.items())
    options.sort(key=lambda item: len(_normalized(item[0])), reverse=True)
    for phrase, canonical in options:
        if _normalized(phrase) in normalized:
            return canonical
    return None


def detect_intent(prompt: str) -> str:
    text = _normalized(prompt)
    if "save lead" in text:
        return "save-lead"
    if "save report" in text or "save pitch" in text:
        return "save-report"
    if any(phrase in text for phrase in ("build a", "build ", "pitch report", "opportunity report")):
        return "build-company-pitch"
    if any(phrase in text for phrase in (
        "generate", "which companies", "target for", "bd leads", "outreach angle",
    )):
        return "generate-bd-leads"
    if any(phrase in text for phrase in ("show", "evidence", "source links", "find ")):
        return "get-lead-evidence"
    if "limitation" in text or "what do you know" in text:
        return "explain-limitations"
    return "unsupported"


def _requested_limit(prompt: str) -> int:
    match = re.search(r"\b(\d{1,3})\b", prompt)
    return min(max(int(match.group(1)), 1), ai_bd_service.MAX_RESULTS) if match else 10


def _company(prompt: str, theme: str | None) -> str:
    value = str(prompt or "").strip()
    patterns = (
        r"\b(?:build|create|prepare)\s+(?:a\s+)?(.+?)\s+(?:poor[- ]solubility|particle[- ]properties|dissolution innovation|amorphous solid dispersion|modified release|bioavailability enhancement|particle size reduction|nanocrystals?|nanosuspensions?|cocrystals?|salt forms?|lipid[- ]based formulations?|spray drying|hot[- ]melt extrusion)",
        r"\b(?:find|show)\s+(.+?)\s+(?:poor[- ]solubility|particle[- ]properties|dissolution innovation|amorphous solid dispersion|modified release|bioavailability enhancement|particle size reduction|nanocrystals?|nanosuspensions?|cocrystals?|salt forms?|lipid[- ]based formulations?|spray drying|hot[- ]melt extrusion)",
        r"\bfor\s+([A-Za-z0-9&.' -]{2,80})$",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            candidate = re.sub(r"\b(?:a|an|the)\b", " ", match.group(1), flags=re.I)
            candidate = " ".join(candidate.split()).strip(" -.,?")
            if candidate:
                return candidate
    if theme:
        normalized_theme = _normalized(theme)
        normalized_prompt = _normalized(prompt).replace(normalized_theme, " ")
        normalized_prompt = re.sub(
            r"\b(build|create|prepare|show|find|give|me|a|an|pitch|report|opportunity|opportunities|evidence|outreach|angles|for)\b",
            " ", normalized_prompt,
        )
        candidate = " ".join(normalized_prompt.split()).strip()
        if 1 <= len(candidate.split()) <= 5:
            return candidate.title()
    return ""


def _deterministic_message(intent: str, result: dict[str, Any], company: str = "", theme: str = "") -> str:
    if intent == "generate-bd-leads":
        count = len(result.get("data") or [])
        if count:
            return (
                f"I found {count} bounded, evidence-grounded lead candidate(s) for {theme}. "
                "Each card distinguishes company-specific evidence from limitations and requires validation before outreach."
            )
        return "I do not have retained PharmaDrone evidence for that yet."
    if intent == "build-company-pitch":
        report = result.get("data") or {}
        return (
            f"I built {report.get('report_title', 'the requested pitch report')}. "
            f"Readiness: {report.get('readiness_status', 'Not enough evidence')}. "
            "This is pitch support, not a conclusion, and requires analyst validation."
        )
    if intent == "get-lead-evidence":
        if result.get("status") == "no-evidence":
            return "I do not have retained PharmaDrone evidence for that yet."
        return (
            f"Here is the retained PharmaDrone evidence available for {company} and {theme}. "
            "The source links and limitations should be reviewed before using the opportunity externally."
        )
    if intent == "save-lead":
        return "Use Save lead on the evidence-grounded lead card you want to retain in this workspace."
    if intent == "save-report":
        return "Use Save report on the generated company pitch you want to retain in this workspace."
    return "I can operate PharmaDrone lead, pitch-report and evidence tools when you provide a supported theme."


def _llm_draft(prompt: str, tool_result: dict[str, Any], deterministic: str) -> tuple[str, str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return deterministic, "deterministic"
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    bounded_tool_json = json.dumps(tool_result, ensure_ascii=False)[:60_000]
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                    {
                        "role": "system",
                        "content": "Use only this PharmaDrone tool output for factual claims:\n" + bounded_tool_json,
                    },
                ],
            },
            timeout=20.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return str(content).strip() or deterministic, "openai-tool-grounded"
    except Exception:
        return deterministic, "deterministic-fallback"


def handle_chat(prompt: str, *, conn=None, use_llm: bool = False) -> dict[str, Any]:
    user_prompt = " ".join(str(prompt or "").split()).strip()
    if not user_prompt:
        raise ValueError("A non-empty chat prompt is required.")
    if len(user_prompt) > 2_000:
        raise ValueError("Chat prompt exceeds the 2,000 character limit.")
    intent = detect_intent(user_prompt)
    theme = detect_theme(user_prompt)
    company = _company(user_prompt, theme)
    if not theme and intent != "explain-limitations":
        result = {
            "status": "needs-input",
            "data": [],
            "limitations": ["A supported opportunity theme is required to query PharmaDrone intelligence."],
            "source_links": [],
            "suggested_next_actions": ["Choose one of the supported formulation or particle-property themes."],
        }
    elif intent == "generate-bd-leads":
        result = ai_bd_service.generate_bd_leads(theme or "", limit=_requested_limit(user_prompt), conn=conn)
    elif intent == "build-company-pitch":
        if not company:
            result = {
                "status": "needs-input", "data": {},
                "limitations": ["A target company is required to build a company pitch report."],
                "source_links": [], "suggested_next_actions": ["Provide a target company and supported theme."],
            }
        else:
            result = ai_bd_service.build_company_pitch(company, theme or "", conn=conn)
    elif intent == "get-lead-evidence":
        if not company:
            result = {
                "status": "needs-input", "data": {},
                "limitations": ["A target company is required to retrieve company-specific evidence."],
                "source_links": [], "suggested_next_actions": ["Provide a target company and supported theme."],
            }
        else:
            result = ai_bd_service.get_lead_evidence(company, theme or "", conn=conn)
    elif intent in {"save-lead", "save-report"}:
        noun = "lead card" if intent == "save-lead" else "company pitch report"
        result = {
            "status": "needs-action",
            "data": {},
            "limitations": [f"A generated {noun} must be selected before it can be saved."],
            "source_links": [],
            "suggested_next_actions": [f"Generate a {noun}, review its evidence, then use its save action."],
        }
    else:
        result = {
            "status": "needs-input", "data": {},
            "limitations": [
                "PharmaDrone AI answers factual BD questions through retained lead, pitch and evidence tools only."
            ],
            "source_links": [],
            "suggested_next_actions": list(STARTER_PROMPTS[:3]),
        }
    message = _deterministic_message(intent, result, company, theme or "")
    mode = "deterministic"
    if use_llm and result.get("status") in {"ok", "no-evidence"}:
        message, mode = _llm_draft(user_prompt, result, message)
    response = {
        "intent": intent,
        "mode": mode,
        "message": message,
        "theme": theme,
        "company": company,
        "result": result,
    }
    json.dumps(response, ensure_ascii=False)
    return response
