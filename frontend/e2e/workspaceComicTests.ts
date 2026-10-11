import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Registered by workspace-comic.spec.ts (desktop) and workspace-comic.mobile.spec.ts (phone).

// Comic titles have no lines: the line stages point to Scanlate instead.
// The drama read is the seeded one with only its media type replaced.

const progress = {
  drama_id: 1, stage_index: 1, stage: 'source', line_count: 0, untranslated_count: 0,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false,
  stages: [
    { key: 'source', state: 'current' }, { key: 'translate', state: 'blocked' }, { key: 'review', state: 'blocked' },
    { key: 'dub', state: 'blocked' }, { key: 'export', state: 'blocked' },
  ],
}

async function openAs(page: Page, mediaType: string, stage: string) {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), media_type: mediaType } })
  })
  await page.goto(`/#/drama/1/${stage}`)
}

export function defineComicWorkspaceTests() {
  for (const stage of ['translate', 'review', 'dub', 'export']) {
    test(`comic title: ${stage} shows the Scanlate notice instead of the line controls`, async ({ page }) => {
      await openAs(page, 'manhua', stage)
      const open = page.getByTestId('open-in-scanlate')
      await expect(open).toHaveText('Open in Scanlate')
      await expect(page.getByText('Comics are translated bubble by bubble in Scanlate, not as text lines.')).toBeVisible()
      await expect(open).toHaveAttribute('href', /#\/comic\/1$/)
      expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(44)
      await expect(page.getByText('Go to Media')).toHaveCount(0)
    })
  }

  test('comic title: the header link is Open in Scanlate, the unused tabs are dimmed, Media stays', async ({ page }) => {
    await openAs(page, 'manhua', 'source')
    const header = page.locator('.workspace-header')
    const link = page.locator('.ws-actions').getByRole('link', { name: 'Open in Scanlate' })
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', /#\/comic\/1$/)
    await expect(link).toHaveClass(/btn-secondary/)
    await expect(header.getByRole('link', { name: 'Read', exact: true })).toHaveCount(0)
    const nav = page.getByRole('navigation', { name: 'Stages' })
    await expect(nav.locator('a[href$="/drama/1/translate"]')).toHaveAttribute('data-comic-skipped', 'true')
    await expect(nav.locator('a[href$="/drama/1/translate"]')).toHaveAttribute('title', 'Translate: Comics are translated in Scanlate')
    await expect(nav.locator('a[href$="/drama/1/source"]')).not.toHaveAttribute('data-comic-skipped', 'true')
    await expect(page.getByTestId('open-in-scanlate')).toHaveCount(0)
    if ((page.viewportSize()?.width ?? 1000) <= 640) {
      const box = (await link.boundingBox())!
      expect(box.height).toBeGreaterThanOrEqual(44)
      expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
    }
  })

  test('non-comic title: Read link and the Translate stage are unchanged', async ({ page }) => {
    await openAs(page, 'audio_drama', 'translate')
    await expect(page.getByRole('navigation', { name: 'Stages' }).locator('[data-comic-skipped]')).toHaveCount(0)
    await expect(page.getByTestId('open-in-scanlate')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: /Translate/ }).first()).toBeVisible()
    await expect(page.locator('.ws-actions').getByRole('link', { name: 'Read', exact: true })).toHaveAttribute('href', /#\/read\/1/)
  })
}
