// Splitting and joining the term-conventions section of a novel's instructions text.
// Same heading as services/glossary_common.TERM_CONVENTIONS_HEADING: conventions are the
// trailing section of the one instructions text, so the translate prompt and the glossary
// extraction both read them from where they already read instructions.
const HEADING = 'Term conventions:'

export function splitConventions(text: string): { prose: string; conventions: string } {
  const at = `\n${text}`.lastIndexOf(`\n${HEADING}\n`)
  if (at < 0) return { prose: text.trim(), conventions: '' }
  return { prose: text.slice(0, Math.max(at - 1, 0)).trim(), conventions: text.slice(at + HEADING.length + 1).trim() }
}

export function joinConventions(prose: string, conventions: string): string {
  const p = prose.trim()
  const c = conventions.trim()
  if (!c) return p
  return `${p ? `${p}\n\n` : ''}${HEADING}\n${c}`
}
