import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

// Shared by the compare-selected specs: seed N lines on drama 3 and return
// their ids in order, plus the tick box and the selection-bar action.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

export function seedLines(count: number): number[] {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(${count})])
print(','.join(str(l.id) for l in db.load_line_objects(3)))
`
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot }).toString().trim().split(',').map(Number)
}

export const tickBox = (page: Page, n: number) => page.getByRole('checkbox', { name: `Select line #${n}`, exact: true })
export const compareAction = (page: Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Compare transcription…' })
