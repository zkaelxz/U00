import { test } from '@playwright/test'

import { expectUniformShell } from './pageShellHelpers'

for (const theme of ['light', 'dark'] as const) {
  test(`phone: every page shares one column, header gap and left edge, no sideways scroll (${theme})`, async ({ page }) => {
    test.setTimeout(120_000)
    await expectUniformShell(page, theme)
  })
}
