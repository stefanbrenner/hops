import { useEffect, useState } from 'react'
import { api } from '../api'

interface Field { key: string; value?: string; spacer?: boolean }

// same three groups as the Tk "My profile" window (17 entries per column)
const GROUPS = [
  { title: 'Header keywords', note: 'Keywords for useful information, if included in the FITS header.' },
  { title: 'Default values', note: 'Used when the information is not included in the FITS header.' },
  { title: 'Default values and planner', note: 'The observatory time zone and horizon are only used by the observing planner.' },
]

export default function Profile() {
  const [fields, setFields] = useState<Field[] | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<{ fields: Field[] }>('/profile').then(r => {
      setFields(r.fields)
      setValues(Object.fromEntries(r.fields.filter(f => !f.spacer).map(f => [f.key, f.value ?? ''])))
    }).catch(e => setError(e.message))
  }, [])

  if (!fields) return <div className="empty">{error ? <div className="alert err">{error}</div> : <span className="spinner" />}</div>

  const save = () => {
    api.put('/profile', { values }).then(() => { setSaved(true); setTimeout(() => setSaved(false), 2500) })
      .catch(e => setError(e.message))
  }

  const columns = [fields.slice(0, 17), fields.slice(17, 34), fields.slice(34)]

  return (
    <div style={{ maxWidth: 1300 }}>
      <div className="page-header">
        <div>
          <h1>My profile</h1>
          <p>Stored in your home folder and used for every new data directory.</p>
        </div>
        <div className="row">
          {saved && <span className="badge ok">✓ Saved</span>}
          <button className="primary" onClick={save}>Save changes</button>
        </div>
      </div>
      {error && <div className="alert err" style={{ marginBottom: 16 }}>{error}</div>}
      <div className="grid grid-3">
        {columns.map((col, i) => (
          <div className="card" key={i}>
            <h3>{GROUPS[i].title}</h3>
            <p className="faint" style={{ margin: '4px 0 12px' }}>{GROUPS[i].note}</p>
            {col.filter(f => !f.spacer).map(f => (
              <div className="field" key={f.key} style={{ gridTemplateColumns: '170px minmax(0,1fr)' }}>
                <label className="mono" style={{ fontSize: 12 }}>{f.key}</label>
                <input value={values[f.key]} onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
