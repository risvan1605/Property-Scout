import { useState } from 'react'
import { useApp } from '../context/useApp'
import './SourcesPanel.css'

export default function SourcesPanel() {
  const { state } = useApp()
  const { sources } = state
  const [open, setOpen] = useState(true)

  const grouped = sources.reduce((acc, source) => {
    const key = source.neighborhood || 'Map data'
    ;(acc[key] ||= []).push(source)
    return acc
  }, {})

  return (
    <section className="sources" aria-label="Sources">
      <button
        type="button"
        className="sources__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="eyebrow">Sources</span>
        <span className="sources__count num">{sources.length}</span>
        <span className={`sources__chevron${open ? ' sources__chevron--open' : ''}`} aria-hidden="true">
          ›
        </span>
      </button>

      {open && (
        <div className="sources__body">
          {sources.length === 0 ? (
            <p className="sources__empty">
              Every claim the scout makes about an area is listed here with its source.
            </p>
          ) : (
            Object.entries(grouped).map(([group, items]) => (
              <div key={group} className="sources__group">
                <h3 className="sources__group-name">{group}</h3>
                <ul>
                  {items.map((source) => (
                    <li key={`${source.url}-${source.section}`}>
                      <a href={source.url} target="_blank" rel="noreferrer noopener">
                        {source.section}
                      </a>
                      <span className="sources__used">{source.used_for}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  )
}
