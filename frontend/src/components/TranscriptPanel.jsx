import { useEffect, useRef } from 'react'
import { useApp } from '../context/useApp'
import './TranscriptPanel.css'

function clockTime(at) {
  // Restored messages carry no timestamp — the server doesn't record one.
  if (!at) return '·'
  return new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export default function TranscriptPanel() {
  const { state } = useApp()
  const { messages, interim, isListening, isProcessing } = state
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, interim, isProcessing])

  return (
    <section className="transcript" aria-label="Conversation">
      <header className="transcript__head">
        <span className="eyebrow">Transcript</span>
      </header>

      <ol className="transcript__log">
        {messages.length === 0 && !interim && (
          <li className="transcript__idle">
            Press the mic and say what you're looking for — rentals in Koramangala,
            Indiranagar or HSR Layout. Everything said here stays in this session.
          </li>
        )}

        {messages.map((message, index) => (
          <li
            key={`${message.at}-${index}`}
            className={`turn turn--${message.role}`}
          >
            <span className="turn__time num">{clockTime(message.at)}</span>
            <span className="turn__who eyebrow">
              {message.role === 'user' ? 'You' : 'Scout'}
            </span>
            <p className="turn__text">{message.text}</p>
          </li>
        ))}

        {interim && (
          <li className="turn turn--interim">
            <span className="turn__time turn__time--live" aria-hidden="true">•</span>
            <span className="turn__who eyebrow">You</span>
            <p className="turn__text">{interim}</p>
          </li>
        )}

        {isProcessing && (
          <li className="transcript__thinking">
            <i /><i /><i />
            <span className="eyebrow">Scout is checking</span>
          </li>
        )}

        {isListening && !interim && (
          <li className="transcript__thinking transcript__thinking--live">
            <span className="eyebrow">Listening…</span>
          </li>
        )}

        <div ref={endRef} />
      </ol>
    </section>
  )
}
