from backend.seed import DEFAULT_SETTINGS


def test_autofill_settings_present():
    for key in ("autofill_llm_provider", "autofill_llm_model",
                "autofill_default_length", "autofill_prompt"):
        assert key in DEFAULT_SETTINGS, f"missing default: {key}"


def test_autofill_prompt_has_placeholders():
    tmpl = DEFAULT_SETTINGS["autofill_prompt"][0]
    for ph in ("{persona}", "{qa_bank}", "{company}", "{position}", "{question}", "{max_chars}"):
        assert ph in tmpl, f"prompt missing placeholder {ph}"
