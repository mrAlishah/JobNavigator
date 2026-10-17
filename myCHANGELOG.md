# myCHANGELOG

Changes made in this fork on top of upstream JobNavigator. Upstream's own release history stays in
[CHANGELOG.md](CHANGELOG.md); do not record fork changes there.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Select & style for the Professional résumés:** a "Select & style" button in the résumé preview swaps the PDF for a clickable view of the same template. Click the page, the profile image, a section or any part of one (a heading, a job's title, its dates, description or bullets, a skills label, a school…) and a panel sets that part's font, size, line height, colour, bold, italic, alignment, case, letter spacing, margin and padding; a heading also takes its rule line, bullets their character and indent, the page its margins and the image a width and height. A change applies to every instance of the part. "Back to PDF" shows the exact print layout.
- **Margin and padding on every section and card:** each section (Header, Summary, Experience, Skills, Education, Projects, Publications) takes margin and padding on all four sides, and the repeating blocks (one job, school, project or publication) are selectable cards with the same properties as any other part. In the PDF they are plain CSS; in Word, where there are no boxes, they become space before the first and after the last paragraph and an indent on every paragraph of the section or card.
- **Manrope font:** the Professional templates ship Manrope (seven weights, `fonts/Manrope-*.ttf`) and the style panel lists it beside the other fonts. The PDF embeds it only when a section or part uses it (about 0.9 MB of extra HTML for that render; résumés that never pick it carry nothing). Word has no font embedding here, so a Word file names "Manrope" and shows it only where it is installed. The `.ttf` files are git-ignored (`.gitignore`), so they exist in a local checkout and a local Docker build but are not committed.
- **Style saved with the résumé and applied to both exports:** values live in `json_data.style`, are validated server-side (fixed font list, `#RRGGBB` colours, clamped numbers) and print in the PDF and the Word export. Other templates ignore them; a template opts in with `"style_overrides": true` in its `meta.json` (see CONTRIBUTING.md).
- **Style in Word exports:** margin and padding become paragraph spacing and indents (Word has no padding; a heading's bottom padding is the gap above its rule line), letter spacing rounds to Word's units, a picture is centre-cropped to the chosen width and height like the PDF's cover crop, and heading paragraphs keep their XML in the order Word requires.

### Changed
- **Settings section of a résumé** keeps only the "Show profile image" and "Show footer" checkboxes; all styling is done through Select & style.
- **Profile image upload:** files up to 10 MB are accepted (was 2 MB), and the stored copy fits 300×300 px (was 120×120) so the image-size option stays sharp.
