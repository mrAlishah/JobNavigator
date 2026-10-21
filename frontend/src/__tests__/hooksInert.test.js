import { describe, it, expect } from 'vitest'
import { IS_DEMO, ROUTER_BASENAME, loadDemo } from '../demoHooks'

// The demo layer lives in another repo; in a normal checkout the hooks must do nothing.
describe('demo hooks in a normal build', () => {
  it('are off and add no router basename', () => {
    expect(IS_DEMO).toBe(false)
    expect(ROUTER_BASENAME).toBeUndefined()
  })
  it('refuse to load a layer module that is not there', async () => {
    await expect(loadDemo('install.js')).rejects.toThrow(/not available/)
  })
})
