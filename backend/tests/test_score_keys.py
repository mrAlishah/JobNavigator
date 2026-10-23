"""Scores stored by résumé id, shown by name (backend/analyzer/score_keys.py)."""
import uuid

from backend.analyzer import score_keys as K

A, B = str(uuid.uuid4()), str(uuid.uuid4())


def _set():
    s = K.ScoringSet()
    s.add("PM", "pm text", A)
    s.add("PM", "other pm text", B)          # same name: a second, unique label
    s.add("Persona", "persona text", K.PERSONA)
    return s


def test_a_scoring_set_is_a_plain_label_to_text_dict():
    s = _set()
    assert dict(s) == {"PM": "pm text", "PM (2)": "other pm text", "Persona": "persona text"}
    assert list(s) == ["PM", "PM (2)", "Persona"]


def test_the_model_answer_is_stored_by_key():
    s = _set()
    assert K.keyed({"PM": 80, "PM (2)": 70, "Persona": 60}, s) == {A: 80, B: 70, "Persona": 60}
    assert K.key_for("PM (2)", s) == B
    assert K.key_for("Made up", s) == "Made up"
    assert K.keyed({"PM": 80}, {"PM": "text"}) == {"PM": 80}     # a plain dict keeps names


def test_the_name_snapshot_keeps_each_resume_own_name():
    assert K.names_for(_set(), ["PM", "PM (2)", "Persona"]) == {A: "PM", B: "PM"}


def test_update_merges_keys_and_keeps_labels_unique():
    s = K.ScoringSet()
    s.add("PM", "x", A)
    other = K.ScoringSet()
    other.add("PM", "y", B)
    s.update(other)
    assert s.key_of == {"PM": A, "PM (2)": B}


def test_display_uses_the_current_name_then_the_snapshot_then_deleted():
    names = {A: "PM – Platform"}
    scores, report, best = K.display({A: 80, B: 70, "Tailored": 90}, {A: {"summary": "s"}}, A,
                                     {B: "Old name"}, names)
    assert scores == {"PM – Platform": 80, "Old name": 70, "Tailored": 90}
    assert report == {"PM – Platform": {"summary": "s"}}
    assert best == "PM – Platform"
    scores, _r, _b = K.display({B: 70}, None, None, None, {})
    assert scores == {K.DELETED: 70}


def test_display_never_merges_two_resumes_with_one_name():
    scores, _r, best = K.display({A: 80, B: 70}, None, B, None, {A: "PM", B: "PM"})
    assert scores == {"PM": 80, "PM (2)": 70} and best == "PM (2)"


def test_legacy_name_keys_and_markers_pass_through():
    scores, report, best = K.display({"PM": 80, "_skipped": "no_text_available"}, {"summary": "flat"},
                                     "PM", None, {})
    assert scores == {"PM": 80, "_skipped": "no_text_available"}
    assert report == {"summary": "flat"}                     # a 3.0 flat report is left as it is
    assert best == "PM"


def test_a_legacy_name_and_a_resume_with_that_name_stay_apart():
    """A 3.0 key the migration could not map keeps its text; the résumé now named the same gets (2)."""
    scores, _r, _b = K.display({A: 80, "PM": 60}, None, None, None, {A: "PM"})
    assert scores == {"PM (2)": 80, "PM": 60}
