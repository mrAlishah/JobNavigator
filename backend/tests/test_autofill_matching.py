import json
import unicodedata

from backend import seed
from backend.models.db import Setting


def _normalize(value):
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("ß", "ss")
    value = "".join(f" {char}" if char.isupper() and index else char for index, char in enumerate(value)).lower()
    return " ".join("".join(char if char.isalnum() else " " for char in value).split())


def _match(signature, patterns):
    normalized = _normalize(signature)
    return next(
        key
        for key, synonyms in patterns.items()
        if any(f" {_normalize(synonym)} " in f" {normalized} " for synonym in synonyms)
    )


def test_normalized_default_patterns_match_adesso_labels_and_names():
    patterns = json.loads(seed.DEFAULT_SETTINGS["autofill_field_patterns"][0])

    assert _match("Vorname firstName field-firstName", patterns) == "first_name"
    assert _match("Nachname lastName field-lastName", patterns) == "last_name"
    assert _match("E-Mail contactEmail", patterns) == "email"
    assert _match("Telefon cellPhone", patterns) == "phone"
    assert _match("Gehaltsvorstellung custSalaryReq", patterns) == "desired_salary"


def test_migrate_autofill_dicts_upgrades_only_unchanged_field_patterns(test_db):
    defaults = json.loads(seed.DEFAULT_SETTINGS["autofill_field_patterns"][0])
    old_defaults = {
        key: [value for value in values if value not in seed.AUTOFILL_FIELD_PATTERN_ADDITIONS.get(key, [])]
        for key, values in defaults.items()
    }
    old_defaults["first_name"] = ["my custom first-name pattern"]
    test_db.add(Setting(key="autofill_field_patterns", value=json.dumps(old_defaults)))
    test_db.commit()

    seed.migrate_autofill_dicts(test_db)

    patterns = json.loads(test_db.query(Setting).filter(Setting.key == "autofill_field_patterns").one().value)
    assert patterns["first_name"] == ["my custom first-name pattern"]
    assert "nachname" in patterns["last_name"]
