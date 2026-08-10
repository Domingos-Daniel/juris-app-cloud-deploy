import { useState, useRef, useEffect } from 'react'
import { Info } from 'lucide-react'

export function InfoTooltip({ content, position = 'auto' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const [above, setAbove] = useState(position === 'above')

  const handleToggle = () => {
    if (!open && ref.current && position === 'auto') {
      const rect = ref.current.getBoundingClientRect()
      setAbove(rect.top > window.innerHeight * 0.5)
    }
    setOpen(!open)
  }

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [open])

  return (
    <span ref={ref} className="relative inline-flex">
      <button
        type="button"
        onClick={handleToggle}
        className="grid h-6 w-6 place-items-center rounded-full text-[color:var(--ink-soft)]/50 transition-all hover:text-[color:var(--accent)] hover:bg-[color:var(--accent-soft)] sm:h-5 sm:w-5"
        aria-label="Informacao"
      >
        <Info size={14} className="sm:size-3" />
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-[9998]" onClick={() => setOpen(false)} />
          <div className={`absolute left-1/2 z-[9999] w-[calc(100vw-2rem)] max-w-[280px] -translate-x-1/2 rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-3.5 text-xs leading-relaxed text-[color:var(--ink-soft)] shadow-[var(--shadow-3)] animate-[fadeIn_0.15s_ease-out] sm:left-6 sm:top-0 sm:w-72 sm:max-w-[320px] sm:translate-x-0 ${above ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}`}>
            {content}
          </div>
        </>
      ) : null}
    </span>
  )
}
