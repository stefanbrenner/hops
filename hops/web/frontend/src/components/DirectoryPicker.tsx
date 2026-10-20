import { useEffect, useState } from 'react'
import { api } from '../api'
import Modal from './Modal'

interface Listing {
  path: string
  parent: string | null
  dirs: string[]
  fits: number
  roots: string[]
}

interface Props {
  initial?: string
  onClose: () => void
  onChoose: (path: string) => Promise<void>
}

export default function DirectoryPicker({ initial, onClose, onChoose }: Props) {
  const [listing, setListing] = useState<Listing | null>(null)
  const [pathInput, setPathInput] = useState(initial || '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const open = (path?: string) => {
    setError('')
    api.get<Listing>('/fs', path ? { path } : undefined)
      .then(l => { setListing(l); setPathInput(l.path) })
      .catch(e => setError(e.message))
  }

  useEffect(() => open(initial), [])

  const choose = async () => {
    if (!listing) return
    setBusy(true)
    try {
      await onChoose(listing.path)
      onClose()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Choose data directory"
      onClose={onClose}
      footer={
        <>
          <span className="faint" style={{ marginRight: 'auto' }}>
            {listing ? `${listing.fits} FITS files in this folder` : ''}
          </span>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={choose} disabled={!listing || busy}>
            {busy ? <span className="spinner" /> : null} Use this folder
          </button>
        </>
      }
    >
      <form className="row" style={{ marginBottom: 12 }} onSubmit={e => { e.preventDefault(); open(pathInput) }}>
        <input style={{ flex: 1 }} className="mono" value={pathInput} onChange={e => setPathInput(e.target.value)} />
        <button type="submit">Go</button>
      </form>
      {listing && (
        <div className="row wrap" style={{ marginBottom: 10 }}>
          {listing.roots.map(r => (
            <button key={r} className="small" onClick={() => open(r)}>{r === listing.roots[0] ? '⌂ Home' : r}</button>
          ))}
        </div>
      )}
      {error && <div className="alert err" style={{ marginBottom: 10 }}>{error}</div>}
      {listing && (
        <div className="list" style={{ maxHeight: 360 }}>
          {listing.parent && (
            <div className="list-item" onClick={() => open(listing.parent!)}>
              <span>↰</span><span className="muted">..</span>
            </div>
          )}
          {listing.dirs.map(d => (
            <div key={d} className="list-item" onClick={() => open(listing.path.replace(/[\\/]$/, '') + '/' + d)}>
              <span>📁</span><span>{d}</span>
            </div>
          ))}
          {listing.dirs.length === 0 && <div className="list-item faint">No subfolders</div>}
        </div>
      )}
    </Modal>
  )
}
