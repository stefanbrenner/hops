import { AppState, StageKey } from '../api'
import { STEPS } from '../steps'

interface Props {
  state: AppState
  onOpen: (key: StageKey) => void
  onChooseDirectory: () => void
}

export default function Overview({ state, onOpen, onChooseDirectory }: Props) {
  return (
    <div style={{ maxWidth: 900 }}>
      <div className="page-header">
        <div>
          <h1>Analyse your data step by step</h1>
          <p>HOlomon Photometric Software · v{state.version}</p>
        </div>
        <a className="btn" href="https://www.exoworldsspies.com/en/software" target="_blank" rel="noreferrer">
          Updates and user manual ↗
        </a>
      </div>

      {state.new_version && (
        <div className="alert info" style={{ marginBottom: 16, whiteSpace: 'pre-line' }}>
          <strong>{state.new_version}.</strong> Download it from exoworldsspies.com/en/software
        </div>
      )}

      {!state.directory_ok && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row">
            <div style={{ flex: 1 }}>
              <h2>Start with a data directory</h2>
              <p className="muted" style={{ margin: '4px 0 0' }}>Choose the folder that contains your FITS files.</p>
            </div>
            <button className="primary" onClick={onChooseDirectory}>Choose directory</button>
          </div>
        </div>
      )}

      {STEPS.map((step, i) => {
        const s = state.stages[step.key]
        return (
          <div key={step.key} className={'step-card' + (s.complete ? ' done' : '') + (!s.available ? ' locked' : '')}>
            <div className="num">{s.complete ? '✓' : i + 1}</div>
            <div>
              <div className="row">
                <strong>{step.title}</strong>
                {step.mandatory && <span className="badge">mandatory</span>}
              </div>
              <div className="faint">{s.text || step.description}</div>
            </div>
            <button className={s.available && !s.complete ? 'primary' : ''} disabled={!s.available} onClick={() => onOpen(step.key)}>
              {s.complete ? 'Open' : 'Start'}
            </button>
          </div>
        )
      })}

      <p className="faint" style={{ marginTop: 24 }}>
        Copyright (c) 2017-{new Date().getFullYear()} Angelos Tsiaras, aggelostsiaras@gmail.com
      </p>
    </div>
  )
}
