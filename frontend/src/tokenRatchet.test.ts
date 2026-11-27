/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n: string) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? cssFiles(p) : p.endsWith('.css') ? [p] : []
  })
}
const css = cssFiles(fileURLToPath(new URL('.', import.meta.url)))
  .map((f) => readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ''))
  .join('\n')

/** Distinct values of `property` that carry a literal number (px, rem, em, %, or a bare line-height multiplier). */
function rawValues(property: RegExp): Set<string> {
  const out = new Set<string>()
  for (const m of css.matchAll(new RegExp(`(?:^|[;{\\s])${property.source}\\s*:\\s*([^;}]+)`, 'g'))) {
    const v = m[1].replace(/\s*!important/, '').replace(/\s+/g, ' ').trim()
    if (/\d/.test(v.replace(/var\([^)]*\)/g, '').replace(/\b0(px)?\b/g, ''))) out.add(v)
  }
  return out
}

// Ceilings, not targets: lower them when a screen moves to the role tokens (--gap-*, --pad-block, --radius*, --leading*).
// A new literal value fails here, so add a token instead of another number.
const CEILING = { padding: 75, radius: 20, lineHeight: 13 }

describe('token ratchet', () => {
  it('has no more distinct raw paddings than the ceiling', () => {
    expect(rawValues(/padding(?:-(?:block|inline)(?:-(?:start|end))?|-top|-bottom|-left|-right)?/).size).toBeLessThanOrEqual(CEILING.padding)
  })
  it('has no more distinct raw radii than the ceiling', () => {
    expect(rawValues(/border-radius/).size).toBeLessThanOrEqual(CEILING.radius)
  })
  it('has no more distinct raw line heights than the ceiling', () => {
    expect(rawValues(/line-height/).size).toBeLessThanOrEqual(CEILING.lineHeight)
  })
})
