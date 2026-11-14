import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

// Shared by the compare-selected specs: seed N lines on drama 3 and return
// their ids in order, plus the tick box and the selection-bar action. The
// Simple title specs seed the check queue's lines with seedCheckLines.

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

// 24 translated lines on drama 3, none checked. With `warnings`, three of them need a look: #24 misses a glossary
// name, #12 is too fast to read (both flagged) and Whisper was unsure of #7 (unflagged); with `untranslated`, #3 has no English.
export const GLOSSARY_NOTE = 'Glossary: 夷陵老祖 -> Yiling Patriarch missing'
export function seedCheckLines({ warnings = true, untranslated = false } = {}): void {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
ls = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i + 1}句', en=f'Line {i + 1}') for i in range(24)]
if ${warnings ? 'True' : 'False'}:
    ls[23].flag, ls[23].flag_note = 'glossary_missing', ${JSON.stringify(GLOSSARY_NOTE)}
    ls[11].flag, ls[11].flag_note = 'reading_speed', '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.'
    ls[6].confidence = 0.5
if ${untranslated ? 'True' : 'False'}:
    ls[2].en = ''
db.save_lines(3, ls)
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
}

export const tickBox = (page: Page, n: number) => page.getByRole('checkbox', { name: `Select line #${n}`, exact: true })
export const compareAction = (page: Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Compare transcription…' })
export const retimeAction = (page: Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Re-time with Qwen3 aligner…' })
