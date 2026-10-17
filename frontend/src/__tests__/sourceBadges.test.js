// Search-mode badges (--sm-* tokens in theme.css): each source keeps its own colour, and the
// text reads on its fill (WCAG AA, 4.5:1) in light and dark. TrulyRemoteWork is olive (PR #32
// review R12), so it no longer repeats Jobright's teal.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Read from disk: Vitest's CSS handling hands `?raw` imports of a .css file back empty, and
// under jsdom import.meta.url is not a file URL.
const css = readFileSync(resolve(__dirname, '../theme.css'), 'utf8')

const PAIR = /--sm-([\w]+)-bg:(#[0-9a-fA-F]{6}); --sm-\1-fg:(#[0-9a-fA-F]{6});/g
// The light block comes first in theme.css, then the dark one.
const blocks = () => {
  const all = [...css.matchAll(PAIR)].map(([, name, bg, fg]) => ({ name, bg: bg.toLowerCase(), fg: fg.toLowerCase() }))
  const half = all.length / 2
  return { light: all.slice(0, half), dark: all.slice(half) }
}

const luminance = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('search-mode badge tokens', () => {
  it('declares every badge in both the light and the dark block', () => {
    const { light, dark } = blocks()
    expect(light.length).toBeGreaterThan(0)
    expect(light.map((p) => p.name)).toEqual(dark.map((p) => p.name))
  })

  it('TrulyRemoteWork is olive in light and dark', () => {
    const { light, dark } = blocks()
    expect(light.find((p) => p.name === 'trulyremote')).toEqual({ name: 'trulyremote', bg: '#eef0e0', fg: '#5a6326' })
    expect(dark.find((p) => p.name === 'trulyremote')).toEqual({ name: 'trulyremote', bg: '#2d3121', fg: '#c5cf96' })
  })

  it('no two sources share a text colour, and every badge reads at 4.5:1', () => {
    for (const block of Object.values(blocks())) {
      const fgs = block.map((p) => p.fg)
      expect(new Set(fgs).size).toBe(fgs.length)
      for (const p of block) expect(contrast(p.bg, p.fg), p.name).toBeGreaterThanOrEqual(4.5)
    }
  })
})
