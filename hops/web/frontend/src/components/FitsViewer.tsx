import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { fetchImage, FitsImage, Overlay } from '../api'

/** Display options, same layout as `photometry_fov_options` in the log:
 * [vmin_sigma, vmax_sigma, gamma, flip, mirror, white_sky, xlim0, xlim1, ylim0, ylim1] */
export type FovOptions = (number | string)[]

export interface View { x0: number; x1: number; y0: number; y1: number }

export interface FitsViewerHandle {
  getFovOptions: () => FovOptions | null
  getView: () => View | null
  reset: () => void
}

/** Where the full-resolution parts of the image can be loaded from (endpoint + its parameters);
 * `key` changes whenever the image itself changes (e.g. a new preview frame). */
export interface ImageSource {
  path: string
  params?: Record<string, string | number | boolean>
  key?: string | number
}

interface Props {
  image: FitsImage | null
  source?: ImageSource
  overlays?: Overlay[]
  height?: number | string
  controls?: boolean
  axes?: boolean
  initialOptions?: FovOptions | null
  keepView?: boolean
  showHalf?: boolean
  caption?: string
  onDoubleClick?: (x: number, y: number) => void
  loading?: boolean
}

// contrast slider mapping of the Tk FitsWindow: cubic around the mean so that the
// useful range near the sky level gets most of the slider travel
const A = 0.5
const G = 3

function scaleFromValue(v: number, mean: number, max: number) {
  const xx = Math.pow(((v - mean) ** 2) * Math.pow(1 - A, 2 * G) / ((max - mean) ** 2), 1 / (2 * G))
  return v >= mean ? A + xx : A - xx
}

function valueFromScale(s: number, mean: number, max: number) {
  return Math.pow(1 - A, -G) * (max - mean) * Math.pow(s - A, G) + mean
}

