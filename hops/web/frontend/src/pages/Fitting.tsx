import { useEffect, useMemo, useState } from 'react'
import { api, AppState } from '../api'
import Modal from '../components/Modal'
import Plot from '../components/Plot'
import { JobStatus, StopButton, TaskBars, useJob } from '../components/JobPanel'

interface Settings { light_curve_file: string; iterations: number; burn: number; a_i_fit: boolean; detrending: string; manual_planet: boolean }
type Planet = Record<string, string | number>

interface Setup {
  files: string[]
  settings: Settings
  auto: Planet
  manual: Planet
  exposure_time: number
  filter: string
  observer: string
  observatory: string
  telescope: string
  camera: string
  frames: { jd: number; name: string }[]
}

interface LightCurve { time: number[]; flux: number[]; flux_unc: number[]; date: string; duration: number }

interface Series {
  type: 'Raw' | 'De-trended'
  planet: string
  time: number[]; flux: number[]; flux_unc: number[]
  outliers: { time: number[]; flux: number[] }
  best_fit: { time: number[]; flux: number[] }
  expected: { time: number[]; flux: number[] }
  residuals: { phase: number[]; flux: number[]; flux_unc: number[] }
  std_res: number
  detrended_std_res: number
  autocorr: number
  autocorr_flag: boolean
  mid_time: { print_name: string; print_value: string; print_m_error: string; print_p_error: string }
  rp_over_rs: { print_name: string; print_value: string; print_m_error: string; print_p_error: string }
  predicted_mid_time: number
  predicted_rp_over_rs: number
  o_c: { value: number; m_error: number | null; p_error: number | null }
  detrending: string
  a_i: string
}

const FIELDS: [string, string][] = [
  ['planet', 'Planet'],
  ['target_ra_dec', 'Planet RA DEC'],
  ['period', 'Period [days]'],
  ['mid_time', 'Mid-time [BJD_TDB]'],
  ['rp_over_rs', 'Rp/Rs'],
  ['sma_over_rs', 'a/Rs'],
  ['inclination', 'Inclination [deg]'],
  ['eccentricity', 'Eccentricity'],
  ['periastron', 'Periastron [deg]'],
  ['metallicity', 'M* [Fe/H, dex]'],
  ['temperature', 'T* [K]'],
  ['logg', 'log(g*) [cm/s²]'],
]

interface Props {
  go: (page: string) => void
  onState: (s: AppState) => void
  refresh: () => Promise<void>
}

