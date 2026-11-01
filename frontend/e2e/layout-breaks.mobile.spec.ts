import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics, openSection } from './diagnosticsInstallsMocks'

// Phone width: layout breaks found in a sweep of every page.

// A desktop browser window 390 wide has real (reserved) scrollbar room; a touch phone does not.
test.describe('narrow window', () => {
  test.use({ isMobile: false, hasTouch: false })

  test('the page uses the whole width: no dead strip beside it', async ({ page }) => {
    await page.goto('/#/settings')
    await expect(page.getByRole('heading', { name: 'Settings', level: 1 })).toBeVisible()
    const widths = await page.evaluate(() => ({
      page: document.documentElement.getBoundingClientRect().width,
      window: document.documentElement.clientWidth,
    }))
    expect(widths.page).toBe(widths.window)
  })
})

test('the Windows-service notice keeps its heading on one line beside its text', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m)
  await page.goto('/#/diagnostics?card=deno')
  await openSection(page, /^Packages/)
  const notice = page.getByTestId('services-off-notice')
  await expect(notice).toBeVisible()
  const lead = await notice.locator('strong').boundingBox()
  const whole = await notice.boundingBox()
  // As a flex row the bold lead-in got a column of about 60px and broke mid-word.
  expect(lead!.width).toBeGreaterThan(whole!.width / 2)
  expect(m.unmocked).toEqual([])
})

test('the selected Jobs filter chip keeps its count readable (4.5:1)', async ({ page }) => {
  await page.goto('/#/settings')
  await expect(page.getByRole('heading', { name: 'Settings', level: 1 })).toBeVisible()
  const ratio = await page.evaluate(() => {
    const wrap = document.createElement('div')
    wrap.className = 'jobs-chips'
    wrap.innerHTML = '<button type="button" class="btn btn-primary btn-sm" aria-pressed="true">All <span class="jobs-chip-count">3</span></button>'
    document.body.append(wrap)
    const btn = wrap.firstElementChild!
    const count = btn.querySelector('.jobs-chip-count')!
    const rgb = (c: string) => c.match(/[\d.]+/g)!.slice(0, 3).map(Number)
    const bg = rgb(getComputedStyle(btn).backgroundColor)
    const alpha = Number(getComputedStyle(count).opacity)
    // The text fades toward the chip's own colour.
    const fg = rgb(getComputedStyle(count).color).map((v, i) => v * alpha + bg[i] * (1 - alpha))
    const lum = ([r, g, b]: number[]) => {
      const f = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 }
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    }
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    wrap.remove()
    return (a + 0.05) / (b + 0.05)
  })
  expect(ratio).toBeGreaterThanOrEqual(4.5)
})
