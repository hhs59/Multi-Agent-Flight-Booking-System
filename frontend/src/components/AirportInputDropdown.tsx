import { MapPin, Plane, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { searchAirports, type AirportOption } from '../data/airports'

interface AirportInputDropdownProps {
  label: string
  icon: 'origin' | 'dest'
  value: string
  onChange: (code: string) => void
  placeholder?: string
  required?: boolean
  className?: string
}

export function AirportInputDropdown({
  label,
  icon,
  value,
  onChange,
  placeholder = 'SGN',
  required = false,
  className = '',
}: AirportInputDropdownProps) {
  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [highlightIndex, setHighlightIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Find airport details from code
  const currentAirport = searchAirports(value).find((a) => a.code.toUpperCase() === value.toUpperCase())
  const filteredAirports = searchAirports(query)

  // Sync query when value changes externally (e.g. via swap button)
  useEffect(() => {
    if (!isOpen) {
      setQuery('')
    }
  }, [value, isOpen])

  // Click outside listener
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelect = (airport: AirportOption) => {
    onChange(airport.code)
    setQuery('')
    setIsOpen(false)
    inputRef.current?.blur()
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        setIsOpen(true)
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIndex((prev) => (prev + 1 < filteredAirports.length ? prev + 1 : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIndex((prev) => (prev > 0 ? prev - 1 : filteredAirports.length - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightIndex >= 0 && highlightIndex < filteredAirports.length) {
        handleSelect(filteredAirports[highlightIndex])
      } else if (filteredAirports.length > 0) {
        handleSelect(filteredAirports[0])
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
    }
  }

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation()
    onChange('')
    setQuery('')
    setIsOpen(true)
    inputRef.current?.focus()
  }

  return (
    <div
      ref={containerRef}
      className={`travel-input-card airport-autocomplete-card ${className} ${isOpen ? 'active-dropdown' : ''}`}
      onClick={() => {
        inputRef.current?.focus()
        setIsOpen(true)
      }}
    >
      <div className="travel-input-header">
        <span className="travel-input-label">
          {icon === 'origin' ? (
            <MapPin size={12} className="label-icon" />
          ) : (
            <Plane size={12} className="label-icon dest-icon" />
          )}
          {label} {required ? '*' : ''}
        </span>
        {value ? (
          <button
            type="button"
            className="clear-input-x-btn"
            onClick={handleClear}
            title="Xóa sân bay đã chọn"
            aria-label="Clear airport selection"
          >
            <X size={13} />
          </button>
        ) : null}
      </div>

      <div className="travel-input-body">
        <input
          ref={inputRef}
          type="text"
          value={isOpen ? query : (value || '')}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
            setHighlightIndex(0)
            if (e.target.value.length === 3) {
              const exact = searchAirports(e.target.value).find(
                (a) => a.code.toLowerCase() === e.target.value.toLowerCase()
              )
              if (exact) {
                onChange(exact.code)
              }
            }
          }}
          onFocus={() => {
            setIsOpen(true)
            setQuery('')
          }}
          onKeyDown={handleKeyDown}
          placeholder={isOpen ? 'Tìm TP hoặc mã sân bay...' : placeholder}
          className="travel-code-input"
          aria-label={label}
        />
        <span className="travel-city-name">
          {currentAirport
            ? `${currentAirport.city} • ${currentAirport.name}`
            : value
              ? `Sân bay ${value.toUpperCase()}`
              : 'Gõ tên thành phố hoặc chọn gợi ý'}
        </span>
      </div>

      {/* Floating Autocomplete Dropdown */}
      {isOpen ? (
        <div className="airport-dropdown-menu" onMouseDown={(e) => e.preventDefault()}>
          <div className="dropdown-header-bar">
            <span className="dropdown-title">
              <Sparkles size={13} />
              {query ? `Gợi ý phù hợp cho "${query}"` : 'Sân bay phổ biến & được chọn nhiều'}
            </span>
          </div>

          <div className="dropdown-items-list">
            {filteredAirports.length > 0 ? (
              filteredAirports.map((airport, idx) => (
                <div
                  key={airport.code}
                  className={`airport-item ${idx === highlightIndex ? 'highlighted' : ''} ${
                    airport.code === value ? 'selected' : ''
                  }`}
                  onClick={() => handleSelect(airport)}
                  onMouseEnter={() => setHighlightIndex(idx)}
                >
                  <div className="airport-item-icon">
                    <Plane size={15} />
                  </div>
                  <div className="airport-item-info">
                    <div className="airport-item-city">
                      <strong>{airport.city}</strong>
                      <span className="airport-item-country">{airport.country}</span>
                    </div>
                    <div className="airport-item-name">{airport.name}</div>
                  </div>
                  <span className="airport-item-code">{airport.code}</span>
                </div>
              ))
            ) : (
              <div className="no-airports-found">
                <p>Không tìm thấy sân bay khớp với &ldquo;{query}&rdquo;</p>
                <span>Thử gõ: <strong>Hà Nội, Sài Gòn, Đà Nẵng, Phú Quốc, Tokyo, Bangkok...</strong></span>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
