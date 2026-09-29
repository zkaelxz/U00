// Pure helper for TypedConfirm (kept out of the component file for fast refresh).
// Exact word, ignoring surrounding spaces and letter case (phones capitalise
// the first letter on their own).
export function typedMatches(typed: string, word: string): boolean {
  return typed.trim().toLowerCase() === word.toLowerCase()
}
