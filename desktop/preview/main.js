// Mounts the real plugin into the harness: the same register() the desktop app calls, with the
// registered renders dropped into a 300px pane column and a 420px page column. Nothing in
// plugin.js is aware of the harness, so what shows here is what the app shows.

import { createRoot } from 'react-dom/client'
import { createElement } from 'react'
import plugin from '../plugin.js'
import { rest } from './fixture.js'

const entries = []

plugin.register({
  register: (entry) => entries.push(entry),
  registerMany: (many) => many.forEach((entry) => entries.push(entry)),
  rest
})

const pane = entries.find((entry) => entry.id === 'amp-face')
const page = entries.find((entry) => entry.id === 'page')
if (!pane || !page) throw new Error(`harness expected a pane and a page, got ${entries.map((e) => e.id).join(', ')}`)

createRoot(document.getElementById('pane')).render(createElement(pane.render))
createRoot(document.getElementById('page')).render(createElement(page.render))

window.__amphead = { entries, plugin }
