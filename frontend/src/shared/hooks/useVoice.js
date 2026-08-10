import { useCallback, useRef, useState } from 'react'
import { API_BASE_URL } from '../constants/app'

const WS_BASE = API_BASE_URL.replace(/^http/, 'ws')
const SAMPLE_RATE = 24000

function encodePCM(float32) {
  const int16 = new Int16Array(float32.length)
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]))
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
  }
  return int16.buffer
}

function playBase64Audio(b64) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 })
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    ctx.decodeAudioData(bytes.buffer, (buffer) => {
      const source = ctx.createBufferSource()
      source.buffer = buffer
      source.connect(ctx.destination)
      source.start(0)
    }, () => {})
  } catch {}
}

export function useVoice(authToken) {
  const [voiceState, setVoiceState] = useState('idle')
  const [interimText, setInterimText] = useState('')
  const audioContextRef = useRef(null)
  const analyserRef = useRef(null)
  const streamRef = useRef(null)
  const processorRef = useRef(null)
  const wsRef = useRef(null)
  const partialRef = useRef('')
  const callbacksRef = useRef({})

  const startListening = useCallback(async ({ onInterim, onTranscript, onDone, onError, onAudio } = {}) => {
    callbacksRef.current = { onInterim, onTranscript, onDone, onError, onAudio }
    setInterimText('')
    partialRef.current = ''

    try {
      setVoiceState('connecting')

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
      })
      streamRef.current = stream

      const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE })
      audioContextRef.current = ctx

      const analyser = ctx.createAnalyser()
      analyser.fftSize = 128
      analyserRef.current = analyser

      const source = ctx.createMediaStreamSource(stream)
      source.connect(analyser)

      const processor = ctx.createScriptProcessor(4096, 1, 1)
      processorRef.current = processor
      source.connect(processor)
      processor.connect(ctx.destination)

      const ws = new WebSocket(`${WS_BASE}/ws/voice?token=${encodeURIComponent(authToken || '')}`)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      ws.onopen = () => {
        processor.onaudioprocess = (e) => {
          if (wsRef.current?.readyState !== WebSocket.OPEN) return
          wsRef.current.send(encodePCM(e.inputBuffer.getChannelData(0)))
        }
        setVoiceState('listening')
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          switch (msg.type) {
            case 'interim':
              setInterimText(msg.text)
              callbacksRef.current.onInterim?.(msg.text)
              break
            case 'transcript':
              setInterimText('')
              partialRef.current = msg.text
              callbacksRef.current.onTranscript?.(msg.text)
              break
            case 'answer_text':
              callbacksRef.current.onAnswerToken?.(msg.text)
              break
            case 'audio':
              playBase64Audio(msg.data)
              callbacksRef.current.onAudio?.(msg.data)
              break
            case 'done':
              setVoiceState('idle')
              callbacksRef.current.onDone?.()
              try { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.close() } catch {}
              wsRef.current = null
              break
            case 'error':
              setVoiceState('idle')
              callbacksRef.current.onError?.(msg.message)
              try { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.close() } catch {}
              wsRef.current = null
              break
          }
        } catch {}
      }

      ws.onclose = () => setVoiceState((prev) => (prev !== 'processing' ? 'idle' : prev))
      ws.onerror = () => {
        setVoiceState('idle')
        callbacksRef.current.onError?.('Erro na ligacao de voz')
      }
    } catch (err) {
      setVoiceState('idle')
      if (err.name === 'NotAllowedError') {
        callbacksRef.current.onError?.('Permissao do microfone negada')
      } else {
        callbacksRef.current.onError?.(err.message || 'Erro ao iniciar microfone')
      }
    }
  }, [authToken])

  const stopListening = useCallback(() => {
    try {
      if (processorRef.current) { processorRef.current.disconnect(); processorRef.current = null }
      if (streamRef.current) { streamRef.current.getTracks().forEach((t) => t.stop()); streamRef.current = null }
      if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null }
    } catch {}
    analyserRef.current = null
    setVoiceState('processing')
    try {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'done' }))
      }
    } catch {}
    // Keep WS alive — backend will send done (with audio) when ready
  }, [])

  const cancelListening = useCallback(() => {
    stopListening()
    try { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.close() } catch {}
    wsRef.current = null
    setVoiceState('idle')
    setInterimText('')
    partialRef.current = ''
  }, [stopListening])

  const getTranscript = useCallback(() => partialRef.current, [])

  return { voiceState, interimText, analyserNode: analyserRef, startListening, stopListening, cancelListening, getTranscript }
}
