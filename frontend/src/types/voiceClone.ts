// Mirrors api/schemas/voice.py VoiceClone* (voice-clone setup).
// Candidate clips are addressed by an opaque id; no path or filename exists here.

export interface VoiceCloneCandidate {
  id: string
  start: number
  end: number
  duration: number
  ref_text: string
}

export interface VoiceCloneSpeakerCandidates {
  speaker_label: string
  candidates: VoiceCloneCandidate[]
  // 'too_short' | 'too_long' | 'no_segments' when an extraction found nothing.
  skip_reason: string | null
  closest_duration: number | null
}

export interface VoiceCloneCandidates {
  drama_id: number
  speakers: VoiceCloneSpeakerCandidates[]
}
