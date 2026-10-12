import { test } from '@playwright/test'

import { expectUniformShell } from './pageShellHelpers'

// 1280 is a normal laptop (the rail leaves less than 1120px, so standard and wide meet); 1600 shows the presets apart.
for (const viewport of [{ width: 1280, height: 800 }, { width: 1600, height: 900 }]) {
  for (const theme of ['light', 'dark'] as const) {
    test.describe(`page shell ${viewport.width}px ${theme}`, () => {
      test.use({ viewport })

      test('every page uses its preset width, one header gap, one left edge and no sideways scroll', async ({ page }) => {
        test.setTimeout(120_000)
        await expectUniformShell(page, theme)
      })
    })
  }
}
