/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')
const reviewCss = readFileSync(new URL('./pages/workspace/stages/review/review.css', import.meta.url), 'utf8')

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
  ['--muted', '--highlight'],
  ['--muted', '--surface-2'],
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

// Field edges and the focus ring are UI components, not text: WCAG 1.4.11 and 2.4.7 ask 3:1.
describe('field edge and focus ring (3:1)', () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const base of ['--bg', '--surface', '--field-bg']) {
      it(`${name}: --field-edge on ${base}`, () => {
        expect(contrast(resolve(theme, '--field-edge'), resolve(theme, base))).toBeGreaterThanOrEqual(3)
      })
    }
    for (const base of ['--bg', '--surface', '--surface-2']) {
      it(`${name}: the focus ring (--accent) on ${base}`, () => {
        expect(contrast(resolve(theme, '--accent'), resolve(theme, base))).toBeGreaterThanOrEqual(3)
      })
    }
  }

  it('draws focus as a solid accent outline, not only the translucent halo', () => {
    expect(css).toMatch(/\n:focus-visible \{[^}]*outline: 2px solid var\(--accent\);/)
    expect(css).toMatch(/\ninput:focus, select:focus, textarea:focus \{[^}]*outline: 2px solid var\(--accent\);/)
    expect(css).toMatch(/\.drama-card:has\(a\.drama-card-title:focus-visible\) \{[^}]*outline: 2px solid var\(--accent\);/)
    expect(css).toMatch(/@media \(forced-colors: active\) \{\s*:focus-visible \{ outline: 2px solid CanvasText !important; \}/)
  })
})

// Glass panels are translucent, so the colour under the text is the glass blended over the wash blob over the page.
// A blob is drawn at 60% (frost.css body rule); the worse of the two blobs is the one that counts.
describe('text on frost glass over the wash', () => {
  const frost = readFileSync(new URL('./frost.css', import.meta.url), 'utf8')
  const frostBlock = (selector: string) => {
    const open = frost.indexOf('{', frost.indexOf(`\n${selector} {`))
    expect(open, selector).toBeGreaterThan(0)
    const body = frost.slice(open + 1, frost.indexOf('\n}', open))
    return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]))
  }
  const light = frostBlock(':root')
  const dark = frostBlock(':root:is([data-theme="dark"], [data-theme="oled"])')
  const FROST: Record<string, Record<string, string>> = {
    light,
    dark,
    oled: { ...dark, ...frostBlock(':root[data-theme="oled"]') },
    sepia: { ...light, ...frostBlock(':root[data-theme="sepia"]') },
  }

  function blend(top: string, base: string): string {
    const m = top.match(/^rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)$/)
    expect(m, top).toBeTruthy()
    const alpha = +m![4]
    const mixed = rgb(base).map((b, i) => Math.round(+m![i + 1] * alpha + b * (1 - alpha)))
    return `#${mixed.map((c) => c.toString(16).padStart(2, '0')).join('')}`
  }

  for (const [name, theme] of Object.entries(THEMES)) {
    // --active only ever carries the checked segment's --accent-text label (frost.css), so --muted is not asked of it.
    const FILLS: Record<string, string[]> = {
      '--glass': ['--text', '--muted', '--accent-text'],
      '--btn': ['--text', '--muted', '--accent-text'],
      '--active': ['--text', '--accent-text'],
    }
    for (const [fill, fgs] of Object.entries(FILLS)) {
      for (const fg of fgs) {
        it(`${name}: ${fg} on ${fill} over the wash`, () => {
          const value = FROST[name][fill]
          for (const wash of ['--wash-1', '--wash-2']) {
            const page = over(FROST[name][wash], 60, resolve(theme, '--bg'))
            const under = value.startsWith('rgba') ? blend(value, page) : value
            expect(contrast(resolve(theme, fg), under), `${wash} ${value}`).toBeGreaterThanOrEqual(4.5)
          }
        })
      }
    }
  }
})

// The split dialog shades the two pieces with a translucent token over the
// surface; text and the cut marker must still read on the blended colour.
function cutShade(cls: string): { token: string; pct: number } {
  const m = reviewCss.match(new RegExp(`\\.${cls} \\{[^}]*color-mix\\(in srgb, var\\((--[\\w-]+)\\) (\\d+)%, transparent\\)`))
  expect(m, cls).toBeTruthy()
  return { token: m![1], pct: +m![2] }
}

function over(top: string, pct: number, base: string): string {
  const [t, b] = [rgb(top), rgb(base)]
  const mix = t.map((c, i) => Math.round(c * (pct / 100) + b[i] * (1 - pct / 100)))
  return `#${mix.map((c) => c.toString(16).padStart(2, '0')).join('')}`
}

