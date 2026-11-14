// What the Add box says about a pasted link or a chosen file, and the in-memory hand-off that carries
// a choice from the Library's box to the Add page (a run) or from the Add page to the Library (New title).
// Pure of React. Every sentence is built only from facts the app already has before a run.
import type { MediaLinkProbe } from '../../api/linkDetails'
import type { PcMode } from '../../api/pcOnly'
import { engineLabel, languageLabel } from '../../labels'
import { AUDIO_EXTENSIONS, isVideoFile } from '../workspace/sourceForm'
import { formatBytes } from '../workspace/exportForm'

/** What "Make English subtitles" on a file hands to the run: the file and the run's choices. */
export interface FileRunRequest {
  file: File
  engine: string
  language: string
  title: string
  variant: string
}

/** What "Only add it, don't start" or "Add a title by hand" hands to the New title sheet. */
export type NewTitleStart = { link: string } | { fileName: string } | null

/** A pasted link: anything that parses as an http(s) address with a host. */
export function linkFrom(text: string): string | null {
  const t = text.trim()
  if (!/^https?:\/\//i.test(t)) return null
  try {
    const u = new URL(t)
    return u.hostname ? t : null
  } catch {
    return null
  }
}

const hostOf = (link: string): string => {
  try {
    return new URL(link).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

export function fileKind(name: string): string {
  if (isVideoFile(name)) return 'a video file'
  return AUDIO_EXTENSIONS.some((e) => name.toLowerCase().endsWith(e)) ? 'an audio file' : 'a file'
}

/** "Found:" for a file: its kind from the extension, its name and its size. */
export function foundForFile(file: { name: string; size: number }): string {
  return `${fileKind(file.name)}, “${file.name}”, ${formatBytes(file.size)}.`
}

export type LinkProbeState =
  | { state: 'off' } // away from the PC: the probe is PC only
  | { state: 'reading' }
  | { state: 'failed' }
  | { state: 'read'; probe: MediaLinkProbe }

/** "Found:" for a link: the free link probe's answer, or only the address's host when it can't run. */
export function foundForLink(link: string, p: LinkProbeState): string {
  const host = hostOf(link)
  const where = host ? ` on ${host}` : ''
  const plain = host ? `a link to ${host}` : 'a link'
  if (p.state === 'off') return `${plain}.`
  if (p.state === 'reading') return `${plain}. Reading it…`
  if (p.state === 'failed') return `${plain}. Baihe could not read it.`
  const { probe } = p
  const title = probe.title ? `: “${probe.title}”` : ''
  if (probe.kind === 'source') return `a Sources page${where}${title}.`
  if (probe.kind === 'page') return `a page${where}. ${probe.note}`.trim()
  const parts = [
    `${probe.is_live ? 'a live video' : 'a video'} on ${probe.platform || host || 'the web'}${title}`,
    probe.channel && `from ${probe.channel}`,
    probe.release_date,
  ].filter(Boolean)
  return `${parts.join(', ')}.`
}

/** Subtitles come from video, so a Sources page (a novel or a comic) only adds a title. Away from the PC
 *  the link download can't run. */
export const linkCanRun = (pc: PcMode, p: LinkProbeState): boolean =>
  pc !== 'remote' && !(p.state === 'read' && p.probe.kind === 'source')

/** The line saying what "Make English subtitles" will do with a file, from the choices on screen. */
export function planForFile(o: { pc: PcMode; language: string; engine: string }): string {
  const where = o.pc === 'remote' ? 'on the main PC' : 'on this PC'
  const how = o.engine ? `, then translate it with ${engineLabel(o.engine)}` : ', then translate it'
  return `Transcribe it as ${languageLabel(o.language)} speech ${where}${how}.`
}

/** The same for a link, from the One-Click Translate profile chosen in Settings. */
export function planForLink(o: { pc: PcMode; source: boolean; loaded: boolean; profile: string | null; engine: string | null }): string {
  if (o.pc === 'remote') return 'Making subtitles from a link runs only on the main PC. You can still add it as a title.'
  if (o.source) return 'This is a Sources page, not a video: add it as a title.'
  if (o.loaded && !o.profile) return 'Choose a One-Click Translate profile in Settings > Quick video translate first.'
  const engine = o.engine ? ` with ${engineLabel(o.engine)}` : ''
  const profile = o.profile ? ` (profile “${o.profile}”)` : ''
  return `Download it on this PC, transcribe it, then translate it${engine}${profile}, through the Queue.`
}

type Handoff = { kind: 'run-file'; request: FileRunRequest } | { kind: 'new-title'; start: NewTitleStart }

// A File can't go through the URL or storage, so the choice waits here for the page it opens.
// Reading is separate from clearing so React's doubled first render in development reads the same value.
let pending: Handoff | null = null

export function handOff(h: Handoff): void {
  pending = h
}

export function peekHandoff<K extends Handoff['kind']>(kind: K): Extract<Handoff, { kind: K }> | null {
  return pending?.kind === kind ? (pending as Extract<Handoff, { kind: K }>) : null
}

export function clearHandoff(h: Handoff | null): void {
  if (h && pending === h) pending = null
}
