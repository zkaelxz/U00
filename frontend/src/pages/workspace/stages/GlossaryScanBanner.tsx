import { buttonClass } from '../../../components/uiClasses'
import { scanBannerButton, scanBannerText, type ScanBanner } from './glossaryScan'

// Shown wherever the person comes back to: the scan outlives the page that started it.
export function GlossaryScanBanner({ banner, onOpen }: { banner: ScanBanner; onOpen: () => void }) {
  return (
    <div className="glossary-scan-banner actions" role="status" data-testid="glossary-scan-banner" data-kind={banner.kind}>
      <span>{scanBannerText(banner)}</span>
      <button type="button" className={buttonClass('secondary')} onClick={onOpen}>{scanBannerButton(banner)}</button>
    </div>
  )
}
