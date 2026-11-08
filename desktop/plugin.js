// AmpHead 2: the front of an amp head, for the local engine.
//
// One backend, no host application, no plug-in parameters dressed up as knobs. Everything here is
// something the engine really owns: which capture it runs, which cabinet follows it, the three
// tone knobs, and the master level. The colours come from the app's own tokens (`--ui-*`), so the
// amp looks like part of whatever theme is selected instead of a black box pasted into it.
//
// Three rules held throughout:
//   * every number is labelled where it sits, never behind a tooltip;
//   * a control that cannot answer is not drawn (no knobs with no engine);
//   * the tone's name is derived from the two files that make it, so it can never lie.

import { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const MUTED = { color: 'var(--ui-text-quaternary)' }
const SECOND = { color: 'var(--ui-text-secondary)' }
const ACCENT = { color: 'var(--ui-accent)' }

const TONE_NAMES = ['bass', 'mid', 'treble']
const TONE_LABELS = { bass: 'BASS', mid: 'MID', treble: 'TREBLE' }
const TONE_RANGE = 12 // ±12 dB, same as the tone stack the engine runs
const MASTER_LOW = -60
const MASTER_HIGH = 12

const toDb = (peak) => (peak <= 0.000001 ? -60 : Math.max(-60, 20 * Math.log10(peak)))
const dbWords = (db) => `${db > 0 ? '+' : ''}${Number(db).toFixed(1)} dB`

// Amber when the amp is ready, red when something is broken, dim when it is only standing by.
const VERDICT_COLOR = {
  READY: 'var(--ui-accent)',
  STANDBY: 'var(--ui-text-quaternary)',
  'NO SIGNAL': 'var(--ui-accent)',
  'NO INPUT': 'var(--ui-accent)',
  'NO CHAIN': 'var(--ui-accent)',
  CLIPPING: '#e5533d',
  'DEVICE CLOSED': '#e5533d',
  'DEVICE UNPLUGGED': '#e5533d',
  'ENGINE OFF': 'var(--ui-text-quaternary)',
  MUTED: 'var(--ui-accent)',
  'REOPENED DEVICE': 'var(--ui-accent)',
  CONNECTING: 'var(--ui-text-quaternary)'
}

const CHIP_WORD = {
  READY: 'quiet',
  STANDBY: 'standby',
  'ENGINE OFF': 'engine off',
  'DEVICE CLOSED': 'device closed',
  'DEVICE UNPLUGGED': 'unplugged',
  MUTED: 'muted',
  'NO INPUT': 'no input',
  'NO CHAIN': 'no chain',
  'NO SIGNAL': 'no signal',
  CLIPPING: 'clipping',
  'REOPENED DEVICE': 'device reopened',
  CONNECTING: 'connecting'
}

const pageOnScreen = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'
function useOnScreen() {
  const [on, setOn] = useState(pageOnScreen)
  useEffect(() => {
    const look = () => setOn(pageOnScreen())
    document.addEventListener('visibilitychange', look)
    return () => document.removeEventListener('visibilitychange', look)
  }, [])
  return on
}
const meterEvery = (armed) => (armed ? 150 : 500)

// The cabinet: a tolex box, drawn with the theme's own colours so a light theme gets a pale amp
// and a dark theme a dark one. The texture is a one-pixel stripe pattern at 4% foreground, which
// reads as covering without inventing a colour of its own.
const BOX = {
  borderRadius: 12,
  border: '1px solid var(--ui-stroke-secondary)',
  background:
    'repeating-linear-gradient(90deg, color-mix(in srgb, var(--ui-text-primary) 4%, transparent) 0 1px, transparent 1px 4px),' +
    'linear-gradient(180deg, var(--ui-bg-secondary) 0%, var(--ui-bg-primary) 100%)',
  boxShadow: 'inset 0 1px 0 color-mix(in srgb, var(--ui-text-primary) 10%, transparent), 0 2px 6px rgba(0,0,0,0.18)'
}

// The control panel: a recessed brushed plate, slightly darker than the box it sits in.
const PLATE = {
  borderRadius: 8,
  border: '1px solid var(--ui-stroke-secondary)',
  background: 'linear-gradient(180deg, var(--ui-bg-tertiary) 0%, var(--ui-bg-secondary) 100%)',
  boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.22)'
}

const WELL = {
  borderRadius: 6,
  border: '1px solid var(--ui-stroke-secondary)',
  background: 'var(--ui-bg-primary)',
  boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.25)'
}

