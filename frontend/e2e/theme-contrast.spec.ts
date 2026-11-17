import { expect, test } from './fixtures'

// Text contrast on real screens in every theme. themeContrast.test.ts checks the token pairs; this catches what only
// the rendered page shows: a see-through tint over the page's colour wash, a token used on the wrong surface.
// axe cannot do this check here: it gives up on every element above the wash ("background could not be determined").

// 'system' is no saved choice on a dark device: the prefers-color-scheme copy of the dark tokens, kept in step by hand.
const THEMES = ['light', 'dark', 'sepia', 'oled', 'system'] as const

const SCREENS = [
  '#/', '#/drama/1', '#/drama/1/source?tab=transcribe', '#/drama/1/translate', '#/drama/1/export',
  '#/drama/2', '#/settings', '#/diagnostics', '#/jobs', '#/sources', '#/admin', '#/library-tools', '#/translate',
]

// What this check exists for must be on the screens it walks, or a green run proves nothing.
const MUST_SEE = ['.pill', '.segmented label:has(input:checked)', '.stage-blocker', '.source-needed']

type Scan = { problems: string[]; checked: number; seen: string[] }

/** Visible text whose colour is under WCAG AA (4.5:1, or 3:1 for large text) against its background. The background is
 *  the element's stack of background colours laid over the page colour, and over each wash blob at its strongest, so the
 *  check holds wherever the element ends up on the page. Exempt: disabled controls (WCAG 1.4.3), invisible text, and text
 *  on a gradient or picture (a select's chevron, a cover), which has no single background colour. */
function scan(mustSee: string[]): Scan {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!
  type Rgba = [number, number, number, number]
  // The canvas turns any CSS colour (color-mix, color(srgb ...), named) into sRGB bytes; one it cannot read is an error,
  // not black.
  const parse = (c: string): Rgba => {
    ctx.fillStyle = '#010203'
    ctx.fillStyle = c
    if (ctx.fillStyle === '#010203' && c.trim() !== '#010203') throw new Error(`unreadable colour "${c}"`)
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0], d[1], d[2], d[3] / 255]
  }
  const over = (top: Rgba, below: Rgba): Rgba => [0, 1, 2].map((i) => top[i] * top[3] + below[i] * (1 - top[3])).concat(1) as Rgba
  const lum = (c: Rgba) => {
    const f = (x: number) => (x /= 255) <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])
  }
  const ratio = (a: Rgba, b: Rgba) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }
  const root = getComputedStyle(document.documentElement)
  const page = parse(root.getPropertyValue('--bg'))
  // frost.css draws each wash blob at 60% at its centre.
  const washes = ['--wash-1', '--wash-2'].map((name) => parse(root.getPropertyValue(name)))
    .filter((w) => w[3] > 0).map((w) => over([w[0], w[1], w[2], 0.6], page))
  const bases = [page, ...washes]

  const problems: string[] = []
  let checked = 0
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode
    const text = node.textContent?.trim()
    if (!text) continue
    // The text's own box: a display: contents parent (a segmented label's span) has none of its own.
    const range = document.createRange()
    range.selectNodeContents(node)
    const box = range.getBoundingClientRect()
    let el = node.parentElement
    while (el && getComputedStyle(el).display === 'contents') el = el.parentElement
    if (!el || !box.width || !box.height) continue
    const style = getComputedStyle(el)
    if (style.visibility === 'hidden') continue
    if (el.closest('[disabled], [aria-disabled="true"], [aria-hidden="true"], option')) continue
    let opacity = 1
    for (let a: Element | null = el; a; a = a.parentElement) opacity *= parseFloat(getComputedStyle(a).opacity)
    if (opacity === 0) continue
    const layers: Rgba[] = []
    let onPicture = false
    for (let a: Element | null = el; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a)
      if (s.backgroundImage !== 'none') onPicture = true
      const bg = parse(s.backgroundColor)
      if (bg[3] > 0) layers.push(bg)
      if (bg[3] >= 1) break
    }
    if (onPicture) continue
    checked++
    let worst = Infinity
    for (const base of bases) {
      const bg = layers.reduceRight<Rgba>((below, layer) => over(layer, below), base)
      const fg = parse(style.color)
      fg[3] *= opacity
      worst = Math.min(worst, ratio(over(fg, bg), bg))
    }
    const size = parseFloat(style.fontSize)
    const large = size >= 24 || (parseInt(style.fontWeight) >= 700 && size >= 18.66)
    if (worst < (large ? 3 : 4.5)) {
      problems.push(`${worst.toFixed(2)}:1 "${text.slice(0, 40)}" <${el.tagName.toLowerCase()} class="${el.getAttribute('class') ?? ''}">`)
    }
  }
  return { problems, checked, seen: mustSee.filter((sel) => document.querySelector(sel)) }
}

for (const theme of THEMES) {
  test(`text is readable on every screen in the ${theme} theme`, async ({ page }) => {
    test.setTimeout(120_000)
    if (theme !== 'system') await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    // Screens fade in, and mid-fade text would be measured at a fraction of its colour; reduced motion turns the fades off.
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
    const seen = new Set<string>()
    for (const hash of SCREENS) {
      // A fresh document per screen: a hash change alone keeps the last screen's heading, which would satisfy the wait below.
      await page.goto('about:blank')
      await page.goto(`/${hash}`)
      await page.getByRole('heading', { level: 1 }).first().waitFor()
      if (theme === 'system') await expect(page.locator('html')).not.toHaveAttribute('data-theme', /.*/)
      else await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      // Polled until two reads in a row measure the same amount of text: a screen still filling in from the API (a loading
      // line, a half-drawn card) must neither fail the check nor pass it before its badges and boxes are drawn.
      let last: Scan = { problems: [], checked: -1, seen: [] }
      await expect.poll(async () => {
        const before = last.checked
        last = await page.evaluate(scan, MUST_SEE)
        return last.checked === before ? last.problems : ['still drawing']
      }, { message: `${hash}: text under 4.5:1 (3:1 when large)`, timeout: 8_000 }).toEqual([])
      expect(last.checked, `${hash}: no text was measured`).toBeGreaterThan(5)
      last.seen.forEach((sel) => seen.add(sel))
    }
    expect([...seen].sort(), 'controls this check is for were not on any screen').toEqual([...MUST_SEE].sort())
  })
}
