import { PC_ONLY_BODY, type PcMode } from '../../hooks/usePcOnly'
import { BackupBlock, ExportBlock, RestoreBlock, UserBackup } from '../libraryAdmin/AdminBlocks'
import { SnapshotBlock } from '../libraryAdmin/SnapshotBlock'
import type { AdminJob } from '../libraryAdmin/useAdminJob'
import { TabCard } from './TabCard'

/** "Backup and restore": export, backup, restore and the automatic copies. PC only. */
export function BackupTab({ pc, exportable, exporter }: {
  pc: PcMode
  exportable: number
  // The page-level export job (shared with the selection bar).
  exporter: AdminJob
}) {
  return (
    <>
      <h3 className="tools-group-title">Backup and restore</h3>
      {pc === 'remote' ? (
        <p className="muted">{PC_ONLY_BODY}</p>
      ) : (
        <>
          <TabCard card="export"><ExportBlock exportable={exportable} job={exporter} /></TabCard>
          <TabCard card="backup"><BackupBlock /></TabCard>
          <TabCard card="user-backup"><UserBackup /></TabCard>
          <TabCard card="restore"><RestoreBlock /></TabCard>
          <TabCard card="auto-copies"><SnapshotBlock /></TabCard>
        </>
      )}
    </>
  )
}
