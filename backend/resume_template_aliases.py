"""Legacy résumé template ids and the one template that replaced them.

`professional-top-left-image` and `professional-top-right-image` differed by one CSS line (the right one reverses
the header row), so there is a single `professional-photo` template whose photo side is chosen per résumé in
`json_data.profile_image_position` ("left" / "right").

Résumés saved with an old id are never rewritten: the id is resolved wherever a template is looked up, and the
old id also supplies the side when the résumé has none saved. This module has no filesystem access, so the old
ids keep working after their (now unused) template folders are deleted.

Side of the photo, first match wins:
    1. `json_data.profile_image_position`, when it is "left" or "right"
    2. the default of the legacy id that was requested (or, when the canonical template is requested over a row
       that still stores a legacy id, of that stored id)
    3. "right"
"""
from typing import NamedTuple, Optional

CANONICAL_TEMPLATE = "professional-photo"
IMAGE_POSITIONS = ("left", "right")
DEFAULT_IMAGE_POSITION = "right"
LEGACY_TEMPLATE_ALIASES = {
    "professional-top-left-image": {"template": CANONICAL_TEMPLATE, "default_image_position": "left"},
    "professional-top-right-image": {"template": CANONICAL_TEMPLATE, "default_image_position": "right"},
}


class Resolved(NamedTuple):
    template: str
    position: Optional[str]      # None for a template that has no photo


def explicit_position(json_data) -> Optional[str]:
    """The résumé's own saved side, or None when it has none or the value is not "left" / "right"."""
    value = json_data.get("profile_image_position") if isinstance(json_data, dict) else None
    return value if isinstance(value, str) and value in IMAGE_POSITIONS else None


def _legacy_default(template_id) -> Optional[str]:
    alias = LEGACY_TEMPLATE_ALIASES.get(template_id) if isinstance(template_id, str) else None
    return alias["default_image_position"] if alias else None


def resolve(template_id, json_data=None, stored_template=None) -> Resolved:
    """The template to load for `template_id` and, for the photo template, the side of the photo.
    `stored_template` is the id the résumé row still stores, for a request that names the canonical template."""
    alias = LEGACY_TEMPLATE_ALIASES.get(template_id) if isinstance(template_id, str) else None
    template = alias["template"] if alias else template_id
    if template != CANONICAL_TEMPLATE:
        return Resolved(template, None)
    position = explicit_position(json_data) or _legacy_default(template_id) or _legacy_default(stored_template) or DEFAULT_IMAGE_POSITION
    return Resolved(template, position)


def for_render(json_data, requested_template, stored_template):
    """`json_data` as the renderer should see it: when the canonical template is requested over a row that still
    stores a legacy id and nothing is saved, the legacy side is filled in (on a copy). Otherwise unchanged."""
    if requested_template == CANONICAL_TEMPLATE and _legacy_default(stored_template) and explicit_position(json_data) is None:
        return {**(json_data if isinstance(json_data, dict) else {}), "profile_image_position": _legacy_default(stored_template)}
    return json_data


def keep_position(old_template, new_template, json_data) -> Optional[dict]:
    """When a row that stores a legacy id is given another template and has no side saved, `json_data` with the
    old side saved (so neither the switch nor a later switch back loses it); None when there is nothing to keep."""
    default = _legacy_default(old_template)
    if default is None or old_template == new_template or explicit_position(json_data) is not None:
        return None
    return {**(json_data if isinstance(json_data, dict) else {}), "profile_image_position": default}


def present(templates: list) -> list:
    """The discovery list for the UI: the legacy ids are not offered, and the photo template lists the ids it
    answers to (id -> default side) so the editor can show a legacy résumé as using it."""
    shown = [t for t in templates if t.get("id") not in LEGACY_TEMPLATE_ALIASES]
    for t in shown:
        if t.get("id") == CANONICAL_TEMPLATE:
            t["aliases"] = {legacy: a["default_image_position"] for legacy, a in LEGACY_TEMPLATE_ALIASES.items()}
    return shown
