import { useCallback, useEffect, useState } from 'react'
import { api, AppState, StageKey } from './api'
import { STEPS } from './steps'
import { isApp, pickFolder } from './native'
import DirectoryPicker from './components/DirectoryPicker'
import Overview from './pages/Overview'
import DataTarget from './pages/DataTarget'
import Profile from './pages/Profile'
import Reduction from './pages/Reduction'
import Inspection from './pages/Inspection'
import Alignment from './pages/Alignment'
import Photometry from './pages/Photometry'
import Fitting from './pages/Fitting'
import Planner from './pages/Planner'

export type Page = 'overview' | StageKey | 'planner' | 'profile'

function currentPage(): Page {
  const hash = window.location.hash.replace('#/', '') as Page
  return hash || 'overview'
}

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [error, setError] = useState('')
  const [page, setPage] = useState<Page>(currentPage())
  const [picker, setPicker] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setState(await api.get<AppState>('/state'))
      setError('')
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const onHash = () => setPage(currentPage())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = (p: Page) => { window.location.hash = '/' + p }

  // the desktop app uses the native folder dialog, the browser the built-in folder browser
  const openPicker = async () => {
    if (!isApp) { setPicker(true); return }
    let path: string | null
    try {
      path = await pickFolder(state?.directory_ok ? state.directory : undefined)
    } catch {
      // native dialog not available: use the built-in folder browser
      setPicker(true)
      return
    }
    if (!path) return
    try {
      setState(await api.post<AppState>('/directory', { path }))
    } catch (e: any) {
      window.alert(e.message)
    }
  }

  if (!state) {
    return (
      <div className="empty">
        {error ? <div className="alert err">Cannot reach the HOPS server: {error}</div> : <><span className="spinner" /> Loading HOPS…</>}
      </div>
    )
  }

  const chooseDirectory = async (path: string) => {
    const s = await api.post<AppState>('/directory', { path })
    setState(s)
  }

  const renderPage = () => {
    switch (page) {
      case 'overview':
        return <Overview state={state} onOpen={k => go(k)} onChooseDirectory={() => openPicker()} />
      case 'data_target':
        return state.directory_ok
          ? <DataTarget key={state.directory} onSaved={(s, proceed) => { setState(s); go(proceed ? 'reduction' : 'overview') }} />
          : <div className="empty"><button className="primary" onClick={() => openPicker()}>Choose a data directory</button></div>
      case 'reduction':
        return <Reduction state={state} refresh={refresh} go={p => go(p as Page)} />
      case 'inspection':
        return <Inspection key={state.stages.reduction.text} onSaved={(s, proceed) => { setState(s); go(proceed ? 'alignment' : 'overview') }} />
      case 'alignment':
        return <Alignment state={state} refresh={refresh} go={p => go(p as Page)} />
      case 'photometry':
        return <Photometry key={state.stages.alignment.text} refresh={refresh} go={p => go(p as Page)} onState={setState} />
      case 'fitting':
        return <Fitting go={p => go(p as Page)} onState={setState} refresh={refresh} />
      case 'profile':
        return <Profile />
      case 'planner':
        return <Planner />
      default:
        return null
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-name">HOPS</span>
          <span className="brand-version">v{state.version}</span>
        </div>
        <button className={'nav-item' + (page === 'overview' ? ' active' : '')} onClick={() => go('overview')}>
          <span className="step-dot">⌂</span><span className="nav-label">Overview</span>
        </button>
        <div className="nav-section">Pipeline</div>
        {STEPS.map((step, i) => {
          const s = state.stages[step.key]
          return (
            <button
              key={step.key}
              className={'nav-item' + (page === step.key ? ' active' : '') + (s.complete ? ' done' : '')}
              disabled={!s.available}
              onClick={() => go(step.key)}
              title={s.text}
            >
              <span className="step-dot">{s.complete ? '✓' : i + 1}</span>
              <span className="nav-label">{step.title}</span>
            </button>
          )
        })}
        <div className="nav-section">Tools</div>
        <button className={'nav-item' + (page === 'planner' ? ' active' : '')} onClick={() => go('planner')}>
          <span className="step-dot">☾</span><span className="nav-label">Observing planner</span>
        </button>
        <button className={'nav-item' + (page === 'profile' ? ' active' : '')} onClick={() => go('profile')}>
          <span className="step-dot">◉</span><span className="nav-label">My profile</span>
        </button>
        <div className="sidebar-footer">
          {state.target_name && state.stages.data_target.complete ? (
            <>
              <div style={{ color: 'var(--text)' }}>{state.target_name}</div>
              <div className="mono">{state.target_ra_dec}</div>
              <div>Filter: {state.filter}</div>
            </>
          ) : 'No target selected'}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="dir">
            <span className="faint">Directory</span>
            <span className="dir-path" title={state.directory}>{state.directory_ok ? state.directory : 'not selected'}</span>
          </div>
          <button className="small" onClick={() => openPicker()}>Change</button>
          <span className="spacer" />
          {state.job && state.job.status === 'running' && (
            <span className="badge info"><span className="spinner" /> {state.job.kind} running</span>
          )}
        </div>
        <div className="content">{renderPage()}</div>
      </main>

      {picker && (
        <DirectoryPicker
          initial={state.directory_ok ? state.directory : undefined}
          onClose={() => setPicker(false)}
          onChoose={chooseDirectory}
        />
      )}
    </div>
  )
}
