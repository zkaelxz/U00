// Prefill rule for the Transcribe stage's "Initial prompt" box: the server's automatic
// prompt (series glossary plus raw-novel excerpt) fills an empty box, but never replaces
// a prompt the user typed (including one restored from sessionStorage).
export function prefillPrompt(current: string, auto: string | null | undefined): string {
  return current.trim() ? current : (auto ?? '')
}
