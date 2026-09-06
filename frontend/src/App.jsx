import { useEffect, useRef, useState } from 'react'
import BookingPanel from './components/BookingPanel'
import NeighborhoodPanel from './components/NeighborhoodPanel'
import ShortlistPanel from './components/ShortlistPanel'
import SourcesPanel from './components/SourcesPanel'
import TranscriptPanel from './components/TranscriptPanel'
import VoiceInput from './components/VoiceInput'
import { AppProvider } from './context/AppContext'
import { useApp } from './context/useApp'
import { checkHealth, getSession } from './services/api'
import './App.css'

const STAGES = [
  { key: 'greeting', label: 'Listening' },
  { key: 'collecting', label: 'Collecting' },
  { key: 'shortlist_ready', label: 'Shortlist' },
  { key: 'booking', label: 'Booking' },
  { key: 'visit_booked', label: 'Booked' },
]

function Masthead() {
  const { state } = useApp()
  const [service, setService] = useState({ status: 'checking' })

  useEffect(() => {
    checkHealth()
      .then((payload) => setService({ status: 'ok', ...payload }))
      .catch(() => setService({ status: 'down' }))
  }, [])

  const stageIndex = Math.max(
    0,
    STAGES.findIndex((s) => s.key === (state.stage === 'confirming' ? 'collecting' : state.stage)),
  )

  return (
    <header className="masthead">
      <div className="masthead__mark">
        <span className="masthead__pin" aria-hidden="true" />
        <div>
          <h1 className="masthead__name">Property Scout</h1>
          <p className="masthead__where">
            Rentals in Koramangala, Indiranagar &amp; HSR Layout
          </p>
        </div>
      </div>

      <ol className="progress" aria-label="Conversation stage">
        {STAGES.map((stage, index) => (
          <li
            key={stage.key}
            className={`progress__step${index <= stageIndex ? ' progress__step--done' : ''}${
              index === stageIndex ? ' progress__step--now' : ''
            }`}
          >
            <span className="progress__dot" aria-hidden="true" />
            <span className="progress__label">{stage.label}</span>
          </li>
        ))}
      </ol>

      <p className={`service service--${service.status}`}>
        <span className="service__dot" aria-hidden="true" />
        <span className="eyebrow">
          {service.status === 'ok'
            ? `${service.listings} listings · ${service.chroma ? 'guides loaded' : 'guides missing'}`
            : service.status === 'down'
              ? 'Backend unreachable'
              : 'Connecting…'}
        </span>
      </p>
    </header>
  )
}

const SESSION_KEY = 'property-scout.session'

function Workspace() {
  const { state, dispatch } = useApp()
  const showContext = Boolean(state.selectedListingId) || state.sources.length > 0 || state.booking
  const railRef = useRef(null)
  const confirmationCode = state.booking?.confirmation_code

  // A new booking scrolls the rail up so the confirmation is the first thing seen.
  useEffect(() => {
    if (confirmationCode) railRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [confirmationCode])

  // Survive a refresh: the backend still holds the session, so restore the
  // conversation and shortlist rather than dropping the user back to empty.
  useEffect(() => {
    const saved = sessionStorage.getItem(SESSION_KEY)
    if (!saved) {
      dispatch({ type: 'SESSION_CHECKED' })
      return
    }
    getSession(saved)
      .then((payload) => dispatch({ type: 'RESTORE_SESSION', payload }))
      .catch(() => {
        sessionStorage.removeItem(SESSION_KEY) // session expired with the server
        dispatch({ type: 'SESSION_CHECKED' })
      })
  }, [dispatch])

  useEffect(() => {
    if (state.sessionId) sessionStorage.setItem(SESSION_KEY, state.sessionId)
  }, [state.sessionId])

  return (
    <div className="app">
      <Masthead />
      <main className="workspace">
        <aside className="rail rail--left">
          <TranscriptPanel />
        </aside>

        <section className="stage">
          <ShortlistPanel />
        </section>

        <aside ref={railRef} className={`rail rail--right${showContext ? '' : ' rail--quiet'}`}>
          <BookingPanel />
          <NeighborhoodPanel />
          <SourcesPanel />
        </aside>
      </main>
      <VoiceInput />
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <Workspace />
    </AppProvider>
  )
}
