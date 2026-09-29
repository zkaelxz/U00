import { execFileSync } from 'node:child_process'
import path from 'node:path'

// Seeds drama 3 of the throwaway e2e library (the same SQLite file the test
// API server uses) with lines and stored AI results: a consistency issue,
// emotion tags, two translation versions, a note and an edit sample. clear()
// removes the results again so other specs see drama 3 as they expect.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

const CLEAR = `
import contextlib
with contextlib.closing(db.get_conn()) as c:
    for t in ('line_emotions', 'consistency_issues', 'translation_versions', 'translation_notes', 'edit_samples'):
        c.execute(f'DELETE FROM {t} WHERE drama_id = 3')
    c.commit()
`

// extraUntranslated adds that many untranslated lines after the four (to
// fill Coverage past its first 20 rows).
export function seedReviewResults(extraUntranslated = 0) {
  python(`${CLEAR}
from core import Line
db.update_drama(3, audio_filename=None)
lines = [
    Line(idx=0, start=0.0, end=0.6, zh='你好', en='Hello there, my dear old friend from so many years ago'),
    Line(idx=1, start=1.5, end=3.0, zh='魏婴来了', en='Wei Ying is here'),
    Line(idx=2, start=3.0, end=4.5, zh='魏婴走了', en='Wei Wuxian left', flag='uncertain', flag_note='check'),
    Line(idx=3, start=9.0, end=26.0, zh='谢谢', en=''),
] + [Line(idx=4 + i, start=26.0 + i, end=27.0 + i, zh=f'第{i + 1}句', en='') for i in range(${extraUntranslated})]
db.save_lines(3, lines)
db.save_consistency_issues(3, [{'term': '魏婴', 'variants': ['Wei Ying', 'Wei Wuxian'], 'note': 'One person, two names.'}])
db.save_emotions(3, {1: {'emotion': 'anger', 'intensity': 0.9, 'note': 'shouting'}, 0: {'emotion': 'calm', 'intensity': 0.2, 'note': ''}})
old = [Line(idx=l.idx, start=l.start, end=l.end, zh=l.zh, en=l.en) for l in lines]
old[1].en = 'Wei Ying arrives'
db.save_translation_version(3, old, 'First pass', engine='gemini', model='m1')
db.save_translation_version(3, lines, 'Second pass', engine='gemini', model='m2', make_active=True)
db.save_translation_notes(3, [{'line_idx': 0, 'term': '你好', 'note_type': 'culture', 'note': 'A plain greeting.'}])
db.record_edit_sample(3, '你好', 'Hello', 'Hello there, my dear old friend from so many years ago')
`)
}

export function clearReviewResults() {
  python(CLEAR)
}

// Deletes line #n behind the page's back (a finding still points at it).
export function deleteLine(lineNumber: number) {
  python(`
import contextlib
with contextlib.closing(db.get_conn()) as c:
    c.execute('DELETE FROM lines WHERE drama_id = 3 AND idx = ?', (${lineNumber - 1},))
    c.commit()
`)
}
