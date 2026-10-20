import { useEffect, useMemo, useRef, useState } from 'react'
import { api, AppState, fetchImage, FitsImage, Overlay } from '../api'
import FitsViewer, { FitsViewerHandle } from '../components/FitsViewer'
import Modal from '../components/Modal'
import Plot from '../components/Plot'
import Cutout, { CutoutData } from '../components/Cutout'
import { JobStatus, StopButton, TaskBars, useJob } from '../components/JobPanel'
import { openExternal } from '../native'

interface Row {
  x: number; y: number; aperture: number; flux: number; peak: number; max_hwhm: number
  wx: number; wy: number; colour: string | number; warning: string
}

interface Settings {
  sky_inner_aperture: number; sky_outer_aperture: number; saturation: number; star_size_arcsec: number
  camera_gain: number; use_variable_aperture: boolean; use_geometric_center: boolean
  show_good_comparisons: boolean; show_good_comparisons_percent: number
}

interface Setup {
  frame: string
  psf: number
  fov: [number, number, number, number]
  settings: Settings
  rows: Row[]
  fov_options: (number | string)[]
  previous_runs: string[]
  photometry_complete: boolean
  target_ra_dec: string
  target_name: string
  plate_solution: PlateSolution | null
  has_results: boolean
}

interface PlateSolution { target_x: number; target_y: number; identified_stars: { x: number; y: number; bp_rp: number | string }[] }

interface Update { rows: Row[]; good_comparisons: [number, number][]; message: string; can_run: boolean; lost: number[] }

interface Live {
  hours: number[]; files: string[]; gauss: (number | null)[][]; aperture: (number | null)[][]
  fails: number[]; active: boolean[]; cutouts: (CutoutData | null)[]; apertures: number[]
}

const TARGET = '#ff5a5a'
const COMP = '#07fefc'
const label = (i: number) => (i === 0 ? 'Target' : `Comparison ${i}`)

interface Props {
  refresh: () => Promise<void>
  go: (page: string) => void
  onState: (s: AppState) => void
}

