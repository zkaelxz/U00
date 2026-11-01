import type { PcMode } from '../../api/pcOnly'
import type { DiagnosticsGpuTorchStatus } from '../../types/diagnostics'

type GpuBannerState = 'turn_on' | 'ask_pc' | null

export const GPU_BANNER_TEXT = 'GPU PyTorch is installed, but "Use the GPU for transcription" is off.'
export const GPU_BANNER_REMOTE = 'Turning it on is done on the main PC (Settings > Performance and advanced).'

// Same detection as the GPU PyTorch card: an NVIDIA GPU answered and the installed
// torch is a CUDA build. A CPU-only torch or no GPU shows nothing here.
const hasGpuTorch = (s: DiagnosticsGpuTorchStatus | null): boolean =>
  !!s && s.nvidia.found && s.installed.some((p) => p.name === 'torch' && p.build === 'cuda')

/**
 * useGpu is null until the settings were read: a viewer without the settings
 * permission (a 403) never sees the banner, so there is no button to refuse.
 * The write is PC only, so everyone else is told where it is done.
 */
export function gpuBannerState(s: DiagnosticsGpuTorchStatus | null, useGpu: boolean | null, pc: PcMode): GpuBannerState {
  if (useGpu !== false || !hasGpuTorch(s)) return null
  return pc === 'remote' ? 'ask_pc' : 'turn_on'
}
