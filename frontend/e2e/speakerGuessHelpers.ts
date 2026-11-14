import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

// Shared by the speaker-guess specs: drama 3 with six lines, three of them with no
// speaker, and saved detection turns whose labels were since renamed to Anok and Cray.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const py = (code: string) =>
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot }).toString()

// #2: Anok above, Cray below and by voice. #3: Cray above (once #2 is set) and below, Anok by voice.
// #5: Anok above, Cray below, no voice.
export function seedBlankSpeakers(): void {
  py(`from core import Line
import diarize
db.update_drama(3, audio_filename=None, source_video_filename=None)
who = ['Anok', None, None, 'Cray', 'Anok', None, 'Cray']
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}', speaker=s) for i, s in enumerate(who)])
voice = ['S0', 'S1', 'S0', 'S1', 'S0', None, 'S1']
diarize.save_turns(db.drama_dir(3), [{'start': i * 2.0, 'end': i * 2.0 + 1.5, 'speaker': v} for i, v in enumerate(voice) if v])
`)
}

export function clearTurns(): void {
  py(`import os
p = os.path.join(db.drama_dir(3), 'diarization_turns.json')
if os.path.exists(p): os.remove(p)
`)
}

export function speakers(): (string | null)[] {
  return JSON.parse(py(`import json\nprint(json.dumps([l.speaker for l in db.load_line_objects(3)]))`))
}

export const row = (page: Page, n: number) =>
  page.locator('.review-line').filter({ has: page.getByRole('checkbox', { name: `Select line #${n}`, exact: true }) })
export const guesses = (page: Page, n: number) => page.getByRole('group', { name: `Suggested speaker for line ${n}` })
