/**
 * Web Speech API helpers — speech in, speech out.
 *
 * Recognition is Chrome/Edge only, so `isRecognitionSupported()` gates the UI
 * and the typed input takes over where it isn't available.
 */

const SpeechRecognition =
  typeof window !== 'undefined' &&
  (window.SpeechRecognition || window.webkitSpeechRecognition)

export function isRecognitionSupported() {
  return Boolean(SpeechRecognition)
}

export function isSynthesisSupported() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Start listening. Returns a handle with stop(); null if unsupported.
 *
 * Interim results stream to `onInterim` for the live transcript; `onFinal`
 * fires once the speaker pauses, with the settled text.
 */
export function startRecognition({ onInterim, onFinal, onEnd, onError }) {
  if (!SpeechRecognition) return null

  const recognition = new SpeechRecognition()
  recognition.lang = 'en-IN'
  recognition.continuous = false
  recognition.interimResults = true
  recognition.maxAlternatives = 1

  recognition.onresult = (event) => {
    let interim = ''
    let final = ''
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i]
      if (result.isFinal) final += result[0].transcript
      else interim += result[0].transcript
    }
    if (interim) onInterim?.(interim)
    if (final.trim()) onFinal?.(final.trim())
  }

  recognition.onerror = (event) => {
    // "aborted" is what a deliberate stop() looks like — not worth reporting.
    if (event.error !== 'aborted') onError?.(describeError(event.error))
  }
  recognition.onend = () => onEnd?.()

  recognition.start()
  return {
    stop: () => {
      try {
        recognition.stop()
      } catch {
        /* already stopped */
      }
    },
    abort: () => {
      try {
        recognition.abort()
      } catch {
        /* already stopped */
      }
    },
  }
}

function describeError(code) {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access is blocked. Allow it in your browser settings, or type instead.'
    case 'no-speech':
      return "I didn't catch that. Try again, or type your message."
    case 'audio-capture':
      return 'No microphone found. Type your message instead.'
    case 'network':
      return 'Speech recognition needs a network connection.'
    default:
      return 'Speech recognition stopped unexpectedly. You can type instead.'
  }
}

/** Prefer an Indian English voice; fall back to whatever the browser has. */
function pickVoice() {
  const voices = window.speechSynthesis.getVoices()
  return (
    voices.find((v) => v.lang === 'en-IN') ||
    voices.find((v) => v.lang?.startsWith('en-IN')) ||
    voices.find((v) => v.lang?.startsWith('en-GB')) ||
    voices.find((v) => v.lang?.startsWith('en')) ||
    null
  )
}

/** Returns false when the browser voice can't be used at all. */
export function speak(text, { onStart, onEnd } = {}) {
  if (!isSynthesisSupported() || !text) return false
  window.speechSynthesis.cancel()

  const utterance = new SpeechSynthesisUtterance(text)
  const voice = pickVoice()
  if (voice) utterance.voice = voice
  utterance.lang = voice?.lang || 'en-IN'
  utterance.rate = 1.02
  utterance.pitch = 1
  utterance.onstart = () => onStart?.()
  utterance.onend = () => onEnd?.()
  utterance.onerror = () => onEnd?.()

  window.speechSynthesis.speak(utterance)
  return true
}

let currentAudio = null

/**
 * Play synthesized audio from the backend.
 *
 * Resolves false if playback can't start (autoplay blocked, decode failure),
 * so the caller can fall back to the browser voice.
 */
export function playAudio(url, { onStart, onEnd } = {}) {
  return new Promise((resolve) => {
    stopSpeaking()
    const audio = new Audio(url)
    currentAudio = audio

    const finish = () => {
      if (currentAudio === audio) currentAudio = null
      URL.revokeObjectURL(url)
      onEnd?.()
    }

    audio.onended = finish
    audio.onerror = () => {
      finish()
      resolve(false)
    }
    audio
      .play()
      .then(() => {
        onStart?.()
        resolve(true)
      })
      .catch(() => {
        finish()
        resolve(false)
      })
  })
}

export function stopSpeaking() {
  if (currentAudio) {
    currentAudio.pause()
    currentAudio = null
  }
  if (isSynthesisSupported()) window.speechSynthesis.cancel()
}

/** Voice lists load asynchronously in Chrome; nudge them early. */
export function primeVoices() {
  if (!isSynthesisSupported()) return
  window.speechSynthesis.getVoices()
}
