/// <reference types="node" />
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n: string) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? cssFiles(p) : p.endsWith('.css') ? [p] : []
  })
}
// fileURLToPath, not .pathname: on Windows .pathname starts '/E:/' and the drive letter ends up doubled.
const files = cssFiles(fileURLToPath(new URL('.', import.meta.url)))
const index = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

describe('type foundation', () => {
  it('self-hosts both Atkinson weights and the licence', () => {
    for (const f of ['atkinson-hyperlegible-latin-400-normal.woff2', 'atkinson-hyperlegible-latin-700-normal.woff2', 'OFL-Atkinson-Hyperlegible.txt']) {
      expect(existsSync(new URL(`../public/fonts/${f}`, import.meta.url)), f).toBe(true)
    }
    expect(index.match(/font-display: swap/g)).toHaveLength(2)
    expect(index).toContain('unicode-range')
  })

  it('has one scale: no legacy --font-sm/--font-md and no literal px or rem font sizes', () => {
    for (const f of files) {
      const css = readFileSync(f, 'utf8')
      expect(css, f).not.toMatch(/--font-(sm|md)\b/)
      if (!f.endsWith('diskUsage.css') && !f.endsWith('comic.css')) expect(css, f).not.toMatch(/font-size:\s*[\d.]+(rem|px)/)
    }
  })

  it('declares a CJK stack per language', () => {
    for (const l of ['zh', 'ja', 'ko']) expect(index).toContain(`:lang(${l})`)
  })
})
