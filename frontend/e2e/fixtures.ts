import { expect, test as base } from '@playwright/test'

// `test` with one addition: routes a spec registered with page.route() are
// removed before the page closes. An inline route.fetch() still in flight when
// a test ends otherwise fails with "Test ended".
export const test = base.extend({
  page: async ({ page }, provide) => {
    await provide(page)
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  },
})

export { expect }
