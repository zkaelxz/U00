import type { Page } from '@playwright/test'

const SHOT_DIR = process.env.SHOT_DIR

// Screenshots are for reviewing a change by hand, so a plain run (CI included) takes none.
export async function shot(page: Page, name: string, fullPage = false): Promise<void> {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage })
}
