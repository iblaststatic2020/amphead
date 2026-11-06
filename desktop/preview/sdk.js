// The smallest stand-in for @hermes/plugin-sdk that still behaves like the real one: the same area
// constants, the same host verbs (as no-ops that log), and a useQuery that polls and re-runs when
// something invalidates it. It exists so the face can be looked at and clicked in a plain browser
// without the desktop app, and so a layout regression is visible before a plugin reload.

import { useEffect, useRef, useState } from 'react'

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette.commands'
export const PANES_AREA = 'panes'

export const host = {
  navigate: (path) => console.log('[host.navigate]', path),
  notify: (note) => console.log('[host.notify]', note)
}

// One bus for every query, so invalidateQueries reaches the pane, the page and the chip at once,
// which is what the real client does after a mutation.
const bus = { subs: new Set() }
function fire() {
  for (const sub of bus.subs) sub()
}

export function useQueryClient() {
  return {
    invalidateQueries() {
      fire()
    }
  }
}

export function useQuery({ queryFn, refetchInterval }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const fn = useRef(queryFn)
  fn.current = queryFn
  useEffect(() => {
    let alive = true
    const run = async () => {
      try {
        const next = await fn.current()
        if (alive) {
          setData(next)
          setError(null)
        }
      } catch (err) {
        if (alive) setError(err)
      }
    }
    run()
    const timer = refetchInterval ? setInterval(run, refetchInterval) : null
    bus.subs.add(run)
    return () => {
      alive = false
      if (timer) clearInterval(timer)
      bus.subs.delete(run)
    }
  }, [refetchInterval])
  return { data, error }
}
