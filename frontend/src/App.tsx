import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, LoaderCircle, RotateCcw } from 'lucide-react'
import { AdvisoryJourney } from './components/AdvisoryJourney'
import { api } from './api'

type ServiceState = 'waking' | 'ready' | 'unavailable'

const retryDelays = [0, 2_000, 4_000, 7_000, 10_000, 15_000, 20_000]

function wait(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds)
    signal.addEventListener('abort', () => {
      window.clearTimeout(timeout)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

export default function App() {
  const [serviceState, setServiceState] = useState<ServiceState>('waking')
  const [attempt, setAttempt] = useState(0)

  const wakeService = useCallback(async (signal: AbortSignal) => {
    setServiceState('waking')
    for (let index = 0; index < retryDelays.length; index += 1) {
      setAttempt(index + 1)
      try {
        await wait(retryDelays[index], signal)
        const timeout = new AbortController()
        const abortTimeout = window.setTimeout(() => timeout.abort(), 12_000)
        const stop = () => timeout.abort()
        signal.addEventListener('abort', stop, { once: true })
        try {
          const health = await api.health(timeout.signal)
          if (health.status === 'ok') {
            setServiceState('ready')
            return
          }
        } finally {
          window.clearTimeout(abortTimeout)
          signal.removeEventListener('abort', stop)
        }
      } catch (reason) {
        if (signal.aborted) return
      }
    }
    if (!signal.aborted) setServiceState('unavailable')
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void wakeService(controller.signal)
    return () => controller.abort()
  }, [wakeService])

  if (serviceState === 'ready') return <AdvisoryJourney />

  return (
    <main className="service-gate" aria-live="polite">
      <section className="service-gate-card">
        <span className="service-mark">m</span>
        {serviceState === 'waking' ? (
          <>
            <LoaderCircle className="service-spinner" aria-hidden="true" />
            <h1>Waking secure demo service…</h1>
            <p>The free demo may need up to a minute to start after being idle.</p>
            <small>Connection attempt {attempt} of {retryDelays.length}</small>
          </>
        ) : (
          <>
            <AlertTriangle className="service-warning" aria-hidden="true" />
            <h1>The demo service did not respond</h1>
            <p>Please retry. Your browser data has not been cleared.</p>
            <button type="button" onClick={() => {
              const controller = new AbortController()
              void wakeService(controller.signal)
            }}><RotateCcw size={17} /> Retry connection</button>
          </>
        )}
      </section>
    </main>
  )
}
