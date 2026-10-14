import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
const frost = css('./frost.css')
const workspace = css('./pages/workspace/workspace.css')

// Rules whose selector targets a page tab strip (Tabs, the workspace stage strip, Discover's strip).
function tabRules(source: string) {
  return [...source.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(([, selector, body]) => ({ selector: selector.replace(/\/\*[\s\S]*?\*\//g, '').trim(), body }))
    .filter((r) => /\.ui-tab\b|\.ui-tabs\b|\.stage-tabs\b|\.discover-tab/.test(r.selector))
}

describe('page tabs are underline tabs', () => {
  it('frost.css gives no tab strip a tray, a filled pill or a raised shadow', () => {
    for (const r of tabRules(frost)) {
      expect(r.body, r.selector).not.toMatch(/background:\s*var\(--(active|glass)\)/)
      expect(r.body, r.selector).not.toMatch(/box-shadow:\s*var\(--btn-shadow\)/)
      expect(r.body, r.selector).not.toMatch(/border-radius:\s*var\(--radius(-panel)?\)/)
      expect(r.body, r.selector).not.toMatch(/inset 0 0 0 1px/)
    }
  })

  it('no workspace stage looks disabled: every tab stays at full strength', () => {
    for (const r of tabRules(workspace)) expect(r.body, r.selector).not.toMatch(/opacity/)
  })
})
