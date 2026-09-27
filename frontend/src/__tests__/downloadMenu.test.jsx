// The résumé toolbar's Download button opens a menu offering PDF and Word.
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DownloadMenu } from '../screens/ResumeEditor'

const urls = { pdfUrl: '/api/resumes/1/pdf?format=a4', docxUrl: '/api/resumes/1/docx?format=a4' }

describe('DownloadMenu', () => {
  it('starts closed, with a menu-popup button', () => {
    render(<DownloadMenu {...urls} />)
    const btn = screen.getByRole('button', { name: /Download/ })
    expect(btn.getAttribute('aria-haspopup')).toBe('menu')
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('Word (.docx)')).toBeNull()
  })

  it('offers PDF and Word as real download links', () => {
    render(<DownloadMenu {...urls} />)
    fireEvent.click(screen.getByRole('button', { name: /Download/ }))
    expect(screen.getByText('PDF').closest('a').getAttribute('href')).toBe(urls.pdfUrl)
    expect(screen.getByText('Word (.docx)').closest('a').getAttribute('href')).toBe(urls.docxUrl)
  })

  it('closes after a choice', () => {
    render(<DownloadMenu {...urls} />)
    fireEvent.click(screen.getByRole('button', { name: /Download/ }))
    fireEvent.click(screen.getByText('Word (.docx)'))
    expect(screen.queryByText('Word (.docx)')).toBeNull()
  })
})
