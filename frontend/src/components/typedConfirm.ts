// Pure helper for TypedConfirm (kept out of the component file for fast refresh).
// Exact word, ignoring surrounding spaces and letter case (phones capitalise
// the first letter on their own). exact: letter case must match too (words
// the server checks in capitals, e.g. DELETE); spaces are still trimmed.
export function typedMatches(typed: string, word: string, exact = false): boolean {
  return exact ? typed.trim() === word : typed.trim().toLowerCase() === word.toLowerCase()
}
