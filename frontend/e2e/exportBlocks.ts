import type { Locator, Page } from '@playwright/test'

import { untilTestEnds } from './stageLineMocks'

// Export blocks are collapsible sections; only the last one opened stays open. Specs that press a button in a
// block call openExportBlock first, so they do not depend on the title kind's default.
export const exportBlock = (page: Page, title: string): Locator => page.getByRole('group', { name: title, exact: true })

export const exportSection = (page: Page, title: string): Locator => exportBlock(page, title).locator('details.section').first()

export async function openExportBlock(page: Page, title: string): Promise<void> {
  const section = exportSection(page, title)
  if ((await section.getAttribute('open')) === null) await section.locator('summary').first().click()
}

// Media and setup answers that let the three video exports start. The seeded titles have no source video,
// and ffmpeg may be missing on the machine running the specs.
export async function mockExportReady(page: Page, over: { hasSourceVideo?: boolean; ffmpeg?: boolean } = {}): Promise<void> {
  await page.route('**/api/media/dramas/*/status', (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, has_source_video: over.hasSourceVideo ?? true } })
    }),
  )
  await page.route('**/api/diagnostics/setup-checks', (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, ffmpeg: { found: over.ffmpeg ?? true, version: '7.0', libass: true } } })
    }),
  )
}

// Serves the seeded title with another media type, to see the layout a kind gets.
export async function asMediaType(page: Page, mediaType: string | null, id = 1): Promise<void> {
  await page.route(new RegExp(`/api/library/dramas/${id}$`), (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, media_type: mediaType } })
    }),
  )
}
