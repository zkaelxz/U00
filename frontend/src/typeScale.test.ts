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
  it('self-hosts Figtree, Fraunces and the Atkinson fallback, each with its licence', () => {
    for (const f of [
      'figtree-latin-wght-normal.woff2', 'figtree-latin-ext-wght-normal.woff2', 'OFL-Figtree.txt',
      'fraunces-latin-600-normal.woff2', 'fraunces-latin-ext-600-normal.woff2', 'OFL-Fraunces.txt',
      'atkinson-hyperlegible-latin-400-normal.woff2', 'atkinson-hyperlegible-latin-700-normal.woff2', 'OFL-Atkinson-Hyperlegible.txt',
    ]) {
      expect(existsSync(new URL(`../public/fonts/${f}`, import.meta.url)), f).toBe(true)
    }
    const faces = index.match(/@font-face \{[^}]*\}/g) ?? []
    expect(faces).toHaveLength(7)
    for (const face of faces) {
      expect(face).toContain('font-display: swap')
      expect(face).toContain('unicode-range')
      // Only the bundled files: the app makes no request to the internet for fonts.
      const src = face.match(/url\('([^']+)'\)/)?.[1] ?? ''
      expect(src, face).toMatch(/^\/fonts\/[\w-]+\.woff2$/)
      expect(existsSync(new URL(`../public${src}`, import.meta.url)), src).toBe(true)
    }
  })

  it('has one scale: no legacy --font-sm/--font-md and no literal px or rem font sizes', () => {
    for (const f of files) {
      const css = readFileSync(f, 'utf8')
      expect(css, f).not.toMatch(/--font-(sm|md)\b/)
      if (!f.endsWith('diskUsage.css') && !f.endsWith('comic.css')) expect(css, f).not.toMatch(/font-size:\s*[\d.]+(rem|px)/)
    }
  })

  it('uses the heading face (Fraunces) only for page titles and the names of titles and series', () => {
    const allowed = ['.page-title', '.continue-title', '.drama-card-title', '.table-title', '.cover-title', '.workspace-header h1']
    const used: string[] = []
    for (const f of files) {
      const css = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
      for (const m of css.matchAll(/([^{}]+)\{[^{}]*var\(--font-heading\)[^{}]*\}/g)) used.push(m[1].trim().replace(/\s+/g, ' '))
    }
    expect(used.filter((sel) => !allowed.includes(sel))).toEqual([])
  })

  it('defines the type roles on top of the scale', () => {
    for (const r of ['meta', 'body', 'label', 'title', 'page']) expect(index).toMatch(new RegExp(`--type-${r}: var\\(--text-`))
  })

  it('declares a CJK stack per language', () => {
    for (const l of ['zh', 'ja', 'ko']) expect(index).toContain(`:lang(${l})`)
    expect(index).toMatch(/:lang\(zh\) \{ font-family: 'Figtree Latin', var\(--font-zh\)/)
  })

  it('breaks Korean at word boundaries and Japanese with strict kana rules', () => {
    expect(index).toMatch(/:lang\(ko\) \{[^}]*word-break: keep-all/)
    expect(index).toMatch(/:lang\(ja\) \{[^}]*line-break: strict/)
  })
})
