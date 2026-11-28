/* backupBannerGate: who may see the shell backup warning bar. */
import type { PcMode } from '../api/pcOnly'

/** The route is PC-only, not role-gated, so a member signed in at the PC would otherwise get the owner's warning. */
export function backupBannerWanted(pc: PcMode, member: boolean): boolean {
  return pc === 'local' && !member
}
