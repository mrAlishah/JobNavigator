from backend.autofill_schema import ANSWER_SCHEMA, project_answers


def test_schema_has_core_keys_with_valid_shape():
    for key in ("gender", "veteran_status", "authorized_us", "email", "willing_to_relocate"):
        assert key in ANSWER_SCHEMA
    veteran = ANSWER_SCHEMA["veteran_status"]
    assert veteran["node"] == "demographics"
    assert veteran["kind"] == "enum"
    assert "not_protected_veteran" in veteran["enum"]
    assert ANSWER_SCHEMA["authorized_us"]["kind"] == "bool"
    assert ANSWER_SCHEMA["email"]["kind"] == "text"


def test_project_answers_flattens_and_omits_blanks():
    persona = {
        "demographics": {"gender": "male", "veteran_status": "", "decline_demographics": True},
        "work_auth": {"authorized_us": True, "requires_sponsorship_now": False},
        "contact": {"email": "a@b.com", "phone": ""},
        "preferences": {"willing_to_relocate": True},
        "compensation": {},
    }
    out = project_answers(persona)
    assert out["gender"] == "male"                     # explicit value kept
    assert out["authorized_us"] is True
    assert out["requires_sponsorship_now"] is False
    assert out["email"] == "a@b.com"
    assert out["willing_to_relocate"] is True
    assert out["decline_demographics"] is True
    # decline_demographics fills every unset demographic field with "decline"
    assert out["veteran_status"] == "decline"
    assert out["sexual_orientation"] == "decline"
    assert "phone" not in out                          # non-demographic blanks still omitted


def test_project_answers_no_decline_omits_unset_demographics():
    # Without the checkbox, unset demographic fields are simply omitted (no auto-decline).
    out = project_answers({"demographics": {"gender": "male"}})
    assert out["gender"] == "male"
    assert "veteran_status" not in out
    assert "age_range" not in out
    assert "sexual_orientation" not in out