function Engraved({ children, className = '' }) {
  return jsx('span', {
    className: `text-[0.5rem] font-semibold uppercase tracking-[0.18em] ${className}`,
    style: { color: 'var(--ui-text-tertiary)' },
    children
  })
}

// A pilot lamp: the one thing readable from across the room.
function Lamp({ verdict }) {
  const color = VERDICT_COLOR[verdict] || 'var(--ui-text-quaternary)'
  const lit = verdict === 'READY' || verdict === 'CLIPPING' || verdict === 'NO SIGNAL'
  return jsx('span', {
    className: 'shrink-0 rounded-full',
    style: {
      width: 12,
      height: 12,
      background: `radial-gradient(circle at 35% 30%, ${color}, color-mix(in srgb, ${color} 25%, transparent) 75%)`,
      boxShadow: lit ? `0 0 8px 1px ${color}, inset 0 0 2px rgba(255,255,255,0.6)` : 'inset 0 1px 2px rgba(0,0,0,0.5)',
      border: '1px solid color-mix(in srgb, var(--ui-text-primary) 18%, transparent)',
      opacity: lit ? 1 : 0.6
    }
  })
}

// A chicken-head knob: a pointer on a turning cap, a ring of ticks, and a readout under it.
// Drag up/down to turn, shift for fine, double-click to put it back to neutral.
function ChickenKnob({ label, db, min, max, neutral = 0, size = 44, busy, disabled, onCommit }) {
  const norm = Math.max(0, Math.min(1, (db - min) / (max - min)))
  const [at, setAt] = useState(norm)
  const [dragging, setDragging] = useState(false)
  const cur = useRef(norm)
  const finger = useRef(0)
  const sent = useRef(0)
  const flight = useRef({ busy: false, queued: null })
  const settled = useRef(0)

  // The face follows the engine, but not while a finger is on it or right after a write landed.
  useEffect(() => {
    if (dragging || Date.now() < settled.current) return
    cur.current = norm
    setAt(norm)
  }, [norm, dragging])

  if (disabled) return null

  const dbOf = (n) => min + Math.max(0, Math.min(1, n)) * (max - min)
  const push = (n, final) => {
    const order = flight.current
    if (order.busy) {
      order.queued = { n, final }
      return
    }
    order.busy = true
    sent.current = Date.now()
    Promise.resolve(onCommit(dbOf(n), final)).then((answer) => {
      if (answer && answer.ok) {
        settled.current = Date.now() + 1200
        if (n === cur.current) setAt(n)
      }
    }).catch(() => {}).finally(() => {
      order.busy = false
      const queued = order.queued
      order.queued = null
      if (queued) push(queued.n, queued.final)
    })
  }

  const move = (next, final) => {
    const landed = Math.max(0, Math.min(1, next))
    cur.current = landed
    setAt(landed)
    if (final || Date.now() - sent.current >= 70) push(landed, final)
  }

  const reset = () => move((neutral - min) / (max - min), true)
  const angle = -135 + at * 270
  const ticks = Array.from({ length: 11 }, (_, i) => i)

  return jsxs('div', {
    className: 'flex select-none flex-col items-center gap-1',
    children: [
      jsxs('div', {
        className: 'relative',
        style: { width: size + 16, height: size + 16 },
        children: [
          ...ticks.map((i) => jsx('span', {
            className: 'absolute left-1/2 top-1/2',
            style: {
              width: 1,
              height: i % 5 === 0 ? 5 : 3,
              marginLeft: -0.5,
              marginTop: -(size / 2 + 7),
              transformOrigin: `0.5px ${size / 2 + 7}px`,
              transform: `rotate(${-135 + (i / 10) * 270}deg)`,
              background: 'var(--ui-stroke-secondary)'
            }
          }, i)),
          jsxs('div', {
            role: 'slider',
            tabIndex: 0,
            'aria-label': `${label}, ${dbWords(dbOf(at))}`,
            'aria-valuemin': min,
            'aria-valuemax': max,
            'aria-valuenow': Number(dbOf(at).toFixed(1)),
            'aria-valuetext': dbWords(dbOf(at)),
            onPointerDown: (event) => {
              if (busy) return
              event.currentTarget.setPointerCapture(event.pointerId)
              finger.current = event.clientY
              setDragging(true)
            },
            onPointerMove: (event) => {
              if (!dragging) return
              const dy = finger.current - event.clientY
              finger.current = event.clientY
              if (dy !== 0) move(cur.current + dy / (event.shiftKey ? 900 : 180), false)
            },
            onPointerUp: () => {
              if (!dragging) return
              setDragging(false)
              move(cur.current, true)
            },
            onPointerCancel: () => {
              if (!dragging) return
              setDragging(false)
              move(cur.current, true)
            },
            onDoubleClick: () => { if (!busy) reset() },
            onKeyDown: (event) => {
              if (busy) return
              const step = event.shiftKey ? 0.005 : 0.025
              if (event.key === 'ArrowUp' || event.key === 'ArrowRight') {
                event.preventDefault()
                move(cur.current + step, true)
              } else if (event.key === 'ArrowDown' || event.key === 'ArrowLeft') {
                event.preventDefault()
                move(cur.current - step, true)
              } else if (event.key === 'Home') {
                event.preventDefault()
                reset()
              }
            },
            className: 'absolute left-1/2 top-1/2 rounded-full outline-none',
            style: {
              width: size,
              height: size,
              marginLeft: -size / 2,
              marginTop: -size / 2,
              cursor: busy ? 'default' : 'ns-resize',
              touchAction: 'none',
              background:
                'radial-gradient(circle at 34% 26%, var(--ui-bg-quinary) 0%, var(--ui-bg-quaternary) 42%, var(--ui-bg-primary) 100%)',
              boxShadow:
                'inset 0 -1px 2px color-mix(in srgb, var(--ui-text-primary) 22%, transparent),' +
                ' inset 0 2px 4px rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.35)',
              border: '1px solid var(--ui-stroke-primary)'
            },
            children: jsx('span', {
              className: 'absolute inset-0',
              style: { transform: `rotate(${angle}deg)` },
              children: jsx('span', {
                className: 'absolute left-1/2',
                style: {
                  top: 5,
                  width: 3,
                  height: size * 0.34,
                  marginLeft: -1.5,
                  borderRadius: 2,
                  background: dragging ? 'var(--ui-accent)' : 'var(--ui-text-primary)',
                  boxShadow: dragging ? '0 0 5px var(--ui-accent)' : '0 1px 1px rgba(0,0,0,0.3)'
                }
              })
            })
          })
        ]
      }),
      jsx(Engraved, { children: label }),
      jsx('span', {
        className: 'text-[0.5625rem] tabular-nums leading-none',
        style: { color: dragging ? 'var(--ui-accent)' : 'var(--ui-text-secondary)' },
        children: dbWords(dbOf(at))
      })
    ]
  })
}

