import { useCallback, useEffect, useRef, useState } from 'react'
import { useApp } from '../context/useApp'
import { sendMessage, synthesizeSpeech } from '../services/api'
import {
  isRecognitionSupported,
  isSynthesisSupported,
  playAudio,
  primeVoices,
  speak,
  startRecognition,
  stopSpeaking,
} from '../utils/speechUtils'
import './VoiceInput.css'

// Spoken once when the page opens. Fixed text, so the backend's TTS cache
// serves it after the first synthesis instead of spending quota every load.
const GREETING =
  "Hi, I'm your property scout. Tell me your budget and how many bedrooms you " +
  "need, and I'll pull up rentals in Koramangala, Indiranagar or HSR Layout."

export default function VoiceInput() {
  const { state, dispatch } = useApp()
  const {
    isListening, isProcessing, isSpeaking, voiceEnabled, sessionId, error,
    sessionChecked, messages,
  } = state

  const [typing, setTyping] = useState(!isRecognitionSupported())
  const [draft, setDraft] = useState('')
  const recognitionRef = useRef(null)
  // A turn in flight must not be interrupted by a second one.
  const busyRef = useRef(false)
  const greetedRef = useRef(false)

  useEffect(() => {
    primeVoices()
    return () => {
      recognitionRef.current?.abort()
      stopSpeaking()
    }
  }, [])

  /**
   * Speak a reply with the good voice, falling back to the browser's own.
   *
   * The backend returns 503 when no key is set or the quota is spent, which is
   * an expected outcome — the user still hears the reply, just synthetically.
   */
  const speakReply = useCallback(
    async (text) => {
      const marks = {
        onStart: () => dispatch({ type: 'SET_SPEAKING', isSpeaking: true }),
        onEnd: () => dispatch({ type: 'SET_SPEAKING', isSpeaking: false }),
      }

      const url = await synthesizeSpeech(text)
      if (url) {
        const outcome = await playAudio(url, marks)
        if (outcome === true) return
        if (outcome === 'blocked') {
          // The browser won't play audio until the visitor interacts. The
          // ElevenLabs audio is fine and still alive, so wait for the first
          // gesture and play THAT, rather than discarding it for the robotic
          // voice — which is what made every greeting sound synthetic.
          const replay = () => {
            window.removeEventListener('pointerdown', replay)
            window.removeEventListener('keydown', replay)
            playAudio(url, marks)
          }
          window.addEventListener('pointerdown', replay, { once: true })
          window.addEventListener('keydown', replay, { once: true })
          return 'blocked'
        }
        console.warn('[tts] audio fetched but could not be played; using the browser voice')
      }

      if (isSynthesisSupported() && speak(text, marks) !== false) return
      return 'blocked'
    },
    [dispatch],
  )

  // Greet on arrival. Browsers refuse audio before the visitor has interacted
  // with the page, so the greeting is always written to the transcript and the
  // audio is retried on their first click or keypress if it was blocked.
  useEffect(() => {
    if (!sessionChecked || greetedRef.current || messages.length > 0) return
    greetedRef.current = true

    dispatch({ type: 'ADD_MESSAGE', message: { role: 'assistant', text: GREETING } })
    if (!voiceEnabled) return

    // speakReply now owns the blocked-audio retry and replays the audio it
    // already has; re-calling it here would buy a second copy from ElevenLabs.
    speakReply(GREETING)
  }, [sessionChecked, messages.length, voiceEnabled, dispatch, speakReply])

  const submit = useCallback(
    async (text) => {
      const trimmed = text.trim()
      if (!trimmed || busyRef.current) return

      busyRef.current = true
      dispatch({ type: 'ADD_MESSAGE', message: { role: 'user', text: trimmed } })
      dispatch({ type: 'SET_INTERIM', interim: '' })
      dispatch({ type: 'SET_PROCESSING', isProcessing: true })

      try {
        const payload = await sendMessage(sessionId, trimmed)
        dispatch({ type: 'RECEIVE_TURN', payload })
        dispatch({
          type: 'ADD_MESSAGE',
          message: { role: 'assistant', text: payload.response_text },
        })
        if (voiceEnabled) await speakReply(payload.response_text)
      } catch (err) {
        dispatch({ type: 'SET_ERROR', error: err.message })
      } finally {
        busyRef.current = false
      }
    },
    [dispatch, sessionId, speakReply, voiceEnabled],
  )

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
    recognitionRef.current = null
    dispatch({ type: 'SET_LISTENING', isListening: false })
  }, [dispatch])

  const startListening = useCallback(() => {
    if (busyRef.current) return
    stopSpeaking()
    dispatch({ type: 'SET_SPEAKING', isSpeaking: false })
    dispatch({ type: 'CLEAR_ERROR' })

    const handle = startRecognition({
      onInterim: (interim) => dispatch({ type: 'SET_INTERIM', interim }),
      onFinal: (text) => {
        stopListening()
        submit(text)
      },
      onEnd: () => dispatch({ type: 'SET_LISTENING', isListening: false }),
      onError: (message) => {
        dispatch({ type: 'SET_ERROR', error: message })
        setTyping(true)
      },
    })

    if (!handle) {
      setTyping(true)
      return
    }
    recognitionRef.current = handle
    dispatch({ type: 'SET_LISTENING', isListening: true })
  }, [dispatch, stopListening, submit])

  const micState = isProcessing ? 'thinking' : isListening ? 'listening' : 'idle'
  const micLabel = isListening ? 'Stop listening' : 'Start listening'

  return (
    <div className="voice">
      {error && (
        <p className="voice__error" role="status">
          {error}
          <button type="button" onClick={() => dispatch({ type: 'CLEAR_ERROR' })}>
            Dismiss
          </button>
        </p>
      )}

      <div className="voice__bar">
        {typing ? (
          <form
            className="voice__form"
            onSubmit={(event) => {
              event.preventDefault()
              submit(draft)
              setDraft('')
            }}
          >
            <input
              className="voice__input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Type what you're looking for…"
              aria-label="Message the scout"
              disabled={isProcessing}
              autoFocus
            />
            <button type="submit" className="voice__send" disabled={isProcessing || !draft.trim()}>
              Send
            </button>
            {isRecognitionSupported() && (
              <button
                type="button"
                className="voice__switch"
                onClick={() => setTyping(false)}
              >
                Use voice
              </button>
            )}
          </form>
        ) : (
          <>
            <button
              type="button"
              className={`mic mic--${micState}`}
              onClick={isListening ? stopListening : startListening}
              disabled={isProcessing}
              aria-label={micLabel}
              aria-pressed={isListening}
            >
              <span className="mic__ring mic__ring--1" aria-hidden="true" />
              <span className="mic__ring mic__ring--2" aria-hidden="true" />
              <span className="mic__face">
                <svg viewBox="0 0 24 24" aria-hidden="true" className="mic__glyph">
                  <path
                    d="M12 3.5a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0v-6a3 3 0 0 1 3-3Z"
                    fill="currentColor"
                  />
                  <path
                    d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                  />
                </svg>
              </span>
            </button>

            <p className="voice__hint">
              {isProcessing
                ? 'Checking listings, guides and the map…'
                : isListening
                  ? 'Listening — speak now'
                  : 'Press to speak'}
            </p>

            <button type="button" className="voice__switch" onClick={() => setTyping(true)}>
              Type instead
            </button>
          </>
        )}

        {isSynthesisSupported() && (
          <button
            type="button"
            className={`voice__mute${voiceEnabled ? '' : ' voice__mute--off'}`}
            onClick={() => {
              stopSpeaking()
              dispatch({ type: 'SET_SPEAKING', isSpeaking: false })
              dispatch({ type: 'TOGGLE_VOICE' })
            }}
            aria-pressed={voiceEnabled}
          >
            {voiceEnabled ? (isSpeaking ? 'Stop speaking' : 'Mute replies') : 'Unmute replies'}
          </button>
        )}
      </div>
    </div>
  )
}
