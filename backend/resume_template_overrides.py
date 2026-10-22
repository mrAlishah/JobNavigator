"""Fork behaviour the renderer layers on the official (upstream-owned) résumé templates.

Those template files stay as JobNavigator ships them, so an updated or newly added upstream template needs no
patch. `_render_html` passes `footer_reserved_in` and `section_titles` to every template. A template that reads
one of them (the Professional templates, helvetica) handles that part itself and the matching function here is
not applied. "Reads" is what Jinja's own parser says (`reads`): a mention in a comment or in text is not a read,
a `{% include %}`d or imported partial is. Otherwise:

* print_layout: the footer's reserved band at the bottom of every printed page, the template's own top and
  side `@page` margins kept, and entries kept whole across a page break;
* section_titles: custom section headings replace the default text (`json_data["section_titles"]`).

A template without the expected structure is left as it is, which is upstream's own rendering;
`test_resume_template_ownership.py` fails for such a template so the difference is noticed.
"""
import re

from jinja2 import TemplateNotFound, meta
from markupsafe import escape

VARIABLES = ("footer_reserved_in", "section_titles")
# `@page { ... margin: <top> <sides>; ... }` as the official templates write it
_PAGE_MARGIN = re.compile(r"@page\s*\{[^}]*?\bmargin:\s*(\S+)\s+(\S+)\s*;")
_HEADINGS = (("summary", "Summary"), ("experience", "Experience"), ("skills", "Skills"),
             ("education", "Education"), ("projects", "Projects"), ("publications", "Publications"))
_HEADING = re.compile(r'(<div class="section-title">)\s*(' + "|".join(d for _, d in _HEADINGS) + r')\s*(</div>)')
# a section whose first child is the summary text: this template prints no Summary heading
_HEADINGLESS_SUMMARY = re.compile(r'(<div class="section"[^>]*>\s*)(<div class="summary-text">)')


def reads(env, name: str, _seen=None) -> set:
    """Which of `VARIABLES` the template `name` reads, by Jinja's parser, following includes, imports and extends.
    A partial that cannot be found or whose name is only known at render time is not followed (it cannot be
    told), which leaves the overrides on: they only touch the structures they recognise."""
    seen = _seen if _seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)
    tree = env.parse(env.loader.get_source(env, name)[0])
    found = set(VARIABLES) & meta.find_undeclared_variables(tree)
    for partial in meta.find_referenced_templates(tree):
        if partial:
            try:
                found |= reads(env, partial, seen)
            except TemplateNotFound:
                pass
    return found


def print_layout(html: str, bottom_in: float) -> str:
    """Append a later `@page` rule (it wins) that keeps the template's top and side margins and sets the
    bottom to the footer's reserved band, plus `break-inside: avoid` for entries when printing."""
    page = _PAGE_MARGIN.search(html)
    if not page or "</head>" not in html:
        return html
    top, side = page.groups()
    css = (f"@page {{ margin: {top} {side} {bottom_in:g}in {side}; }} "
           "@media print { ul.bullets li, .entry, .edu-entry { break-inside: avoid; } }")
    return html.replace("</head>", f"<style>{css}</style>\n</head>", 1)


def section_titles(html: str, titles: dict) -> str:
    """Replace the default text of each section heading (`<div class="section-title">Skills</div>`) with the
    résumé's own title, HTML-escaped, in one pass: a title that is another section's default name stays as
    written. A template with no Summary heading gets one when a summary title is set."""
    custom = {default: str(escape(titles[key])) for key, default in _HEADINGS if titles.get(key)}
    if not custom:
        return html
    swapped = set()

    def swap(match):
        text = custom.get(match.group(2))
        if text is None:
            return match.group(0)
        swapped.add(match.group(2))
        return match.group(1) + text + match.group(3)

    html = _HEADING.sub(swap, html)
    if "Summary" in custom and "Summary" not in swapped:
        html = _HEADINGLESS_SUMMARY.sub(
            lambda m: f'{m.group(1)}<div class="section-title">{custom["Summary"]}</div>\n    {m.group(2)}', html, count=1)
    return html
