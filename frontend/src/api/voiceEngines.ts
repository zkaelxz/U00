// Diagnostics > Packages and installs: voice engines that install into a folder of their own
// (api/routers/diagnostics_voice_engine_routes.py). Installing is PC only (pcOnlyFetch); the
// status read is admin.diagnostics and is polled while a job runs.
import type { DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import type { VoiceEngineInstallStatus } from '../types/voiceEngines'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getVoiceEngines = (f?: Fetch) =>
  getJson<VoiceEngineInstallStatus>('/api/diagnostics/voice-engines', f)

// The packages and folder are the server's own; only the engine key and the confirm are sent.
export const installVoiceEngine = (engine: string, f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(`/api/diagnostics/voice-engines/${encodeURIComponent(engine)}/install`,
    { confirm: true }, pcOnlyFetch(f))
