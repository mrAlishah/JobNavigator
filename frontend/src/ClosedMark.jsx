// A red dot and "Closed" when the job's posting looks closed (backend still_open.py). The check runs
// when an editor for the job opens; the server keeps its answer a day, so reopening asks nothing new.
import React, { useEffect, useState } from 'react'
import api from './api'
import { Dot } from './ui'
import { closedTag } from './linkKinds'

export default function ClosedMark({ jobId }) {
  const [state, setState] = useState(null)
  const [force, setForce] = useState(0)   // a click on the mark checks again, ignoring the day-old answer
  useEffect(() => {
    if (!jobId || __DEMO__) return undefined
    let gone = false
    api.post(`/jobs/${jobId}/open-check`, null, { params: force ? { force: 1 } : {} }).then(({ data }) => { if (!gone) setState(data) })
      .catch(() => { /* silent: the mark is a hint; nothing shows when the check fails */ })
    return () => { gone = true }
  }, [jobId, force])
  const tag = closedTag(state)
  if (!tag) return null
  return (
    <span title={tag.title} onClick={() => setForce((n) => n + 1)} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginLeft: 10, color: 'var(--dot-bad)', cursor: 'pointer' }}>
      <Dot tone="bad" />{tag.text}
    </span>
  )
}
