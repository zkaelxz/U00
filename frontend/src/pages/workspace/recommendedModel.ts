/** The recommended model for a kind of title, shown in the AI engine picker's help. */
import type { TitleKind } from './titleKind'

// From the project's own small tests, so each line says it is a recommendation and what it rests on.
const NOTES: Record<TitleKind, string> = {
  comic:
    'Recommended for comics: Claude Sonnet 5.5. In a blind comparison of comic pages it ranked first, ahead of translategemma:27b (free, runs on this PC) and GPT-6 Luna. Small test, so judge on your own pages.',
  video:
    "Recommended for streams and videos: gemma4:31b-cloud, Ollama's hosted model (it sends the text off this PC). translategemma:27b is the local choice.",
  audio:
    "Recommended for streams and audio: gemma4:31b-cloud, Ollama's hosted model (it sends the text off this PC). translategemma:27b is the local choice.",
  subtitle:
    "Recommended for streams and subtitles: gemma4:31b-cloud, Ollama's hosted model (it sends the text off this PC). translategemma:27b is the local choice.",
  novel:
    'Recommended for novels: GPT-6 Luna with "GPT-6 Luna reasoning" set to Less (Settings > Translation and keys), and the series glossary filled in. On a test chunk Less was faster and cheaper with about the same quality.',
}

export function recommendedModelNote(kind: TitleKind): string {
  return NOTES[kind]
}
