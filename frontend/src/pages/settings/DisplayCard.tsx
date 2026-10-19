/*
 * Settings > Display: how wide the page column is, how large the text is, how tight the spacing is and where the nav sits, for this browser only.
 */
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { NAV_POSITION_OPTIONS, parseNavPosition, setNavPosition, useNavPosition } from '../../nav/navDock'
import { DENSITY_OPTIONS, parseDensity, parseTextSize, parseWidth, setDisplay, TEXT_OPTIONS, useDisplay, WIDTH_OPTIONS } from '../../display'

export function DisplayCard() {
  const display = useDisplay()
  const navPosition = useNavPosition()
  return (
    <Card title="Display" meta="This browser only" aria-label="Display">
      <div className="setting-list">
        <Field label="Page width">
          <select value={display.width} onChange={(e) => setDisplay({ width: parseWidth(e.target.value) })}>
            {WIDTH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="Text size">
          <select value={display.text} onChange={(e) => setDisplay({ text: parseTextSize(e.target.value) })}>
            {TEXT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="Spacing">
          <select value={display.density} onChange={(e) => setDisplay({ density: parseDensity(e.target.value) })}>
            {DENSITY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="Nav position">
          <select value={navPosition} onChange={(e) => setNavPosition(parseNavPosition(e.target.value))}>
            {NAV_POSITION_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
      </div>
      <p className="muted">Standard keeps lines short; Full window uses the whole width. Text size scales all text and controls, and browser zoom still works on top. Compact tightens gaps and padding so more fits on screen; text size is separate. Nav position moves the menu to another edge; on a phone only Top changes anything (the bar moves up from the bottom).</p>
    </Card>
  )
}
