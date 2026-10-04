import type { Locator, Page } from '@playwright/test'

// Dense (.btn-sm) buttons look 32px tall on touch but keep a 44px hit area (an ::after reaches past the box), so a
// bounding box under-reports what a thumb can hit. `window.hitHeight(el)` measures that area by probing
// document.elementFromPoint above and below the centre: it counts the pixels that still answer as the element.
// Anything that is not .btn-sm returns its box height unchanged.
declare global {
  interface Window {
    hitHeight(el: Element): number
  }
}

export async function installHitArea(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.hitHeight = (el) => {
      if (!el.classList.contains('btn-sm')) return el.getBoundingClientRect().height
      // Not rendered (a closed fold): there is nothing to hit-test; report the area the ::after would give.
      if (!el.checkVisibility()) return el.getBoundingClientRect().height + 12
      el.scrollIntoView({ block: 'center' })
      const r = el.getBoundingClientRect()
      const x = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const mine = (y: number) => {
        const at = document.elementFromPoint(x, y)
        return !!at && (el === at || el.contains(at))
      }
      let up = 0
      let down = 0
      while (up < 40 && mine(cy - up - 0.5)) up++
      while (down < 40 && mine(cy + down + 0.5)) down++
      return up + down
    }
  })
}

export const hitHeight = (loc: Locator): Promise<number> => loc.evaluate((e) => window.hitHeight(e))
