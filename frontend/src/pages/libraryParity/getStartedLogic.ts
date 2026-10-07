import { engineShortName } from '../../api/translate'
import type { TranslateEngine } from '../../types/translate'

export const GET_STARTED_PREF = 'library.getStarted.dismissed'

/** The card is for a library with no dramas, until the user dismisses it. The
 *  count is unknown (null) while stats load or when they fail: show nothing. */
export function showGetStarted(totalDramas: number | null | undefined, dismissed: boolean): boolean {
  return totalDramas === 0 && !dismissed
}

export const GET_STARTED_STEPS: { title: string; text: string }[] = [
  { title: 'Add', text: 'Create a drama, or find a title in Discover or Sources. Add its audio, novel text or pages.' },
  { title: 'Transcribe or read', text: 'Audio and video are transcribed into lines. Novels and comics open in the reader.' },
  { title: 'Translate', text: 'Pick the translator below, then run Translate in the drama workspace.' },
  { title: 'Review', text: 'Fix lines side by side with the source and check for problems.' },
  { title: 'Export', text: 'Save subtitles (SRT, ASS, VTT), a text file or a dubbed track.' },
]

// What each translator needs and costs, in plain words. Unknown engines get
// the generic cloud line.
const NEEDS: Record<string, string> = {
  claude: 'Cloud. Needs an Anthropic API key. Pay per use.',
  deepseek: 'Cloud. Needs a DeepSeek API key. Pay per use, usually the cheapest.',
  gemini: 'Cloud. Needs a Google Gemini API key. Has a free tier with daily limits.',
  openai: 'Cloud. Needs an OpenAI API key. Pay per use.',
  ollama: 'Runs on this PC. Free. Needs Ollama installed and running with a model pulled.',
}

export function engineNeeds(name: string): string {
  return NEEDS[name] ?? 'Cloud. Needs an API key. Pay per use.'
}

export interface TranslatorOption {
  name: string
  label: string
  needs: string
  ready: boolean
  readyText: string
}

export function translatorOptions(engines: TranslateEngine[]): TranslatorOption[] {
  return engines.map((e) => ({
    name: e.name,
    label: engineShortName(e),
    needs: engineNeeds(e.name),
    ready: e.key_configured,
    readyText: e.key_configured ? (e.free ? 'Ready' : 'Key added') : 'Needs a key',
  }))
}

/** The engine to preselect: the saved default when listed, else the first. */
export function initialTranslator(engines: TranslateEngine[], saved: string | null): string {
  return engines.find((e) => e.name === saved)?.name ?? engines[0]?.name ?? ''
}
