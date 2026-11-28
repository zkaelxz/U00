/** Which Live line a screen reader and the spoken English get, and how the browser's voice keeps up. */
import type { LiveCue } from '../../types/live'

/** The newest line whose English has arrived: a list redrawn every chunk would be read out again and again. */
export function newestEnglish(cues: LiveCue[]): LiveCue | null {
  return cues.find((c) => c.translation === 'done' && c.translated.trim()) ?? null
}

type Speech = Pick<SpeechSynthesis, 'speak' | 'cancel' | 'pending'>

/**
 * Reads one line aloud with the browser's own voice. A voice reads slower than lines arrive on a fast stream, so a
 * line already waiting behind the one being read is dropped with it: the newest is never more than one line behind.
 */
export function speakLine(synth: Speech, text: string, makeUtterance: (t: string) => SpeechSynthesisUtterance = (t) => new SpeechSynthesisUtterance(t)) {
  if (synth.pending) synth.cancel()
  const u = makeUtterance(text)
  u.lang = 'en-US'
  synth.speak(u)
}

export const canSpeak = () => typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'
