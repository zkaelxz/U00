import { expect, type Page } from '@playwright/test'

// Every top-level page and the PageShell width it must use (components/PageShell.tsx). The title workspace
// stages and the readers are not on this list: they do not use the shell yet.
export const SHELL_PAGES: { hash: string; width: 'narrow' | 'standard' | 'wide' }[] = [
  { hash: '#/settings', width: 'narrow' },
  { hash: '#/make-subtitles', width: 'narrow' },
  { hash: '#/admin', width: 'narrow' },
  { hash: '#/assistant', width: 'narrow' },
  { hash: '#/translate', width: 'standard' },
  { hash: '#/live', width: 'standard' },
  { hash: '#/benchmark', width: 'standard' },
  { hash: '#/diagnostics', width: 'standard' },
  { hash: '#/library-tools', width: 'standard' },
  { hash: '#/sources?tab=manage', width: 'wide' },
  { hash: '#/manga', width: 'standard' },
  { hash: '#/jobs', width: 'wide' },
  { hash: '#/sources', width: 'wide' },
  { hash: '#/discover', width: 'wide' },
  { hash: '#/', width: 'wide' },
]

// Every preset is the one --content-max column (index.css), so every page has the same width and left edge.
const CONTENT_MAX_PX = 1440
const TOLERANCE = 2

type Measure = {
  shellWidth: number
  shellLeft: number
  available: number
  headerTop: number
  gapToFirst: number | null
  shellTopBelowAppHeader: number
  overflow: number
  viewport: number
  // The widest the side rail can be at this width (none below 1024px, where the nav is a drawer).
  railMax: number
  width: string | null
  titleSize: number
  titleWeight: string
  // Offsets of the meta line from the title: it must sit under the title, left-aligned with it.
  metaBelowTitle: number | null
  metaLeftOfTitle: number | null
  // Gaps between neighbouring blocks (page body, the open tab panel, a fold stack).
  blockGaps: number[]
  // Tab bar (or search + tabs) to the first content block, null on a page without tabs.
  tabsToContent: number | null
}

const measure = (page: Page): Promise<Measure> =>
  page.evaluate(() => {
    const shell = document.querySelector('[data-page-shell]')!
    const main = document.querySelector('.app-main')!
    const mainStyle = getComputedStyle(main)
    const available = main.clientWidth - parseFloat(mainStyle.paddingLeft) - parseFloat(mainStyle.paddingRight)
    const box = shell.getBoundingClientRect()
    const header = shell.querySelector('[data-page-header]')!.getBoundingClientRect()
    const appHeader = document.querySelector('.app-header')!.getBoundingClientRect()
    const tabs = shell.querySelector('[data-page-tabs]')
    const visible = (el: Element) => el.getBoundingClientRect().height > 0
    const first = tabs && visible(tabs)
      ? tabs
      : [...shell.querySelector('[data-page-body]')!.children].find(visible)
    const title = shell.querySelector('.page-title')!
    const titleBox = title.getBoundingClientRect()
    const titleStyle = getComputedStyle(title)
    const meta = shell.querySelector('[data-page-header] .page-meta')
    const metaBox = meta?.getBoundingClientRect()
    const blockGaps: number[] = []
    const stacks = [
      shell.querySelector('[data-page-body]')!,
      ...shell.querySelectorAll('[data-page-body] [role="tabpanel"]:not([hidden]), [data-page-body] .diag-folds'),
    ]
    for (const stack of stacks) {
      const kids = [...stack.children].filter(visible)
      for (let i = 1; i < kids.length; i++) {
        blockGaps.push(kids[i].getBoundingClientRect().top - kids[i - 1].getBoundingClientRect().bottom)
      }
    }
    const body = shell.querySelector('[data-page-body]')!
    const firstBlock = [...body.children].find(visible)
    const tabsBox = tabs && visible(tabs) ? tabs.getBoundingClientRect() : null
    return {
      tabsToContent: tabsBox && firstBlock ? firstBlock.getBoundingClientRect().top - tabsBox.bottom : null,
      titleSize: parseFloat(titleStyle.fontSize),
      titleWeight: titleStyle.fontWeight,
      metaBelowTitle: metaBox && metaBox.height > 0 ? metaBox.top - titleBox.bottom : null,
      metaLeftOfTitle: metaBox && metaBox.height > 0 ? metaBox.left - titleBox.left : null,
      blockGaps,
      shellWidth: box.width,
      shellLeft: box.left,
      available,
      headerTop: header.top,
      gapToFirst: first ? first.getBoundingClientRect().top - header.bottom : null,
      shellTopBelowAppHeader: box.top - appHeader.bottom,
      viewport: window.innerWidth,
      railMax: window.innerWidth >= 1024 ? 240 : 0,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      width: shell.getAttribute('data-page-shell'),
    }
  })

