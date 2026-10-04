/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

/** Declarations of the first rule whose selector is exactly `selector` (outside any @media). */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(`\n${selector} {`)
  expect(start, selector).toBeGreaterThanOrEqual(0)
  const open = css.indexOf('{', start)
  const body = css.slice(open + 1, css.indexOf('\n}', open))
  const out: Record<string, string> = {}
  for (const m of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim()
  return out
}

const LIGHT = block(':root')
const DARK = block(':root:is([data-theme="dark"], [data-theme="oled"])')
const THEMES: Record<string, Record<string, string>> = {
  light: LIGHT,
  dark: { ...LIGHT, ...DARK },
  oled: { ...LIGHT, ...DARK, ...block(':root[data-theme="oled"]') },
  sepia: { ...LIGHT, ...block(':root[data-theme="sepia"]') },
}

function resolve(theme: Record<string, string>, name: string): string {
  let v = theme[name]
  const ref = /^var\((--[\w-]+)\)$/
  while (v && ref.test(v)) v = theme[v.match(ref)![1]]
  expect(v, name).toBeTruthy()
  return v
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  s /= 100
  l /= 100
  const k = (n: number) => (n + h / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return [f(0) * 255, f(8) * 255, f(4) * 255]
}

function rgb(color: string): [number, number, number] {
  const hex = color.match(/^#([0-9a-f]{6})$/i)
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)) as [number, number, number]
  const hsl = color.match(/^hsl\(\s*(\d+)\s+(\d+)%\s+(\d+)%\s*\)$/)
  if (hsl) return hslToRgb(+hsl[1], +hsl[2], +hsl[3])
  throw new Error(`unsupported colour ${color}`)
}

function luminance(color: string): number {
  const [r, g, b] = rgb(color).map((c) => {
    const x = c / 255
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const PAIRS: [string, string][] = [
  ['--text', '--bg'],
  ['--text', '--surface'],
  ['--muted', '--bg'],
  ['--muted', '--surface'],
  ['--on-accent', '--accent'],
  ['--text', '--highlight'],
  ['--accent', '--bg'],
  ['--accent', '--surface'],
  ['--warn', '--bg'],
  ['--bad', '--bg'],
  ['--ok', '--bg'],
  ['--info', '--bg'],
  ['--speaker-1-fg', '--speaker-1-bg'],
  ['--speaker-2-fg', '--speaker-2-bg'],
  ['--speaker-3-fg', '--speaker-3-bg'],
  ['--speaker-4-fg', '--speaker-4-bg'],
]

describe('theme colour contrast (WCAG AA, 4.5:1)', () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const [fg, bg] of PAIRS) {
      it(`${name}: ${fg} on ${bg}`, () => {
        expect(contrast(resolve(theme, fg), resolve(theme, bg))).toBeGreaterThanOrEqual(4.5)
      })
    }
  }

  it('the system-dark media block carries the same values as data-theme=dark', () => {
    const media = css.slice(css.indexOf('@media (prefers-color-scheme: dark)'))
    const body = media.slice(media.indexOf('{', media.indexOf(':root:not(')) + 1, media.indexOf('\n  }'))
    for (const m of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) expect(DARK[m[1]], m[1]).toBe(m[2].trim())
  })
})