export default function Photometry({ refresh, go, onState }: Props) {
  const [setup, setSetup] = useState<Setup | null>(null)
  const [image, setImage] = useState<FitsImage | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [update, setUpdate] = useState<Update | null>(null)
  const [selected, setSelected] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [plate, setPlate] = useState<PlateSolution | null>(null)
  const [view, setView] = useState<'select' | 'run'>('select')
  const viewer = useRef<FitsViewerHandle>(null)
  const plateJob = useJob('plate solving', j => { if (j.status === 'done') setPlate(j.result) })
  const runJob = useJob('photometry', () => refresh())

  useEffect(() => {
    api.get<Setup>('/photometry').then(s => {
      setSetup(s); setSettings(s.settings); setRows(s.rows); setPlate(s.plate_solution)
      if (s.has_results) setView('run')
    }).catch(e => setError(e.message))
    fetchImage('/photometry/image', { max_size: 1600 }).then(setImage).catch(e => setError(e.message))
  }, [])

  useEffect(() => { if (runJob.job && runJob.job.kind === 'photometry') setView('run') }, [runJob.job?.id])

  // validate / measure whenever rows or settings change (debounced)
  const key = JSON.stringify([rows.map(r => [r.x, r.y, r.aperture, r.flux]), settings, plate?.target_x])
  useEffect(() => {
    if (!settings || !rows.length) return
    const t = setTimeout(() => {
      api.post<Update>('/photometry/update', { settings, rows }).then(u => {
        setUpdate(u)
        if (u.lost.length) setNotice(`${u.lost.map(label).join(', ')} could not be located and was cleared.`)
        if (JSON.stringify(u.rows) !== JSON.stringify(rows)) setRows(u.rows)
      }).catch(e => setError(e.message))
    }, 250)
    return () => clearTimeout(t)
  }, [key])

  const used = rows.filter(r => r.x && r.y).length
  // like the Tk radio buttons: selectable up to the first empty slot
  const firstEmpty = rows.findIndex(r => !(r.x && r.y))
  const visibleRows = firstEmpty === -1 ? rows.length : firstEmpty + 1

  const overlays = useMemo<Overlay[]>(() => {
    if (!setup || !settings) return []
    const o: Overlay[] = []
    const [x0, y0, x1, y1] = setup.fov
    o.push({ type: 'rect', x: x0 + 1, y: y0 + 1, w: x1 - x0 - 2, h: y1 - y0 - 2, color: '#f85149' })
    if (settings.show_good_comparisons && update) {
      const s = 10 * setup.psf
      for (const [x, y] of update.good_comparisons) o.push({ type: 'rect', x: x - s / 2, y: y - s / 2, w: s, h: s, color: '#d29922' })
    }
    rows.forEach((r, i) => {
      if (!(r.x && r.y)) return
      const color = i === 0 ? TARGET : COMP
      const cx = settings.use_geometric_center ? r.wx : r.x
      const cy = settings.use_geometric_center ? r.wy : r.y
      o.push({ type: 'circle', x: cx, y: cy, r: r.aperture, color, width: i === selected ? 2.5 : 1.5 })
      o.push({ type: 'circle', x: cx, y: cy, r: r.aperture * settings.sky_inner_aperture, color, dash: true, width: 1 })
      o.push({ type: 'circle', x: cx, y: cy, r: r.aperture * settings.sky_outer_aperture, color, dash: true, width: 1 })
      o.push({ type: 'text', x: cx + r.aperture * settings.sky_outer_aperture, y: cy + r.aperture * settings.sky_outer_aperture, text: i === 0 ? 'T' : `C${i}`, color })
    })
    if (plate && image) {
      const dd = image.info.orig_width * 0.1
      const p = 3 * setup.psf
      o.push({ type: 'arrow', x: plate.target_x - p - dd, y: plate.target_y - p - dd, dx: dd, dy: dd, color: TARGET })
      o.push({ type: 'text', x: plate.target_x - p - dd, y: plate.target_y - p - dd * 1.1, text: setup.target_name, color: TARGET })
    }
    return o
  }, [setup, settings, rows, update, selected, plate, image])

  if (!setup || !settings) return <div className="empty">{error ? <div className="alert err">{error}</div> : <span className="spinner" />}</div>

  const pick = async (x: number, y: number) => {
    setNotice('')
    try {
      const star = await api.post<any>('/photometry/pick', { x, y, saturation: settings.saturation })
      setRows(rs => {
        const next = rs.map(r => ({ ...r }))
        next[selected] = { ...next[selected], x: star.x, y: star.y, flux: 0 }
        return next
      })
      // move on to the next empty slot
      setSelected(s => Math.min(s + 1, rows.length - 1))
    } catch (e: any) {
      setNotice(e.message)
    }
  }

  const clear = (i: number) => {
    setRows(rs => rs.map((r, j) => (j === i ? { ...r, x: 0, y: 0, aperture: 0, flux: 0 } : r)))
    setSelected(0)
  }

  const setAperture = (i: number, value: number) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, aperture: value } : r)))

  const body = () => ({ settings, rows: update?.rows ?? rows, fov_options: viewer.current?.getFovOptions() ?? null })

  const saveSettings = async () => {
    try {
      onState(await api.post<AppState>('/photometry/save-settings', body()))
      setNotice('Options saved.')
    } catch (e: any) { setError(e.message) }
  }

  const run = async () => {
    setError('')
    try {
      await runJob.start('/photometry/run', body())
      setView('run')
    } catch (e: any) { setError(e.message) }
  }

  const loadPrevious = async (folder: string) => {
    if (!folder) return
    const r = await api.post<{ settings: Settings; rows: Row[] }>('/photometry/load-previous', { folder })
    setSettings(r.settings)
    setRows(r.rows.map(row => ({ ...row, flux: 0 })))
    setSelected(0)
  }

  const simbad = () => {
    const radec = setup.target_ra_dec.replace('+', '%2B').replace(' ', '+')
    openExternal(`http://simbad.u-strasbg.fr/simbad/sim-coo?Coord=${radec}&CooFrame=ICRS&CooEpoch=2000&CooEqui=2000&CooDefinedFrames=none&Radius=20&Radius.unit=arcmin&submit=submit+query&CoordList=`)
  }

  if (view === 'run') {
    return <PhotometryRun job={runJob} onBack={() => { setView('select'); refresh() }} go={go} />
  }

  const plateRunning = plateJob.running
  const canRun = update?.can_run && !runJob.running

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Photometry</h1>
          <p>Double-click a star to assign it to the selected slot. Choose comparisons close to the target, of similar magnitude and colour, and not variable.</p>
        </div>
        <div className="row wrap">
          <select value="" onChange={e => loadPrevious(e.target.value)} disabled={!setup.previous_runs.length}>
            <option value="">Load options from previous run</option>
            {setup.previous_runs.map(f => <option key={f}>{f}</option>)}
          </select>
          <button onClick={() => setAdvanced(true)}>Advanced settings</button>
          <button onClick={saveSettings} disabled={!update?.can_run}>Save options</button>
          {setup.photometry_complete && <button onClick={() => go('fitting')}>Fitting →</button>}
          <button className="primary" onClick={run} disabled={!canRun}>Run photometry</button>
        </div>
      </div>

      {error && <div className="alert err" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div className="alert info row" style={{ marginBottom: 12 }}><span style={{ flex: 1 }}>{notice}</span><button className="small ghost" onClick={() => setNotice('')}>✕</button></div>}
      {update && update.message && <div className="alert warn" style={{ marginBottom: 12 }}>{update.message}</div>}

      <div className="split" style={{ gridTemplateColumns: 'minmax(0, 1.3fr) minmax(420px, 1fr)' }}>
        <div className="card">
          <div className="row wrap" style={{ marginBottom: 8 }}>
            <button className="small" onClick={() => plateJob.start('/photometry/plate-solve', { star_size_arcsec: settings.star_size_arcsec })} disabled={plateRunning}>
              {plateRunning ? <><span className="spinner" /> Plate solving…</> : plate ? '✓ Plate solved' : 'Plate solve image'}
            </button>
            <button className="small" onClick={simbad}>Check SIMBAD ↗</button>
            <span className="spacer" />
            <label className="check faint">
              <input type="checkbox" checked={settings.show_good_comparisons} onChange={e => setSettings({ ...settings, show_good_comparisons: e.target.checked })} />
              Stars with flux similar to the target ±
            </label>
            <select value={settings.show_good_comparisons_percent} onChange={e => setSettings({ ...settings, show_good_comparisons_percent: Number(e.target.value) })}>
              {[10, 20, 30, 40, 50].map(p => <option key={p} value={p}>{p}%</option>)}
            </select>
          </div>
          {plateJob.job?.status === 'error' && <div className="alert err" style={{ marginBottom: 8 }}>{plateJob.job.error}</div>}
          <FitsViewer ref={viewer} image={image} source={{ path: '/photometry/image' }} loading={!image} height={600} axes keepView overlays={overlays}
            initialOptions={setup.fov_options} onDoubleClick={pick} />
          <p className="faint" style={{ margin: '6px 0 0' }}>
            <span style={{ color: '#f85149' }}>▭</span> available FOV&nbsp;&nbsp;
            <span style={{ color: '#d29922' }}>▭</span> stars of similar flux&nbsp;&nbsp;
            <span style={{ color: TARGET }}>◯</span> target&nbsp;&nbsp;<span style={{ color: COMP }}>◯</span> comparisons
          </p>
        </div>

        <div className="card">
          <div className="card-title">
            <h3>Stars</h3>
            <span className="faint">{used} selected · selecting <strong style={{ color: 'var(--text)' }}>{label(selected)}</strong></span>
          </div>
          <table className="table">
            <thead>
              <tr><th></th><th>Aperture</th><th>Gbp-Grp</th><th>Total</th><th>Max</th><th>HWHM</th><th></th></tr>
            </thead>
            <tbody>
              {rows.slice(0, visibleRows).map((r, i) => {
                const set = r.x && r.y
                return (
                  <tr key={i} style={{ background: i === selected ? 'var(--accent-bg)' : undefined, cursor: 'pointer' }} onClick={() => setSelected(i)}>
                    <td>
                      <label className="check" style={{ whiteSpace: 'nowrap' }}>
                        <input type="radio" checked={i === selected} onChange={() => setSelected(i)} />
                        <span style={{ color: i === 0 ? TARGET : COMP }}>●</span>{i === 0 ? 'Target' : `C${i}`}
                      </label>
                    </td>
                    <td>
                      <input type="number" step={0.1} style={{ width: 70 }} disabled={!set} value={set ? r.aperture : ''}
                        onClick={e => e.stopPropagation()} onChange={e => setAperture(i, Number(e.target.value))} />
                    </td>
                    <td className="mono">{set ? r.colour : ''}</td>
                    <td className="mono">{set ? Math.round(r.flux).toLocaleString() : ''}</td>
                    <td className="mono">{set ? Math.round(r.peak).toLocaleString() : ''}</td>
                    <td className="mono">{set ? r.max_hwhm : ''}</td>
                    <td style={{ textAlign: 'right' }}>
                      {r.warning && <span className="badge warn" style={{ marginRight: 6 }}>{r.warning}</span>}
                      {set && i > 0 ? <button className="small ghost" onClick={e => { e.stopPropagation(); clear(i) }}>Clear</button> : null}
                      {!set && <span className="faint">double-click a star</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="faint" style={{ marginTop: 10 }}>Aperture radii must be larger than 1.5 pixels. Total counts are sky-subtracted.</p>
        </div>
      </div>

      {advanced && <AdvancedModal settings={settings} onClose={() => setAdvanced(false)} onChange={setSettings} />}
    </div>
  )
}

function AdvancedModal({ settings, onClose, onChange }: { settings: Settings; onClose: () => void; onChange: (s: Settings) => void }) {
  const [draft, setDraft] = useState(settings)
  const num = (key: keyof Settings, labelText: string, def: string) => (
    <div className="field" style={{ gridTemplateColumns: 'minmax(0,1fr) 100px 90px' }}>
      <label>{labelText}</label>
      <input type="number" step={0.1} value={draft[key] as number} onChange={e => setDraft({ ...draft, [key]: Number(e.target.value) })} />
      <span className="faint">default {def}</span>
    </div>
  )
  return (
    <Modal title="Advanced settings" onClose={onClose} footer={
      <><button onClick={onClose}>Cancel</button><button className="primary" onClick={() => { onChange(draft); onClose() }}>Apply</button></>
    }>
      {num('sky_inner_aperture', 'Inner sky-ring radius, relative to the aperture', '1.7')}
      {num('sky_outer_aperture', 'Outer sky-ring radius, relative to the aperture', '2.4')}
      <p className="faint">Bright pixels inside the sky-ring are not taken into account for the sky background estimation.</p>
      {num('saturation', 'Saturation limit, relative to the full-well depth', '0.95')}
      {num('star_size_arcsec', 'Star FWHM in arcseconds (approximate)', '4.0')}
      {num('camera_gain', 'Camera gain in e/ADU', '1.0')}
      <div className="stack" style={{ marginTop: 12 }}>
        <label className="check"><input type="checkbox" checked={draft.use_variable_aperture} onChange={e => setDraft({ ...draft, use_variable_aperture: e.target.checked })} />
          Vary the aperture size proportionally to the variations of the PSF size</label>
        <label className="check"><input type="checkbox" checked={draft.use_geometric_center} onChange={e => setDraft({ ...draft, use_geometric_center: e.target.checked })} />
          Align the aperture with the geometric center instead of the PSF peak</label>
      </div>
    </Modal>
  )
}

function PhotometryRun({ job, onBack, go }: { job: ReturnType<typeof useJob>; onBack: () => void; go: (p: string) => void }) {
  const [live, setLive] = useState<Live | null>(null)
  const [folder, setFolder] = useState<string | null>(null)
  const [error, setError] = useState('')
  const j = job.job

  useEffect(() => {
    api.get<Live | null>('/photometry/live').then(l => l && setLive(l))
  }, [j?.live_version])

  useEffect(() => { if (j?.status === 'done') setFolder(j.result?.folder) }, [j?.status])

  const plot = useMemo(() => {
    if (!live) return null
    const n = live.gauss.length
    const data: any[] = []
    const layout: any = { showlegend: true, margin: { l: 55, r: 15, t: 30, b: 40 }, grid: { rows: n, columns: 1, pattern: 'independent' } }
    let lo = 0.99, hi = 1.01
    for (const c of [...live.gauss, ...live.aperture]) for (const v of c) {
      if (v === null || !isFinite(v)) continue
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
    for (let i = 0; i < n; i++) {
      const ax = i === 0 ? '' : String(i + 1)
      data.push({ x: live.hours, y: live.gauss[i], text: live.files, type: 'scattergl', mode: 'markers', name: 'PSF', legendgroup: 'psf', showlegend: i === 0,
        marker: { color: '#ff5a5a', size: 4 }, xaxis: 'x' + ax, yaxis: 'y' + ax, hovertemplate: '%{text}<br>%{y:.4f}<extra>PSF</extra>' })
      data.push({ x: live.hours, y: live.aperture[i], text: live.files, type: 'scattergl', mode: 'markers', name: 'Aperture', legendgroup: 'ap', showlegend: i === 0,
        marker: { color: '#c9d1d9', size: 4 }, xaxis: 'x' + ax, yaxis: 'y' + ax, hovertemplate: '%{text}<br>%{y:.4f}<extra>Aperture</extra>' })
      layout['yaxis' + ax] = { title: { text: i === 0 ? 'T' : `C${i}` }, range: [lo - 0.002, hi + 0.002], gridcolor: '#202833' }
      layout['xaxis' + ax] = { showticklabels: i === n - 1, matches: i === 0 ? undefined : 'x', gridcolor: '#202833',
        title: i === n - 1 ? { text: 'Time (hours in observation)' } : undefined }
    }
    return { data, layout, height: Math.max(260, 120 * n + 80) }
  }, [live])

  const setActive = async (i: number, value: boolean) => {
    if (!live) return
    const active = live.active.map((a, k) => (k === i ? value : a))
    try {
      setLive(await api.post<Live>('/photometry/active', { active }))
      setError('')
    } catch (e: any) { setError(e.message) }
  }

  const saveAgain = async () => {
    try {
      const r = await api.post<{ folder: string }>('/photometry/save-results')
      setFolder(r.folder)
    } catch (e: any) { setError(e.message) }
  }

  const running = j?.status === 'running'
  const finished = !running && live !== null && (j?.status === 'done' || !j)
  const span = live ? 1.5 * Math.max(...live.apertures) : 10

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Photometry progress</h1>
          <p>Relative light curves: each star over the sum of the other active comparisons.</p>
        </div>
        <div className="row">
          {running ? <StopButton job={j!} label="Stop photometry" onStop={job.cancel} /> : (
            <>
              <button onClick={onBack}>← Photometry menu</button>
              {finished && <button onClick={saveAgain}>Save results again</button>}
              {finished && <button className="primary" onClick={() => go('fitting')}>Proceed to fitting →</button>}
            </>
          )}
        </div>
      </div>
      {error && <div className="alert err" style={{ marginBottom: 12 }}>{error}</div>}
      {j && <div style={{ marginBottom: 12 }}><JobStatus job={j} /></div>}
      {folder && (
        <div className="alert info row" style={{ marginBottom: 12 }}>
          <span style={{ flex: 1 }}>Results saved in <strong>{folder}</strong></span>
          <a className="btn small" href={`/api/file?path=${encodeURIComponent(folder + '/FOV.pdf')}`} target="_blank" rel="noreferrer">FOV.pdf</a>
          <a className="btn small" href={`/api/file?path=${encodeURIComponent(folder + '/RESULTS.pdf')}`} target="_blank" rel="noreferrer">RESULTS.pdf</a>
          <button className="small" onClick={() => api.post('/reveal', { folder })}>Show folder</button>
        </div>
      )}

      <div className="split" style={{ gridTemplateColumns: 'minmax(260px, 340px) minmax(0, 1fr)' }}>
        <div className="card">
          {j && running && <div style={{ marginBottom: 14 }}><TaskBars job={j} /></div>}
          <table className="table">
            <thead><tr><th></th><th>Star</th><th>Active</th></tr></thead>
            <tbody>
              {live?.cutouts.map((c, i) => (
                <tr key={i}>
                  <td><Cutout cutout={c} span={span} /></td>
                  <td>
                    <div style={{ color: i === 0 ? TARGET : COMP }}>{label(i)}</div>
                    {live.fails[i] > 0 && <div className="faint" style={{ color: 'var(--warning)' }}>Not found in {live.fails[i]} frame(s)</div>}
                    {i > 0 && !live.active[i] && <div className="faint">Inactive</div>}
                  </td>
                  <td>
                    <input type="checkbox" checked={live.active[i]} disabled={i === 0 || !finished || live.gauss.length <= 2}
                      onChange={e => setActive(i, e.target.checked)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {live && live.gauss.length === 2 && <p className="faint">Can't plot relative curves of the comparison with only one comparison.</p>}
          {finished && <p className="faint">Changing the active comparisons updates the curves. Use “Save results again” to write a new PHOTOMETRY folder.</p>}
        </div>
        <div className="card">
          {plot ? <Plot data={plot.data} layout={plot.layout} height={plot.height} /> : <div className="empty"><span className="spinner" /></div>}
        </div>
      </div>
    </div>
  )
}
