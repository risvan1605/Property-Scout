import ProximityDial from './ProximityDial'
import './ListingCard.css'

function formatRent(rent) {
  return new Intl.NumberFormat('en-IN').format(rent)
}

const MAX_REASONS = 3
const MAX_AMENITIES = 5

export default function ListingCard({ listing, index, selected, nearby, nearbyLoading, onSelect }) {
  const pois = nearby ?? listing.nearby_pois

  // The furnishing reason repeats the spec row above it, so it earns no chip.
  const furnishing = (listing.furnishing || '').replace('-', ' ').toLowerCase()
  const reasons = (listing.match_reasons ?? [])
    .filter((reason) => reason.toLowerCase() !== furnishing)
    .slice(0, MAX_REASONS)

  const amenities = listing.amenities ?? []
  const shownAmenities = selected ? amenities : amenities.slice(0, MAX_AMENITIES)
  const hiddenAmenities = amenities.length - shownAmenities.length
  const hasPois = Boolean(
    pois && ['metro_stations', 'groceries', 'hospitals'].some((k) => pois[k]?.length),
  )

  return (
    <article
      className={`card${selected ? ' card--selected' : ''}`}
      style={{ '--stagger': `${Math.min(index, 8) * 55}ms` }}
    >
      <button
        type="button"
        className="card__hit"
        aria-expanded={selected}
        onClick={() => onSelect(listing.id)}
      >
        <header className="card__head">
          <div>
            <h3 className="card__society">{listing.society_name}</h3>
            <p className="card__area">
              {listing.neighborhood}
              <span className="card__coords num">
                {listing.latitude?.toFixed(3)}, {listing.longitude?.toFixed(3)}
              </span>
            </p>
          </div>
          <p className="card__rent num">
            ₹{formatRent(listing.rent)}
            <span>/mo</span>
          </p>
        </header>

        <dl className="card__specs">
          <div>
            <dt className="eyebrow">Layout</dt>
            <dd className="num">{listing.bedrooms} BHK</dd>
          </div>
          <div>
            <dt className="eyebrow">Size</dt>
            <dd className="num">{listing.sqft ? `${listing.sqft} sqft` : '—'}</dd>
          </div>
          <div>
            <dt className="eyebrow">Furnishing</dt>
            <dd>{(listing.furnishing || '—').replace('-', ' ')}</dd>
          </div>
        </dl>
      </button>

      {reasons.length > 0 && (
        <ul className="card__reasons">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {amenities.length > 0 && (
        <ul className="card__amenities">
          {shownAmenities.map((amenity) => (
            <li key={amenity}>{amenity.replace('-', ' ')}</li>
          ))}
          {hiddenAmenities > 0 && (
            <li className="card__amenities-more num">+{hiddenAmenities}</li>
          )}
        </ul>
      )}

      <div className="card__nearby">
        {hasPois ? (
          <>
            <p className="card__nearby-head">
              <span className="eyebrow">Nearby</span>
              <span className="tag tag--sourced">OpenStreetMap</span>
            </p>
            <ProximityDial listing={listing} pois={pois} />
          </>
        ) : nearbyLoading ? (
          <p className="card__nearby-note">Checking what's nearby…</p>
        ) : pois?.error ? (
          <p className="card__nearby-note card__nearby-note--absent">
            Nearby places are unavailable right now.
          </p>
        ) : (
          <p className="card__nearby-note">
            {selected ? 'No places found within 1.5 km.' : 'Open to check what’s nearby.'}
          </p>
        )}
      </div>
    </article>
  )
}
