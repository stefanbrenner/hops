import { useState } from 'react'
import { api, AppState } from '../api'
import FitsViewer from '../components/FitsViewer'
import { JobStatus, StopButton, TaskBars, useJob } from '../components/JobPanel'

interface Props {
  state: AppState
  refresh: () => Promise<void>
  go: (page: string) => void
}

export default function Reduction({ state, refresh, go }: Props) {
  const [showAll, setShowAll] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState('')
  const { job, preview, running, start, cancel } = useJob('reduction', () => refresh())
  const complete = state.stages.reduction.complete

  const run = async () => {
    setError('')
    setConfirm(false)
    try {
      await start('/reduction/run', { show_all_frames: showAll })
      refresh()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const toggleShowAll = (value: boolean) => {
    setShowAll(value)
    api.post('/reduction/show-all', { show_all_frames: value })
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Reduction</h1>
          <p>Bias, dark and flat correction, frame statistics, PSF and timing of every science frame.</p>
        </div>
        <div className="row">
          {running ? (
            <StopButton job={job!} label="Stop reduction" onStop={cancel} />
          ) : (
            <>
              {complete && <button onClick={() => go('inspection')}>Inspect frames →</button>}
              <button className={complete ? '' : 'primary'} onClick={() => (complete ? setConfirm(true) : run())}>
                {complete ? 'Run again' : 'Run reduction'}
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="alert err" style={{ marginBottom: 16 }}>{error}</div>}
      {confirm && (
        <div className="alert warn row" style={{ marginBottom: 16 }}>
          <span style={{ flex: 1 }}>Reduction has been completed, do you want to run it again? All reduced files and later steps will be replaced.</span>
          <button onClick={() => setConfirm(false)}>Cancel</button>
          <button className="primary" onClick={run}>Run again</button>
        </div>
      )}

      <div className="split">
        <div className="card">
          <FitsViewer image={preview} source={{ path: '/job/preview', key: job?.preview_version }} keepView height={560} caption={job?.preview_name ?? undefined} />
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-title">
              <h3>Progress</h3>
              <label className="check faint"><input type="checkbox" checked={showAll} onChange={e => toggleShowAll(e.target.checked)} />Show all frames</label>
            </div>
            {job ? <TaskBars job={job} /> : (
              <p className="muted" style={{ margin: 0 }}>
                {complete ? state.stages.reduction.text : 'Press “Run reduction” to start.'}
              </p>
            )}
          </div>
          {job && <JobStatus job={job} />}
          {job?.status === 'done' && (
            <div className="card">
              <div className="kv">
                <span>Reduced frames</span><span>{job.result?.frames}</span>
                <span>PSF not found</span><span>{job.result?.skipped}</span>
              </div>
              <button className="primary" style={{ marginTop: 14 }} onClick={() => go('inspection')}>Continue to inspection →</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
