"""Application-question autofill: generate an answer from persona + qa_bank."""
import json as _json
import logging
from fastapi import APIRouter, HTTPException
from backend.models.db import SessionLocal, Setting, Persona
from backend.analyzer.llm_client import call_autofill_llm
from backend.analyzer.llm_logger import track_llm_call

logger = logging.getLogger("jobnavigator.autofill")
router = APIRouter(prefix="/autofill", tags=["autofill"])


def _flatten_persona(p: Persona) -> str:
    parts = []
    for label, node in (("Contact", p.contact), ("Work authorization", p.work_auth),
                        ("Preferences", p.preferences), ("Resume content", p.resume_content)):
        if node:
            parts.append(f"{label}:\n{_json.dumps(node, indent=2)}")
    return "\n\n".join(parts) if parts else "(empty)"


def _flatten_qa_bank(bank) -> str:
    if not bank:
        return "(empty)"
    return "\n\n".join(f"Q: {e.get('question','')}\nA: {e.get('answer','')}" for e in bank)


@router.post("/answer")
async def autofill_answer(body: dict):
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    company = (body.get("company") or "").strip() or "(unknown company)"
    position = (body.get("position") or "").strip() or "(unknown role)"

    db = SessionLocal()
    try:
        persona = db.query(Persona).filter(Persona.id == 1).first()
        len_row = db.query(Setting).filter(Setting.key == "autofill_default_length").first()
        default_len = int(len_row.value) if len_row and (len_row.value or "").isdigit() else 120
        tmpl_row = db.query(Setting).filter(Setting.key == "autofill_prompt").first()
        if not tmpl_row or not (tmpl_row.value or "").strip():
            raise HTTPException(500, "autofill_prompt setting is empty")
        template = tmpl_row.value
        persona_txt = _flatten_persona(persona) if persona else "(no persona)"
        qa_txt = _flatten_qa_bank(persona.qa_bank if persona else [])

        # Resolve provider/model for logging the same way call_autofill_llm resolves
        # them for dispatch (autofill_llm_* settings, falling back to primary llm_*).
        def _s(key, fallback_key, default=None):
            row = db.query(Setting).filter(Setting.key == key).first()
            if row and (row.value or "").strip():
                return row.value
            fb = db.query(Setting).filter(Setting.key == fallback_key).first()
            return fb.value if fb and fb.value else default
        provider = _s("autofill_llm_provider", "llm_provider", "claude_api")
        model = _s("autofill_llm_model", "llm_model", "claude-sonnet-5")
    finally:
        db.close()

    max_chars = body.get("max_chars")
    max_chars = int(max_chars) if isinstance(max_chars, (int, str)) and str(max_chars).isdigit() else default_len

    # Stable prefix (persona + bank + instructions) is cacheable; the per-question
    # suffix (company/position/question) is not. Split at the first {company}
    # placeholder so the model sees each part exactly once: [cached_prefix, suffix].
    if "{company}" in template:
        before, after = template.split("{company}", 1)
        suffix_template = "{company}" + after
    else:
        before, suffix_template = "", template

    cached_prefix = (before
                     .replace("{persona}", persona_txt)
                     .replace("{qa_bank}", qa_txt)
                     .replace("{max_chars}", str(max_chars))) or None

    suffix = (suffix_template
              .replace("{company}", company)
              .replace("{position}", position)
              .replace("{question}", question)
              .replace("{max_chars}", str(max_chars)))
    system = "You write concise, truthful first-person job-application answers grounded only in the provided profile."

    # token budget: room for the answer + JSON wrapper + any (discarded) preamble
    max_tokens = max(256, min(1024, max_chars // 3 + 256))
    try:
        async with track_llm_call("autofill", provider, model) as tracker:
            resp = await call_autofill_llm(suffix, system, max_tokens=max_tokens,
                                           cached_prefix=cached_prefix)
            tracker.usage = resp.get("usage", tracker.usage)
        raw = (resp.get("text") or "").strip()
        # The model returns {"answer": "..."}; extract it so any leaked reasoning /
        # preamble outside the JSON is discarded. Fall back to the raw text.
        answer = raw
        try:
            import json as _json
            import re as _re
            m = _re.search(r'\{[\s\S]*\}', raw)
            if m:
                parsed = _json.loads(m.group(0))
                if isinstance(parsed, dict) and parsed.get("answer"):
                    answer = str(parsed["answer"])
        except Exception:
            pass
        answer = answer.strip().strip('"')
    except Exception as e:
        logger.error(f"autofill generation failed: {e}")
        raise HTTPException(502, "autofill generation failed") from e
    return {"answer": answer}
