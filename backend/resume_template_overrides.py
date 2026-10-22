"""Fork behaviour the renderer layers on the official (upstream-owned) résumé templates.

Those template files stay as JobNavigator ships them, so an updated or newly added upstream template needs no
patch. `_render_html` passes `footer_reserved_in` and `section_titles` to every template; a template that
reads one of them (the Professional templates, helvetica) handles that part itself, and the matching function
here is not applied (the same "does the template mention it" check as `has_profile_image`). Otherwise:

* print_layout: the footer's reserved band at the bottom of every printed page, the template's own top and
  side `@page` margins kept, and entries kept whole across a page break;
* section_titles: custom section headings replace the default text (`json_data["section_titles"]`).

A template without the expected structure is left as it is, which is upstream's own rendering;
`test_resume_template_ownership.py` fails for such a template so the difference is noticed.
"""
import re

from markupsafe import escape

# `@page { ... margin: <top> <sides>; ... }` as the official templates write it
_PAGE_MARGIN = re.compile(r"@page\s*\{[^}]*?\bmargin:\s*(\S+)\s+(\S+)\s*;")
_HEADINGS = (("summary", "Summary"), ("experience", "Experience"), ("skills", "Skills"),
             ("education", "Education"), ("projects", "Projects"), ("publications", "Publications"))
# a section whose first child is the summary text: this template prints no Summary heading
_HEADINGLESS_SUMMARY = re.compile(r'(<div class="section"[^>]*>\s*)(<div class="summary-text">)')


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
    résumé's own title, HTML-escaped. A template with no Summary heading gets one when a summary title is set."""
    for key, default in _HEADINGS:
        if not titles.get(key):
            continue
        text = str(escape(titles[key]))
        html, replaced = re.subn(rf'(<div class="section-title">)\s*{default}\s*(</div>)',
                                 lambda m, text=text: m.group(1) + text + m.group(2), html)
        if key == "summary" and not replaced:
            html = _HEADINGLESS_SUMMARY.sub(lambda m, text=text: f'{m.group(1)}<div class="section-title">{text}</div>\n    {m.group(2)}', html, count=1)
    return html
