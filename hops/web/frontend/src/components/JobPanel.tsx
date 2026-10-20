import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fetchImage, FitsImage, Job } from '../api'
import Modal from './Modal'

type Listener = (job: Job | null) => void

/** One WebSocket for the whole page: the server pushes the current job whenever it changes.
 * While the socket is down the hook falls back to polling. */
const jobFeed = (() => {
  const listeners = new Set<Listener>()
  let socket: WebSocket | null = null
  let connected = false

  const connect = () => {
    const url = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/ws`
    socket = new WebSocket(url)
    socket.onopen = () => { connected = true }
    socket.onmessage = event => {
      const job = JSON.parse(event.data) as Job | null
      listeners.forEach(listener => listener(job))
    }
    socket.onclose = () => {
      connected = false
      socket = null
      if (listeners.size) setTimeout(connect, 1000)
    }
  }

  return {
    subscribe(listener: Listener) {
      listeners.add(listener)
      if (!socket) connect()
      return () => { listeners.delete(listener) }
    },
    get connected() { return connected },
  }
})()

/** Follows the job of the given kind (live via WebSocket) and loads its preview image whenever it changes. */
export function useJob(kind: string, onFinished?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const [preview, setPreview] = useState<FitsImage | null>(null)
  const previewVersion = useRef(0)
  const finishedRef = useRef(onFinished)
  finishedRef.current = onFinished
  const running = job?.status === 'running'

  const receive = useCallback((j: Job | null) => {
    if (!j || j.kind !== kind) return
    setJob(prev => {
      if (prev && prev.id === j.id && prev.status === 'running' && j.status !== 'running') finishedRef.current?.(j)
      return j
    })
    if (j.preview_version && j.preview_version !== previewVersion.current) {
      previewVersion.current = j.preview_version
      fetchImage('/job/preview', { max_size: 1200 }).then(setPreview).catch(() => undefined)
    }
  }, [kind])

  const poll = useCallback(async () => {
    const j = await api.get<Job | null>('/job')
    receive(j)
    return j
  }, [receive])

  useEffect(() => { poll() }, [poll])
  useEffect(() => jobFeed.subscribe(receive), [receive])

  // fallback while the WebSocket is not connected
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => { if (!jobFeed.connected) poll() }, 700)
    return () => clearInterval(t)
  }, [running, poll])

  const start = async (path: string, body?: unknown) => {
    previewVersion.current = 0
    const j = await api.post<Job>(path, body)
    setJob(j)
    return j
  }
  const cancel = () => api.post('/job/cancel').then(poll)
  const answer = (value: string) => api.post('/job/answer', { value }).then(poll)

  return { job, preview, running, start, cancel, answer, poll }
}

export function TaskBars({ job }: { job: Job }) {
  return (
    <div className="stack" style={{ gap: 12 }}>
      {job.tasks.map(t => {
        const pct = (100 * t.current) / t.total
        return (
          <div key={t.key}>
            <div className="row" style={{ justifyContent: 'space-between', fontSize: 13 }}>
              <span>{t.label}</span>
              <span className="faint mono">{pct.toFixed(1)}% · {t.current}/{t.total}{t.eta && !t.done ? ` · ${t.eta} left` : ''}</span>
            </div>
            <div className={'progress' + (t.done ? ' done' : '')} style={{ marginTop: 4 }}><div style={{ width: `${pct}%` }} /></div>
            {t.message && <div className="faint mono" style={{ marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.message}</div>}
          </div>
        )
      })}
      {job.status === 'running' && job.message && <div className="row muted"><span className="spinner" />{job.message}</div>}
    </div>
  )
}

/** The stop button of a running job; shows that stopping takes a moment. */
export function StopButton({ job, label, onStop }: { job: Job; label: string; onStop: () => void }) {
  return job.stopping
    ? <button className="danger" disabled><span className="spinner" /> Stopping…</button>
    : <button className="danger" onClick={onStop}>{label}</button>
}

export function JobStatus({ job }: { job: Job }) {
  if (job.status === 'error') return <div className="alert err" style={{ whiteSpace: 'pre-line' }}>{job.error}</div>
  if (job.status === 'cancelled') return <div className="alert warn">Stopped. The results of this step were not saved.</div>
  if (job.status === 'done') return <div className="alert info">Completed in {Math.round(job.elapsed)} s.</div>
  return null
}

export function JobQuestion({ job, onAnswer }: { job: Job; onAnswer: (value: string) => void }) {
  if (!job.question) return null
  return (
    <Modal title={`HOPS – ${job.kind}`} onClose={() => undefined} footer={
      job.question.options.map((o, i) => (
        <button key={o} className={i === 0 ? 'primary' : ''} onClick={() => onAnswer(o)}>{o}</button>
      ))
    }>
      <p style={{ whiteSpace: 'pre-line', margin: 0 }}>{job.question.text}</p>
    </Modal>
  )
}