/** Render an image into a canvas (row 0 of the data is the bottom of the image). */
function renderImage(image: FitsImage, canvas: HTMLCanvasElement, vmin: number, vmax: number, gamma: number, whiteSky: boolean) {
  const { width, height } = image.info
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')!
  const img = ctx.createImageData(width, height)
  const p = Math.pow(10, -gamma)
  const lo = Math.pow(Math.max(vmin, 0), p)
  const hi = Math.pow(Math.max(vmax, 0), p)
  const range = hi - lo || 1
  const data = image.data
  const out = img.data
  for (let row = 0; row < height; row++) {
    const target = (height - 1 - row) * width
    for (let col = 0; col < width; col++) {
      const v = data[row * width + col]
      let t = ((v > 0 ? Math.pow(v, p) : 0) - lo) / range
      t = t < 0 ? 0 : t > 1 ? 1 : t
      let g = Math.round(255 * t)
      if (whiteSky) g = 255 - g
      const o = (target + col) * 4
      out[o] = g; out[o + 1] = g; out[o + 2] = g; out[o + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
}

/** Value under a data coordinate, or NaN outside the image. */
function valueAt(image: FitsImage | null, x: number, y: number) {
  if (!image) return NaN
  const { width, height, factor } = image.info
  const col = Math.floor((x - (image.info.x_offset ?? 0)) / factor)
  const row = Math.floor((y - (image.info.y_offset ?? 0)) / factor)
  return col >= 0 && row >= 0 && col < width && row < height ? image.data[row * width + col] : NaN
}

const FitsViewer = forwardRef<FitsViewerHandle, Props>(function FitsViewer(
  { image, source, overlays = [], height = 520, controls = true, axes = false, initialOptions, keepView, showHalf,
    caption, onDoubleClick, loading },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const offscreen = useRef<HTMLCanvasElement | null>(null)
  const regionCanvas = useRef<HTMLCanvasElement | null>(null)
  const [region, setRegion] = useState<FitsImage | null>(null)
  const [size, setSize] = useState({ w: 600, h: 400 })
  const [vmin, setVmin] = useState(0)
  const [vmax, setVmax] = useState(1)
  const [gamma, setGamma] = useState(0)
  const [flip, setFlip] = useState(false)
  const [mirror, setMirror] = useState(false)
  const [whiteSky, setWhiteSky] = useState(false)
  const [view, setView] = useState<View | null>(null)
  const [hover, setHover] = useState('')
  const drag = useRef<{ x: number; y: number; view: View } | null>(null)
  const loadedFor = useRef<FitsImage | null>(null)
  const options = useRef(initialOptions)
  options.current = initialOptions

  const info = image?.info

  const fullView = useCallback((): View | null => {
    if (!info) return null
    if (showHalf) {
      return { x0: 0.25 * info.orig_width, x1: 0.75 * info.orig_width, y0: 0.25 * info.orig_height, y1: 0.75 * info.orig_height }
    }
    return { x0: 0, x1: info.orig_width, y0: 0, y1: info.orig_height }
  }, [info, showHalf])

  // initialise display settings when a new image arrives
  useEffect(() => {
    if (!image || !info) return
    const first = loadedFor.current === null
    loadedFor.current = image
    const opts = options.current
    let lo = info.mean + info.low_std * info.std
    let hi = info.mean + info.upper_std * info.std
    if (opts && opts.length >= 6 && (first || !keepView)) {
      if (opts[0] !== 'auto') lo = info.mean + Number(opts[0]) * info.std
      if (opts[1] !== 'auto') hi = info.mean + Number(opts[1]) * info.std
      setGamma(Number(opts[2]) || 0)
      setFlip(Boolean(Number(opts[3])))
      setMirror(Boolean(Number(opts[4])))
      setWhiteSky(Boolean(Number(opts[5])))
    }
    if (!keepView || first) {
      setVmin(lo)
      setVmax(hi)
    }
    if (!keepView || first || view === null) {
      if (opts && opts.length >= 10 && !opts.slice(6).includes('auto') && first) {
        const [x0, x1, y0, y1] = opts.slice(6).map(Number)
        setView({ x0: Math.min(x0, x1), x1: Math.max(x0, x1), y0: Math.min(y0, y1), y1: Math.max(y0, y1) })
      } else {
        setView(fullView())
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image])

  useEffect(() => { if (showHalf !== undefined && info) setView(fullView()) }, [showHalf])

  // container size
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect
      setSize({ w: Math.max(100, r.width), h: Math.max(100, r.height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // render the overview and the full-resolution region into offscreen canvases with the current contrast
  const rendered = useMemo(() => {
    if (!image || !info) return 0
    offscreen.current = offscreen.current ?? document.createElement('canvas')
    renderImage(image, offscreen.current, vmin, vmax, gamma, whiteSky)
    return Math.random()
  }, [image, info, vmin, vmax, gamma, whiteSky])

  const renderedRegion = useMemo(() => {
    if (!region) return 0
    regionCanvas.current = regionCanvas.current ?? document.createElement('canvas')
    renderImage(region, regionCanvas.current, vmin, vmax, gamma, whiteSky)
    return Math.random()
  }, [region, vmin, vmax, gamma, whiteSky])

  // large images: when zoomed in, load the visible part at full resolution
  const sourceKey = source ? JSON.stringify([source.path, source.params, source.key]) : ''
  useEffect(() => { setRegion(null) }, [sourceKey])
  useEffect(() => {
    if (!source || !info || !view || info.factor <= 1 || info.prebinned) { setRegion(null); return }
    const dpr = window.devicePixelRatio || 1
    const x0 = Math.max(0, Math.min(view.x0, view.x1)), x1 = Math.min(info.orig_width, Math.max(view.x0, view.x1))
    const y0 = Math.max(0, Math.min(view.y0, view.y1)), y1 = Math.min(info.orig_height, Math.max(view.y0, view.y1))
    if (x1 <= x0 || y1 <= y0) return
    const maxSize = Math.min(2400, Math.ceil(Math.max(size.w, size.h) * dpr))
    // only worth it when the region would be sharper than the overview
    if (Math.max(x1 - x0, y1 - y0) / maxSize >= info.factor * 0.9) { setRegion(null); return }
    const pad = 0.15 * Math.max(x1 - x0, y1 - y0)
    let cancelled = false
    const t = setTimeout(() => {
      fetchImage(source.path, {
        ...(source.params ?? {}), max_size: maxSize,
        x0: Math.max(0, x0 - pad), x1: Math.min(info.orig_width, x1 + pad),
        y0: Math.max(0, y0 - pad), y1: Math.min(info.orig_height, y1 + pad),
      }).then(r => { if (!cancelled) setRegion(r) }).catch(() => undefined)
    }, 250)
    return () => { cancelled = true; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, info, view, size])

  const geometry = useCallback(() => {
    if (!view) return null
    const W = size.w, H = size.h
    const scale = Math.min(W / (view.x1 - view.x0), H / (view.y1 - view.y0))
    const xc = (view.x0 + view.x1) / 2, yc = (view.y0 + view.y1) / 2
    const sx = mirror ? -1 : 1, sy = flip ? -1 : 1
    return {
      W, H, scale, xc, yc, sx, sy,
      toCanvas: (x: number, y: number) => [W / 2 + (x - xc) * scale * sx, H / 2 - (y - yc) * scale * sy],
      toData: (cx: number, cy: number) => [xc + (cx - W / 2) / (scale * sx), yc - (cy - H / 2) / (scale * sy)],
    }
  }, [view, size, flip, mirror])

  // draw
  useEffect(() => {
    const canvas = canvasRef.current
    const geo = geometry()
    if (!canvas) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = size.w * dpr
    canvas.height = size.h * dpr
    const ctx = canvas.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#05070a'
    ctx.fillRect(0, 0, size.w, size.h)
    if (!geo || !info || !offscreen.current) return

    const { scale, xc, yc, sx, sy, W, H } = geo
    // row r of an offscreen image is data y = y_offset + (height - r) * factor
    const drawLayer = (layer: FitsImage, canvas: HTMLCanvasElement) => {
      const f = layer.info.factor
      const xo = layer.info.x_offset ?? 0, yo = layer.info.y_offset ?? 0
      ctx.save()
      ctx.imageSmoothingEnabled = scale * f < 2
      ctx.setTransform(dpr * scale * f * sx, 0, 0, dpr * scale * f * sy,
        dpr * (W / 2 + (xo - xc) * scale * sx), dpr * (H / 2 - (yo + layer.info.height * f - yc) * scale * sy))
      ctx.drawImage(canvas, 0, 0)
      ctx.restore()
    }
    drawLayer(image!, offscreen.current)
    if (region && regionCanvas.current) drawLayer(region, regionCanvas.current)

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    for (const o of overlays) {
      ctx.strokeStyle = o.color
      ctx.fillStyle = o.color
      ctx.lineWidth = 1.5
      if (o.type === 'circle') {
        const [cx, cy] = geo.toCanvas(o.x, o.y)
        ctx.setLineDash(o.dash ? [3, 3] : [])
        ctx.lineWidth = o.width ?? 1.5
        ctx.beginPath()
        ctx.arc(cx, cy, Math.max(o.r * scale, 1), 0, Math.PI * 2)
        ctx.stroke()
      } else if (o.type === 'rect') {
        const [ax, ay] = geo.toCanvas(o.x, o.y)
        const [bx, by] = geo.toCanvas(o.x + o.w, o.y + o.h)
        ctx.setLineDash(o.dash ? [4, 4] : [])
        ctx.strokeRect(Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay))
      } else if (o.type === 'text') {
        const [cx, cy] = geo.toCanvas(o.x, o.y)
        ctx.font = `600 ${o.size ?? 14}px ${getComputedStyle(document.body).fontFamily}`
        ctx.fillText(o.text, cx, cy)
      } else if (o.type === 'arrow') {
        const [ax, ay] = geo.toCanvas(o.x, o.y)
        const [bx, by] = geo.toCanvas(o.x + o.dx, o.y + o.dy)
        ctx.setLineDash([])
        ctx.lineWidth = 3
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
        const angle = Math.atan2(by - ay, bx - ax)
        ctx.beginPath()
        ctx.moveTo(bx, by)
        ctx.lineTo(bx - 12 * Math.cos(angle - 0.4), by - 12 * Math.sin(angle - 0.4))
        ctx.lineTo(bx - 12 * Math.cos(angle + 0.4), by - 12 * Math.sin(angle + 0.4))
        ctx.closePath(); ctx.fill()
      }
    }
    ctx.setLineDash([])

    if (axes) {
      ctx.fillStyle = 'rgba(230,237,243,0.75)'
      ctx.font = `11px ${getComputedStyle(document.body).fontFamily}`
      const [x0] = geo.toData(0, 0)
      const [x1] = geo.toData(W, 0)
      const [, y0] = geo.toData(0, H)
      const [, y1] = geo.toData(0, 0)
      ctx.fillText(`x ${Math.round(Math.min(x0, x1))}–${Math.round(Math.max(x0, x1))}   y ${Math.round(Math.min(y0, y1))}–${Math.round(Math.max(y0, y1))}`, 8, H - 8)
    }
  }, [rendered, renderedRegion, geometry, overlays, size, info, axes])

  // interaction
  const eventToData = (e: React.MouseEvent) => {
    const geo = geometry()
    const rect = canvasRef.current!.getBoundingClientRect()
    if (!geo) return null
    return geo.toData(e.clientX - rect.left, e.clientY - rect.top)
  }

  const zoomAt = (factor: number, cx?: number, cy?: number) => {
    if (!view || !info) return
    const geo = geometry()!
    // no zooming out beyond the whole image: at most, the image fills the viewer
    const fitScale = Math.min(size.w / info.orig_width, size.h / info.orig_height)
    if (factor > 1 && geo.scale / factor <= fitScale) {
      setView({ x0: 0, x1: info.orig_width, y0: 0, y1: info.orig_height })
      return
    }
    const [dx, dy] = cx === undefined ? [(view.x0 + view.x1) / 2, (view.y0 + view.y1) / 2] : geo.toData(cx, cy!)
    setView({
      x0: dx - (dx - view.x0) * factor, x1: dx + (view.x1 - dx) * factor,
      y0: dy - (dy - view.y0) * factor, y1: dy + (view.y1 - dy) * factor,
    })
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      zoomAt(e.deltaY < 0 ? 1 / 1.2 : 1.2, e.clientX - rect.left, e.clientY - rect.top)
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  })

  const onMouseDown = (e: React.MouseEvent) => {
    if (view) drag.current = { x: e.clientX, y: e.clientY, view }
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const geo = geometry()
    if (drag.current && geo) {
      const dx = (e.clientX - drag.current.x) / (geo.scale * geo.sx)
      const dy = (e.clientY - drag.current.y) / (geo.scale * geo.sy)
      const v = drag.current.view
      setView({ x0: v.x0 - dx, x1: v.x1 - dx, y0: v.y0 + dy, y1: v.y1 + dy })
      return
    }
    const p = eventToData(e)
    if (!p || !info || !image) return
    const [x, y] = p
    const exact = valueAt(region, x, y)
    const sharp = !isNaN(exact) && region!.info.factor === 1
    const value = isNaN(exact) ? valueAt(image, x, y) : exact
    const binned = sharp ? 1 : (isNaN(exact) ? info.factor : region!.info.factor)
    setHover(`x=${x.toFixed(1)}, y=${y.toFixed(1)}, counts=${isNaN(value) ? '–' : value.toFixed(1)}${binned > 1 ? ` (${binned}×${binned} binned)` : ''}`)
  }
  const onMouseUp = () => { drag.current = null }
  const onDouble = (e: React.MouseEvent) => {
    const p = eventToData(e)
    if (p && onDoubleClick) onDoubleClick(p[0], p[1])
  }

  const reset = () => {
    if (!info) return
    setView(fullView())
    setVmin(info.mean + info.low_std * info.std)
    setVmax(info.mean + info.upper_std * info.std)
    setGamma(0)
  }

  useImperativeHandle(ref, () => ({
    getFovOptions: () => {
      if (!info || !view) return null
      const xs = mirror ? [view.x1, view.x0] : [view.x0, view.x1]
      const ys = flip ? [view.y1, view.y0] : [view.y0, view.y1]
      return [(vmin - info.mean) / info.std, (vmax - info.mean) / info.std, gamma, flip ? 1 : 0, mirror ? 1 : 0,
        whiteSky ? 1 : 0, Math.round(xs[0]), Math.round(xs[1]), Math.round(ys[0]), Math.round(ys[1])]
    },
    getView: () => view,
    reset,
  }))

  const minScale = info ? scaleFromValue(info.min, info.mean, info.max) : 0
  const blackScale = info ? scaleFromValue(vmin, info.mean, info.max) : 0
  const whiteScale = info ? scaleFromValue(vmax, info.mean, info.max) : 1

  return (
    <div className="fits-viewer">
      <div ref={wrapRef} style={{ height, position: 'relative', borderRadius: 8, overflow: 'hidden', background: '#05070a', border: '1px solid var(--border)' }}>
        <canvas
          ref={canvasRef}
          style={{ width: '100%', height: '100%', display: 'block', cursor: drag.current ? 'grabbing' : 'crosshair' }}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={() => { onMouseUp(); setHover('') }}
          onDoubleClick={onDouble}
        />
        {(loading || !image) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-2)' }}>
            {loading ? <><span className="spinner" />&nbsp; Loading image…</> : 'No image'}
          </div>
        )}
        <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 4 }}>
          <button className="small" onClick={() => zoomAt(1 / 1.2)} title="Zoom in">＋</button>
          <button className="small" onClick={() => zoomAt(1.2)} title="Zoom out">－</button>
          <button className="small" onClick={() => setView(fullView())} title="Fit image">⤢</button>
        </div>
      </div>
      <div className="faint" style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 4, minHeight: 18 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{caption ?? info?.name ?? ''}</span>
        <span className="mono">{hover || 'Scroll to zoom, drag to move'}</span>
      </div>
      {controls && info && (
        <div className="viewer-controls">
          <label>Minimum<input type="range" min={minScale} max={1} step={0.0001} value={blackScale}
            onChange={e => {
              const s = Math.min(Number(e.target.value), whiteScale * 0.999)
              setVmin(valueFromScale(s, info.mean, info.max))
            }} /><span className="mono">{vmin.toFixed(0)}</span></label>
          <label>Maximum<input type="range" min={minScale} max={1} step={0.0001} value={whiteScale}
            onChange={e => setVmax(valueFromScale(Math.max(Number(e.target.value), blackScale * 1.001), info.mean, info.max))} />
            <span className="mono">{vmax.toFixed(0)}</span></label>
          <label>Stretch<input type="range" min={0} max={1} step={0.01} value={gamma}
            onChange={e => setGamma(Number(e.target.value))} /><span className="mono">{gamma.toFixed(2)}</span></label>
          <div className="row wrap" style={{ gap: 14 }}>
            <label className="check"><input type="checkbox" checked={flip} onChange={e => setFlip(e.target.checked)} />Flip</label>
            <label className="check"><input type="checkbox" checked={mirror} onChange={e => setMirror(e.target.checked)} />Mirror</label>
            <label className="check"><input type="checkbox" checked={whiteSky} onChange={e => setWhiteSky(e.target.checked)} />White sky</label>
            <button className="small" onClick={reset}>Reset</button>
          </div>
        </div>
      )}
    </div>
  )
})

export default FitsViewer
