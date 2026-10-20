import { useState } from 'react'
import { AppState } from '../api'
import FitsViewer from '../components/FitsViewer'
import { JobQuestion, JobStatus, StopButton, TaskBars, useJob } from '../components/JobPanel'

interface Props {
  state: AppState
  refresh: () => Promise<void>
  go: (page: string) => void
}

export default function Alignment({ state, refresh, go }: Props) {
  const [half, setHalf] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState('')
  const { job, preview, running, start, cancel, answer } = useJob('alignment', () => refresh())
  const complete = state.stages.alignment.complete

  const run = async () => {
    setError('')
    setConfirm(false)
    try {
      await start('/alignment/run')
      refresh()
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Alignment</h1>
          <p>Finds the shift and rotation of every frame relative to the first one.</p>
        </div>
        <div className="row">
          {running ? (
            <StopButton job={job!} label="Stop alignment" onStop={cancel} />
          ) : (
            <>
              {complete && <button onClick={() => go('photometry')}>Photometry →</button>}
              <button className={complete ? '' : 'primary'} onClick={() => (complete ? setConfirm(true) : run())}>
                {complete ? 'Run again' : 'Run alignment'}
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="alert err" style={{ marginBottom: 16 }}>{error}</div>}
      {confirm && (
        <div className="alert warn row" style={{ marginBottom: 16 }}>
          <span style={{ flex: 1 }}>Alignment has been completed, do you want to run it again?</span>
          <button onClick={() => setConfirm(false)}>Cancel</button>
          <button className="primary" onClick={run}>Run again</button>
        </div>
      )}

      <div className="split">
        <div className="card">
          <FitsViewer image={preview} source={{ path: '/job/preview', key: job?.preview_version }} keepView showHalf={half} height={560} overlays={job?.preview_overlays ?? []}
            caption={job?.preview_name ?? undefined} />
          <p className="faint" style={{ margin: '6px 0 0' }}>
            <span style={{ color: 'var(--target)' }}>●</span> reference star&nbsp;&nbsp;
            <span style={{ color: '#ffd33d' }}>●</span> calibration stars
          </p>
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-title">
              <h3>Progress</h3>
              <label className="check faint"><input type="checkbox" checked={half} onChange={e => setHalf(e.target.checked)} />Show smaller frame</label>
            </div>
            {job ? <TaskBars job={job} /> : (
              <p className="muted" style={{ margin: 0 }}>{complete ? state.stages.alignment.text : 'Press “Run alignment” to start.'}</p>
            )}
          </div>
          {job && <JobStatus job={job} />}
          {job?.status === 'done' && (
            <div className="card">
              <div className="kv">
                <span>Frames</span><span>{job.result?.frames}</span>
                <span>Aligned</span><span>{job.result?.aligned}</span>
                <span>Stars in FOV</span><span>{job.result?.stars}</span>
              </div>
              <button className="primary" style={{ marginTop: 14 }} onClick={() => go('photometry')}>Continue to photometry →</button>
            </div>
          )}
        </div>
      </div>
      {job && <JobQuestion job={job} onAnswer={answer} />}
    </div>
  )
}
