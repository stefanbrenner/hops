import { useEffect, useRef, useState } from 'react'
import { api, AppState, fetchImage, FitsImage } from '../api'
import Modal from '../components/Modal'
import FitsViewer, { FitsViewerHandle } from '../components/FitsViewer'

type Values = Record<string, any>

interface Check {
  science_files: number
  first_file: string | null
  counts: Record<string, number>
  status: Record<string, boolean>
  observation_time_key: string
  time_key_locked: boolean
  filter: string
  auto_target_ra_dec: string | null
  auto_target_name: string | null
  auto_location: string | null
  profile_location: string | null
  observing_info: Record<string, string>
  image_shape: [number, number] | null
  can_save: boolean
}

interface Props {
  onSaved: (state: AppState, proceed: boolean) => void
}

function Status({ ok, okText = 'OK', badText }: { ok: boolean | undefined; okText?: string; badText: string }) {
  if (ok === undefined) return <span className="badge">…</span>
  return ok ? <span className="badge ok">✓ {okText}</span> : <span className="badge err">{badText}</span>
}

export default function DataTarget({ onSaved }: Props) {
  const [values, setValues] = useState<Values | null>(null)
  const [filters, setFilters] = useState<string[]>([])
  const [timeStamps, setTimeStamps] = useState<string[]>([])
  const [check, setCheck] = useState<Check | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [modal, setModal] = useState<'' | 'files' | 'header' | 'target' | 'location'>('')
  const [advanced, setAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const lastAuto = useRef<string | null>(null)

  useEffect(() => {
    // a late response of an earlier load must not replace the values set automatically since then
    let active = true
    api.get<{ values: Values; filters: string[]; time_stamps: string[] }>('/data-target')
      .then(r => { if (active) { setValues(r.values); setFilters(r.filters); setTimeStamps(r.time_stamps) } })
      .catch(e => active && setError(e.message))
    return () => { active = false }
  }, [])

  const set = (patch: Values) => setValues(v => ({ ...v!, ...patch }))

  // validate (debounced) whenever a checked field changes
  const checkKey = values && JSON.stringify([
    values.observation_files, values.bias_files, values.dark_files, values.darkf_files, values.flat_files,
    values.exposure_time_key, values.observation_date_key, values.observation_time_key, values.filter,
    values.target_ra_dec, values.location,
  ])

  useEffect(() => {
    if (!values) return
    setChecking(true)
    const t = setTimeout(() => {
      api.post<Check>('/data-target/check', { values })
        .then(c => {
          setCheck(c)
          setError('')
          const patch: Values = {}
          if (c.filter !== values.filter) patch.filter = c.filter
          if (c.observation_time_key !== values.observation_time_key) patch.observation_time_key = c.observation_time_key
          for (const k of ['observer', 'observatory', 'telescope', 'camera']) {
            if (values[k] === 'default') patch[k] = c.observing_info[k]
          }
          // the target is taken from the header automatically, unless one was chosen by hand: first the
          // object name (OBJECT) if SIMBAD finds it, otherwise the header coordinates (like the Tk window)
          const auto = c.auto_target_ra_dec ?? 'Not found'
          const autoName = c.auto_target_name ?? ''
          if (auto + autoName !== lastAuto.current) {
            lastAuto.current = auto + autoName
            patch.auto_target_ra_dec = auto
            patch.auto_target_name = autoName
            const automatic = values.target_ra_dec_choice === 0 ||
              (values.target_ra_dec_choice === 1 && !!values.auto_target_name && values.simbad_target_name === values.auto_target_name)
            const fromCoordinates = () => {
              if (c.auto_target_ra_dec) {
                api.post<{ target_ra_dec: string; target_name: string }>('/data-target/resolve-target', { choice: 0, auto_target_ra_dec: auto })
                  .then(r => set({ target_ra_dec_choice: 0, simbad_target_name: '', target_ra_dec: r.target_ra_dec, target_name: r.target_name }))
              }
            }
            if (automatic && autoName) {
              api.post<{ target_ra_dec: string; target_name: string; ok: boolean }>('/data-target/resolve-target', { choice: 1, simbad_target_name: autoName })
                .then(r => r.ok
                  ? set({ target_ra_dec_choice: 1, simbad_target_name: autoName, target_ra_dec: r.target_ra_dec, target_name: r.target_name })
                  : fromCoordinates())
                .catch(fromCoordinates)
            } else if (automatic) {
              fromCoordinates()
            }
          }
          patch.auto_location = c.auto_location ?? 'Not found'
          patch.profile_location = c.profile_location ?? 'Not found'
          if (values.location_choice === 0 && c.auto_location && values.location !== c.auto_location) patch.location = c.auto_location
          if (values.location_choice === 1 && c.profile_location && values.location !== c.profile_location) patch.location = c.profile_location
          if (Object.keys(patch).some(k => patch[k] !== values[k])) set(patch)
        })
        .catch(e => setError(e.message))
        .finally(() => setChecking(false))
    }, 400)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkKey])

  if (!values) return <div className="empty">{error ? <div className="alert err">{error}</div> : <span className="spinner" />}</div>

  const save = async (proceed: boolean) => {
    setSaving(true)
    try {
      const state = await api.post<AppState>('/data-target/save', { values })
      onSaved(state, proceed)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const st = check?.status ?? {}
  const fileRow = (key: string, label: string) => (
    <div className="field" key={key}>
      <label>{label}</label>
      <input value={values[key]} onChange={e => set({ [key]: e.target.value })} />
      <span className="status-cell">
        {key === 'observation_files'
          ? <Status ok={check ? check.science_files > 0 : undefined} okText={`${check?.science_files} files`} badText="0 files found" />
          : <span className="badge">{check?.counts[key] ?? '…'} files</span>}
      </span>
    </div>
  )

  const keyRow = (key: string, label: string, disabled = false) => (
    <div className="field" key={key}>
      <label>{label}</label>
      <input value={values[key]} disabled={disabled} onChange={e => set({ [key]: e.target.value })} />
      <span className="status-cell"><Status ok={check ? st[key] : undefined} badText="Keyword not found" /></span>
    </div>
  )

  return (
    <div style={{ maxWidth: 1200 }}>
      <div className="page-header">
        <div>
          <h1>Data & target</h1>
          <p>Name identifiers of your files, header keywords, target and observatory location.</p>
        </div>
        <div className="row">
          {checking && <span className="spinner" />}
          <button onClick={() => save(false)} disabled={!check?.can_save || saving}>Save</button>
          <button className="primary" onClick={() => save(true)} disabled={!check?.can_save || saving}>Save and run reduction →</button>
        </div>
      </div>

      {error && <div className="alert err" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">
            <h3>Name identifiers</h3>
            <button className="small ghost" onClick={() => setModal('files')}>Show files</button>
          </div>
          {fileRow('observation_files', 'Observation files')}
          {fileRow('bias_files', 'Bias files')}
          {fileRow('dark_files', 'Dark files')}
          {fileRow('darkf_files', 'Dark-flat files')}
          {fileRow('flat_files', 'Flat files')}
          <p className="faint" style={{ margin: '8px 0 0' }}>Files are matched by any part of their name, e.g. “bias” finds bias_001.fits.</p>
        </div>

        <div className="card">
          <div className="card-title">
            <h3>Header information</h3>
            <button className="small ghost" onClick={() => setModal('header')} disabled={!check?.science_files}>Show header</button>
          </div>
          {keyRow('exposure_time_key', 'Exposure time key')}
          {keyRow('observation_date_key', 'Observation date key')}
          {keyRow('observation_time_key', 'Observation time key', check?.time_key_locked)}
          <div className="field">
            <label>Time-stamp</label>
            <select value={values.time_stamp} onChange={e => set({ time_stamp: e.target.value })}>
              {timeStamps.map(t => <option key={t}>{t}</option>)}
            </select>
            <span className="status-cell"><span className="badge ok">✓ OK</span></span>
          </div>
          <div className="field">
            <label>Filter</label>
            <select value={values.filter} onChange={e => set({ filter: e.target.value })}>
              {filters.map(f => <option key={f}>{f}</option>)}
            </select>
            <span className="status-cell"><Status ok={check ? st.filter : undefined} badText="Filter not valid" /></span>
          </div>
        </div>

        <div className="card">
          <div className="card-title">
            <h3>Target</h3>
            <Status ok={check ? st.target : undefined} badText="Wrong coordinates" />
          </div>
          <div className="kv" style={{ marginBottom: 12 }}>
            <span>Name</span><span>{values.target_name}</span>
            <span>RA / Dec</span><span className="mono">{values.target_ra_dec}</span>
          </div>
          <button onClick={() => setModal('target')}>Select / change target</button>
        </div>

        <div className="card">
          <div className="card-title">
            <h3>Location</h3>
            <Status ok={check ? st.location : undefined} badText="Wrong coordinates" />
          </div>
          <div className="kv" style={{ marginBottom: 12 }}>
            <span>Lat / Long</span><span className="mono">{values.location}</span>
          </div>
          <button onClick={() => setModal('location')}>Select / change location</button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title">
          <h3>Observer information <span className="faint" style={{ textTransform: 'none', letterSpacing: 0 }}>(optional)</span></h3>
        </div>
        <div className="form-grid">
          {[['observer', 'Observer name'], ['observatory', 'Observatory name'], ['telescope', 'Telescope name'], ['camera', 'Camera name']].map(([k, l]) => (
            <div className="field" key={k} style={{ gridTemplateColumns: '170px minmax(0,1fr)' }}>
              <label>{l}</label>
              <input value={values[k] ?? ''} onChange={e => set({ [k]: e.target.value })} />
            </div>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title" style={{ marginBottom: advanced ? 12 : 0 }}>
          <h3>Advanced settings</h3>
          <button className="small" onClick={() => setAdvanced(a => !a)}>{advanced ? 'Hide' : 'Show'}</button>
        </div>
        {advanced && <Advanced values={values} set={set} imageShape={check?.image_shape ?? null} />}
      </div>

      {modal === 'files' && <FilesModal onClose={() => setModal('')} />}
      {modal === 'header' && <HeaderModal pattern={values.observation_files} onClose={() => setModal('')} />}
      {modal === 'target' && <TargetModal values={values} onClose={() => setModal('')} onChoose={set} />}
      {modal === 'location' && <LocationModal values={values} onClose={() => setModal('')} onChoose={set} />}
    </div>
  )
}

function FilesModal({ onClose }: { onClose: () => void }) {
  const [files, setFiles] = useState<string[] | null>(null)
  useEffect(() => { api.get<{ files: string[] }>('/data-target/files').then(r => setFiles(r.files)) }, [])
  return (
    <Modal title="Files in your directory" onClose={onClose}>
      {!files ? <span className="spinner" /> : (
        <div className="list mono" style={{ maxHeight: 480 }}>
          {files.map(f => <div key={f} className="list-item" style={{ cursor: 'default' }}>{f}</div>)}
          {files.length === 0 && <div className="list-item faint">No FITS files found</div>}
        </div>
      )}
    </Modal>
  )
}

function HeaderModal({ pattern, onClose }: { pattern: string; onClose: () => void }) {
  const [data, setData] = useState<{ file: string; header: [string, string][] } | null>(null)
  const [filter, setFilter] = useState('')
  useEffect(() => { api.get<any>('/data-target/header', { observation_files: pattern }).then(setData) }, [pattern])
  return (
    <Modal title="Header keywords" onClose={onClose} wide>
      {!data ? <span className="spinner" /> : (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <span className="faint" style={{ flex: 1 }}>{data.file}</span>
            <input placeholder="Search keywords" value={filter} onChange={e => setFilter(e.target.value)} />
          </div>
          <div className="list" style={{ maxHeight: 520 }}>
            <table className="table mono">
              <tbody>
                {data.header.filter(([k, v]) => !filter || (k + v).toLowerCase().includes(filter.toLowerCase())).map(([k, v]) => (
                  <tr key={k}><td style={{ width: 160, color: 'var(--accent)' }}>{k}</td><td>{v}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  )
}

function TargetModal({ values, onClose, onChoose }: { values: Values; onClose: () => void; onChoose: (v: Values) => void }) {
  const [choice, setChoice] = useState<number>(values.target_ra_dec_choice)
  const headerName: string = values.auto_target_name || ''
  const [name, setName] = useState<string>(
    values.simbad_target_name && values.simbad_target_name !== 'Target name' ? values.simbad_target_name : headerName)
  const [manual, setManual] = useState<string>(values.manual_target_ra_dec)
  const [result, setResult] = useState<{ target_ra_dec: string; target_name: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const autoAvailable = values.auto_target_ra_dec && values.auto_target_ra_dec !== 'Not found'

  const resolve = (c = choice) => {
    setBusy(true)
    api.post<any>('/data-target/resolve-target', {
      choice: c, auto_target_ra_dec: values.auto_target_ra_dec, simbad_target_name: name, manual_target_ra_dec: manual,
    }).then(setResult).finally(() => setBusy(false))
  }

  useEffect(() => {
    if (choice === 0 && autoAvailable) resolve(0)
    if (choice === 1 && name.trim()) resolve(1)
  }, [choice])

  const choose = () => {
    if (!result) return
    onChoose({
      target_ra_dec_choice: choice,
      simbad_target_name: choice === 1 ? name : '',
      manual_target_ra_dec: choice === 2 ? manual : '',
      target_ra_dec: result.target_ra_dec,
      target_name: result.target_name,
    })
    onClose()
  }

  return (
    <Modal title="Select / change target" onClose={onClose} footer={
      <>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!result || busy} onClick={choose}>Choose</button>
      </>
    }>
      <p className="muted" style={{ marginTop: 0 }}>How would you like to choose your target?</p>
      <div className={'choice' + (choice === 0 ? ' selected' : '') + (!autoAvailable ? ' disabled' : '')}
        onClick={() => autoAvailable && setChoice(0)}>
        <input type="radio" checked={choice === 0} disabled={!autoAvailable} readOnly />
        <div>
          <div>Use the RA/DEC found in the file's header</div>
          <div className="mono faint">{values.auto_target_ra_dec}</div>
        </div>
      </div>
      <div className={'choice' + (choice === 1 ? ' selected' : '')} onClick={() => setChoice(1)}>
        <input type="radio" checked={choice === 1} readOnly />
        <div className="stack">
          <div>Provide the name of the target (SIMBAD)</div>
          {headerName && <div className="faint">From the FITS header (OBJECT): <span className="mono">{headerName}</span></div>}
          <form className="row" onSubmit={e => { e.preventDefault(); setChoice(1); resolve(1) }}>
            <input style={{ flex: 1 }} placeholder="KPS-1" value={name} disabled={choice !== 1} onChange={e => setName(e.target.value)} />
            <button type="submit" disabled={choice !== 1 || busy}>Search</button>
          </form>
        </div>
      </div>
      <div className={'choice' + (choice === 2 ? ' selected' : '')} onClick={() => setChoice(2)}>
        <input type="radio" checked={choice === 2} readOnly />
        <div className="stack">
          <div>Provide the RA/DEC of the target <span className="faint">(hh:mm:ss +/-dd:mm:ss)</span></div>
          <form className="row" onSubmit={e => { e.preventDefault(); setChoice(2); resolve(2) }}>
            <input className="mono" style={{ flex: 1 }} value={manual} disabled={choice !== 2} onChange={e => setManual(e.target.value)} />
            <button type="submit" disabled={choice !== 2 || busy}>Check</button>
          </form>
        </div>
      </div>
      <div className="card" style={{ marginTop: 14, background: 'var(--surface-2)' }}>
        {busy ? <><span className="spinner" /> Searching…</> : result ? (
          <div className="kv">
            <span>Target RA/DEC</span><span className="mono">{result.target_ra_dec} {!result.ok && <span className="badge err">not valid</span>}</span>
            <span>Target name</span><span>{result.target_name}</span>
          </div>
        ) : <span className="faint">Choose an option to resolve the target.</span>}
      </div>
    </Modal>
  )
}

function LocationModal({ values, onClose, onChoose }: { values: Values; onClose: () => void; onChoose: (v: Values) => void }) {
  const [choice, setChoice] = useState<number>(values.location_choice)
  const [manual, setManual] = useState<string>(values.manual_location)
  const [ok, setOk] = useState<boolean | null>(null)
  const options = [values.auto_location, values.profile_location]
  const available = (i: number) => options[i] && options[i] !== 'Not found'
  const location = choice === 2 ? manual : options[choice]

  useEffect(() => {
    if (!location) { setOk(false); return }
    api.post<{ ok: boolean }>('/data-target/check-location', { location }).then(r => setOk(r.ok))
  }, [location])

  const row = (i: number, label: string, content: React.ReactNode) => (
    <div className={'choice' + (choice === i ? ' selected' : '') + (i < 2 && !available(i) ? ' disabled' : '')}
      onClick={() => (i === 2 || available(i)) && setChoice(i)}>
      <input type="radio" checked={choice === i} readOnly />
      <div><div>{label}</div>{content}</div>
    </div>
  )

  return (
    <Modal title="Select / change location" onClose={onClose} footer={
      <>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!ok} onClick={() => {
          onChoose({ location_choice: choice, manual_location: manual, location })
          onClose()
        }}>Choose</button>
      </>
    }>
      {row(0, "Use the LAT/LONG found in the file's header", <div className="mono faint">{options[0]}</div>)}
      {row(1, 'Use the LAT/LONG found in your profile', <div className="mono faint">{options[1]}</div>)}
      {row(2, 'Provide the LAT/LONG of the location', (
        <>
          <div className="faint">+/-dd:mm:ss +/-dd:mm:ss — positive LAT is north, positive LONG is east</div>
          <input className="mono" style={{ width: '100%', marginTop: 6 }} value={manual} disabled={choice !== 2}
            onChange={e => setManual(e.target.value)} />
        </>
      ))}
      <div style={{ marginTop: 12 }}>
        {ok === false && <span className="badge err">Wrong coordinates</span>}
        {ok && <span className="badge ok">✓ {location}</span>}
      </div>
    </Modal>
  )
}

function Advanced({ values, set, imageShape }: { values: Values; set: (v: Values) => void; imageShape: [number, number] | null }) {
  const [original, setOriginal] = useState<FitsImage | null>(null)
  const [preview, setPreview] = useState<FitsImage | null>(null)
  const [loading, setLoading] = useState(false)
  const viewer = useRef<FitsViewerHandle>(null)

  useEffect(() => {
    setLoading(true)
    fetchImage('/data-target/image', { observation_files: values.observation_files, max_size: 1400 })
      .then(setOriginal).finally(() => setLoading(false))
  }, [values.observation_files])

  // like the Tk window: an empty crop means "whole image"
  useEffect(() => {
    if (imageShape && Number(values.crop_x2) === 0 && Number(values.crop_y2) === 0) {
      set({ crop_x2: imageShape[1], crop_y2: imageShape[0] })
    }
  }, [imageShape])

  const previewParams = {
    observation_files: values.observation_files, crop_x1: values.crop_x1 || 0, crop_x2: values.crop_x2 || 0,
    crop_y1: values.crop_y1 || 0, crop_y2: values.crop_y2 || 0, crop_edge_pixels: values.crop_edge_pixels || 0,
    bin_fits: values.bin_fits || 1,
  }
  const previewKey = JSON.stringify(previewParams)
  useEffect(() => {
    const t = setTimeout(() => {
      fetchImage('/data-target/preview', { ...previewParams, max_size: 1000 })
        .then(setPreview).catch(() => setPreview(null))
    }, 300)
    return () => clearTimeout(t)
  }, [previewKey])

  const selectArea = () => {
    const v = viewer.current?.getView()
    if (!v || !imageShape) return
    set({
      crop_x1: Math.round(Math.max(0, v.x0)), crop_x2: Math.round(Math.min(v.x1, imageShape[1])),
      crop_y1: Math.round(Math.max(0, v.y0)), crop_y2: Math.round(Math.min(v.y1, imageShape[0])),
    })
  }

  const modes: [string, string][] = [
    ['faint_target_mode', 'UltraShortExposure mode (use for occultations)'],
    ['moving_target_mode', 'MovingTarget mode (use for asteroids)'],
    ['rotating_field_mode', 'RotatingField mode (use for AltAz mounts without rotator)'],
    ['colour_camera_mode', 'ColourCamera mode (use for colour cameras)'],
  ]

  const num = (key: string) => (
    <input type="number" style={{ width: 90 }} value={values[key]} onChange={e => set({ [key]: Number(e.target.value) })} />
  )

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid grid-2">
        <div className="stack">
          {modes.map(([k, l]) => (
            <label key={k} className="check"><input type="checkbox" checked={Boolean(values[k])} onChange={e => set({ [k]: e.target.checked })} />{l}</label>
          ))}
        </div>
        <div className="stack">
          <div className="field" style={{ gridTemplateColumns: '220px 120px' }}>
            <label>Binning (use 2 for colour cameras)</label>
            <select value={values.bin_fits} onChange={e => set({ bin_fits: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5, 6, 7, 8].map(b => <option key={b}>{b}</option>)}
            </select>
          </div>
          <div className="field" style={{ gridTemplateColumns: '220px 120px' }}>
            <label title="Useful for cameras with GPS data in the first rows/columns">Remove pixels from the edge</label>
            <select value={values.crop_edge_pixels} onChange={e => set({ crop_edge_pixels: Number(e.target.value) })}>
              {Array.from({ length: 50 }, (_, i) => <option key={i}>{i}</option>)}
            </select>
          </div>
          <div className="row wrap">
            <span className="muted">Crop x</span>{num('crop_x1')}–{num('crop_x2')}
            <span className="muted" style={{ marginLeft: 8 }}>y</span>{num('crop_y1')}–{num('crop_y2')}
          </div>
        </div>
      </div>

      <div className="grid grid-2">
        <div>
          <div className="row" style={{ marginBottom: 6 }}>
            <strong style={{ flex: 1 }}>Image before reduction <span className="faint">{original?.info.size_mb} MB</span></strong>
            <button className="small" onClick={selectArea}>Select area</button>
          </div>
          <FitsViewer ref={viewer} image={original} source={{ path: '/data-target/image', params: { observation_files: values.observation_files } }} loading={loading} height={380} axes
            overlays={imageShape ? [{ type: 'rect', x: Number(values.crop_x1), y: Number(values.crop_y1),
              w: Number(values.crop_x2) - Number(values.crop_x1), h: Number(values.crop_y2) - Number(values.crop_y1), color: '#4f8cff', dash: true }] : []} />
          <p className="faint">To crop the image, zoom in to the area you want to keep and press “Select area”.</p>
        </div>
        <div>
          <div className="row" style={{ marginBottom: 6 }}>
            <strong>Image after reduction <span className="faint">{preview?.info.size_mb} MB</span></strong>
          </div>
          <FitsViewer image={preview} source={{ path: '/data-target/preview', params: previewParams }} height={380} axes controls={false} keepView={false}
            caption="This is only an example of the reduction output." />
        </div>
      </div>
    </div>
  )
}
