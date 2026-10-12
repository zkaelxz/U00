import { PC_ONLY_BODY, type PcMode } from '../../hooks/usePcOnly'
import { BackupBlock, RestoreBlock, UserBackup } from '../libraryAdmin/AdminBlocks'
import { SnapshotBlock } from '../libraryAdmin/SnapshotBlock'
import { TabCard } from './TabCard'

/** "Backup and restore", in the order people use them: back up, restore, then the automatic copies. PC only. */
export function BackupTab({ pc }: { pc: PcMode }) {
  return (
    <>
      <h3 className="tools-group-title">Backup and restore</h3>
      {pc === 'remote' ? (
        <p className="muted">{PC_ONLY_BODY}</p>
      ) : (
        <>
          <TabCard card="backup">
            <BackupBlock />
            <UserBackup />
          </TabCard>
          <TabCard card="restore"><RestoreBlock /></TabCard>
          <TabCard card="auto-copies"><SnapshotBlock /></TabCard>
        </>
      )}
    </>
  )
}
