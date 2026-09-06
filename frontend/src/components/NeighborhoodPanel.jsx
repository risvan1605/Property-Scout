import { useApp } from '../context/useApp'
import './NeighborhoodPanel.css'

const SNAPSHOT_ROWS = [
  { key: 'summary', label: 'Character' },
  { key: 'safety', label: 'Safety' },
  { key: 'transit', label: 'Transit' },
]

/**
 * The guide corpus is lightly marked-up plain text: `**bold**` runs and `- `
 * bullets. Render that as real structure rather than leaking the asterisks.
 */
function GuideText({ text }) {
  const lines = text.split('\n').map((line) => line.trim()).filter(Boolean)
  const lead = lines.filter((line) => !line.startsWith('- '))
  const bullets = lines.filter((line) => line.startsWith('- ')).map((line) => line.slice(2))

  const plain = (value) => value.replace(/\*\*/g, '')

  return (
    <>
      {lead.length > 0 && <p className="hood__row-text">{lead.map(plain).join(' ')}</p>}
      {bullets.length > 0 && (
        <ul className="hood__bullets">
          {bullets.map((bullet) => {
            const [head, ...rest] = plain(bullet).split(': ')
            return (
              <li key={bullet}>
                {rest.length > 0 ? (
                  <>
                    <b>{head}</b> {rest.join(': ')}
                  </>
                ) : (
                  head
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

const POI_ROWS = [
  { key: 'metro_stations', label: 'Metro' },
  { key: 'groceries', label: 'Groceries' },
  { key: 'hospitals', label: 'Hospitals' },
]

export default function NeighborhoodPanel() {
  const { state, dispatch } = useApp()
  const { shortlist, selectedListingId, nearbyById, nearbyLoadingId } = state

  const listing = shortlist.find((l) => l.id === selectedListingId)
  if (!listing) return null

  const snapshot = listing.neighborhood_snapshot ?? {}
  const pois = nearbyById[listing.id] ?? listing.nearby_pois
  const loading = nearbyLoadingId === listing.id

  return (
    <section className="hood" aria-label={`About ${listing.neighborhood}`}>
      <header className="hood__head">
        <div>
          <span className="eyebrow">Neighborhood</span>
          <h2 className="hood__name">{listing.neighborhood}</h2>
          <p className="hood__for">for {listing.society_name}</p>
        </div>
        <button
          type="button"
          className="hood__close"
          onClick={() => dispatch({ type: 'SELECT_LISTING', id: listing.id })}
          aria-label="Close neighborhood details"
        >
          ×
        </button>
      </header>

      <div className="hood__section">
        <p className="hood__section-head">
          <span className="eyebrow">From the guides</span>
          <span className="tag tag--sourced">Cited</span>
        </p>
        {SNAPSHOT_ROWS.map(({ key, label }) =>
          snapshot[key] ? (
            <div key={key} className="hood__row">
              <h3 className="hood__row-label">{label}</h3>
              <GuideText text={snapshot[key]} />
            </div>
          ) : (
            <div key={key} className="hood__row">
              <h3 className="hood__row-label">{label}</h3>
              <p className="hood__row-text hood__row-text--absent">
                No {label.toLowerCase()} notes for {listing.neighborhood}.
              </p>
            </div>
          ),
        )}
      </div>

      <div className="hood__section">
        <p className="hood__section-head">
          <span className="eyebrow">Within 1.5 km</span>
          <span className="tag tag--sourced">OpenStreetMap</span>
        </p>

        {loading && <p className="hood__note">Checking what's nearby…</p>}

        {!loading && pois?.error && (
          <p className="hood__note hood__note--absent">
            Nearby places are unavailable right now. Everything else on this card is unaffected.
          </p>
        )}

        {!loading &&
          !pois?.error &&
          POI_ROWS.map(({ key, label }) => {
            const places = pois?.[key] ?? []
            const wanted = key === 'metro_stations' ? 'metro_station' : key === 'groceries' ? 'grocery' : 'hospital'
            const failed = (pois?.unavailable ?? []).includes(wanted)

            return (
              <div key={key} className="hood__row">
                <h3 className="hood__row-label">{label}</h3>
                {failed ? (
                  <p className="hood__row-text hood__row-text--absent">Lookup failed.</p>
                ) : places.length === 0 ? (
                  <p className="hood__row-text hood__row-text--absent">None within 1.5 km.</p>
                ) : (
                  <ul className="hood__places">
                    {places.slice(0, 3).map((place) => (
                      <li key={`${place.name}-${place.osm_id ?? place.distance_km}`}>
                        <span>{place.name}</span>
                        <span className="num">{place.distance_km} km</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
      </div>
    </section>
  )
}
