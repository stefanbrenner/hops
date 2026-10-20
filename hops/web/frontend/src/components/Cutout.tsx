import { useEffect, useRef } from 'react'

export interface CutoutData {
  data: number[][]
  x0: number
  y0: number
  x: number
  y: number
  r: number
  vmin: number
  vmax: number
}

/** Small star stamp with its aperture, drawn from a 2D array (row 0 = bottom). */
export default function Cutout({ cutout, size = 72, span }: { cutout: CutoutData | null; size?: number; span: number }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    canvas.width = size
    canvas.height = size
    ctx.fillStyle = '#05070a'
    ctx.fillRect(0, 0, size, size)
    if (!cutout || !cutout.data.length) return
    const h = cutout.data.length, w = cutout.data[0].length
    const img = ctx.createImageData(w, h)
    const range = cutout.vmax - cutout.vmin || 1
    for (let row = 0; row < h; row++) {
      for (let col = 0; col < w; col++) {
        const t = Math.min(1, Math.max(0, (cutout.data[row][col] - cutout.vmin) / range))
        const o = ((h - 1 - row) * w + col) * 4
        img.data[o] = img.data[o + 1] = img.data[o + 2] = Math.round(255 * t)
        img.data[o + 3] = 255
      }
    }
    const tmp = document.createElement('canvas')
    tmp.width = w
    tmp.height = h
    tmp.getContext('2d')!.putImageData(img, 0, 0)
    // show +/- span pixels around the star centre
    const scale = size / (2 * span)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(tmp, (cutout.x0 - (cutout.x - span)) * scale, size - (cutout.y0 + h - (cutout.y - span)) * scale, w * scale, h * scale)
    ctx.strokeStyle = '#ff5a5a'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.arc(size / 2, size / 2, cutout.r * scale, 0, Math.PI * 2)
    ctx.stroke()
  }, [cutout, size, span])

  return <canvas ref={ref} style={{ width: size, height: size, borderRadius: 6, border: '1px solid var(--border)' }} />
}