// Pages load data after first paint; wait until two reads 150ms apart agree.
async function settled(page: Page): Promise<Measure> {
  await expect(page.locator('[data-page-shell]')).toBeVisible()
  let last = await measure(page)
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(150)
    const next = await measure(page)
    if (JSON.stringify(next) === JSON.stringify(last)) return next
    last = next
  }
  return last
}

// Visits every page and checks the four shell rules the owner reported as "not uniform".
export async function expectUniformShell(page: Page, theme: 'light' | 'dark') {
  await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
  let left: number | null = null
  let gap: number | null = null
  let topOffset: number | null = null
  let title: { size: number; weight: string } | null = null
  for (const { hash, width } of SHELL_PAGES) {
    await page.goto(`/${hash}`)
    const m = await settled(page)
    const at = `${hash} (${width}, ${theme})`
    expect(m.width, `${at}: shell preset`).toBe(width)
    const expected = Math.min(CONTENT_MAX_PX, m.available)
    expect(Math.abs(m.shellWidth - expected), `${at}: column ${m.shellWidth}px, want ${expected}px`).toBeLessThanOrEqual(TOLERANCE)
    expect(m.overflow, `${at}: horizontal scroll`).toBeLessThanOrEqual(0)

    expect(m.gapToFirst, `${at}: something follows the header`).not.toBeNull()
    gap ??= m.gapToFirst!
    expect(Math.abs(m.gapToFirst! - gap), `${at}: header-to-content gap ${m.gapToFirst}px vs ${gap}px`).toBeLessThanOrEqual(TOLERANCE)

    topOffset ??= m.shellTopBelowAppHeader
    expect(Math.abs(m.shellTopBelowAppHeader - topOffset), `${at}: top spacing`).toBeLessThanOrEqual(TOLERANCE)

    title ??= { size: m.titleSize, weight: m.titleWeight }
    expect(m.titleSize, `${at}: title size`).toBe(title.size)
    expect(m.titleWeight, `${at}: title weight`).toBe(title.weight)
    if (m.metaBelowTitle !== null) {
      expect(m.metaBelowTitle, `${at}: meta line sits under the title`).toBeGreaterThanOrEqual(-TOLERANCE)
      expect(Math.abs(m.metaLeftOfTitle!), `${at}: meta line is left-aligned with the title`).toBeLessThanOrEqual(TOLERANCE)
    }
    if (m.tabsToContent !== null) {
      expect(Math.abs(m.tabsToContent - gap), `${at}: tab bar to content ${m.tabsToContent}px vs page gap ${gap}px`).toBeLessThanOrEqual(TOLERANCE)
    }
    for (const g of m.blockGaps) {
      expect(Math.abs(g - gap), `${at}: gap between blocks ${g}px vs page gap ${gap}px`).toBeLessThanOrEqual(TOLERANCE)
    }

    left ??= m.shellLeft
    expect(Math.abs(m.shellLeft - left), `${at}: left edge ${m.shellLeft}px vs ${left}px`).toBeLessThanOrEqual(TOLERANCE)
  }
}
