// Mounts the real plugin into the harness: the same register() the desktop app calls, with the
// registered renders dropped into their own columns. Nothing in plugin.js is aware of the harness,
// so what shows here is what the app shows.
//
// There are five things to mount because there are two faces: the original pane and page from the
// shipped build (ids amp-face / page), and the rebuild's pane and page (amp2-face / page2) plus its
// status chip. The harness mounts all of them at once on purpose: if the two faces ever collapse
// onto one backend, both columns would show the same numbers and the split would be visible here
// before it ships.

import { createRoot } from 'react-dom/client'
import { createElement } from 'react'
import plugin from '../plugin.js'
import { rest, calls } from './fixture.js'

const entries = []

plugin.register({
  register: (entry) => entries.push(entry),
  registerMany: (many) => many.forEach((entry) => entries.push(entry)),
  rest
})

const want = {
  'amp-face': 'pane',
  page: 'page',
  'amp2-face': 'pane2',
  page2: 'page2',
  'amp2-chip': 'chip'
}

const missing = Object.keys(want).filter((id) => !entries.some((entry) => entry.id === id))
if (missing.length) {
  throw new Error(`harness expected ${Object.keys(want).join(', ')}, got ${entries.map((e) => e.id).join(', ')}`)
}

for (const [id, slot] of Object.entries(want)) {
  const entry = entries.find((candidate) => candidate.id === id)
  createRoot(document.getElementById(slot)).render(createElement(entry.render))
}

// Read by the screenshot check and by hand in the console: every route both faces called, and the
// engine each one asked for.
window.__amphead = { entries, plugin, calls }
