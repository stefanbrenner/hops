import { useEffect, useMemo, useRef, useState } from 'react'
import Plotly from 'plotly.js-dist-min'

interface Props {
  data: any[]
  layout?: Record<string, any>
  config?: Partial<Plotly.Config>
  height?: number
  onClick?: (event: Plotly.PlotMouseEvent) => void
  onSelected?: (event: Plotly.PlotSelectionEvent) => void
  onRelayout?: (event: Plotly.PlotRelayoutEvent) => void
  revision?: unknown
}

export const darkLayout: Partial<Plotly.Layout> = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: '#0d1117',
  font: { color: '#9aa7b4', size: 12, family: '-apple-system, BlinkMacSystemFont, Segoe UI, Inter, sans-serif' },
  margin: { l: 60, r: 20, t: 20, b: 45 },
  xaxis: { gridcolor: '#202833', zerolinecolor: '#2a3441', linecolor: '#2a3441' },
  yaxis: { gridcolor: '#202833', zerolinecolor: '#2a3441', linecolor: '#2a3441' },
  legend: { bgcolor: 'rgba(0,0,0,0)', orientation: 'h', y: 1.12 },
  dragmode: 'zoom',
}

/** A clicked point, in data coordinates of its axes (its pixel position follows re-renders). */
interface Popover { x: any; y: any; xaxis: string; yaxis: string; lines: string[]; key: string }

/** Fill a Plotly hovertemplate (%{x}, %{y:.4f}, %{text}, <br>, <extra>) for our own popover. */
function formatTemplate(template: string | undefined, point: any): string[] {
  const value = (name: string, format?: string) => {
    const v = name === 'text' ? point.text : point[name]
    if (typeof v === 'number' && format) {
      const digits = /\.(\d+)f/.exec(format)
      if (digits) return v.toFixed(Number(digits[1]))
    }
    return v === undefined || v === null ? '' : String(v)
  }
  const name = point.data?.name ?? ''
  if (!template) {
    return [name, `x = ${value('x')}`, `y = ${value('y', '.4f')}`, point.text ? String(point.text) : ''].filter(Boolean)
  }
  let extra = ''
  const body = template
    .replace(/<extra>(.*?)<\/extra>/, (_, e) => { extra = e; return '' })
    .replace(/%\{(\w+)(?::([^}]*))?\}/g, (_, key, format) => value(key, format))
  return [...body.split('<br>'), extra || name].filter(line => line.trim() !== '')
}

/** Plotly with the dark HOPS theme. Point details appear only on click, in a popover beside the point. */
export default function Plot({ data, layout, config, height = 320, onClick, onSelected, onRelayout, revision }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const ref = useRef<HTMLDivElement>(null)
  const [popover, setPopover] = useState<Popover | null>(null)
  const handlers = useRef({ onClick, onSelected, onRelayout })
  handlers.current = { onClick, onSelected, onRelayout }

  // no tooltips on hover: the templates are kept for the click popover
  const traces = useMemo(() => data.map(trace => trace.hoverinfo === 'skip'
    ? trace
    : { ...trace, hoverinfo: 'none', hovertemplate: undefined, _popover: trace.hovertemplate }), [data])

  useEffect(() => {
    const el = ref.current as any
    if (!el) return
    const merged: Partial<Plotly.Layout> = {
      ...darkLayout,
      ...layout,
      xaxis: { ...darkLayout.xaxis, ...(layout?.xaxis ?? {}) },
      yaxis: { ...darkLayout.yaxis, ...(layout?.yaxis ?? {}) },
      hovermode: 'closest',
      height,
    }
    Plotly.react(el, traces as Plotly.Data[], merged, { displaylogo: false, responsive: true, ...config })
  }, [traces, layout, config, height, revision])

  useEffect(() => {
    const el = ref.current as any
    if (!el) return
    const click = (e: any) => {
      handlers.current.onClick?.(e)
      const point = e.points?.[0]
      if (!point) return
      const key = `${point.curveNumber}:${point.pointNumber}`
      setPopover(prev => (prev?.key === key ? null : {
        x: point.x, y: point.y, xaxis: point.xaxis._name, yaxis: point.yaxis._name, key,
        lines: formatTemplate(point.data._popover, point),
      }))
    }
    const selected = (e: Plotly.PlotSelectionEvent) => handlers.current.onSelected?.(e)
    const relayout = (e: Plotly.PlotRelayoutEvent) => { setPopover(null); handlers.current.onRelayout?.(e) }
    // the listeners are attached once Plotly has initialised the element
    const attach = () => {
      if (!el.on) return false
      el.on('plotly_click', click)
      el.on('plotly_selected', selected)
      el.on('plotly_relayout', relayout)
      return true
    }
    if (!attach()) {
      const t = setInterval(() => attach() && clearInterval(t), 100)
      return () => clearInterval(t)
    }
    return () => { el.removeAllListeners?.('plotly_click'); el.removeAllListeners?.('plotly_selected'); el.removeAllListeners?.('plotly_relayout') }
  }, [])

  // a click elsewhere or Escape closes the popover
  useEffect(() => {
    if (!popover) return
    const close = (e: MouseEvent) => { if (!wrapRef.current?.contains(e.target as Node)) setPopover(null) }
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setPopover(null)
    document.addEventListener('mousedown', close)
    window.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', close); window.removeEventListener('keydown', escape) }
  }, [popover])

  useEffect(() => () => { if (ref.current) Plotly.purge(ref.current) }, [])

  // beside the point, never on it: above-right by default, flipped near the right / top edge
  let popoverStyle: React.CSSProperties | null = null
  const fullLayout = (ref.current as any)?._fullLayout
  if (popover && wrapRef.current && fullLayout?.[popover.xaxis] && fullLayout?.[popover.yaxis]) {
    const xa = fullLayout[popover.xaxis], ya = fullLayout[popover.yaxis]
    const px = xa._offset + xa.l2p(xa.d2l(popover.x))
    const py = ya._offset + ya.l2p(ya.d2l(popover.y))
    const width = wrapRef.current.clientWidth
    const left = px > width * 0.6
    const below = py < 70
    popoverStyle = {
      left: px + (left ? -12 : 12),
      top: py + (below ? 12 : -12),
      transform: `translate(${left ? '-100%' : '0'}, ${below ? '0' : '-100%'})`,
    }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <div ref={ref} style={{ width: '100%', minHeight: height }} />
      {popover && popoverStyle && (
        <div className="plot-popover" style={popoverStyle} role="tooltip">
          {popover.lines.map((line, i) => <div key={i} className={i === 0 ? 'mono' : 'mono faint'}>{line}</div>)}
        </div>
      )}
    </div>
  )
}
