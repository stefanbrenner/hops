import { useEffect, useMemo, useRef, useState } from 'react'
import { api, AppState, fetchImage, FitsImage } from '../api'
import FitsViewer, { FitsViewerHandle } from '../components/FitsViewer'
import Plot from '../components/Plot'

interface Series {
  files: string[]
  jd: number[]
  time: number[]
  sky: number[]
  psf: number[]
  airmass: number[]
  skip: boolean[]
}

interface Range { x: [number, number] | null; y: [number, number] | null }

interface Props {
  onSaved: (state: AppState, proceed: boolean) => void
}

export default function Inspection({ onSaved }: Props) {
  const [series, setSeries] = useState<Series | null>(null)
  const [skip, setSkip] = useState<boolean[]>([])
  const [selected, setSelected] = useState(0)
  const [image, setImage] = useState<FitsImage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const ranges = useRef<{ sky: Range; psf: Range }>({ sky: { x: null, y: null }, psf: { x: null, y: null } })
  const viewer = useRef<FitsViewerHandle>(null)

  useEffect(() => {
    api.get<Series>('/inspection').then(s => { setSeries(s); setSkip(s.skip) }).catch(e => setError(e.message))
  }, [])

  useEffect(() => {
    if (!series) return
    setLoading(true)
    fetchImage('/frame', { name: series.files[selected], max_size: 1400 })
      .then(setImage).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [series, selected])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!series || (e.target as HTMLElement).tagName === 'INPUT') return
      if (e.key === 'ArrowRight') setSelected(s => Math.min(s + 1, series.files.length - 1))
      if (e.key === 'ArrowLeft') setSelected(s => Math.max(s - 1, 0))
      if (e.key === 'x') setSkip(k => k.map((v, i) => (i === selected ? !v : v)))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [series, selected])

  const plotData = useMemo(() => {
    if (!series) return null
    const make = (values: number[], name: string) => {
      const keep = series.time.map((_, i) => i).filter(i => !skip[i])
      const excluded = series.time.map((_, i) => i).filter(i => skip[i])
      return [
        { x: keep.map(i => series.time[i]), y: keep.map(i => values[i]), customdata: keep, text: keep.map(i => series.files[i]),
          type: 'scattergl' as const, mode: 'markers' as const, name, marker: { color: '#c9d1d9', size: 6 },
          hovertemplate: '%{text}<br>t=%{x:.3f} h<br>%{y:.2f}<extra></extra>' },
        { x: excluded.map(i => series.time[i]), y: excluded.map(i => values[i]), customdata: excluded, text: excluded.map(i => series.files[i]),
          type: 'scattergl' as const, mode: 'markers' as const, name: 'Excluded', marker: { color: '#f85149', size: 7, symbol: 'x' },
          hovertemplate: '%{text} (excluded)<extra></extra>' },
        { x: [series.time[selected]], y: [values[selected]], type: 'scatter' as const, mode: 'markers' as const, name: 'Selected',
          marker: { color: 'rgba(0,0,0,0)', size: 16, line: { color: '#4f8cff', width: 2.5 } }, hoverinfo: 'skip' as const },
        { x: series.time, y: series.airmass, type: 'scatter' as const, mode: 'lines' as const, name: 'Airmass', yaxis: 'y2',
          line: { color: '#d29922', dash: 'dash', width: 1.5 }, hoverinfo: 'skip' as const },
      ]
    }
    return { sky: make(series.sky, 'Sky'), psf: make(series.psf, 'PSF') }
  }, [series, skip, selected])

  if (!series || !plotData) return <div className="empty">{error ? <div className="alert err">{error}</div> : <span className="spinner" />}</div>

  const layout = (ytitle: string) => ({
    xaxis: { title: { text: 'Time (hours in observation)' } },
    yaxis: { title: { text: ytitle } },
    yaxis2: { title: { text: 'Airmass' }, overlaying: 'y' as const, side: 'right' as const, showgrid: false, color: '#d29922' },
    margin: { l: 60, r: 55, t: 10, b: 45 },
    showlegend: false,
  })

  const onClick = (e: any) => {
    const p = e.points?.find((pt: any) => pt.customdata !== undefined)
    if (p) setSelected(p.customdata)
  }

  const storeRange = (which: 'sky' | 'psf') => (e: any) => {
    const r = ranges.current[which]
    if (e['xaxis.autorange']) r.x = null
    if (e['yaxis.autorange']) r.y = null
    if (e['xaxis.range[0]'] !== undefined) r.x = [e['xaxis.range[0]'], e['xaxis.range[1]']]
    if (e['yaxis.range[0]'] !== undefined) r.y = [e['yaxis.range[0]'], e['yaxis.range[1]']]
  }

  const onSelected = (e: any) => {
    if (!e?.points?.length) return
    const ids = new Set<number>(e.points.map((p: any) => p.customdata).filter((c: any) => c !== undefined))
    if (ids.size === 0) return
    const first = [...ids][0]
    const exclude = !skip[first]
    setSkip(k => k.map((v, i) => (ids.has(i) ? exclude : v)))
  }

  // port of exclude_in_sky / include_in_psf...: all frames inside the current zoom of a graph
  const setInView = (which: 'sky' | 'psf', value: boolean) => {
    const r = ranges.current[which]
    const values = which === 'sky' ? series.sky : series.psf
    setSkip(k => k.map((v, i) => {
      const inX = !r.x || (series.time[i] >= Math.min(...r.x) && series.time[i] <= Math.max(...r.x))
      const inY = !r.y || (values[i] >= Math.min(...r.y) && values[i] <= Math.max(...r.y))
      return inX && inY ? value : v
    }))
  }

  const save = async (proceed: boolean) => {
    setSaving(true)
    try {
      const state = await api.post<AppState>('/inspection/save', { skip: Object.fromEntries(series.files.map((f, i) => [f, skip[i]])) })
      onSaved(state, proceed)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const excludedCount = skip.filter(Boolean).length

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Inspection</h1>
          <p>{series.files.length} frames · {excludedCount} excluded</p>
        </div>
        <div className="row">
          <button onClick={() => save(false)} disabled={saving}>Save</button>
          <button className="primary" onClick={() => save(true)} disabled={saving}>Save and run alignment →</button>
        </div>
      </div>

      <div className="alert warn" style={{ marginBottom: 16 }}>
        Important: the first frame must be of good quality. Exclude it if it has low S/N or star trails.
      </div>
      {error && <div className="alert err" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="split" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)' }}>
        <div className="card">
          <div className="row" style={{ marginBottom: 8 }}>
            <button className="small" onClick={() => setSelected(s => Math.max(s - 1, 0))} disabled={selected === 0}>←</button>
            <span className="mono" style={{ flex: 1, textAlign: 'center', fontSize: 12 }}>
              {selected + 1} / {series.files.length}
              {skip[selected] && <span className="badge err" style={{ marginLeft: 8 }}>excluded</span>}
            </span>
            <button className="small" onClick={() => setSelected(s => Math.min(s + 1, series.files.length - 1))} disabled={selected === series.files.length - 1}>→</button>
          </div>
          <FitsViewer ref={viewer} image={image} source={{ path: '/frame', params: { name: series.files[selected] } }} loading={loading} keepView height={460} axes caption={series.files[selected]} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className={skip[selected] ? '' : 'danger'} onClick={() => setSkip(k => k.map((v, i) => (i === selected ? !v : v)))}>
              {skip[selected] ? 'Include selected frame' : 'Exclude selected frame'}
            </button>
            <span className="faint">Keys: ← → to browse, X to toggle</span>
          </div>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          {(['sky', 'psf'] as const).map(which => (
            <div className="card" key={which}>
              <div className="card-title">
                <h3>{which === 'sky' ? 'Sky (counts/pixel/second)' : 'PSF max HWHM (pixels)'}</h3>
                <div className="row">
                  <button className="small danger" onClick={() => setInView(which, true)} title="Zoom in on a region first">Exclude all in view</button>
                  <button className="small" onClick={() => setInView(which, false)}>Include all in view</button>
                </div>
              </div>
              <Plot data={plotData[which]} layout={layout(which === 'sky' ? 'Sky' : 'PSF HWHM')} height={250}
                onClick={onClick} onSelected={onSelected} onRelayout={storeRange(which)} />
            </div>
          ))}
          <p className="faint" style={{ margin: 0 }}>
            Click a point to show the frame. Use the box or lasso select tool in the graph toolbar to toggle several frames at once.
          </p>
        </div>
      </div>
    </div>
  )
}
