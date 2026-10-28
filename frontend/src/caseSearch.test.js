import assert from 'node:assert/strict'
import test from 'node:test'

import { matchesCaseSearch } from './caseSearch.js'

const item = {
  id: 7,
  booking_ref: 'INDEMO0007',
  passenger_name: 'Priya Reddy',
  flight_id: 'DEMO-6E-2102',
  status: 'open',
  created_at: '',
}
const detail = {
  itinerary: [{ origin: 'HYD', destination: 'DEL' }],
}

for (const [label, query] of [
  ['passenger name', 'Priya Reddy'],
  ['flight number', 'DEMO-6E-2102'],
  ['PNR', 'INDEMO0007'],
  ['numeric case ID', '7'],
  ['formatted case ID', 'CS-0007'],
  ['origin airport', 'HYD'],
  ['destination airport', 'DEL'],
]) {
  test(`search matches ${label}`, () => {
    assert.equal(matchesCaseSearch(item, detail, query), true)
  })
}

test('search is case-insensitive', () => {
  assert.equal(matchesCaseSearch(item, detail, 'priya reddy'), true)
})

test('search ignores surrounding whitespace', () => {
  assert.equal(matchesCaseSearch(item, detail, '  INDEMO0007  '), true)
})

test('search returns false when no cases match', () => {
  assert.equal(matchesCaseSearch(item, detail, 'unknown passenger'), false)
})
