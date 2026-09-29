// Whisper prompt fields for a transcribe or auto-tune request. A non-empty override
// replaces the automatic prompt entirely; otherwise the server builds glossary names +
// extra names + raw-novel excerpt (services/transcribe_service.build_auto_initial_prompt).
export function promptFields(
  override: string,
  extraNames: string,
): { initial_prompt?: string; extra_names?: string } {
  if (override.trim()) return { initial_prompt: override }
  if (extraNames.trim()) return { extra_names: extraNames.trim() }
  return {}
}