describe('split dialog cut shading', () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    for (const cls of ['split-cut-a', 'split-cut-b']) {
      for (const base of ['--surface', '--bg']) {
        it(`${name}: ${cls} text and cut marker on ${base}`, () => {
          const { token, pct } = cutShade(cls)
          const shade = over(resolve(theme, token), pct, resolve(theme, base))
          expect(contrast(resolve(theme, '--text'), shade)).toBeGreaterThanOrEqual(4.5)
          expect(contrast(resolve(theme, '--text'), resolve(theme, base))).toBeGreaterThanOrEqual(3)
          expect(contrast(resolve(theme, '--text'), shade)).toBeGreaterThanOrEqual(3)
        })
      }
    }
  }
})

// Frost shape: panels share one radius token, controls use the 8px control radius, and the primary button is a flat accent fill.
describe('frost shape and primary button', () => {
  const frost = readFileSync(new URL('./frost.css', import.meta.url), 'utf8')
  const primary = frost.slice(frost.indexOf('.btn.btn-primary,'), frost.indexOf('.btn.btn-ghost'))

  it('defines the panel radius once and frost.css uses it instead of 22px', () => {
    expect(LIGHT['--radius-panel']).toBe('14px')
    expect(frost).not.toMatch(/border-radius:\s*22px/)
  })

  it('has no pill-shaped controls', () => {
    const controls = frost.slice(frost.indexOf('/* One control shape'), frost.indexOf('/* Fields */'))
    expect(controls).not.toContain('--radius-pill')
    expect(frost.slice(frost.indexOf('/* Tabs:'), frost.indexOf('/* Switches */'))).not.toContain('--radius-pill')
  })

  it('primary is a solid accent with a darker edge, no gradient or glow', () => {
    expect(primary).toContain('background: var(--accent);')
    expect(primary).not.toMatch(/gradient|rgba/)
    expect(primary).toMatch(/border-color:\s*color-mix\(in srgb, var\(--accent\) \d+%, black\)/)
    expect(primary).toContain('box-shadow: none;')
  })

  it('primary keeps the polish fix: no background transition, hover darkens with filter', () => {
    const transition = frost.match(/\n\.btn \{\s*transition:([^;]+);/)?.[1] ?? ''
    expect(transition).not.toMatch(/background/)
    expect(frost).toContain('.btn.btn-primary:hover:not(:disabled) { filter: brightness(0.9); }')
  })
})

describe('frost wash and cards', () => {
  const frost = readFileSync(new URL('./frost.css', import.meta.url), 'utf8')

  it('paints two off-centre blobs at 60%, not four corner ones', () => {
    const body = frost.slice(frost.indexOf('\nbody {'), frost.indexOf('/* Panels'))
    expect(body.match(/radial-gradient/g)).toHaveLength(2)
    expect(body.match(/60%, transparent\)/g)).toHaveLength(2)
    expect(frost).not.toMatch(/--wash-[34]/)
  })

  it('draws a card as a top hairline inside the same 1px border box, without blur', () => {
    // .card shares the rule with the panels that sit on the page, so one block draws them all.
    const [selectors, card] = frost.match(/\n(\.card,[^{]*)\{([^}]*)\}/)?.slice(1) ?? ['', '']
    for (const sel of ['.panel', '.tools-stack', '.discover-panel']) expect(selectors).toContain(sel)
    expect(card).toContain('border: 1px solid transparent')
    expect(card).toContain('border-top-color: var(--border)')
    expect(card).toContain('background: none')
    expect(frost.slice(frost.indexOf('@supports (backdrop-filter: blur(1px)) {'))).not.toMatch(/^\s*\.card,/m)
  })
})

// Covers: flat hue fills under white text, in every theme (sepia reads the light set, oled the dark set).
describe('cover hues under cover text', () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    for (let i = 0; i < 6; i++) {
      it(`${name}: --on-tile on --tile-h${i}`, () => {
        expect(contrast(resolve(theme, '--on-tile'), resolve(theme, `--tile-h${i}`))).toBeGreaterThanOrEqual(4.5)
      })
    }
  }

  it('dark hues are darker than the light ones', () => {
    for (let i = 0; i < 6; i++) {
      expect(luminance(resolve(THEMES.dark, `--tile-h${i}`))).toBeLessThan(luminance(resolve(THEMES.light, `--tile-h${i}`)))
    }
  })

  it('draws covers as a flat fill, no gradient', () => {
    const frost = readFileSync(new URL('./frost.css', import.meta.url), 'utf8')
    const rule = frost.match(/\.continue-tile\[class\*='tile-h'\] \{([^}]*)\}/)?.[1] ?? ''
    expect(rule).toContain('background: var(--tile);')
    expect(rule).not.toMatch(/gradient/)
    expect(css).not.toMatch(/gradient\(160deg/)
  })
})

describe('mono numbers', () => {
  it('timecodes and line ids use the mono face with tabular figures', () => {
    for (const cls of ['review-time', 'review-idx']) {
      const decl = [...reviewCss.matchAll(new RegExp(`\\.${cls} \\{([^}]*)\\}`, 'g'))].map((m) => m[1]).join(';')
      expect(decl, cls).toContain('font-family: var(--font-mono)')
      expect(decl, cls).toContain('tabular-nums')
    }
  })
})
