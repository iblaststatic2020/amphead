// Mounts the real plugin into the harness: the same register() the desktop app calls, with the
// registered renders dropped into their own columns. Nothing in plugin.js is aware of the harness,
// so what shows here is what the app shows.
//
// Three slots, because the face ships three doors: the pane it docks on the right, the page it
// opens at /amp2, and the status-bar chip. All three read the same routes, so a disagreement
// between them shows up here before it ships.

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
  face: 'pane',
  page: 'page',
  chip: 'chip'
}

const missing = Object.keys(want).filter((id) => !entries.some((entry) => entry.id === id))
if (missing.length) {
  throw new Error(`harness expected ${Object.keys(want).join(', ')}, got ${entries.map((e) => e.id).join(', ')}`)
}

for (const [id, slot] of Object.entries(want)) {
  const entry = entries.find((candidate) => candidate.id === id)
  createRoot(document.getElementById(slot)).render(createElement(entry.render))
}

// Read by the screenshot check and by hand in the console: every route the face called, and the
// engine it asked for. A row here without `amphead2` is a click that escaped the face.
window.__amphead = { entries, plugin, calls }
