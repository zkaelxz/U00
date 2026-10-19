import { expect, type Locator, type Page } from '@playwright/test'

// Dense (.btn-sm) buttons look 32px tall on touch but keep a 44px hit area (an ::after reaches past the box), so a
// bounding box under-reports what a thumb can hit. `window.hitHeight(el)` measures that area by probing
// document.elementFromPoint above and below the centre: it counts the pixels that still answer as the element.
// Anything else returns its box height unchanged. Specs assert on this, not on the box: the
// 32px box is expected, the 44px hit area is the thing that must not regress.
declare global {
  interface Window {
    hitHeight(el: Element): number
  }
}

export async function installHitArea(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.hitHeight = (el) => {
      // A Toggle and a field help button also reach past their box with an ::after (index.css), to a 44px hit area.
      const probed = ['btn-sm', 'toggle', 'field-help-btn'].some((c) => el.classList.contains(c))
      if (!probed) return el.getBoundingClientRect().height
      // Not rendered (a closed fold): there is nothing to hit-test; report the area the ::after would give.
      // The 12 is the 6px the .btn-sm ::after adds above and below the box (index.css), so 32 + 12 = 44.
      if (!el.checkVisibility()) return el.getBoundingClientRect().height + (el.classList.contains('btn-sm') ? 12 : 0)
      // elementFromPoint only sees the viewport, so centre the button before probing.
      el.scrollIntoView({ block: 'center' })
      const r = el.getBoundingClientRect()
      const x = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const mine = (y: number) => {
        const at = document.elementFromPoint(x, y)
        return !!at && (el === at || el.contains(at))
      }
      // The 40px cap only bounds the probe; a real hit area is 44px in total, 22px each way.
      let up = 0
      let down = 0
      while (up < 40 && mine(cy - up - 0.5)) up++
      while (down < 40 && mine(cy + down + 0.5)) down++
      return up + down
    }
  })
}

export const hitHeight = (loc: Locator): Promise<number> => loc.evaluate((e) => window.hitHeight(e))

// Every visible element matching `selector` inside `scope` is at least 44px tall. Measures the hit area by default;
// `box: true` measures the bounding box for specs that must not lean on the ::after reach. `nonEmpty` guards a
// selector that silently stops matching.
export async function expectTargetsAtLeast44(
  scope: Locator,
  selector: string,
  { box = false, nonEmpty = false }: { box?: boolean; nonEmpty?: boolean } = {},
): Promise<void> {
  const measured = await scope.locator(selector).evaluateAll(
    (els, byBox) => els
      .filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({
        h: byBox ? e.getBoundingClientRect().height : window.hitHeight(e),
        text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30),
      })),
    box,
  )
  if (nonEmpty) expect(measured.length).toBeGreaterThan(0)
  expect(measured.filter((x) => x.h < 44)).toEqual([])
}
