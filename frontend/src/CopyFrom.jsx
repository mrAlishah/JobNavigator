// "Copy from…" (MT-20): copy one company or search of another account into this
// one. Settings only (no jobs, no notes); the server drops résumé choices and makes
// the copy due at once. Rendered only with two or more accounts, so a one-account
// install shows nothing new.
import React, { useEffect, useState } from 'react'
import api from './api'
import { useAccounts } from './accounts'
import { Avatar, Button, FooterRow, HeaderRow, Heading, Helper, Label, ModalPanel, Row, Spinner, Tag } from './ui'

const KINDS = {
  companies: {
    noun: 'company', title: 'Copy a company from…',
    hint: 'Copies the careers URLs and filters. No jobs or notes. Pick the résumés to score against afterwards.',
    sub: (i) => (i.scrape_urls || [])[0] || '',
  },
  searches: {
    noun: 'search', title: 'Copy a search from…',
    hint: 'Copies the search settings. It runs at the next scheduled pass.',
    sub: (i) => [i.search_term || i.direct_url, i.location].filter(Boolean).join(' · '),
  },
}

function errMsg(e, fallback) {
  const d = e?.response?.data
  return (typeof d?.message === 'string' && d.message) || (typeof d?.detail === 'string' && d.detail) || fallback
}

function CopyModal({ kind, onClose, onCloned, pushToast }) {
  const k = KINDS[kind]
  const [groups, setGroups] = useState(null)   // null = loading
  const [busyId, setBusyId] = useState(null)
  useEffect(() => {
    let live = true
    api.get(`/${kind}/clone-sources`)
      .then(({ data }) => { if (live) setGroups((data?.accounts || []).filter((a) => (a.items || []).length)) })
      .catch((e) => { console.error(e); if (live) { setGroups([]); pushToast({ kind: 'error', msg: errMsg(e, `Could not load the other accounts' ${kind}`) }) } })
    return () => { live = false }
  }, [kind, pushToast])

  const pick = async (item) => {
    if (busyId || item.exists) return
    setBusyId(item.id)
    try {
      const { data } = await api.post(`/${kind}/clone`, { source_id: item.id })
      onClose()
      await onCloned(data)
    } catch (e) {
      console.error(e)
      pushToast({ kind: 'error', msg: errMsg(e, `Could not copy this ${k.noun}`) })
      setBusyId(null)
    }
  }

  return (
    <ModalPanel width={520} title={k.title} onClose={onClose} zIndex={60} style={{ overflow: 'hidden' }}>
      <HeaderRow align="stretch" style={{ flexDirection: 'column', gap: 3 }}>
        <Heading className="v2-dialogtitle">{k.title}</Heading>
        <Helper>{k.hint}</Helper>
      </HeaderRow>
      <div className="v2-scroll" style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 12, maxHeight: 470, overflow: 'auto' }}>
        {groups === null && <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><Spinner /></div>}
        {groups && !groups.length && <Helper style={{ display: 'block', padding: '18px 8px' }}>The other accounts have no {kind} to copy.</Helper>}
        {(groups || []).map((g) => (
          <div key={g.id} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 8px 4px' }}>
              <Avatar name={g.name} color={g.color} size={20} />
              <Label>{g.name}</Label>
            </div>
            {g.items.map((i) => (
              <Row key={i.id} onClick={i.exists ? undefined : () => pick(i)} title={i.exists ? `You already have a ${k.noun} named ${i.name}` : `Copy ${i.name}`}
                style={{ height: 'auto', minHeight: 40, padding: '6px 8px', opacity: i.exists || (busyId && busyId !== i.id) ? 'var(--disabled-opacity)' : 1 }}>
                <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
                  <span style={{ fontSize: 13, lineHeight: '18px', color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{i.name}</span>
                  <Helper style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{k.sub(i) || '—'}</Helper>
                </span>
                {i.exists && <Tag>You have it</Tag>}
                {busyId === i.id && <Spinner />}
              </Row>
            ))}
          </div>
        ))}
      </div>
      <FooterRow bg="page" style={{ flex: '0 0 auto' }}>
        <Button variant="secondary" size="sm" onClick={onClose} style={{ marginLeft: 'auto' }}>Cancel</Button>
      </FooterRow>
    </ModalPanel>
  )
}

// The header button + its modal. `onCloned(row)` gets the new row (the API's own
// company / search dict) and should refresh the list and open the row.
export default function CopyFrom({ kind, onCloned, pushToast }) {
  const { multi } = useAccounts()
  const [open, setOpen] = useState(false)
  if (!multi) return null
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>Copy from…</Button>
      {open && <CopyModal kind={kind} onClose={() => setOpen(false)} onCloned={onCloned} pushToast={pushToast} />}
    </>
  )
}
