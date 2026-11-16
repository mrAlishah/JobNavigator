// Avatar + PinPad — the two MT-12 account primitives.
//
// Avatar is pure paint: initials, size, the account colour (data) with a token
// fallback, and the win98 swatch hook. PinPad is behaviour: one digit per field,
// auto-advance, Backspace steps back, a pasted PIN fills every field, the last
// digit completes, and the caller-driven busy / error / cooldown states.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, act, cleanup } from '@testing-library/react'
import { Avatar, ModalPanel, PinPad, initialsOf, ACCOUNT_SWATCHES } from '../ui'

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('initialsOf', () => {
  it('takes the first and last word, or the first two letters of one word', () => {
    expect(initialsOf('Jordan Blake')).toBe('JB')
    expect(initialsOf('alex')).toBe('AL')
    expect(initialsOf('Mary Ann Lee')).toBe('ML')
    expect(initialsOf('  dana  ')).toBe('DA')
    expect(initialsOf('Å')).toBe('Å')
    expect(initialsOf('')).toBe('?')
    expect(initialsOf(null)).toBe('?')
  })
})

describe('Avatar', () => {
  it('draws the initials in a box of the requested size', () => {
    for (const size of [20, 22, 26, 44]) {
      const { container } = render(<Avatar name="Alex Kim" color="#2f6fed" size={size} />)
      const el = container.firstChild
      expect(el.textContent).toBe('AK')
      expect(el.style.width).toBe(`${size}px`)
      expect(el.style.height).toBe(`${size}px`)
      cleanup()
    }
  })

  it('fills with the account colour, white ink, the avatar radius token', () => {
    const { container } = render(<Avatar name="Alex" color="#D9534F" />)
    const el = container.firstChild
    expect(el.style.background).toMatch(/rgb\(217, 83, 79\)|#d9534f/)
    expect(el.style.color).toBe('var(--avatar-ink)')
    expect(el.style.borderRadius).toBe('var(--radius-avatar)')
    expect(el.className).toContain('v2-avatar')
  })

  it('marks the five default colours for the win98 swatch swap, and only those', () => {
    ACCOUNT_SWATCHES.forEach((c, i) => {
      const { container } = render(<Avatar name="A" color={c} />)
      expect(container.firstChild.getAttribute('data-swatch')).toBe(String(i))
      cleanup()
    })
    const { container } = render(<Avatar name="A" color="#123456" />)
    expect(container.firstChild.getAttribute('data-swatch')).toBe(null)
  })

  it('falls back to the token ground for a missing or non-hex colour', () => {
    for (const color of [undefined, '', 'red', 'url(x)', '#12345']) {
      const { container } = render(<Avatar name="A" color={color} />)
      expect(container.firstChild.style.background).toBe('var(--avatar-bg)')
      cleanup()
    }
  })

  it('names itself for assistive tech', () => {
    const { container } = render(<Avatar name="Dana" />)
    expect(container.firstChild.getAttribute('role')).toBe('img')
    expect(container.firstChild.getAttribute('aria-label')).toBe('Dana')
  })
})

const cells = (c) => [...c.querySelectorAll('input')]
const type = (el, v) => fireEvent.change(el, { target: { value: v } })

describe('PinPad', () => {
  it('draws four masked numeric fields and focuses the first', () => {
    const { container } = render(<PinPad />)
    const cs = cells(container)
    expect(cs).toHaveLength(4)
    cs.forEach((el) => {
      expect(el.type).toBe('password')
      expect(el.getAttribute('inputmode')).toBe('numeric')
    })
    expect(document.activeElement).toBe(cs[0])
  })

  it('advances on each digit and completes on the last', () => {
    const onComplete = vi.fn()
    const { container } = render(<PinPad onComplete={onComplete} />)
    const cs = cells(container)
    type(cs[0], '1'); expect(document.activeElement).toBe(cs[1])
    type(cs[1], '2'); expect(document.activeElement).toBe(cs[2])
    type(cs[2], '3'); expect(document.activeElement).toBe(cs[3])
    expect(onComplete).not.toHaveBeenCalled()
    type(cs[3], '4')
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith('1234')
  })

  it('ignores anything that is not a digit', () => {
    const onComplete = vi.fn()
    const { container } = render(<PinPad onComplete={onComplete} />)
    const cs = cells(container)
    type(cs[0], 'a')
    expect(cs[0].value).toBe('')
    expect(document.activeElement).toBe(cs[0])
  })

  it('Backspace on an empty field steps back and clears the previous digit', () => {
    const { container } = render(<PinPad />)
    const cs = cells(container)
    type(cs[0], '7'); type(cs[1], '8')
    expect(document.activeElement).toBe(cs[2])
    fireEvent.keyDown(cs[2], { key: 'Backspace' })
    expect(document.activeElement).toBe(cs[1])
    expect(cs[1].value).toBe('')
    expect(cs[0].value).toBe('7')
  })

  it('a pasted PIN fills every field and completes', () => {
    const onComplete = vi.fn()
    const { container } = render(<PinPad onComplete={onComplete} />)
    const cs = cells(container)
    fireEvent.paste(cs[2], { clipboardData: { getData: () => ' 98-76 ' } })
    expect(cs.map((el) => el.value).join('')).toBe('9876')
    expect(onComplete).toHaveBeenCalledWith('9876')
  })

  it('shows a spinner and goes read-only while busy', () => {
    const { container } = render(<PinPad busy />)
    expect(container.querySelector('[role="group"]').getAttribute('aria-busy')).toBe('true')
    expect(container.querySelector('.v2-spin')).not.toBeNull()
    cells(container).forEach((el) => expect(el.readOnly).toBe(true))
    expect(container.textContent).toContain('Checking…')
  })

  it('an error marks the fields invalid, and a new resetKey clears them and shakes', () => {
    const onComplete = vi.fn()
    const { container, rerender } = render(<PinPad onComplete={onComplete} resetKey={0} />)
    let cs = cells(container)
    type(cs[0], '1'); type(cs[1], '1'); type(cs[2], '1'); type(cs[3], '1')
    rerender(<PinPad onComplete={onComplete} resetKey={1} error="Wrong PIN · 4 tries left" />)
    cs = cells(container)
    expect(container.textContent).toContain('Wrong PIN · 4 tries left')
    cs.forEach((el) => {
      expect(el.getAttribute('aria-invalid')).toBe('true')
      expect(el.value).toBe('')
    })
    expect(container.querySelector('.v2-shake')).not.toBeNull()
    expect(document.activeElement).toBe(cs[0])
  })

  it('counts a cooldown down in seconds, disabled, then hands control back', () => {
    vi.useFakeTimers()
    const onEnd = vi.fn()
    const { container } = render(<PinPad cooldown={3} onCooldownEnd={onEnd} />)
    expect(container.textContent).toContain('try again in 3s')
    cells(container).forEach((el) => expect(el.disabled).toBe(true))
    act(() => { vi.advanceTimersByTime(1000) })
    expect(container.textContent).toContain('try again in 2s')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(container.textContent).toContain('try again in 1s')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(container.textContent).not.toContain('try again')
    cells(container).forEach((el) => expect(el.disabled).toBe(false))
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('shows the idle hint when nothing else is going on', () => {
    const { container } = render(<PinPad hint="Enter Jordan's PIN" />)
    expect(container.textContent).toContain("Enter Jordan's PIN")
  })
})

// QA F7 / R6-05: the gate's accessibility — the status line describes every cell,
// and a trapping ModalPanel keeps Tab inside and has a name.
describe('R6-05 accessibility', () => {
  it('every PIN cell is described by the status line', () => {
    const { container } = render(<PinPad error="Wrong PIN · 3 tries left" ariaLabel="Ann's PIN" />)
    const cells = container.querySelectorAll('input')
    const id = cells[0].getAttribute('aria-describedby')
    expect(id).toBeTruthy()
    cells.forEach((c) => expect(c.getAttribute('aria-describedby')).toBe(id))
    expect(container.ownerDocument.getElementById(id).textContent).toBe('Wrong PIN · 3 tries left')
  })

  it('a trapFocus panel takes focus and wraps Tab; it is named by its title', async () => {
    vi.useFakeTimers()
    const outside = document.createElement('button'); outside.textContent = 'behind'; document.body.appendChild(outside)
    outside.focus()
    const { getByRole, getByText } = render(<ModalPanel title="Choose an account" trapFocus><button>one</button><button>two</button></ModalPanel>)
    act(() => { vi.runAllTimers() })
    expect(getByRole('dialog').getAttribute('aria-label')).toBe('Choose an account')
    expect(document.activeElement).toBe(getByText('one'))
    getByText('two').focus()
    fireEvent.keyDown(document.activeElement, { key: 'Tab' })
    expect(document.activeElement).toBe(getByText('one'))
    fireEvent.keyDown(document.activeElement, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(getByText('two'))
    act(() => { outside.focus() })
    expect(document.activeElement).toBe(getByText('one'))
    outside.remove()
  })
})