// One choice, shown as a plate you press, opening a searchable list of the files behind it.
function Plate({ label, value, sub, open, onToggle, disabled, children }) {
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsxs('button', {
        type: 'button',
        disabled,
        'aria-expanded': open,
        onClick: onToggle,
        className: 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none',
        style: { ...WELL, cursor: disabled ? 'default' : 'pointer' },
        children: [
          jsx('span', {
            className: 'min-w-0 flex-1',
            children: jsxs('span', {
              className: 'flex flex-col',
              children: [
                jsx(Engraved, { children: label }),
                jsx('span', {
                  className: 'truncate text-[0.75rem] font-medium leading-tight',
                  style: { color: 'var(--ui-text-primary)' },
                  children: value || 'nothing loaded'
                }),
                sub
                  ? jsx('span', { className: 'truncate text-[0.5rem] leading-tight', style: MUTED, children: sub })
                  : null
              ]
            })
          }),
          jsx('span', {
            className: 'shrink-0 text-[0.625rem]',
            style: { color: open ? 'var(--ui-accent)' : 'var(--ui-text-quaternary)' },
            children: open ? '▴' : '▾'
          })
        ]
      }),
      open ? children : null
    ]
  })
}

// The folder a loaded file came from, so the plate under a knob says where the amp lives.
const folderOf = (items, path) => {
  const hit = (items || []).find((file) => file.path === path)
  return hit ? hit.folder : ''
}

