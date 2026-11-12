// Diagnostics > Packages and installs "Install ffmpeg" (api/routers/diagnostics_ffmpeg_routes.py).
// Starting it is PC only (pcOnlyFetch); the status read is admin.diagnostics and is polled while the job runs.
import type { DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import type { FfmpegInstallStatus } from '../types/ffmpegInstall'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getFfmpegInstallStatus = (f?: Fetch) =>
  getJson<FfmpegInstallStatus>('/api/diagnostics/ffmpeg', f)

// The download address and folder are the server's own; nothing but the confirm is sent.
export const installFfmpeg = (f?: Fetch) =>
  postJson<DiagnosticsJobStarted>('/api/diagnostics/ffmpeg/install', { confirm: true }, pcOnlyFetch(f))
