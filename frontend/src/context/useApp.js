import { useContext } from 'react'
import { AppContext } from './store'

/** Read and update the conversation store. */
export function useApp() {
  const context = useContext(AppContext)
  if (!context) throw new Error('useApp must be used inside <AppProvider>')
  return context
}