// A cabinet or an amp: a search box and the files under it. 600 captures is a list nobody scrolls,
// so typing narrows it, and the folder under each name tells two close relatives apart.
function FilePicker({ items, currentPath, busy, onPick, placeholder }) {
  const [needle, setNeedle] = useState('')
  const words = needle.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const matches = (items || []).filter((file) => {
    if (!words.length) return true
    const hay = `${file.name} ${file.folder}`.toLowerCase()
    return words.every((word) => hay.includes(word))
  })
  const shown = matches.slice(0, 60)
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('input', {
        type: 'search',
        value: needle,
        placeholder,
        'aria-label': placeholder,
        onChange: (event) => setNeedle(event.target.value),
        className: 'w-full rounded-md px-2 py-1 text-[0.6875rem] outline-none',
        style: { ...WELL, color: 'var(--ui-text-primary)' }
      }),
      jsxs('div', {
        className: 'text-[0.5rem] leading-tight',
        style: MUTED,
        children: `${matches.length} matched${matches.length > shown.length ? `, showing ${shown.length}` : ''}`
      }),
      jsx('div', {
        className: 'flex max-h-44 flex-col overflow-y-auto rounded-md',
        style: { ...WELL },
        children: shown.length
          ? shown.map((file) => {
            const here = file.path === currentPath
            return jsxs('button', {
              type: 'button',
              disabled: busy,
              onClick: () => onPick(file.path),
              className: 'flex flex-col items-start gap-0 px-2 py-1 text-left outline-none',
              style: {
                background: here ? 'color-mix(in srgb, var(--ui-accent) 16%, transparent)' : 'transparent',
                borderLeft: here ? '2px solid var(--ui-accent)' : '2px solid transparent'
              },
              children: [
                jsx('span', {
                  className: 'w-full truncate text-[0.6875rem] leading-tight',
                  style: { color: here ? 'var(--ui-accent)' : 'var(--ui-text-primary)' },
                  children: file.name
                }),
                jsx('span', {
                  className: 'w-full truncate text-[0.5rem] leading-tight',
                  style: MUTED,
                  children: file.folder
                })
              ]
            }, file.path)
          })
          : jsx('span', {
            className: 'px-2 py-1.5 text-[0.5625rem]',
            style: MUTED,
            children: needle ? 'nothing in the folders matches that' : 'no files found; set AMPHEAD_CAPTURE_DIR or AMPHEAD_CAB_DIR'
          })
      })
    ]
  })
}

// A segmented LED meter: it reads like the front-panel meter on a real head, and the clip lamp
// latches for a moment so a peak is not gone before the eye gets there.
function Meter({ peak, clip }) {
  const db = toDb(peak)
  const lit = Math.max(0, Math.min(12, Math.round(((db + 36) / 36) * 12)))
  return jsxs('div', {
    className: 'flex items-center gap-2',
    children: [
      jsx('div', {
        className: 'flex flex-1 items-center gap-[3px]',
        children: Array.from({ length: 12 }, (_, i) => {
          const on = i < lit
          const hot = i >= 10
          return jsx('span', {
            style: {
              flex: 1,
              height: 6,
              borderRadius: 1,
              background: on
                ? (hot ? '#e5533d' : 'var(--ui-accent)')
                : 'color-mix(in srgb, var(--ui-text-primary) 10%, transparent)',
              boxShadow: on ? `0 0 4px ${hot ? '#e5533d' : 'var(--ui-accent)'}` : 'none'
            }
          }, i)
        })
      }),
      jsxs('span', {
        className: 'flex items-center gap-1 text-[0.5rem] tabular-nums',
        style: MUTED,
        children: [
          jsx('span', {
            className: 'inline-block h-1.5 w-1.5 rounded-full',
            style: { background: clip ? '#e5533d' : 'color-mix(in srgb, var(--ui-text-primary) 12%, transparent)' }
          }),
          'clip'
        ]
      })
    ]
  })
}

// The one switch on the front: it says what the next press does, in the next press's own words.
function PowerButton({ running, armed, busy, onSetup, onLive }) {
  const state = !running ? 'setup' : armed ? 'live' : 'standby'
  const look = {
    setup: { text: 'SET UP', sub: 'open the engine', fill: 'var(--ui-accent)', ink: 'var(--ui-bg-primary)' },
    standby: { text: 'GO LIVE', sub: 'arm at the safe level', fill: 'var(--ui-bg-quaternary)', ink: 'var(--ui-text-primary)' },
    live: { text: 'LIVE', sub: 'press to stand by', fill: '#e5533d', ink: '#fff' }
  }[state]
  return jsxs('button', {
    type: 'button',
    disabled: busy,
    onClick: state === 'setup' ? onSetup : onLive,
    'aria-label': `${look.text}: ${look.sub}`,
    className: 'flex shrink-0 flex-col items-center rounded-md px-3 py-1.5 outline-none',
    style: {
      background: look.fill,
      border: '1px solid var(--ui-stroke-primary)',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.25), 0 1px 3px rgba(0,0,0,0.3)',
      cursor: busy ? 'default' : 'pointer',
      minWidth: 92
    },
    children: [
      jsx('span', {
        className: 'text-[0.8125rem] font-bold uppercase leading-none tracking-[0.12em]',
        style: { color: look.ink },
        children: look.text
      }),
      jsx('span', {
        className: 'mt-0.5 text-[0.4375rem] uppercase leading-none tracking-[0.08em]',
        style: { color: look.ink, opacity: 0.72 },
        children: look.sub
      })
    ]
  })
}

