// The editor's top-bar name: a click opens a field; Enter/blur report the value, Escape reports null, and each edit reports once.
import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import ResumeName from '../screens/ResumeName'

function Harness({ onDone }) {
  const [editing, setEditing] = useState(false)
  return <ResumeName name="Volodymyr_Kotyk_Resume" editing={editing} onEdit={() => setEditing(true)}
    onDone={(v) => { setEditing(false); onDone(v) }} />
}

const open = () => {
  fireEvent.click(screen.getByRole('button', { name: /Volodymyr_Kotyk_Resume/ }))
  return screen.getByRole('textbox', { name: 'Résumé name' })
}

describe('résumé name', () => {
  it('shows the name as the page heading', () => {
    render(<Harness onDone={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Volodymyr_Kotyk_Resume')
  })

  it('opens a focused field with the current name', () => {
    render(<Harness onDone={vi.fn()} />)
    const field = open()
    expect(field.value).toBe('Volodymyr_Kotyk_Resume')
    expect(document.activeElement).toBe(field)
  })

  it('Enter reports the typed value once', () => {
    const onDone = vi.fn()
    render(<Harness onDone={onDone} />)
    const field = open()
    fireEvent.change(field, { target: { value: 'Backend — Platform' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    fireEvent.blur(field)
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onDone).toHaveBeenCalledWith('Backend — Platform')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('blur reports the typed value', () => {
    const onDone = vi.fn()
    render(<Harness onDone={onDone} />)
    const field = open()
    fireEvent.change(field, { target: { value: 'DevOps' } })
    fireEvent.blur(field)
    expect(onDone).toHaveBeenCalledWith('DevOps')
  })

  it('Escape reports null and is marked handled', () => {
    const onDone = vi.fn()
    render(<Harness onDone={onDone} />)
    const field = open()
    fireEvent.change(field, { target: { value: 'discarded' } })
    const handled = !fireEvent.keyDown(field, { key: 'Escape' })
    expect(handled).toBe(true)
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onDone).toHaveBeenCalledWith(null)
  })
})
