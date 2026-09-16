// ZoomFloater — the posting pane's + / − pill.
//
// The primitive owns none of the level: it is handed `zoom` and reports clicks.
// So what has to hold is (a) the two steps exist and are labelled, (b) the pill
// says what it does in its title, (c) a click at the clamp is SWALLOWED — the
// caller must never be asked to step past 200 or below 50 — and (d) a
// double-click on the pill resets.
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { ZoomFloater } from '../ui'

const plus = (c) => c.querySelector('[aria-label="Zoom in"]')
const minus = (c) => c.querySelector('[aria-label="Zoom out"]')

describe('ZoomFloater', () => {
  it('draws a + and a − step', () => {
    const { container } = render(<ZoomFloater zoom={100} />)
    expect(plus(container).textContent).toBe('+')
    expect(minus(container).textContent).toBe('−')   // U+2212, not a hyphen
  })

  it('names the level and both gestures in the pill title', () => {
    const { container } = render(<ZoomFloater zoom={130} />)
    expect(container.firstChild.getAttribute('title'))
      .toBe('Zoom 130% · double-click to reset · hide in Settings')
  })

  it('calls onIn / onOut in the middle of the range', () => {
    const onIn = vi.fn(); const onOut = vi.fn()
    const { container } = render(<ZoomFloater zoom={100} onIn={onIn} onOut={onOut} />)
    fireEvent.click(plus(container))
    fireEvent.click(minus(container))
    expect(onIn).toHaveBeenCalledTimes(1)
    expect(onOut).toHaveBeenCalledTimes(1)
  })

  it('dims + at 200 and swallows the click', () => {
    const onIn = vi.fn(); const onOut = vi.fn()
    const { container } = render(<ZoomFloater zoom={200} onIn={onIn} onOut={onOut} />)
    expect(plus(container).getAttribute('aria-disabled')).toBe('true')
    expect(plus(container).style.opacity).toBe('0.35')
    expect(minus(container).getAttribute('aria-disabled')).toBe(null)
    fireEvent.click(plus(container))
    expect(onIn).not.toHaveBeenCalled()
    fireEvent.click(minus(container))
    expect(onOut).toHaveBeenCalledTimes(1)
  })

  it('dims − at 50 and swallows the click', () => {
    const onIn = vi.fn(); const onOut = vi.fn()
    const { container } = render(<ZoomFloater zoom={50} onIn={onIn} onOut={onOut} />)
    expect(minus(container).getAttribute('aria-disabled')).toBe('true')
    expect(minus(container).style.opacity).toBe('0.35')
    expect(plus(container).getAttribute('aria-disabled')).toBe(null)
    fireEvent.click(minus(container))
    expect(onOut).not.toHaveBeenCalled()
    fireEvent.click(plus(container))
    expect(onIn).toHaveBeenCalledTimes(1)
  })

  it('resets on a double-click of the pill', () => {
    const onReset = vi.fn()
    const { container } = render(<ZoomFloater zoom={170} onReset={onReset} />)
    fireEvent.doubleClick(container.firstChild)
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  // The pill floats over a pane whose ancestors select on click: nothing it
  // handles may reach them.
  it('never lets a click or a double-click through to the pane behind it', () => {
    const behind = vi.fn()
    const { container } = render(
      <div onClick={behind} onDoubleClick={behind}>
        <ZoomFloater zoom={100} onIn={() => {}} onOut={() => {}} onReset={() => {}} />
      </div>)
    fireEvent.click(plus(container.firstChild))
    fireEvent.click(minus(container.firstChild))
    fireEvent.doubleClick(container.firstChild.firstChild)
    expect(behind).not.toHaveBeenCalled()
  })

  it('is keyboard-operable', () => {
    const onIn = vi.fn()
    const { container } = render(<ZoomFloater zoom={100} onIn={onIn} />)
    expect(plus(container).getAttribute('role')).toBe('button')
    expect(plus(container).getAttribute('tabindex')).toBe('0')
    fireEvent.keyDown(plus(container), { key: 'Enter' })
    expect(onIn).toHaveBeenCalledTimes(1)
  })
})