export default function Fitting({ go, onState, refresh }: Props) {
  const [setup, setSetup] = useState<Setup | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [manual, setManual] = useState<Planet>({})
  const [lc, setLc] = useState<LightCurve | null>(null)
  const [series, setSeries] = useState<Series | null>(null)
  const [error, setError] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [results, setResults] = useState<{ folder: string; files: string[] } | null>(null)

  const testJob = useJob('fitting test', j => { if (j.status === 'done') setSeries(j.result.series) })
  const fitJob = useJob('fitting', j => {
    if (j.status === 'done') { setSeries(j.result.series); setResults({ folder: j.result.folder, files: j.result.files }) }
    refresh()
  })

  useEffect(() => {
    api.get<Setup>('/fitting').then(s => { setSetup(s); setSettings(s.settings); setManual(s.manual) }).catch(e => setError(e.message))
  }, [])

  const validFile = !!setup && !!settings && setup.files.includes(settings.light_curve_file)

  useEffect(() => {
    setSeries(null)
    setLc(null)
    if (!validFile) return
    api.get<LightCurve>('/fitting/light-curve', { file: settings!.light_curve_file }).then(setLc).catch(e => setError(e.message))
  }, [settings?.light_curve_file, validFile])

  const nameOf = useMemo(() => {
    if (!setup) return (_: number) => ''
    return (t: number) => {
      let best = setup.frames[0], d = Infinity
      for (const f of setup.frames) { const dd = Math.abs(f.jd - t); if (dd < d) { d = dd; best = f } }
      return best?.name ?? ''
    }
  }, [setup])

  const plot = useMemo(() => {
    if (!lc) return null
    const data: any[] = []
    const layout: any = {
      margin: { l: 70, r: 20, t: 10, b: 45 },
      xaxis: { anchor: 'y', showticklabels: false, domain: [0, 1], tickformat: '.3f' },
      yaxis: { domain: [0.32, 1], title: { text: 'Relative flux (norm)' } },
      xaxis2: { anchor: 'y2', title: { text: series ? 'Phase' : 'Time (JD)' }, gridcolor: '#202833' },
      yaxis2: { domain: [0, 0.24], title: { text: 'Residuals' }, gridcolor: '#202833', zerolinecolor: '#2a3441' },
      legend: { orientation: 'h', y: 1.08, x: 0 },
    }
    if (!series) {
      data.push({ x: lc.time, y: lc.flux, error_y: { type: 'data', array: lc.flux_unc, color: '#6b7785', thickness: 0.8 },
        text: lc.time.map(nameOf), type: 'scatter', mode: 'markers', name: 'Data', marker: { color: '#c9d1d9', size: 4 },
        hovertemplate: '%{text}<br>JD %{x:.5f}<br>%{y:.4f}<extra></extra>' })
      layout.yaxis.domain = [0, 1]
      layout.xaxis = { title: { text: 'Time (JD)' }, tickformat: '.3f', exponentformat: 'none' }
      delete layout.xaxis2
      delete layout.yaxis2
      return { data, layout }
    }
    const m = series.mid_time, r = series.rp_over_rs
    data.push({ x: series.time, y: series.flux, error_y: { type: 'data', array: series.flux_unc, color: '#6b7785', thickness: 0.8 },
      text: series.time.map(nameOf), type: 'scatter', mode: 'markers', name: series.type === 'Raw' ? 'Raw data' : 'De-trended data',
      marker: { color: '#c9d1d9', size: 4 }, hovertemplate: '%{text}<br>JD %{x:.5f}<br>%{y:.4f}<extra></extra>' })
    if (series.outliers.time.length) {
      data.push({ x: series.outliers.time, y: series.outliers.flux, type: 'scatter', mode: 'markers', name: 'Outliers (not fitted)',
        marker: { color: '#f85149', symbol: 'x', size: 8 } })
    }
    data.push({ x: series.best_fit.time, y: series.best_fit.flux, type: 'scatter', mode: 'lines', line: { color: '#ff5a5a', width: 2 },
      name: `Best fit (${series.detrending}, a/Rs,i: ${series.a_i}): Tmid=${m.print_value} ±${m.print_m_error}, Rp/Rs=${r.print_value} ±${r.print_m_error}` })
    const oc = series.o_c
    data.push({ x: series.expected.time, y: series.expected.flux, type: 'scatter', mode: 'lines', line: { color: '#07fefc', width: 1.5 },
      name: `Expected: Tmid=${series.predicted_mid_time}, Rp/Rs=${series.predicted_rp_over_rs}, O-C=${oc.value} -${oc.m_error}/+${oc.p_error} min` })
    data.push({ x: series.residuals.phase, y: series.residuals.flux, error_y: { type: 'data', array: series.residuals.flux_unc, color: '#6b7785', thickness: 0.8 },
      type: 'scatter', mode: 'markers', marker: { color: '#c9d1d9', size: 4 }, xaxis: 'x2', yaxis: 'y2', showlegend: false, name: 'Residuals' })
    data.push({ x: [Math.min(...series.residuals.phase), Math.max(...series.residuals.phase)], y: [0, 0], type: 'scatter', mode: 'lines',
      line: { color: '#ff5a5a' }, xaxis: 'x2', yaxis: 'y2', showlegend: false, hoverinfo: 'skip' })
    layout.yaxis2.range = [-6 * series.detrended_std_res, 6 * series.detrended_std_res]
    layout.legend = { orientation: 'v', x: 0.01, y: 0.34, yanchor: 'bottom', bgcolor: 'rgba(13,17,23,0.75)', font: { size: 11 } }
    return { data, layout }
  }, [lc, series, nameOf])

  if (!setup || !settings) return <div className="empty">{error ? <div className="alert err">{error}</div> : <span className="spinner" />}</div>

  const body = () => ({ settings, manual, auto: setup.auto })
  const busy = testJob.running || fitJob.running
  const runTest = async () => { setError(''); try { await testJob.start('/fitting/test', body()) } catch (e: any) { setError(e.message) } }
  const runFit = async () => { setError(''); setResults(null); try { await fitJob.start('/fitting/run', body()) } catch (e: any) { setError(e.message) } }
  const save = async (then?: string) => {
    try { onState(await api.post<AppState>('/fitting/save-settings', body())); if (then) go(then) } catch (e: any) { setError(e.message) }
  }

  const planetName = settings.manual_planet ? manual.planet : setup.auto.planet
  const activeJob = fitJob.job?.status === 'running' ? fitJob.job : testJob.job?.status === 'running' ? testJob.job : (fitJob.job ?? testJob.job)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Exoplanet fitting</h1>
          <p>Transit model fit with de-trending; MCMC results are saved next to the light curve.</p>
        </div>
        <div className="row wrap">
          <button onClick={() => save('photometry')} disabled={busy}>← Return to photometry</button>
          <button onClick={() => save()} disabled={busy || !validFile}>Save options</button>
          <button onClick={() => setExportOpen(true)} disabled={!validFile}>Export for databases</button>
          <button onClick={runTest} disabled={!validFile || busy}>Run test</button>
          {fitJob.running ? <StopButton job={fitJob.job!} label="Stop fitting" onStop={fitJob.cancel} />
            : <button className="primary" onClick={runFit} disabled={!validFile || busy}>Run fitting</button>}
        </div>
      </div>

      {error && <div className="alert err" style={{ marginBottom: 12, whiteSpace: 'pre-line' }}>{error}</div>}

      <div className="split" style={{ gridTemplateColumns: 'minmax(0, 1.5fr) minmax(380px, 1fr)' }}>
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="row" style={{ alignItems: 'flex-start', marginBottom: 6 }}>
              <div style={{ flex: 1 }}>
                <h2 style={{ fontSize: 20 }}>{series?.planet ?? planetName}</h2>
                <div className="faint">{setup.observer} · {setup.observatory} / {setup.telescope} / {setup.camera}</div>
              </div>
              {lc && <div className="faint mono" style={{ textAlign: 'right' }}>{lc.date} (UT)<br />Dur: {lc.duration}h / Exp: {setup.exposure_time}s<br />Filter: {setup.filter}</div>}
            </div>
            {plot ? <Plot data={plot.data} layout={plot.layout} height={560} /> : <div className="empty">Choose a light-curve file.</div>}
            {series && (
              <div className="row" style={{ gap: 16, marginTop: 4 }}>
                <span className="badge">STD = {(series.detrended_std_res * 1000).toFixed(2)} ‰</span>
                <span className={'badge ' + (series.autocorr_flag ? 'warn' : 'ok')}>AutoCorr = {series.autocorr.toFixed(2)} {series.autocorr_flag ? '(too high)' : '(acceptable)'}</span>
                <span className="faint">{series.type === 'Raw' ? 'Test fit on raw data' : 'MCMC result, de-trended data'}</span>
              </div>
            )}
          </div>
          {activeJob && (activeJob.status !== 'done' || activeJob.kind === 'fitting') && (
            <div className="card">
              {activeJob.tasks.length > 0 || activeJob.message ? <TaskBars job={activeJob} /> : null}
              <div style={{ marginTop: 8 }}><JobStatus job={activeJob} /></div>
            </div>
          )}
          {results && (
            <div className="card">
              <div className="card-title">
                <h3>Results in {results.folder}</h3>
                <button className="small" onClick={() => api.post('/reveal', { folder: results.folder })}>Show folder</button>
              </div>
              <div className="row wrap">
                {results.files.map(f => (
                  <a key={f} className="btn small" target="_blank" rel="noreferrer" href={`/api/file?path=${encodeURIComponent(results.folder + '/' + f)}`}>{f}</a>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <h3 style={{ marginBottom: 8 }}>Light-curve file</h3>
            <select style={{ width: '100%' }} value={settings.light_curve_file} onChange={e => setSettings({ ...settings, light_curve_file: e.target.value })}>
              <option>Choose Light-curve file</option>
              {setup.files.map(f => <option key={f}>{f}</option>)}
            </select>
          </div>

          <div className="card">
            <div className="card-title">
              <h3>Planet parameters</h3>
              <label className="check faint"><input type="checkbox" checked={settings.manual_planet} disabled={busy}
                onChange={e => setSettings({ ...settings, manual_planet: e.target.checked })} />Enter manually</label>
            </div>
            <table className="table">
              <thead><tr><th></th><th>Catalogue</th><th>Manual</th></tr></thead>
              <tbody>
                {FIELDS.map(([k, l]) => (
                  <tr key={k}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{l}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{String(setup.auto[k])}</td>
                    <td>
                      <input style={{ width: '100%' }} className="mono" disabled={!settings.manual_planet || busy} value={String(manual[k] ?? '')}
                        onChange={e => setManual({ ...manual, [k]: e.target.value })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3 style={{ marginBottom: 8 }}>MCMC options</h3>
            <div className="field" style={{ gridTemplateColumns: '170px minmax(0,1fr)' }}>
              <label>Iterations <span className="faint">(def. 5000)</span></label>
              <input type="number" value={settings.iterations} onChange={e => setSettings({ ...settings, iterations: Number(e.target.value) })} />
            </div>
            <div className="field" style={{ gridTemplateColumns: '170px minmax(0,1fr)' }}>
              <label>Burn-in <span className="faint">(def. 1000)</span></label>
              <input type="number" value={settings.burn} onChange={e => setSettings({ ...settings, burn: Number(e.target.value) })} />
            </div>
            <div className="field" style={{ gridTemplateColumns: '170px minmax(0,1fr)' }}>
              <label>De-trending</label>
              <select value={settings.detrending} onChange={e => setSettings({ ...settings, detrending: e.target.value })}>
                {['Airmass', 'Quadratic', 'Linear'].map(d => <option key={d}>{d}</option>)}
              </select>
            </div>
            <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={settings.a_i_fit}
              onChange={e => setSettings({ ...settings, a_i_fit: e.target.checked })} />Fit for a/Rs, i</label>
          </div>
        </div>
      </div>

      {exportOpen && <ExportModal body={body()} onClose={() => setExportOpen(false)} />}
    </div>
  )
}

function ExportModal({ body, onClose }: { body: any; onClose: () => void }) {
  const [database, setDatabase] = useState('ExoClock')
  const [gain, setGain] = useState(1)
  const [info, setInfo] = useState<{ message: string; default_gain: number | null } | null>(null)
  const [saved, setSaved] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.post<any>('/fitting/export-info', { ...body, database }).then(i => { setInfo(i); if (i.default_gain) setGain(i.default_gain) })
  }, [database])

  const doExport = async () => {
    try {
      const r = await api.post<{ file: string }>('/fitting/export', { ...body, database, camera_gain: gain })
      setSaved(r.file)
    } catch (e: any) { setError(e.message) }
  }

  return (
    <Modal title="Export for databases" onClose={onClose} footer={
      <><button onClick={onClose}>Close</button><button className="primary" onClick={doExport}>Export</button></>
    }>
      <div className="field" style={{ gridTemplateColumns: '120px minmax(0,1fr)' }}>
        <label>Database</label>
        <select value={database} onChange={e => setDatabase(e.target.value)}>
          <option>ExoClock</option><option>ETD</option>
        </select>
      </div>
      <div className="field" style={{ gridTemplateColumns: '120px minmax(0,1fr)' }}>
        <label>Camera gain</label>
        <input type="number" step={0.1} value={gain} onChange={e => setGain(Number(e.target.value))} />
      </div>
      {info && <div className="alert" style={{ whiteSpace: 'pre-line', marginTop: 10 }}>{info.message}</div>}
      {saved && <div className="alert info" style={{ marginTop: 10 }}>File saved: <span className="mono">{saved}</span></div>}
      {error && <div className="alert err" style={{ marginTop: 10 }}>{error}</div>}
    </Modal>
  )
}
