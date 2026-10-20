import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { isApp, pickSaveFile } from '../native'

type Values = Record<string, string>

// compass layout of the eight horizon altitudes
const HORIZON_GRID = [['nw', 'n', 'ne'], ['w', '', 'e'], ['sw', 's', 'se']]

async function fetchPlot(values: Values, format: 'svg' | 'png') {
  const response = await fetch(`/api/planner/plot?format=${format}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  })
  if (!response.ok) throw new Error((await response.json()).detail)
  return response
}

export default function Planner() {
  const [values, setValues] = useState<Values | null>(null)
  const [check, setCheck] = useState<{ coordinates: boolean; date: boolean } | null>(null)
  const [svg, setSvg] = useState('')
  const [busy, setBusy] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const plotted = useRef(false)

  useEffect(() => {
    api.get<Values>('/planner').then(v => setValues({ ...v, name: '', ra: '', dec: '' }))
  }, [])

  const set = (patch: Values) => setValues(v => ({ ...v!, ...patch }))

  useEffect(() => {
    if (!values) return
    api.post<{ coordinates: boolean; date: boolean }>('/planner/check', { values }).then(setCheck)
  }, [values?.ra, values?.dec, values?.year_month])

  const plot = async (v = values) => {
    if (!v) return
    setBusy(true)
    setError('')
    try {
      const response = await fetchPlot(v, 'svg')
      setSvg(await response.text())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const search = async (v = values) => {
    if (!v) return
    setSearching(true)
    const r = await api.post<{ name: string; ra: string; dec: string }>('/planner/search', { name: v.search }).finally(() => setSearching(false))
    const next = { ...v, name: r.name, ra: r.ra, dec: r.dec }
    setValues(next)
    if (r.ra) plot(next)
  }

  // like the Tk window: search the default object once at start
  useEffect(() => {
    if (values && !plotted.current) { plotted.current = true; search(values) }
  }, [values])

  const shiftMonth = (delta: number) => {
    if (!values) return
    const [y, m] = values.year_month.split(' ').map(Number)
    if (!y || !m) return
    const d = new Date(y, m - 1 + delta, 1)
    const next = { ...values, year_month: `${d.getFullYear()} ${String(d.getMonth() + 1).padStart(2, '0')}` }
    setValues(next)
    if (check?.coordinates) plot(next)
  }

  const download = async () => {
    if (!values) return
    const fileName = `visibility_${(values.name || values.search).replace(/\W+/g, '_')}_${values.year_month.replace(' ', '_')}.png`
    try {
      if (isApp) {
        const path = await pickSaveFile(fileName)
        if (path) await api.post('/planner/save', { values, path })
        return
      }
      const blob = await (await fetchPlot(values, 'png')).blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = fileName
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e: any) {
      setError(e.message)
    }
  }

  if (!values) return <div className="empty"><span className="spinner" /></div>

  const text = (key: string, label: string, props: Record<string, unknown> = {}) => (
    <div className="field" style={{ gridTemplateColumns: '120px minmax(0,1fr)' }}>
      <label>{label}</label>
      <input value={values[key]} onChange={e => set({ [key]: e.target.value })} {...props} />
    </div>
  )

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Observing planner</h1>
          <p>Visibility of a target during a month: when it is above your horizon, and whether the sun and moon allow observing.</p>
        </div>
        <div className="row">
          <button onClick={download} disabled={!svg}>Download PNG</button>
          <button className="primary" onClick={() => plot()} disabled={busy || !check?.coordinates || !check?.date}>
            {busy ? <><span className="spinner" /> Plotting…</> : 'Reset plot'}
          </button>
        </div>
      </div>
      {error && <div className="alert err" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="split" style={{ gridTemplateColumns: 'minmax(320px, 420px) minmax(0, 1fr)' }}>
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <h3 style={{ marginBottom: 8 }}>Target</h3>
            <form className="row" onSubmit={e => { e.preventDefault(); search() }}>
              <input style={{ flex: 1 }} value={values.search} onChange={e => set({ search: e.target.value })} placeholder="M42" />
              <button type="submit" disabled={searching}>{searching ? <span className="spinner" /> : null} Search RA/DEC</button>
            </form>
            <div className="faint" style={{ margin: '6px 0 10px' }}>{values.name}</div>
            {text('ra', 'RA', { className: 'mono', placeholder: 'hh:mm:ss' })}
            {text('dec', 'DEC', { className: 'mono', placeholder: '+dd:mm:ss' })}
            <div style={{ textAlign: 'right' }}>
              {check && (check.coordinates ? <span className="badge ok">✓ Coordinates accepted</span> : <span className="badge err">Wrong coordinates</span>)}
            </div>
          </div>

          <div className="card">
            <h3 style={{ marginBottom: 8 }}>Month</h3>
            <div className="row">
              <button onClick={() => shiftMonth(-1)}>←</button>
              <input className="mono" style={{ flex: 1, textAlign: 'center' }} value={values.year_month}
                onChange={e => set({ year_month: e.target.value })} placeholder="yyyy mm" />
              <button onClick={() => shiftMonth(1)}>→</button>
            </div>
            {check && !check.date && <div style={{ marginTop: 6 }}><span className="badge err">Wrong date (yyyy mm)</span></div>}
          </div>

          <div className="card">
            <h3 style={{ marginBottom: 8 }}>Observatory</h3>
            {text('observatory', 'Name')}
            {text('latitude', 'Latitude', { className: 'mono', placeholder: '+dd:mm:ss' })}
            {text('longitude', 'Longitude', { className: 'mono', placeholder: '+dd:mm:ss (east +)' })}
            {text('timezone', 'Time zone', { type: 'number', step: 0.5 })}
            <div className="faint" style={{ margin: '10px 0 6px' }}>Horizon altitude (degrees)</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 6 }}>
              {HORIZON_GRID.flat().map((d, i) => d ? (
                <label key={d} className="stack" style={{ gap: 2, fontSize: 12, color: 'var(--text-2)' }}>
                  {d.toUpperCase()}
                  <input type="number" value={values[`horizon_${d}`]} onChange={e => set({ [`horizon_${d}`]: e.target.value })} />
                </label>
              ) : <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-3)' }}>✛</div>)}
            </div>
            <p className="faint" style={{ marginBottom: 0 }}>Defaults come from your profile and are not saved here.</p>
          </div>
        </div>

        <div className="card">
          {svg ? (
            <div style={{ background: '#fff', borderRadius: 8, padding: 8 }} className="planner-plot" dangerouslySetInnerHTML={{ __html: svg }} />
          ) : (
            <div className="empty">{busy ? <span className="spinner" /> : 'Search an object or enter coordinates to plot its visibility.'}</div>
          )}
          <p className="faint" style={{ marginTop: 8 }}>
            Lines show when the target is above the horizon: <span style={{ color: '#d4b400' }}>yellow</span> daylight,{' '}
            <span style={{ color: '#f85149' }}>red</span> twilight, grey night (darker = less moonlight). ★ rise / set times in local time.
          </p>
        </div>
      </div>
    </div>
  )
}
