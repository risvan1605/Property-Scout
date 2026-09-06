import { useEffect } from 'react'
import { useApp } from '../context/useApp'
import { getNearby } from '../services/api'
import ListingCard from './ListingCard'
import './ShortlistPanel.css'

export default function ShortlistPanel() {
  const { state, dispatch } = useApp()
  const { shortlist, selectedListingId, nearbyById, nearbyLoadingId, isProcessing } = state

  // Opening a card is what asks OpenStreetMap what's around it.
  useEffect(() => {
    if (!selectedListingId) return
    const listing = shortlist.find((l) => l.id === selectedListingId)
    if (!listing || nearbyById[selectedListingId]) return
    const alreadyEnriched = ['metro_stations', 'groceries', 'hospitals'].some(
      (k) => listing.nearby_pois?.[k]?.length,
    )
    if (alreadyEnriched) return

    let cancelled = false
    dispatch({ type: 'SET_NEARBY_LOADING', id: selectedListingId })
    getNearby(selectedListingId)
      .then((nearby) => {
        if (!cancelled) dispatch({ type: 'SET_NEARBY', id: selectedListingId, nearby })
      })
      .catch(() => {
        if (!cancelled) {
          dispatch({
            type: 'SET_NEARBY',
            id: selectedListingId,
            nearby: { error: 'unavailable' },
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedListingId, shortlist, nearbyById, dispatch])

  const rents = shortlist.map((l) => l.rent)

  return (
    <section className="shortlist" aria-label="Shortlist">
      <header className="shortlist__head">
        <h2 className="shortlist__title">Shortlist</h2>
        {shortlist.length > 0 && (
          <p className="shortlist__count num">
            {shortlist.length} {shortlist.length === 1 ? 'match' : 'matches'}
            <span>
              ₹{Math.min(...rents).toLocaleString('en-IN')}–
              {Math.max(...rents).toLocaleString('en-IN')}
            </span>
          </p>
        )}
      </header>

      {shortlist.length === 0 ? (
        <div className="shortlist__empty">
          <p className="shortlist__empty-line">Nothing shortlisted yet.</p>
          <p className="shortlist__empty-hint">
            Say what you're looking for — a monthly budget and a bedroom count is enough
            to start. Try <em>“a two BHK in Koramangala under thirty-five thousand.”</em>
          </p>
          <p className="shortlist__empty-note">
            Rentals only, in Koramangala, Indiranagar and HSR Layout.
          </p>
          {isProcessing && <p className="shortlist__empty-hint">Searching…</p>}
        </div>
      ) : (
        <div className="shortlist__grid">
          {shortlist.map((listing, index) => (
            <ListingCard
              key={listing.id}
              listing={listing}
              index={index}
              selected={selectedListingId === listing.id}
              nearby={nearbyById[listing.id]}
              nearbyLoading={nearbyLoadingId === listing.id}
              onSelect={(id) => dispatch({ type: 'SELECT_LISTING', id })}
            />
          ))}
        </div>
      )}
    </section>
  )
}
