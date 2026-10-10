type Scalar = string | number | boolean

/** The server's allow-listed run_settings as label/value rows, or [] when the
 * job has none (older jobs) or the value isn't a flat object. */
export function runSettingRows(result: Record<string, unknown> | null | undefined): Array<[string, string]> {
  const raw = result?.run_settings
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter((entry): entry is [string, Scalar] => ['string', 'number', 'boolean'].includes(typeof entry[1]))
    .map(([key, value]) => [
      key.replace(/_/g, ' '),
      typeof value === 'boolean' ? (value ? 'on' : 'off') : String(value),
    ])
}

/** "Run settings" for one job's Details panel; renders nothing without any. */
export function RunSettings({ result }: { result: Record<string, unknown> | null | undefined }) {
  const rows = runSettingRows(result)
  if (rows.length === 0) return null
  return (
    <section aria-label="Run settings">
      <h4 className="stage-times-title">Run settings</h4>
      <dl>
        {rows.map(([label, value]) => (
          <span key={label} className="jobs-field"><dt>{label}</dt><dd>{value}</dd></span>
        ))}
      </dl>
    </section>
  )
}
