import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx$/.test(name) && !/\.test\./.test(name) ? [path] : []
  })
}

// A <button> whose label or aria-label says Cancel must use the kit class,
// which carries the 44px touch target; a bare button silently misses it.
describe('Cancel buttons', () => {
  it('all use buttonClass', () => {
    const bare: string[] = []
    for (const file of sources(join(__dirname, '..'))) {
      const text = readFileSync(file, 'utf8')
      for (const m of text.matchAll(/<button\b/g)) {
        const end = text.indexOf('</button>', m.index)
        const block = text.slice(m.index, end)
        if (/\bCancel\b/.test(block) && !block.includes('buttonClass')) {
          bare.push(`${file}:${text.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(bare).toEqual([])
  })
})
