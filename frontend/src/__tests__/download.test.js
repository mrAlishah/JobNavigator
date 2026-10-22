// The name a résumé or cover-letter download is saved under comes from the server's
// Content-Disposition: the RFC 5987 filename* first (it keeps José or Дана), then the plain one.
import { describe, it, expect } from 'vitest'
import { downloadErrorReason, downloadName } from '../download'

describe('downloadName', () => {
  it('prefers filename* (RFC 5987) and decodes it', () => {
    expect(downloadName("attachment; filename=\"Resume.pdf\"; filename*=UTF-8''%D0%94%D0%B0%D0%BD%D0%B0_Resume.pdf", 'x'))
      .toBe('Дана_Resume.pdf')
    expect(downloadName("attachment; filename*=utf-8''%E7%8E%8B_CV.docx", 'x')).toBe('王_CV.docx')
  })

  it('reads a cover-letter header the same way', () => {
    expect(downloadName("attachment; filename=\"JoseMuller_Acme_CoverLetter_7.pdf\"; filename*=UTF-8''Jos%C3%A9M%C3%BCller_Acme_CoverLetter_7.pdf", 'CoverLetter.pdf'))
      .toBe('JoséMüller_Acme_CoverLetter_7.pdf')
  })

  it('falls back to the plain filename, quoted or bare', () => {
    expect(downloadName('attachment; filename="Dana_Base_Resume_7.docx"', 'x')).toBe('Dana_Base_Resume_7.docx')
    expect(downloadName('attachment; filename=Dana.pdf', 'x')).toBe('Dana.pdf')
    expect(downloadName("attachment; filename=\"Jose.pdf\"; filename*=UTF-8''%E0%A4%A", 'x')).toBe('Jose.pdf')   // malformed escape
  })

  it('uses the fallback when there is no name', () => {
    expect(downloadName(undefined, 'Resume.pdf')).toBe('Resume.pdf')
    expect(downloadName('attachment', 'Resume.docx')).toBe('Resume.docx')
  })
})

describe('downloadErrorReason (R30 D-07)', () => {
  it('reads detail out of a Blob error body, a JSON string, or a plain object', async () => {
    const blob = new Blob([JSON.stringify({ detail: 'Résumé too large' })], { type: 'application/json' })
    expect(await downloadErrorReason({ response: { data: blob } })).toBe('Résumé too large')
    expect(await downloadErrorReason({ response: { data: '{"detail":"x"}' } })).toBe('x')
    expect(await downloadErrorReason({ response: { data: { message: 'm' } } })).toBe('m')
  })
  it('is empty when there is no readable reason', async () => {
    expect(await downloadErrorReason(new Error('network'))).toBe('')
    expect(await downloadErrorReason({ response: { data: new Blob(['<html>']) } })).toBe('')
    expect(await downloadErrorReason({ response: { data: { detail: [{ msg: 'x' }] } } })).toBe('')
  })
})
