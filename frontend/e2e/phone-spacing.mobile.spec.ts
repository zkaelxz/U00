import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// Phone rhythm: breathing room between fields, boxes and a primary and its blocker; dense buttons look 32px but
// keep a 44px hit area that overlaps no neighbour; no page-level sideways scroll; panels don't clip focus rings.
// Drama 3 gets three lines (the same library file the server uses) so Review and Characters have content.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeAll(() => {
  execFileSync(
    process.env.PYTHON ?? 'python',
    ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\nfrom core import Line\ndb.update_drama(3, audio_filename=None, source_video_filename=None)\ndb.save_lines(3, [Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'), Line(idx=1, start=1.5, end=3.0, zh='再见朋友', en='', flag='uncertain', flag_note='check'), Line(idx=2, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend')])`],
    { cwd: repoRoot },
  )
})

const sizes = [{ width: 390, height: 844 }, { width: 360, height: 800 }]
const themes = ['light', 'dark', 'sepia', 'oled']
const routes = ['/#/', '/#/drama/3/source', '/#/drama/3/translate', '/#/drama/3/review', '/#/settings', '/#/diagnostics']

async function load(page: Page, url: string, theme = 'dark') {
  await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
  await page.goto(url)
  await page.waitForLoadState('networkidle')
}

// Verticals: [top, bottom] of an element, or null when it isn't rendered.
const box = async (page: Page, sel: string, n = 0) => {
  const b = await page.locator(sel).nth(n).boundingBox()
  return b && { top: b.y, bottom: b.y + b.height }
}

for (const size of sizes) {
  for (const theme of themes) {
    test(`translate stage spacing ${size.width}px ${theme}`, async ({ page }) => {
      await page.setViewportSize(size)
      await load(page, '/#/drama/3/translate', theme)
      const fields = page.locator('.translate-basics > .field-item')
      await expect(fields).toHaveCount(4)
      const first = await box(page, '.translate-basics > .field-item', 0)
      const third = await box(page, '.translate-basics > .field-item', 2)
      expect(third!.top - first!.bottom).toBeGreaterThanOrEqual(16)
      // Boxed sections (the stage's direct children) are 24px apart.
      const tops = await page.locator('.stage-translate > *').evaluateAll((els) =>
        els.map((e) => { const r = e.getBoundingClientRect(); return [r.top, r.bottom] }))
      for (let i = 1; i < tops.length; i++) expect(tops[i][0] - tops[i - 1][1]).toBeGreaterThanOrEqual(24)
      // The first heading sits flush with the panel padding.
      const h3 = await page.locator('.stage-translate > .panel > h3').first().evaluate((e) => getComputedStyle(e).marginTop)
      expect(h3).toBe('0px')
    })
  }

  test(`primary keeps 8px from its blocker ${size.width}px`, async ({ page }) => {
    await page.setViewportSize(size)
    // Drama 1 has no lines, so "Still needed: lines to translate" shows under the button.
    await load(page, '/#/drama/1/translate')
    const blocker = page.getByTestId('translate-blocker')
    await expect(blocker).toBeVisible()
    const primary = page.locator('.translate-go > button.primary')
    const p = (await primary.boundingBox())!
    const b = (await blocker.boundingBox())!
    expect(b.y - (p.y + p.height)).toBeGreaterThanOrEqual(8)
    expect(b.y).toBeGreaterThan(p.y + p.height - 1)
  })

  test(`no page-level sideways scroll ${size.width}px`, async ({ page }) => {
    await page.setViewportSize(size)
    for (const url of routes) {
      await load(page, url)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), url).toBe(true)
    }
  })

  test(`dense buttons keep a 44px hit area and none overlap ${size.width}px`, async ({ page }) => {
    await page.setViewportSize(size)
    for (const url of [...routes, '/#/drama/3/translate']) {
      await load(page, url)
      // The glossary's instruction editors arrive after networkidle; measure with them present, not sometimes without.
      if (url.endsWith('/translate')) await expect(page.getByLabel('Series instructions')).toBeVisible()
      // Open the folds so their dense buttons are measured too.
      await page.evaluate(() => document.querySelectorAll('details.section').forEach((d) => ((d as HTMLDetailsElement).open = true)))
      const res = await page.evaluate(() => {
        const sm = [...document.querySelectorAll<HTMLElement>('.btn-sm')].filter((e) => e.checkVisibility() && e.getBoundingClientRect().height > 0)
        const all = [...document.querySelectorAll<HTMLElement>('button, a[href], input:not([type=hidden]), select, textarea, summary, label.btn')]
          .filter((e) => e.checkVisibility() && e.getBoundingClientRect().height > 0)
        const out: string[] = []
        for (const el of sm) {
          el.scrollIntoView({ block: 'center' })
          const r = el.getBoundingClientRect()
          const label = `${el.textContent?.trim().slice(0, 24)} [${el.className}]`
          if (r.height < 31.5 || (r.height > 34 && r.height < 43.5)) out.push(`${label}: visual height ${r.height}`)
          // Probe the edges of the 44px area: the element itself must answer there (the ::after counts as the element).
          for (const dy of [-21, 21]) {
            const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2 + dy)
            if (!at || !(el === at || el.contains(at))) out.push(`${label}: nothing at ${dy}px from centre (got ${at?.tagName.toLowerCase()}.${at?.className})`)
          }
          const hit = { l: r.left - 4, r: r.right + 4, t: r.top - 6, b: r.bottom + 6 }
          if (hit.b - hit.t < 43.5) out.push(`${label}: hit height ${hit.b - hit.t}`)
          for (const o of all) {
            if (o === el || el.contains(o) || o.contains(el)) continue
            // The sticky strip is meant to sit over scrolled content, so a control scrolled under it isn't an overlap.
            if (!!el.closest('.ws-strip') !== !!o.closest('.ws-strip')) continue
            const q = o.getBoundingClientRect()
            const sameSm = o.classList.contains('btn-sm')
            const area = sameSm ? { l: q.left - 4, r: q.right + 4, t: q.top - 6, b: q.bottom + 6 } : { l: q.left, r: q.right, t: q.top, b: q.bottom }
            if (hit.l < area.r - 0.5 && hit.r > area.l + 0.5 && hit.t < area.b - 0.5 && hit.b > area.t + 0.5)
              out.push(`${label} overlaps ${o.textContent?.trim().slice(0, 24)} [${o.tagName.toLowerCase()}.${o.className}]`)
          }
        }
        return { count: sm.length, out }
      })
      expect(res.out, url).toEqual([])
    }
  })
}

test('panels without a table show the full focus ring; panels with a table still scroll', async ({ page }) => {
  await page.setViewportSize(sizes[0])
  await load(page, '/#/drama/3/translate')
  const styles = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('.panel')].map((p) => ({
      table: !!p.querySelector('table, .table-scroll'),
      overflowX: getComputedStyle(p).overflowX,
    })))
  expect(styles.length).toBeGreaterThan(0)
  for (const s of styles) expect(s.overflowX).toBe(s.table ? 'auto' : 'visible')
  // The focused Translate/Estimate button at the panel edge: its glow is painted outside the panel's box.
  const estimate = page.getByRole('button', { name: 'Estimate cost' })
  await estimate.focus()
  const shadow = await estimate.evaluate((e) => getComputedStyle(e).boxShadow)
  expect(shadow).not.toBe('none')
})
