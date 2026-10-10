/// <reference types="node" />
import { readdirSync, statSync } from 'node:fs'
import { join, matchesGlob, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import config from '../vite.config'

// A test file whose extension the include glob misses is never run and never fails (five .test.tsx files sat
// uncollected that way). Retire this when the config derives its include from the files themselves.
function testFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n: string) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? testFiles(p) : /\.(test|spec)\.[cm]?[jt]sx?$/.test(n) ? [p] : []
  })
}

describe('vitest collection', () => {
  it('collects every test file under src', () => {
    const root = fileURLToPath(new URL('..', import.meta.url))
    const include = config.test?.include ?? []
    const missed = testFiles(join(root, 'src'))
      .map((f) => relative(root, f).split('\\').join('/'))
      .filter((f) => !include.some((g) => matchesGlob(f, g)))
    expect(missed).toEqual([])
  })
})
