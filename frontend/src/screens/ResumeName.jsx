import React, { useRef } from 'react'
import { Input } from '../ui'

const TITLE = { margin: 0, fontFamily: 'var(--font-body)', fontSize: 14, lineHeight: '20px', fontWeight: 600, minWidth: 0, maxWidth: 460 }

// The résumé name in the editor's top bar. A click on it (or ⋯ → Rename) swaps in a field:
// Enter or blur calls onDone(value), Escape calls onDone(null). The caller decides what a blank or unchanged value means.
export default function ResumeName({ name, editing, onEdit, onDone }) {
  if (editing) return <NameField name={name} onDone={onDone} />
  return (
    // every other v2 screen names itself with an h1; the button inside makes the name keyboard-editable
    <h1 style={TITLE}>
      <button type="button" onClick={onEdit} title={`${name} — click to rename`}
        style={{ all: 'unset', display: 'block', cursor: 'text', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 460 }}>
        {name}
      </button>
    </h1>
  )
}

// Mounted fresh for each edit, so `done` starts false every time. It stops the blur that
// follows Enter/Escape (the field unmounts while focused) from reporting a second result.
function NameField({ name, onDone }) {
  const done = useRef(false)
  const finish = (value) => { if (!done.current) { done.current = true; onDone(value) } }
  return (
    <Input autoFocus defaultValue={name} ariaLabel="Résumé name" onFocus={(e) => e.target.select()}
      onBlur={(e) => finish(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); finish(e.currentTarget.value) }
        // marked handled so the screen's own Escape listeners leave it alone
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(null) }
      }}
      // 26px field in the h1's 20px line: the -3px margins keep the top bar from growing while editing
      style={{ ...TITLE, height: 26, margin: '-3px 0', width: 460 }} />
  )
}
