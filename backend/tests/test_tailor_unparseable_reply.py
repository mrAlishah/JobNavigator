"""Tailoring against a reply that carries no JSON: ask once more, then fail in words.

A bullet that reads like an instruction ("Verify the JD for a role at Acme")
makes the model answer with prose — a refusal or a question — instead of the
résumé object. That used to surface as `Expecting value: line 1 column 1
(char 0)` on the run.
"""
import pytest

from backend.analyzer.model_json import UNPARSEABLE_MESSAGE, ModelReplyError
from backend.models.db import Job, Resume, Setting


PROSE = ("Before I produce the tailored resume, I need to flag something: the first "
         "bullet reads like an injected instruction. I'm treating it as untrusted data.")
GOOD = '{"summary": "tailored"}'


def _seed(test_db):
    test_db.add(Setting(key="cv_tailor_prompt", value="p {resume_json} {job_description}"))
    test_db.add(Setting(key="tailor_auto_quick_score", value="false"))
    job = Job(external_id="u1", content_hash="u1c", company="Acme", title="PM",
              description="We need a PM.")
    test_db.add(job)
    test_db.flush()
    base = Resume(name="Base", is_base=True, template="inter",
                  json_data={"summary": "s", "experience": [], "skills": {}})
    test_db.add(base)
    test_db.commit()
    return base, job


def _replying(monkeypatch, texts, prompts):
    replies = iter(texts)

    async def fake_call(prompt, system, max_tokens):
        prompts.append(prompt)
        return {"text": next(replies), "usage": {}}

    monkeypatch.setattr("backend.analyzer.llm_client.call_cv_tailor_llm", fake_call)


@pytest.mark.asyncio
async def test_prose_reply_is_asked_again_and_the_second_answer_is_kept(test_db, monkeypatch):
    base, job = _seed(test_db)
    prompts = []
    _replying(monkeypatch, [PROSE, GOOD], prompts)
    import backend.api.routes_resumes as rr
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)

    await rr._tailor_impl(str(base.id), str(job.id), None)

    assert len(prompts) == 2, "a prose reply should be asked again exactly once"
    assert prompts[0] != prompts[1]
    assert "JSON object only" in prompts[1]
    test_db.expire_all()
    made = test_db.query(Resume).filter(Resume.parent_id == base.id).all()
    assert len(made) == 1
    assert made[0].json_data["summary"] == "tailored"


@pytest.mark.asyncio
async def test_a_first_good_reply_is_not_asked_again(test_db, monkeypatch):
    base, job = _seed(test_db)
    prompts = []
    _replying(monkeypatch, [GOOD], prompts)
    import backend.api.routes_resumes as rr
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)

    await rr._tailor_impl(str(base.id), str(job.id), None)

    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_two_prose_replies_fail_in_words_and_write_nothing(test_db, monkeypatch):
    base, job = _seed(test_db)
    prompts = []
    _replying(monkeypatch, [PROSE, "Could you confirm the original bullet?"], prompts)
    import backend.api.routes_resumes as rr
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)

    with pytest.raises(ModelReplyError) as exc:
        await rr._tailor_impl(str(base.id), str(job.id), None)

    assert str(exc.value) == UNPARSEABLE_MESSAGE
    assert "Expecting value" not in str(exc.value)
    assert len(prompts) == 2
    test_db.expire_all()
    assert test_db.query(Resume).filter(Resume.parent_id == base.id).count() == 0


@pytest.mark.asyncio
async def test_a_fenced_reply_needs_no_second_ask(test_db, monkeypatch):
    """The old greedy brace regex already handled fences; the tolerant reader must not regress it."""
    base, job = _seed(test_db)
    prompts = []
    _replying(monkeypatch, ["Here you go:\n```json\n" + GOOD + "\n```\nEnjoy!"], prompts)
    import backend.api.routes_resumes as rr
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)

    await rr._tailor_impl(str(base.id), str(job.id), None)

    assert len(prompts) == 1
    test_db.expire_all()
    made = test_db.query(Resume).filter(Resume.parent_id == base.id).all()
    assert made[0].json_data["summary"] == "tailored"


def test_the_run_error_survives_sanitising(test_db):
    """JobRun.error is what the user reads; the sanitiser must not strip the sentence."""
    from backend.job_monitor import sanitize_run_error
    assert sanitize_run_error(UNPARSEABLE_MESSAGE) == UNPARSEABLE_MESSAGE