function AmpFace({ rest }) {
  const client = useQueryClient()
  const shown = useOnScreen()
  const state = useQuery({
    queryKey: ['amphead2', 'state'],
    queryFn: () => rest('/state'),
    refetchInterval: shown ? 1500 : false
  })
  const armedNow = !!(state.data && state.data.armed)
  const meter = useQuery({
    queryKey: ['amphead2', 'meter'],
    queryFn: () => rest('/meter'),
    refetchInterval: shown ? meterEvery(armedNow) : false
  })
  const library = useQuery({
    queryKey: ['amphead2', 'library'],
    queryFn: () => rest('/library'),
    staleTime: 20000
  })
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState('')
  const [openPicker, setOpenPicker] = useState('')
  const clipped = useRef(false)

  const s = state.data || {}
  const reading = meter.data || {}
  const peak = Math.max(reading.l || 0, reading.r || 0)
  useEffect(() => {
    if (reading.clip) clipped.current = true
    else if (toDb(peak) < -6) clipped.current = false
  }, [reading.clip, peak])

  const verdict = s.verdict || 'CONNECTING'
  const armed = !!s.armed
  // Only the engine saying it is open counts as open. A state that has not arrived yet, or a
  // route that failed, must leave the face offering SET UP: the safe press is the one that
  // opens a device and stays silent, never the one that arms an amp we cannot see.
  const running = s.running === true
  const tone = s.tone || {}
  const toneName = s.tone_name || [s.capture, s.cab].filter(Boolean).join(' + ') || ''
  const files = library.data || {}

  async function act(path, body) {
    setBusy(true)
    setSaid('')
    try {
      const out = await rest(path, { method: 'POST', body })
      setSaid(String((out && out.output) || 'done').split('\n')[0])
      client.invalidateQueries({ queryKey: ['amphead2'] })
      if (out && out.ok === false) host.notify({ kind: 'warning', message: String(out.output).split('\n')[0] })
      return out
    } catch (err) {
      // A 404 here means the gateway is still running yesterday's plugin code: say so instead of
      // leaving the player staring at a switch that does nothing.
      const missing = err && /404|not found/i.test(String(err.message || ''))
      const line = missing
        ? 'this face is newer than the gateway: restart Hermes once to load the new routes'
        : `failed: ${err && err.message ? err.message : err}`
      setSaid(line)
      host.notify({ kind: 'error', message: line })
    } finally {
      setBusy(false)
    }
  }

  async function turnKnob(name, db) {
    const index = TONE_NAMES.indexOf(name)
    const norm = Math.max(0, Math.min(1, (db + TONE_RANGE) / (TONE_RANGE * 2)))
    try {
      const answer = await rest('/knob', { method: 'POST', body: { param: index, fx: 1, value: norm, mode: 'norm', track: 1 } })
      if (answer && answer.ok === false) setSaid(String(answer.output || 'that knob would not move').split('\n')[0])
      return answer
    } catch (err) {
      setSaid(`that knob would not move: ${err && err.message ? err.message : err}`)
      return { ok: false }
    }
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-2.5',
    children: [
      jsxs('div', {
        style: { ...BOX, padding: 10 },
        className: 'flex flex-col gap-2',
        children: [
          // ---- the top rail: lamp, name, power ----
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Lamp, { verdict }),
              jsxs('div', {
                className: 'min-w-0 flex-1',
                children: [
                  jsxs('div', {
                    className: 'flex items-baseline gap-1.5',
                    children: [
                      jsx('span', {
                        className: 'text-[0.8125rem] font-bold uppercase leading-none tracking-[0.22em]',
                        style: { color: 'var(--ui-text-primary)' },
                        children: 'AmpHead'
                      }),
                      jsx(Engraved, { children: 'engine' })
                    ]
                  }),
                  jsx('div', {
                    className: 'mt-0.5 truncate text-[0.5rem] leading-tight',
                    style: { color: VERDICT_COLOR[verdict] || SECOND.color },
                    children: `${verdict}${s.reason ? ` · ${s.reason}` : ''}`
                  })
                ]
              }),
              jsx(PowerButton, {
                running,
                armed,
                busy,
                onSetup: () => act('/setup', {}),
                onLive: () => act('/live', { on: !armed, master_db: -6 })
              })
            ]
          }),

          // ---- what is loaded, in the player's words ----
          jsxs('div', {
            style: { ...PLATE, padding: '7px 9px' },
            children: [
              jsx(Engraved, { children: 'the tone on the path' }),
              jsx('div', {
                className: 'mt-0.5 text-[0.9375rem] font-semibold leading-tight',
                style: { color: 'var(--ui-text-primary)' },
                children: toneName || 'nothing loaded: pick an amp and a cabinet'
              }),
              jsxs('div', {
                className: 'mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[0.5rem] leading-tight',
                style: MUTED,
                children: [
                  jsx('span', { children: s.capture ? `amp ${s.capture}` : 'no amp' }),
                  jsx('span', { children: s.cab ? `cab ${s.cab}` : 'no cabinet' }),
                  jsx('span', { children: s.samplerate ? `${Math.round(s.samplerate / 100) / 10} kHz` : '' }),
                  jsx('span', { children: s.blocksize ? `${s.blocksize} frames` : '' }),
                  jsx('span', { children: s.latency_ms ? `${Number(s.latency_ms).toFixed(0)} ms` : '' })
                ]
              })
            ]
          }),

          // ---- the knobs, on one plate, the way a head groups them ----
          jsxs('div', {
            style: { ...PLATE, padding: '6px 6px 4px' },
            children: [
              jsxs('div', {
                className: 'flex items-start justify-center gap-1',
                children: [
                  ...TONE_NAMES.map((name) => jsx(ChickenKnob, {
                    label: TONE_LABELS[name],
                    db: Number(tone[name] || 0),
                    min: -TONE_RANGE,
                    max: TONE_RANGE,
                    neutral: 0,
                    busy,
                    disabled: !running,
                    onCommit: (db) => turnKnob(name, db)
                  }, name)),
                  jsx('span', {
                    className: 'mx-1 self-stretch',
                    style: { width: 1, background: 'var(--ui-stroke-secondary)' }
                  }),
                  jsx(ChickenKnob, {
                    label: 'MASTER',
                    db: Number(typeof s.master_db === 'number' ? s.master_db : MASTER_LOW),
                    min: MASTER_LOW,
                    max: MASTER_HIGH,
                    neutral: -6,
                    busy,
                    disabled: !running,
                    onCommit: (db) => act('/master', { db })
                  }, 'master')
                ]
              }),
              jsx('div', {
                className: 'mt-0.5 text-center text-[0.5rem] leading-tight',
                style: MUTED,
                children: running
                  ? 'drag to turn · shift for fine · double-click resets'
                  : 'the engine is off: set up before the knobs answer'
              })
            ]
          }),

          jsx(Meter, { peak, clip: clipped.current || !!reading.clip }),

          // ---- the two choices that make the tone ----
          jsx(Plate, {
            label: 'amp · capture',
            value: s.capture,
            sub: folderOf(files.captures, s.capture_path) || 'the model the engine runs',
            open: openPicker === 'amp',
            onToggle: () => setOpenPicker(openPicker === 'amp' ? '' : 'amp'),
            children: jsx(FilePicker, {
              items: files.captures || [],
              currentPath: s.capture_path,
              busy,
              placeholder: 'narrow the amps: bugera, fender, lead',
              onPick: async (path) => {
                setOpenPicker('')
                await act('/capture', { path })
              }
            })
          }),

          jsx(Plate, {
            label: 'cabinet · impulse',
            value: s.cab,
            sub: folderOf(files.cabs, s.cab_path) || 'the speaker after the amp',
            open: openPicker === 'cab',
            onToggle: () => setOpenPicker(openPicker === 'cab' ? '' : 'cab'),
            children: jsx(FilePicker, {
              items: files.cabs || [],
              currentPath: s.cab_path,
              busy,
              placeholder: 'narrow the cabinets: 212, gov, sm57',
              onPick: async (path) => {
                setOpenPicker('')
                await act('/cab', { path })
              }
            })
          })
        ]
      }),

      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('button', {
            type: 'button',
            disabled: busy,
            onClick: () => act('/panic', {}),
            className: 'rounded-md px-2 py-1 text-[0.5625rem] font-semibold uppercase tracking-[0.1em] outline-none',
            style: {
              background: 'color-mix(in srgb, #e5533d 16%, transparent)',
              border: '1px solid color-mix(in srgb, #e5533d 45%, transparent)',
              color: '#e5533d',
              cursor: busy ? 'default' : 'pointer'
            },
            children: 'silence'
          }),
          jsx('span', {
            className: 'min-w-0 flex-1 truncate text-[0.5625rem] leading-tight',
            style: MUTED,
            children: said || (running ? 'the engine is its own process: no other app is involved' : 'press SET UP to open the engine')
          })
        ]
      })
    ]
  })
}

