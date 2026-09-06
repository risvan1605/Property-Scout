import { useApp } from '../context/useApp'
import './BookingPanel.css'

function formatDate(iso) {
  const parsed = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

export default function BookingPanel() {
  const { state } = useApp()
  const { booking, shortlist } = state
  if (!booking) return null

  const listing = shortlist.find((l) => l.id === booking.listing_id)

  return (
    <aside className="booked" aria-label="Booked visit">
      <header className="booked__head">
        <span className="tag tag--live">Visit booked</span>
        <span className="booked__code num">{booking.confirmation_code?.slice(0, 10)}</span>
      </header>

      <h2 className="booked__society">{listing?.society_name ?? booking.listing_id}</h2>

      <dl className="booked__when">
        <div>
          <dt className="eyebrow">Date</dt>
          <dd>{formatDate(booking.date)}</dd>
        </div>
        <div>
          <dt className="eyebrow">Time</dt>
          <dd className="num">{booking.time_slot}</dd>
        </div>
      </dl>

      <p className="booked__note">Invite sent to {booking.user_email}</p>

      {booking.calendar_link && (
        <a
          className="booked__link"
          href={booking.calendar_link}
          target="_blank"
          rel="noreferrer noopener"
        >
          Open in Google Calendar
        </a>
      )}
    </aside>
  )
}
