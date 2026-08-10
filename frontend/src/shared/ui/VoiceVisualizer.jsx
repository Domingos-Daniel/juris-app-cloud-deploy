import { useEffect, useRef } from 'react'

export function VoiceVisualizer({ isActive, analyserNode }) {
  const canvasRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    if (!isActive || !analyserNode) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const dataArray = new Uint8Array(analyserNode.frequencyBinCount)

    const style = getComputedStyle(document.documentElement)
    const gold = style.getPropertyValue('--gold').trim() || '#c9952e'
    const accent = style.getPropertyValue('--accent').trim() || '#2563eb'

    const draw = () => {
      rafRef.current = requestAnimationFrame(draw)
      analyserNode.getByteFrequencyData(dataArray)

      const w = canvas.width
      const h = canvas.height
      ctx.clearRect(0, 0, w, h)

      const barCount = 28
      const barW = Math.max(2, (w / barCount) * 0.6)
      const gap = (w / barCount) * 0.4
      const step = Math.max(1, Math.floor(dataArray.length / barCount))

      for (let i = 0; i < barCount; i++) {
        let sum = 0
        for (let j = 0; j < step; j++) sum += dataArray[i * step + j] || 0
        const avg = sum / (step * 255)
        const barH = Math.max(3, avg * h * 0.85)
        const x = i * (barW + gap)
        const y = (h - barH) / 2

        const grad = ctx.createLinearGradient(x, y + barH, x, y)
        grad.addColorStop(0, gold)
        grad.addColorStop(1, accent)
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.roundRect(x, y, barW, barH, barW / 2)
        ctx.fill()
      }
    }

    draw()
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [isActive, analyserNode])

  if (!isActive) return null

  return (
    <div className="flex items-center justify-center px-3 pb-2 animate-[fadeIn_0.2s_ease-out]">
      <canvas
        ref={canvasRef}
        width={300}
        height={40}
        className="h-10 w-full max-w-[300px] rounded-[var(--radius-sm)]"
      />
    </div>
  )
}
