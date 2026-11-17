import { expect, test } from './fixtures'

// Text contrast on real screens in every theme. themeContrast.test.ts checks the token pairs; this catches what only
// the rendered page shows: a see-through tint over the page's colour wash, a token used on the wrong surface.
// axe cannot do this check here: it gives up on every element above the wash ("background could not be determined").

const THEMES = ['light', 'dark', 'sepia', 'oled'] as const

const SCREENS = [
  '#/', '#/drama/1', '#/drama/1/source?tab=transcribe', '#/drama/1/translate', '#/drama/1/export',
  '#/drama/2', '#/settings', '#/diagnostics', '#/jobs', '#/sources', '#/admin', '#/library-tools', '#/translate',
]

/** Visible text whose colour is under WCAG AA (4.5:1, or 3:1 for large text) against its background. The background is
 *  the element's stack of background colours laid over the page colour, and over each wash blob at its strongest, so the
 *  check holds wherever the element ends up on the page. Disabled controls are exempt (WCAG 1.4.3), and so is text on a
 *  gradient or picture, which has no single background colour. */
function lowContrastText(): string[] {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!
  type Rgba = [number, number, number, number]
  // The canvas turns any CSS colour (color-mix, color(srgb ...), named) into sRGB bytes.
  const parse = (c: string): Rgba => {
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = '#000'
    ctx.fillStyle = c
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
  const washes = ['--wash-1', '--wash-2'].map((name) => root.getPropertyValue(name).trim()).filter(Boolean)
    .map((w) => over([...parse(w).slice(0, 3), 0.6] as Rgba, page))
  const bases = [page, ...washes]

  const problems: string[] = []
  const seen = new Set<Element>()
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const text = walker.currentNode.textContent?.trim()
    const el = walker.currentNode.parentElement
    if (!text || !el || seen.has(el)) continue
    seen.add(el)
    const box = el.getBoundingClientRect()
    const style = getComputedStyle(el)
    if (!box.width || !box.height || style.visibility === 'hidden') continue
    if (el.closest('[disabled], [aria-disabled="true"], [aria-hidden="true"], option')) continue
    const layers: Rgba[] = []
    let opacity = 1
    let onPicture = false
    for (let a: Element | null = el; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a)
      opacity *= parseFloat(s.opacity)
      if (s.backgroundImage !== 'none') onPicture = true
      const bg = parse(s.backgroundColor)
      if (bg[3] > 0) layers.push(bg)
      if (bg[3] >= 1) break
    }
    if (onPicture) continue
    let worst = Infinity
    for (const base of bases) {
      const bg = layers.reduceRight<Rgba>((below, layer) => over(layer, below), base)
      const fg = parse(style.color)
      fg[3] *= opacity
      worst = Math.min(worst, ratio(over(fg, bg), bg))
    }
    const size = parseFloat(style.fontSize)
    const large = size >= 24 || (parseInt(style.fontWeight) >= 700 && size >= 18.66)
    const need = large ? 3 : 4.5
    if (worst < need) problems.push(`${worst.toFixed(2)}:1 "${text.slice(0, 40)}" <${el.tagName.toLowerCase()} class="${el.getAttribute('class') ?? ''}">`)
  }
  return problems
}

for (const theme of THEMES) {
  test(`text is readable on every screen in the ${theme} theme`, async ({ page }) => {
    test.setTimeout(90_000)
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    // Screens fade in, and mid-fade text would be measured at a fraction of its colour; reduced motion turns the fades off.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const found: string[] = []
    for (const hash of SCREENS) {
      await page.goto(`/${hash}`)
      await page.getByRole('heading', { level: 1 }).first().waitFor()
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      for (const p of await page.evaluate(lowContrastText)) found.push(`${hash} ${p}`)
    }
    expect(found, 'text under 4.5:1 (3:1 when large)').toEqual([])
  })
}
