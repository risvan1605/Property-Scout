/**
 * Proximity dial — the shortlist's signature readout.
 *
 * Plots real OpenStreetMap places around a listing at their true distance and
 * true bearing: rings mark 500m / 1km / 1.5km, north is up. A place 200m due
 * west sits 200m due west on the dial. Nothing here is decorative — every dot
 * is a row the MCP server returned.
 */

import './ProximityDial.css'

const RINGS = [0.5, 1.0, 1.5]
const MAX_KM = 1.5
const SIZE = 128
const CENTER = SIZE / 2
const RADIUS = 52

const SERIES = [
  { key: 'metro_stations', label: 'Metro', color: 'var(--metro)' },
  { key: 'groceries', label: 'Grocery', color: 'var(--grocery)' },
  { key: 'hospitals', label: 'Hospital', color: 'var(--hospital)' },
]

/** Bearing from the listing to a place, as an angle on the dial. */
function plot(listing, poi) {
  const km = poi.distance_km ?? 0
  const r = (Math.min(km, MAX_KM) / MAX_KM) * RADIUS

  const dLat = (poi.lat ?? listing.latitude) - listing.latitude
  const dLng = (poi.lng ?? listing.longitude) - listing.longitude
  // Longitude degrees shrink with latitude; Bengaluru sits at ~13°N.
  const east = dLng * Math.cos((listing.latitude * Math.PI) / 180)
  const angle = Math.atan2(east, dLat) // 0 = north, clockwise

  return {
    x: CENTER + r * Math.sin(angle),
    y: CENTER - r * Math.cos(angle),
  }
}

export default function ProximityDial({ listing, pois }) {
  const unavailable = pois?.unavailable ?? []
  const plotted = SERIES.flatMap(({ key, label, color }) =>
    (pois?.[key] ?? []).slice(0, 3).map((poi, index) => ({
      ...plot(listing, poi),
      color,
      label,
      poi,
      id: `${key}-${index}`,
    })),
  )

  const counts = SERIES.map(({ key, label, color }) => ({
    label,
    color,
    count: (pois?.[key] ?? []).length,
    missing: unavailable.includes(
      key === 'metro_stations' ? 'metro_station' : key === 'groceries' ? 'grocery' : 'hospital',
    ),
  }))

  return (
    <figure className="dial">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="dial__svg" role="img"
           aria-label={`Places near ${listing.society_name}: ${counts
             .map((c) => `${c.count} ${c.label.toLowerCase()}`)
             .join(', ')} within 1.5 kilometres`}>
        {RINGS.map((km) => (
          <circle key={km} cx={CENTER} cy={CENTER} r={(km / MAX_KM) * RADIUS}
                  className="dial__ring" />
        ))}
        <line x1={CENTER} y1={CENTER - RADIUS - 6} x2={CENTER} y2={CENTER + RADIUS + 6}
              className="dial__axis" />
        <line x1={CENTER - RADIUS - 6} y1={CENTER} x2={CENTER + RADIUS + 6} y2={CENTER}
              className="dial__axis" />
        <text x={CENTER} y={10} className="dial__north">N</text>

        {plotted.map(({ id, x, y, color, label, poi }) => (
          <g key={id} className="dial__poi">
            <circle cx={x} cy={y} r="8" fill="transparent" />
            <circle cx={x} cy={y} r="3.4" fill={color} />
            <title>{`${label}: ${poi.name} — ${poi.distance_km} km`}</title>
          </g>
        ))}

        <circle cx={CENTER} cy={CENTER} r="3" className="dial__home" />
      </svg>

      <figcaption className="dial__legend">
        {counts.map(({ label, color, count, missing }) => (
          <span key={label} className="dial__legend-item">
            <i className="dial__swatch" style={{ background: color }} aria-hidden="true" />
            {label}
            <b className="num">{missing ? '—' : count}</b>
          </span>
        ))}
        <span className="dial__scale num">
          rings 0.5 · 1 · 1.5 km · nearest 3 plotted
        </span>
      </figcaption>
    </figure>
  )
}
