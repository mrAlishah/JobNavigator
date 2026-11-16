"""The two legacy Professional template ids and the one template that replaced them.

`professional-top-left-image` and `professional-top-right-image` were one template with a one-line difference
(the right one reverses the header row), so there is one `professional-photo` with a per-résumé photo side.
Résumés saved with an old id keep working and keep their side, with no data migration.
"""
import pytest

from backend import resume_template_aliases as aliases
from backend.resume_template_aliases import (
    CANONICAL_TEMPLATE, DEFAULT_IMAGE_POSITION, LEGACY_TEMPLATE_ALIASES, explicit_position, for_render, keep_position, present, resolve,
)

LEFT, RIGHT = "professional-top-left-image", "professional-top-right-image"


def test_the_mapping_is_exactly_the_two_legacy_ids():
    assert LEGACY_TEMPLATE_ALIASES == {
        LEFT: {"template": CANONICAL_TEMPLATE, "default_image_position": "left"},
        RIGHT: {"template": CANONICAL_TEMPLATE, "default_image_position": "right"},
    }
    assert CANONICAL_TEMPLATE == "professional-photo" and DEFAULT_IMAGE_POSITION == "right"


@pytest.mark.parametrize("legacy,side", [(LEFT, "left"), (RIGHT, "right")])
def test_a_legacy_id_resolves_to_the_canonical_template_with_its_old_side(legacy, side):
    assert resolve(legacy) == (CANONICAL_TEMPLATE, side)
    assert resolve(legacy, {"profile_image_enabled": True}) == (CANONICAL_TEMPLATE, side)


def test_the_canonical_template_without_a_saved_side_defaults_to_right():
    assert resolve(CANONICAL_TEMPLATE) == (CANONICAL_TEMPLATE, "right")
    assert resolve(CANONICAL_TEMPLATE, {}) == (CANONICAL_TEMPLATE, "right")


@pytest.mark.parametrize("template", [LEFT, RIGHT, CANONICAL_TEMPLATE])
@pytest.mark.parametrize("saved", ["left", "right"])
def test_an_explicit_saved_side_beats_every_default(template, saved):
    assert resolve(template, {"profile_image_position": saved}) == (CANONICAL_TEMPLATE, saved)


@pytest.mark.parametrize("junk", ["up", "", "LEFT", None, 5, ["left"], {"x": 1}, True])
def test_an_invalid_saved_side_is_ignored(junk):
    assert explicit_position({"profile_image_position": junk}) is None
    assert resolve(LEFT, {"profile_image_position": junk}) == (CANONICAL_TEMPLATE, "left")
    assert resolve(CANONICAL_TEMPLATE, {"profile_image_position": junk}) == (CANONICAL_TEMPLATE, "right")


def test_explicit_position_tolerates_a_missing_or_wrong_document():
    for data in (None, {}, [], "x", 5):
        assert explicit_position(data) is None


@pytest.mark.parametrize("other", ["inter", "modern", "helvetica", "garamond_alt", "no_such_template"])
def test_templates_without_a_photo_are_passed_through_untouched(other):
    assert resolve(other) == (other, None)
    assert resolve(other, {"profile_image_position": "left"}) == (other, None)


@pytest.mark.parametrize("not_an_id", [None, 5, ["x"], {}])
def test_a_value_that_is_not_a_template_id_is_passed_through(not_an_id):
    assert resolve(not_an_id) == (not_an_id, None)


def test_a_legacy_unknown_lookalike_is_not_an_alias():
    assert resolve("professional-top-up-image") == ("professional-top-up-image", None)
    assert resolve("Professional-Top-Left-Image") == ("Professional-Top-Left-Image", None)


def test_the_canonical_template_requested_over_a_stored_legacy_one_keeps_the_legacy_side():
    """The editor asks for a template by query (`?template=`) while the row still stores the old id."""
    assert resolve(CANONICAL_TEMPLATE, {}, stored_template=LEFT) == (CANONICAL_TEMPLATE, "left")
    assert resolve(CANONICAL_TEMPLATE, {}, stored_template=RIGHT) == (CANONICAL_TEMPLATE, "right")
    assert resolve(CANONICAL_TEMPLATE, {"profile_image_position": "right"}, stored_template=LEFT) == (CANONICAL_TEMPLATE, "right")
    assert resolve(CANONICAL_TEMPLATE, {}, stored_template="inter") == (CANONICAL_TEMPLATE, "right")


def test_for_render_fills_in_only_what_a_stored_legacy_row_would_have_lost():
    data = {"summary": "s"}
    filled = for_render(data, CANONICAL_TEMPLATE, LEFT)
    assert filled == {"summary": "s", "profile_image_position": "left"} and data == {"summary": "s"}      # a copy
    assert for_render(data, LEFT, LEFT) is data
    assert for_render({"profile_image_position": "right"}, CANONICAL_TEMPLATE, LEFT) == {"profile_image_position": "right"}
    assert for_render(data, CANONICAL_TEMPLATE, CANONICAL_TEMPLATE) is data
    assert for_render(data, "inter", LEFT) is data


def test_keep_position_saves_the_old_side_when_a_legacy_row_is_switched():
    assert keep_position(LEFT, CANONICAL_TEMPLATE, {"summary": "s"}) == {"summary": "s", "profile_image_position": "left"}
    assert keep_position(RIGHT, CANONICAL_TEMPLATE, {}) == {"profile_image_position": "right"}
    assert keep_position(LEFT, "inter", None) == {"profile_image_position": "left"}       # switching away must not forget it either


def test_keep_position_does_nothing_when_there_is_nothing_to_keep():
    assert keep_position(LEFT, CANONICAL_TEMPLATE, {"profile_image_position": "right"}) is None     # already explicit
    assert keep_position(CANONICAL_TEMPLATE, "inter", {}) is None                                  # not a legacy row
    assert keep_position("inter", CANONICAL_TEMPLATE, {}) is None
    assert keep_position(LEFT, LEFT, {}) is None                                                   # not a switch


def test_present_hides_the_legacy_ids_and_tells_the_canonical_template_its_aliases():
    listed = present([{"id": "inter", "name": "Inter"}, {"id": LEFT, "name": "L"}, {"id": CANONICAL_TEMPLATE, "name": "Professional Photo"}, {"id": RIGHT, "name": "R"}])
    assert [t["id"] for t in listed] == ["inter", CANONICAL_TEMPLATE]
    assert listed[1]["aliases"] == {LEFT: "left", RIGHT: "right"}
    assert "aliases" not in listed[0]


def test_present_works_when_the_legacy_folders_are_gone():
    listed = present([{"id": CANONICAL_TEMPLATE, "name": "Professional Photo"}])
    assert listed[0]["aliases"] == {LEFT: "left", RIGHT: "right"}


def test_the_module_needs_no_filesystem_or_template_folder():
    assert not hasattr(aliases, "TEMPLATES_DIR")