function AmpPage({ rest, title = 'AmpHead', subtitle = 'the local engine: no other app involved' }) {
  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto',
    children: [
      jsxs('div', {
        className: 'flex items-baseline gap-2 border-b px-4 py-2',
        style: { borderColor: 'var(--ui-stroke-secondary)' },
        children: [
          jsx('span', { className: 'text-sm font-semibold', children: title }),
          jsx('span', { className: 'text-[0.6875rem]', style: MUTED, children: subtitle })
        ]
      }),
      jsx('div', {
        className: 'mx-auto flex w-full max-w-[420px] flex-1 flex-col',
        children: jsx(AmpFace, { rest })
      })
    ]
  })
}

// Every route names the backend that answers, so the pane can never be answered by a rig it did
// not mean (`?engine=` is read per request, not at import).
function engineContext(ctx, engine, areas) {
  const rest = (path, opts) => ctx.rest(`${path}${path.includes('?') ? '&' : '?'}engine=${engine}`, opts)
  const keep = (entry) => !areas || areas.includes(entry.area)
  return {
    ...ctx,
    rest,
    register: (entry) => { if (keep(entry)) ctx.register(entry) },
    registerMany: (entries) => entries.filter(keep).forEach((entry) => ctx.register(entry))
  }
}

export default {
  id: 'amphead',
  name: 'AmpHead',
  register(ctx) {
    const rest = engineContext(ctx, 'amphead2').rest
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/amp2' },
        render: () => jsx(AmpPage, { rest })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 61,
        data: { codicon: 'pulse', label: 'AmpHead', path: '/amp2' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'amphead.open',
          label: 'AmpHead: open the amp',
          keywords: ['amp', 'amphead', 'guitar', 'rig', 'tone', 'capture', 'cabinet'],
          run: () => host.navigate('/amp2')
        }
      }
    ])
    ctx.register({
      id: 'face',
      area: 'panes',
      title: 'amp',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(AmpFace, { rest })
    })
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 120,
      render: () => {
        // The chip is the "is the amp alive" glance from anywhere: one dot and one level, from the
        // same routes the face reads, so the two can never disagree.
        function Chip() {
          const shown = useOnScreen()
          const meter = useQuery({
            queryKey: ['amphead2', 'chip-meter'],
            queryFn: () => rest('/meter'),
            refetchInterval: shown ? 400 : false
          })
          const state = useQuery({
            queryKey: ['amphead2', 'chip-state'],
            queryFn: () => rest('/state'),
            refetchInterval: shown ? 4000 : false
          })
          const peak = Math.max((meter.data && meter.data.l) || 0, (meter.data && meter.data.r) || 0)
          const verdict = (state.data && state.data.verdict) || 'CONNECTING'
          const live = !!(meter.data && meter.data.live)
          const text = live ? `${toDb(peak).toFixed(0)} dB` : CHIP_WORD[verdict] || verdict.toLowerCase()
          return jsxs('span', {
            className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums',
            title: (state.data && state.data.reason) || verdict,
            children: [
              jsx('span', {
                className: 'inline-block h-1.5 w-1.5 rounded-full',
                style: { background: VERDICT_COLOR[verdict] || 'var(--ui-text-quaternary)' }
              }),
              jsx('span', { style: live ? ACCENT : MUTED, children: text })
            ]
          })
        }
        return jsx(Chip, {})
      }
    })
  }
}
