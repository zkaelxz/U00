import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

// Drama 3 gets a real 12 s 16:9 webm (made with ffmpeg) and three lines: one
// subtitle line at 0-3 s, four lines at 3-6 s, and a gap after.
const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

export const FOUR_LINES = 'First line of a long subtitle\nSecond line of a long subtitle\nThird line of a long subtitle\nFourth line of a long subtitle'

export function seedVideo(): void {
  const code = `import db, os, subprocess
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.webm')
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=10:duration=12', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=12', '-c:v', 'libvpx', '-b:v', '200k', '-c:a', 'libvorbis', '-shortest', p], check=True)
db.update_drama(3, source_video_filename='e2e.webm')
db.save_lines(3, [
    Line(idx=0, start=0.0, end=3.0, zh='你好', en='Hello there'),
    Line(idx=1, start=3.0, end=6.0, zh='再见', en=${JSON.stringify(FOUR_LINES)}),
    Line(idx=2, start=8.0, end=9.0, zh='', en=''),
])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
}

export async function currentTime(page: Page): Promise<number> {
  return page.locator('video.review-video').evaluate((v) => (v as HTMLVideoElement).currentTime)
}

export async function seekVideo(page: Page, seconds: number): Promise<void> {
  await page.locator('video.review-video').evaluate((v, t) => {
    ;(v as HTMLVideoElement).currentTime = t
  }, seconds)
}

// Headless Chromium may refuse real full screen, so the API is replaced by a
// stub that records which element was asked and fires fullscreenchange like a browser.
export async function stubFullscreen(page: Page): Promise<void> {
  await page.addInitScript(() => {
    let current: Element | null = null
    const w = window as unknown as { __fsRequests: string[] }
    w.__fsRequests = []
    Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true })
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => current })
    HTMLElement.prototype.requestFullscreen = function (this: HTMLElement) {
      w.__fsRequests.push(this.dataset.testid ?? this.tagName)
      // What the browser's top layer does to the element's box.
      this.style.cssText = 'position:fixed;inset:0;z-index:9999;width:100vw;height:100vh'
      current = this
      document.dispatchEvent(new Event('fullscreenchange'))
      return Promise.resolve()
    }
    document.exitFullscreen = () => {
      if (current) (current as HTMLElement).style.cssText = ''
      current = null
      document.dispatchEvent(new Event('fullscreenchange'))
      return Promise.resolve()
    }
  })
}
