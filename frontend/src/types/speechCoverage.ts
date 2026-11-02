// Speech coverage check (api/routers/transcribe_routes.py), from the generated schema: speech in the
// stored audio that no subtitle line covers. GET answers status "idle" when
// no check is held in this app session.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SpeechCoverageGap = Schemas['SpeechCoverageGap']
export type SpeechCoverageReport = Schemas['SpeechCoverageReport']
export type SpeechCoverageStatus = Schemas['SpeechCoverageStatus']
export type RawStatus = SpeechCoverageGap['raw_status']
