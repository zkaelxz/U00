// Turns a Live session's cues into a subtitle or text file; nothing here touches the network or the DOM.
import { fmtTs } from '../../api/live'
import type { LiveCue } from '../../types/live'

export type SaveContent = 'both' | 'translation' | 'original'

/** Cues whose translation never arrived, failed or was skipped keep their transcript, so a line is never saved blank. */
const translationOf = (c: LiveCue) => (c.translation === 'done' && c.translated.trim() ? c.translated.trim() : '')

export function cueLines(c: LiveCue, content: SaveContent): string[] {
  const original = c.text.trim()
  const translated = translationOf(c)
  if (content === 'original') return original ? [original] : []
  if (content === 'translation') return [translated || original].filter(Boolean)
  return translated ? [translated, original].filter(Boolean) : [original].filter(Boolean)
}

function srtTime(sec: number): string {
  const ms = Math.round(Math.max(0, Number.isFinite(sec) ? sec : 0) * 1000)
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${p(Math.floor(ms / 3_600_000))}:${p(Math.floor(ms / 60_000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`
}

/** Numbered from 1 over the cues that have text. An end at or before its start gets half a second so players show it. */
export function toSrt(cues: LiveCue[], content: SaveContent): string {
  const blocks: string[] = []
  for (const c of cues) {
    const lines = cueLines(c, content)
    if (!lines.length) continue
    const end = c.end > c.start ? c.end : c.start + 0.5
    blocks.push(`${blocks.length + 1}\n${srtTime(c.start)} --> ${srtTime(end)}\n${lines.join('\n')}`)
  }
  return blocks.length ? `${blocks.join('\n\n')}\n` : ''
}

/** One block per line, headed by the time since the session started. */
export function toText(cues: LiveCue[], content: SaveContent): string {
  const blocks = cues.flatMap((c) => {
    const lines = cueLines(c, content)
    return lines.length ? [`[${fmtTs(c.start)}] ${lines.join('\n')}`] : []
  })
  return blocks.length ? `${blocks.join('\n\n')}\n` : ''
}
