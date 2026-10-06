// The support report (GET /api/diagnostics/support-report) is plain
// "Key: value" text. These helpers turn it into rows for the preview and
// name the downloaded file. Pure, so they are unit-tested.

type ReportRow = {
  label: string
  value: string
  // Indented lines under a "Key:" line: "  - name: version" or "  <log line>".
  items: { name: string | null; value: string }[]
  // Log lines (Recent errors) are shown in a monospace font.
  mono: boolean
}

const ROW = /^([^:\s][^:]*):(?: (.*))?$/
const ITEM = /^\s+-\s+([^:]+):\s*(.*)$/

// Python's True/False read as Yes/No.
const tidyValue = (v: string) => (v === 'True' ? 'Yes' : v === 'False' ? 'No' : v)

/**
 * "Python: 3.12" -> a row; "Model/engine versions:" plus "  - name: 1.0" lines
 * -> a row with items; "Recent errors:" plus indented log lines -> a mono row.
 * A line that fits neither becomes its own row with an empty label.
 */
export function parseSupportReport(text: string): ReportRow[] {
  const rows: ReportRow[] = []
  for (const raw of text.split('\n')) {
    if (!raw.trim()) continue
    const last = rows[rows.length - 1]
    if (/^\s/.test(raw) && last) {
      const item = last.mono ? null : ITEM.exec(raw)
      last.items.push(item ? { name: item[1].trim(), value: item[2].trim() } : { name: null, value: raw.trim() })
      continue
    }
    const m = ROW.exec(raw)
    if (m) {
      const label = m[1].trim()
      rows.push({ label, value: tidyValue((m[2] ?? '').trim()), items: [], mono: /error/i.test(label) })
    } else {
      rows.push({ label: '', value: raw.trim(), items: [], mono: false })
    }
  }
  return rows
}

/** "baihe-support-report-2026-09-29.txt" (local date). */
export function supportReportFileName(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `baihe-support-report-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.txt`
}
