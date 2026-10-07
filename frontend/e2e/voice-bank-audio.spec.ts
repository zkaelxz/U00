import { expect, test, type Page } from '@playwright/test'

// Library > Voice bank: a small Play on each entry whose clip exists (L19).
// The list and the clip bytes are mocked; nothing is written.

const tools = (page: Page) => page.getByRole('region', { name: 'Library tools' })

/** A short silent 16-bit mono WAV (8 kHz). */
function wav(seconds = 0.3): Buffer {
  const rate = 8000
  const data = Math.round(rate * seconds) * 2
  const b = Buffer.alloc(44 + data)
  b.write('RIFF', 0); b.writeUInt32LE(36 + data, 4); b.write('WAVE', 8)
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22)
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34)
  b.write('data', 36); b.writeUInt32LE(data, 40)
  return b
}

const voice = (id: number, name: string, clip = true) => ({
  id, name, language: 'zh', clone_engine: 'omnivoice', source_drama: null, clip_available: clip,
})

test('voice bank: Play only where a clip exists; plays the entry clip; a refused clip says why', async ({ page }) => {
  const writes: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET' || r.method() === 'HEAD') return route.continue()
    writes.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route((u) => u.pathname === '/api/library/voice-bank', (r) => r.fulfill({
    json: { items: [voice(1, 'Wei voice'), voice(2, 'Lost clip', false), voice(3, 'No access')], count: 3 },
  }))
  const asked: string[] = []
  await page.route((u) => /^\/api\/library\/voice-bank\/\d+\/audio$/.test(u.pathname), (r) => {
    asked.push(new URL(r.request().url()).pathname)
    if (r.request().url().includes('/3/'))
      return r.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
    return r.fulfill({ body: wav(), headers: { 'content-type': 'audio/wav', 'x-content-type-options': 'nosniff' } })
  })
  await page.goto('/#/library-tools')
  await tools(page).locator('summary', { hasText: /^Voice bank/ }).click()
  const list = tools(page).locator('.deletable-list')
  await expect(list.locator('li', { hasText: 'Lost clip' }).getByRole('button', { name: /Play/ })).toHaveCount(0)
  expect(asked).toEqual([]) // nothing loads before a press

  const play = list.getByRole('button', { name: 'Play Wei voice' })
  await play.click()
  await expect.poll(() => asked).toContain('/api/library/voice-bank/1/audio')
  // It plays and then returns to Play when the clip ends.
  await expect(list.getByRole('button', { name: 'Play Wei voice' })).toHaveAttribute('aria-pressed', 'false', { timeout: 10_000 })

  await list.getByRole('button', { name: 'Play No access' }).click()
  await expect(list.locator('li', { hasText: 'No access' })).toContainText("Couldn't play this clip")
  expect(writes).toEqual([])
})
