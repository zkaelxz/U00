import type { TranscribeConfigUpdate } from '../../../types/workspace'

// The form shows the computed default backend for a title that never saved one;
// sending it back on any other edit would store it as the user's own choice.
export function withoutUntouchedBackend(
  update: TranscribeConfigUpdate,
  shownBackend: string,
): TranscribeConfigUpdate {
  if (update.asr_backend_choice !== shownBackend) return update
  const { asr_backend_choice: _untouched, ...rest } = update
  return rest
}
