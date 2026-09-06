/**
 * Global conversation state.
 *
 * The backend owns the truth (shortlist, sources, booking, stage); this store
 * mirrors the latest response and layers on UI-only concerns: what's selected,
 * whether the mic is live, and any error worth showing.
 */

import { useMemo, useReducer } from 'react'
import { AppContext } from './store'

const initialState = {
  sessionId: null,
  stage: 'greeting',
  preferences: {},
  shortlist: [],
  sources: [],
  booking: null,
  messages: [],
  interim: '',
  isListening: false,
  isProcessing: false,
  isSpeaking: false,
  voiceEnabled: true,
  sessionChecked: false,
  selectedListingId: null,
  nearbyById: {},
  nearbyLoadingId: null,
  error: null,
}

function reducer(state, action) {
  switch (action.type) {
    case 'ADD_MESSAGE':
      return {
        ...state,
        messages: [
          ...state.messages,
          { ...action.message, at: action.message.at ?? Date.now() },
        ],
      }

    case 'SET_INTERIM':
      return { ...state, interim: action.interim }

    case 'SET_LISTENING':
      return { ...state, isListening: action.isListening, interim: action.isListening ? state.interim : '' }

    case 'SET_PROCESSING':
      return { ...state, isProcessing: action.isProcessing }

    case 'SET_SPEAKING':
      return { ...state, isSpeaking: action.isSpeaking }

    case 'TOGGLE_VOICE':
      return { ...state, voiceEnabled: !state.voiceEnabled }

    case 'SESSION_CHECKED':
      return { ...state, sessionChecked: true }

    case 'RESTORE_SESSION': {
      const { payload } = action
      return {
        ...state,
        sessionId: payload.session_id,
        stage: payload.state ?? state.stage,
        preferences: payload.preferences ?? {},
        shortlist: payload.shortlist ?? [],
        sources: payload.sources ?? [],
        booking: payload.booking ?? null,
        messages: (payload.history ?? []).map((m) => ({ ...m, at: null })),
        sessionChecked: true,
      }
    }

    case 'RECEIVE_TURN': {
      const { payload } = action
      // Keep a selection only while that listing is still on screen.
      const stillListed = payload.shortlist?.some((l) => l.id === state.selectedListingId)
      return {
        ...state,
        sessionId: payload.session_id ?? state.sessionId,
        stage: payload.state ?? state.stage,
        preferences: payload.preferences ?? state.preferences,
        shortlist: payload.shortlist ?? [],
        sources: payload.sources ?? [],
        booking: payload.booking ?? state.booking,
        selectedListingId: stillListed ? state.selectedListingId : null,
        isProcessing: false,
        error: null,
      }
    }

    case 'SELECT_LISTING':
      return {
        ...state,
        selectedListingId: state.selectedListingId === action.id ? null : action.id,
      }

    case 'SET_NEARBY_LOADING':
      return { ...state, nearbyLoadingId: action.id }

    case 'SET_NEARBY':
      return {
        ...state,
        nearbyById: { ...state.nearbyById, [action.id]: action.nearby },
        nearbyLoadingId: state.nearbyLoadingId === action.id ? null : state.nearbyLoadingId,
      }

    case 'SET_ERROR':
      return { ...state, error: action.error, isProcessing: false }

    case 'CLEAR_ERROR':
      return { ...state, error: null }

    default:
      return state
  }
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const value = useMemo(() => ({ state, dispatch }), [state])
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}
