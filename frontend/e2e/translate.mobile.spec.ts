import { expect, test } from '@playwright/test'

// Phone: the Translate page stacks (result under the Translate button), has no
// sideways scroll at 390 and 360 px, and its controls are >=44px tall.
for (const width of [390, 360]) {
  test(`translate page fits a ${width}px phone`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/translate')
    await page.getByLabel('Text to translate').fill('你好')
    await page.getByRole('button', { name: 'Translate', exact: true }).click()
    await expect(page.getByTestId('translate-result')).not.toBeEmpty()
    await expect(page.getByTestId('translate-history').locator('li').first()).toBeVisible()

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

    const primary = await page.getByRole('button', { name: 'Translate', exact: true }).boundingBox()
    const result = await page.getByRole('region', { name: 'Result' }).boundingBox()
    expect(primary && result && result.y > primary.y).toBe(true)

    const targets = [
      page.getByRole('button', { name: 'Translate', exact: true }),
      page.getByRole('button', { name: 'Clear', exact: true }),
      page.getByRole('button', { name: 'Swap languages' }),
      page.getByText('Open a file…'),
      page.getByRole('button', { name: 'Copy' }),
      page.getByRole('button', { name: /Download result/ }),
      page.getByRole('button', { name: 'Clear history' }),
      page.getByLabel('Engine', { exact: true }),
      page.getByLabel('Source language'),
    ]
    for (const t of targets) {
      const box = await t.boundingBox()
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
    }
  })
}
