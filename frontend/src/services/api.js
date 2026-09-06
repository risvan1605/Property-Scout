/**
 * Backend client.
 *
 * Every call returns parsed JSON or throws an Error whose message is safe to
 * show the user — the backend writes its `detail` strings for exactly that.
 */

const BASE_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new Error("Can't reach the property service. Is the backend running?")
  }

  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(
      typeof detail === 'string' ? detail : `Request failed (${response.status})`,
    )
  }
  return payload
}

export function sendMessage(sessionId, text) {
  return request('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, text }),
  })
}

export function getSession(sessionId) {
  return request(`/api/chat/${sessionId}`)
}

export function getListings() {
  return request('/api/listings')
}

export function getNearby(listingId) {
  return request(`/api/listings/${listingId}/nearby`)
}

export function bookVisit(sessionId, listingId, date, timeSlot, email) {
  return request('/api/booking', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      listing_id: listingId,
      preferred_date: date,
      preferred_time_slot: timeSlot,
      user_email: email,
    }),
  })
}

export function sendShortlistPDF(sessionId, shortlistIds, email) {
  return request('/api/shortlist/pdf', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      shortlist_ids: shortlistIds,
      user_email: email,
    }),
  })
}

export function checkHealth() {
  return request('/api/health')
}

/**
 * Fetch spoken audio for a reply.
 *
 * Returns an object URL, or null when high-quality speech isn't available —
 * the caller then falls back to the browser's own voice. A 503 here is an
 * expected outcome (no key, quota spent), not a failure worth surfacing.
 */
export async function synthesizeSpeech(text) {
  try {
    const response = await fetch(`${BASE_URL}/api/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!response.ok) return null
    const blob = await response.blob()
    return URL.createObjectURL(blob)
  } catch {
    return null
  }
}
