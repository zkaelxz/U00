import { expect, test } from './fixtures'
import { openSettingsGroups } from './settingsNav'

test('the Ollama unload switch explains itself and saves one boolean', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const box = page.getByRole('switch', { name: "Free Ollama's GPU memory before transcribing" })
  await expect(box).toBeVisible()
  await expect(page.locator('#settings-jobs')).toContainText('Ollama keeps a model loaded for a few minutes after translating')
  const before = await box.isChecked()
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')
  await box.click()
  const resp = await saved
  expect(resp.request().postDataJSON()).toEqual({ unload_ollama_before_transcribe: !before })
  await expect(box).toBeChecked({ checked: !before })
  const restored = page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')
  await box.click() // restore
  await restored
  await expect(box).toBeChecked({ checked: before })
})
