export function matchesCaseSearch(item, detail, query) {
  const needle = query.trim().toLowerCase()
  if (!needle) return true

  const caseLabel = 'cs-' + String(item.id).padStart(4, '0')
  const searchable = [
    String(item.id),
    caseLabel,
    item.booking_ref,
    item.passenger_name,
    item.flight_id,
    ...(detail?.itinerary || []).flatMap((leg) => [leg.origin, leg.destination]),
  ]

  return searchable.some((value) => typeof value === 'string' && value.toLowerCase().includes(needle))
}
