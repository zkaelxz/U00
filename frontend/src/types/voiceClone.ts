// api/schemas/voice.py VoiceClone* (voice-clone setup), from the generated schema.
// Candidate clips are addressed by an opaque id; no path or filename exists here.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type VoiceCloneSpeakerCandidates = Schemas['VoiceCloneSpeakerCandidates']
export type VoiceCloneCandidates = Schemas['VoiceCloneCandidates']
