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

// Seconds as a player says them, so a take's length reads the same in the strip and in a sentence.
const clock = (secs) => {
  const whole = Math.max(0, Math.round(Number(secs) || 0))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

const TONE_NAMES = ['bass', 'mid', 'treble']
const TONE_LABELS = { bass: 'BASS', mid: 'MID', treble: 'TREBLE' }
const TONE_RANGE = 12 // ±12 dB, same as the tone stack the engine runs
const MASTER_LOW = -60
const MASTER_HIGH = 12
// Trim either side of the amp. +/-24 dB is the range the engine accepts: enough to make a clean
// DI drive a lead capture, and enough to sit a loud cabinet beside a quiet one.
const GAIN_LOW = -24
const GAIN_HIGH = 24
// Noise gate in front of the amp. The bottom of the range means off, and off is the default:
// a gate that arrives switched on eats the quiet parts of the first take.
const GATE_LOW = -80
const GATE_HIGH = -20

const toDb = (peak) => (peak <= 0.000001 ? -60 : Math.max(-60, 20 * Math.log10(peak)))
const dbWords = (db) => `${db > 0 ? '+' : ''}${Number(db).toFixed(1)} dB`

// Amber when the amp is ready, red when something is broken, dim when it is only standing by.
const VERDICT_COLOR = {
  READY: 'var(--ui-accent)',
  STANDBY: 'var(--ui-text-quaternary)',
  PARKED: 'var(--ui-text-quaternary)',
  'NO SIGNAL': 'var(--ui-accent)',
  'NO INPUT': 'var(--ui-accent)',
  'NO CHAIN': 'var(--ui-accent)',
  CLIPPING: '#e5533d',
  GLITCH: '#e5533d',
  TUNING: 'var(--ui-accent)',
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
  PARKED: 'parked itself',
  'ENGINE OFF': 'engine off',
  'DEVICE CLOSED': 'device closed',
  'DEVICE UNPLUGGED': 'unplugged',
  MUTED: 'muted',
  'NO INPUT': 'no input',
  'NO CHAIN': 'no chain',
  'NO SIGNAL': 'no signal',
  CLIPPING: 'clipping',
  GLITCH: 'glitching',
  TUNING: 'calibrating',
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

  // A fixed cell, so six knobs sit in known places instead of reflowing as their readouts
  // change width. Without it "TREBLE" and a "-6.0 dB" push their neighbours around.
  return jsxs('div', {
    className: 'flex select-none flex-col items-center gap-1',
    style: { width: size + 18 },
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

// The last segment of a path, for a sentence that has to fit on a 300px plate.
const leafOf = (path) => String(path || '').split(/[\\/]/).filter(Boolean).pop() || ''

// What the pane says in place after a call. A handler that answers in two lines puts the bad
// news second ("capture=X | apply=True" then "not loaded: ..."), and taking the first line
// showed a cheerful sentence over a call that did nothing.
const FAILED_LINE = /^(REFUSED|NOT|CANNOT|ERROR|NO )/
const headline = (out) => {
  const lines = String((out && (out.output || out.summary)) || '').split('\n').map(s => s.trim()).filter(Boolean)
  if (!lines.length) return out && out.ok === false ? 'that did not work' : 'done'
  if (out && out.ok === false) {
    const bad = lines.find((line) => FAILED_LINE.test(line.toUpperCase().split(':')[0]))
    return bad || lines[lines.length - 1]
  }
  return lines[0]
}

// A cabinet or an amp: a search box and the files under it. 600 captures is a list nobody scrolls,
// so typing narrows it, and the folder under each name tells two close relatives apart.
//
// When the list is empty the plate does not scan anything: it says which folders it read and offers
// one press that searches the usual places. The search is local file names, but it is still somebody
// else's disk, so it waits to be asked. The same press writes the folder it finds, which is how a
// library that lives somewhere unusual gets adopted without an environment variable.
function FilePicker({ items, currentPath, busy, onPick, placeholder, kind, rest, roots, onAdopted, onClose }) {
  const [needle, setNeedle] = useState('')
  const [hunt, setHunt] = useState(null)
  const [why, setWhy] = useState('')
  const [note, setNote] = useState('')
  const words = needle.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const matches = (items || []).filter((file) => {
    if (!words.length) return true
    const hay = `${file.name} ${file.folder}`.toLowerCase()
    return words.every((word) => hay.includes(word))
  })
  const shown = matches
  const capture = kind !== 'cab'
  const rows = (hunt && hunt.rows) || []

  async function searchFolders() {
    setWhy('')
    setHunt({ rows: null })
    try {
      const found = await rest('/library/find')
      setHunt({ rows: ((found && found.candidates) || []).slice(0, 8) })
    } catch (err) {
      setHunt({ rows: [] })
      setWhy(`the search failed: ${(err && err.message) || 'no answer'}`)
    }
  }

  async function useFolder(path) {
    setWhy('')
    try {
      const out = await rest('/library/adopt', {
        method: 'POST',
        body: { path, kind: capture ? 'capture' : 'cab' }
      })
      if (out && out.ok === false) {
        setWhy(out.output || 'that folder was refused')
        return
      }
      setHunt(null)
      setNeedle('')
      // Saying which folder is now being read is the whole confirmation: the list filling is the
      // proof, and a toast would have moved on before anybody looked at it.
      setNote(`now reading ${leafOf(path)}`)
      if (onAdopted) onAdopted()
    } catch (err) {
      setWhy(`that folder was not added: ${(err && err.message) || 'no answer'}`)
    }
  }

  const hunting = hunt !== null
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      hunting ? null : jsx('input', {
        type: 'search',
        value: needle,
        placeholder,
        'aria-label': placeholder,
        autoFocus: true,
        onChange: (event) => setNeedle(event.target.value),
        onKeyDown: (event) => {
          // Finding a capture should be click, type, Enter. The list already puts the best
          // match first, so Enter takes it and Escape gives the pane back without a reach.
          if (event.key === 'Escape') {
            event.preventDefault()
            setNeedle('')
            if (onClose) onClose()
            return
          }
          if (event.key === 'Enter' && shown.length && !busy) {
            event.preventDefault()
            onPick(shown[0].path)
          }
        },
        className: 'w-full rounded-md px-2 py-1 text-[0.6875rem] outline-none',
        style: { ...WELL, color: 'var(--ui-text-primary)' }
      }),
      hunting ? null : (note
        ? jsx('div', {
          className: 'text-[0.5rem] leading-tight',
          style: { color: 'var(--ui-accent)' },
          children: note
        })
        : null),
      hunting ? null : jsxs('div', {
        className: 'text-[0.5rem] leading-tight',
        style: MUTED,
        children: `${matches.length} matched${matches.length > 8 ? ' · scroll or type to narrow' : ''}`
      }),
      jsx('div', {
        className: 'flex flex-col overflow-y-auto rounded-md',
        // The height is capped in style, not only by a utility class, so the list can never
        // grow into the face and push the knobs away. Every match lives here and scrolls inside
        // this box; the old 60 row cut hid files that the search could still find.
        style: {
          ...WELL,
          maxHeight: 'clamp(120px, 32vh, 220px)',
          overflowY: 'auto',
          overscrollBehavior: 'contain',
          scrollbarGutter: 'stable'
        },
        children: hunting
          ? (rows.length
            ? rows.map((row) => jsxs('button', {
              type: 'button',
              key: row.path,
              onClick: () => useFolder(row.path),
              className: 'flex flex-col items-start gap-0 px-2 py-1 text-left outline-none',
              style: { background: 'transparent' },
              children: [
                jsx('span', {
                  className: 'w-full truncate text-[0.6875rem] leading-tight',
                  style: { color: 'var(--ui-text-primary)' },
                  children: `${leafOf(row.path)} · ${capture ? `${row.captures} amp${row.captures === 1 ? '' : 's'}` : `${row.cabs} cab${row.cabs === 1 ? '' : 's'}`}`
                }),
                jsx('span', {
                  className: 'w-full truncate text-[0.5rem] leading-tight',
                  style: MUTED,
                  children: row.path
                })
              ]
            }, row.path))
            : jsxs('div', {
              className: 'flex flex-col items-start gap-1 px-2 py-1.5',
              children: [
                jsx('span', {
                  className: 'text-[0.5625rem] leading-tight',
                  style: MUTED,
                  children: hunt && hunt.rows === null
                    ? 'reading the usual folders...'
                    : why || 'nothing found: name the folder in chat and I will set it'
                }),
                jsx('button', {
                  type: 'button',
                  onClick: () => { setHunt(null); setWhy('') },
                  className: 'text-[0.5625rem] font-semibold underline outline-none',
                  style: { color: 'var(--ui-text-tertiary)' },
                  children: 'back'
                })
              ]
            }))
          : (shown.length
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
            : jsxs('div', {
              className: 'flex flex-col items-start gap-1 px-2 py-1.5',
              children: [
                jsx('span', {
                  className: 'text-[0.5625rem] leading-tight',
                  style: MUTED,
                  children: needle
                    ? 'nothing in the folders matches that'
                    : (roots && roots.length
                      ? `nothing in ${roots.map(leafOf).join(', ')}`
                      : 'no folder to read yet')
                }),
                jsx('button', {
                  type: 'button',
                  disabled: busy,
                  onClick: searchFolders,
                  className: 'rounded px-1.5 py-0.5 text-[0.5625rem] font-semibold outline-none',
                  style: {
                    background: 'color-mix(in srgb, var(--ui-accent) 12%, transparent)',
                    color: 'var(--ui-accent)'
                  },
                  children: capture ? 'search my folders for amps' : 'search my folders for cabs'
                })
              ]
            }))
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
function PowerButton({ running, armed, busy, tuning, onSetup, onLive }) {
  const state = tuning ? 'tuning' : !running ? 'setup' : armed ? 'live' : 'standby'
  const look = {
    setup: { text: 'SET UP', sub: 'open the engine', fill: 'var(--ui-accent)', ink: 'var(--ui-bg-primary)' },
    standby: { text: 'GO LIVE', sub: 'opens at the knob', fill: 'var(--ui-bg-quaternary)', ink: 'var(--ui-text-primary)' },
    live: { text: 'LIVE', sub: 'press to stand by', fill: '#e5533d', ink: '#fff' },
    tuning: { text: 'CALIBRATING', sub: 'measuring buffers', fill: 'var(--ui-bg-quaternary)', ink: 'var(--ui-text-primary)' }
  }[state]
  // A lamp beside the words, the way a real front panel has one. It is the only part of the switch
  // that is allowed to glow, and it glows only when the amp is actually making sound.
  const lamp = state === 'live' ? '#fff' : 'rgba(0,0,0,0.35)'
  return jsxs('button', {
    type: 'button',
    disabled: busy,
    onClick: state === 'setup' ? onSetup : onLive,
    'aria-label': `${look.text}: ${look.sub}`,
    className: 'relative flex shrink-0 flex-col items-center rounded-md px-3 py-1.5 outline-none',
    style: {
      // domed, not flat: light on the top lip, shadow under the bottom one
      background:
        `linear-gradient(180deg, color-mix(in srgb, ${look.fill} 86%, #fff) 0%,` +
        ` ${look.fill} 52%,` +
        ` color-mix(in srgb, ${look.fill} 84%, rgba(0,0,0,1)) 100%)`,
      border: '1px solid color-mix(in srgb, var(--ui-stroke-primary) 70%, rgba(0,0,0,1))',
      boxShadow:
        'inset 0 1px 0 rgba(255,255,255,0.32), inset 0 -2px 3px rgba(0,0,0,0.35),' +
        ' 0 2px 5px rgba(0,0,0,0.45)',
      cursor: busy ? 'default' : 'pointer',
      minWidth: 92,
      textShadow: '0 1px 0 rgba(0,0,0,0.3)'
    },
    children: [
      jsx('span', {
        style: {
          position: 'absolute',
          right: 5,
          top: 5,
          width: 4,
          height: 4,
          borderRadius: '50%',
          background: lamp,
          boxShadow: state === 'live' ? '0 0 6px 1px rgba(255,255,255,0.8)' : 'inset 0 1px 1px rgba(0,0,0,0.6)'
        }
      }),
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

// ---- the part that makes it read as an amp head and not a control panel ----
//
// A metal badge with the script name on it, over a woven grille cloth, with a rank of output valves
// standing on the floor of the window. Stacked, never overlapped: an earlier pass drew the plate
// across the middle of the valves and the glass read as dark squares, which is the sort of thing a
// screenshot catches and a unit test does not.
//
// The valves only heat while the engine is open AND armed. A dark amp is an amp with no power; a
// face that glowed on a closed device would be lying about the one thing the eye can check. Their
// brightness follows the meter, so they answer the strings and not just the switch.
const GRILLE = {
  position: 'relative',
  overflow: 'hidden',
  height: 112,
  borderRadius: 8,
  border: '1px solid color-mix(in srgb, var(--ui-text-primary) 20%, transparent)',
  // woven cloth: two diagonal weaves over a dark ground, then a warm wash from the valves below
  background:
    'radial-gradient(115% 85% at 50% 120%, color-mix(in srgb, var(--ui-accent) 16%, transparent) 0%, transparent 64%),' +
    'repeating-linear-gradient(45deg, rgba(0,0,0,0.42) 0 1px, transparent 1px 3px),' +
    'repeating-linear-gradient(-45deg, rgba(255,255,255,0.075) 0 1px, transparent 1px 3px),' +
    'linear-gradient(180deg, var(--ui-bg-secondary) 0%, var(--ui-bg-primary) 100%)',
  boxShadow:
    'inset 0 1px 0 color-mix(in srgb, var(--ui-text-primary) 12%, transparent),' +
    ' inset 0 -2px 12px rgba(0,0,0,0.45)'
}

// Two slow loops, not a strobe: valves breathe, they do not blink.
const FACE_CSS = `
@keyframes amphead-heat { 0%, 100% { opacity: 0.84 } 37% { opacity: 1 } 62% { opacity: 0.9 } }
`

// One of the four screws that hold the grille baffle on. Physicality by the cheapest honest means:
// a recessed slot and a highlight on one edge.
function Screw({ style }) {
  return jsx('div', {
    style: {
      position: 'absolute',
      width: 7,
      height: 7,
      borderRadius: '50%',
      border: '1px solid color-mix(in srgb, var(--ui-text-primary) 26%, transparent)',
      background: 'radial-gradient(circle at 34% 30%, var(--ui-bg-quaternary) 0%, var(--ui-bg-primary) 70%)',
      boxShadow: 'inset 0 1px 1px rgba(255,255,255,0.14), 0 1px 1px rgba(0,0,0,0.5)',
      ...style
    }
  })
}

// A valve, drawn rather than photographed, in five parts: halo, glass, specular edge, plate, mica,
// filament. The halo is not decoration: on a 300px pane a bare 3-pixel filament reads as a scratch.
function Valve({ heat, delay }) {
  const warm = heat > 0
  const glow = warm ? 0.4 + heat * 0.6 : 0
  return jsxs('div', {
    className: 'relative shrink-0',
    style: { width: 22, height: 42, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center' },
    children: [
      // bloom around the glass
      jsx('div', {
        style: {
          position: 'absolute',
          left: '50%',
          bottom: 6,
          width: 30,
          height: 34,
          marginLeft: -15,
          borderRadius: '50%',
          background: 'radial-gradient(circle, color-mix(in srgb, var(--ui-accent) 60%, transparent) 0%, transparent 70%)',
          filter: 'blur(4px)',
          opacity: glow,
          transition: 'opacity 700ms ease',
          pointerEvents: 'none'
        }
      }),
      jsxs('div', {
        style: {
          position: 'relative',
          width: 22,
          height: 31,
          borderRadius: '11px 11px 3px 3px',
          overflow: 'hidden',
          border: `1px solid ${warm ? 'color-mix(in srgb, var(--ui-accent) 50%, transparent)' : 'color-mix(in srgb, var(--ui-text-primary) 30%, transparent)'}`,
          background: warm
            ? 'linear-gradient(180deg, color-mix(in srgb, var(--ui-accent) 20%, var(--ui-bg-primary)) 0%,' +
              ' color-mix(in srgb, var(--ui-accent) 9%, var(--ui-bg-primary)) 100%)'
            : 'linear-gradient(180deg, color-mix(in srgb, var(--ui-text-primary) 14%, transparent) 0%,' +
              ' color-mix(in srgb, var(--ui-text-primary) 5%, transparent) 100%)',
          boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.2), inset 0 -3px 6px rgba(0,0,0,0.35)',
          transition: 'background 700ms ease, border-color 700ms ease'
        },
        children: [
          // the anode plates either side of the filament
          jsx('div', { style: { position: 'absolute', left: 3, top: '22%', width: 3, height: '46%', borderRadius: 1, background: 'rgba(0,0,0,0.34)' } }),
          jsx('div', { style: { position: 'absolute', right: 3, top: '22%', width: 3, height: '46%', borderRadius: 1, background: 'rgba(0,0,0,0.34)' } }),
          // the mica disc, a hairline of light across the top
          jsx('div', { style: { position: 'absolute', left: 2, right: 2, top: '15%', height: 1, background: 'rgba(255,255,255,0.2)' } }),
          // specular edge down the left of the glass
          jsx('div', {
            style: {
              position: 'absolute',
              left: 2,
              top: 3,
              width: 2,
              height: '76%',
              borderRadius: 3,
              background: 'linear-gradient(180deg, rgba(255,255,255,0.30) 0%, rgba(255,255,255,0.04) 100%)',
              opacity: 0.5
            }
          }),
          // the filament itself
          jsx('div', {
            style: {
              position: 'absolute',
              left: '50%',
              top: 6,
              bottom: 5,
              width: 3,
              marginLeft: -1.5,
              borderRadius: 2,
              background: warm ? 'var(--ui-accent)' : 'color-mix(in srgb, var(--ui-text-primary) 18%, transparent)',
              boxShadow: warm ? '0 0 10px 3px color-mix(in srgb, var(--ui-accent) 75%, transparent)' : 'none',
              animation: warm ? 'amphead-heat 2.8s ease-in-out infinite' : 'none',
              animationDelay: `${delay}s`
            }
          })
        ]
      }),
      // bakelite socket
      jsx('div', {
        style: {
          width: 17,
          height: 8,
          marginTop: -2,
          borderRadius: '1px 1px 3px 3px',
          border: '1px solid var(--ui-stroke-primary)',
          background: 'linear-gradient(180deg, var(--ui-bg-quaternary) 0%, var(--ui-bg-primary) 100%)',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.10)'
        }
      })
    ]
  })
}

// The window: badge plate up top, valves along the floor, screws at the corners. Nothing here is a
// control, so nothing here is clickable.
function Grille({ lit, heat }) {
  const level = lit ? Math.max(0.32, Math.min(1, heat)) : 0
  return jsxs('div', {
    style: GRILLE,
    children: [
      // the plate the script is stamped into: brushed metal, lit from above
      jsx('div', {
        style: {
          position: 'absolute',
          left: '50%',
          top: 8,
          width: '86%',
          height: 46,
          transform: 'translateX(-50%)',
          borderRadius: 4,
          border: '1px solid color-mix(in srgb, var(--ui-text-primary) 30%, transparent)',
          background:
            'linear-gradient(180deg, color-mix(in srgb, var(--ui-text-primary) 34%, var(--ui-bg-secondary)) 0%,' +
            ' var(--ui-bg-secondary) 42%,' +
            ' color-mix(in srgb, var(--ui-bg-primary) 90%, var(--ui-accent) 10%) 100%)',
          boxShadow: lit
            ? '0 0 20px -4px color-mix(in srgb, var(--ui-accent) 45%, transparent),' +
              ' inset 0 1px 0 color-mix(in srgb, var(--ui-text-primary) 30%, transparent),' +
              ' inset 0 -1px 0 rgba(0,0,0,0.55)'
            : 'inset 0 1px 0 color-mix(in srgb, var(--ui-text-primary) 22%, transparent), inset 0 -1px 0 rgba(0,0,0,0.5)'
        }
      }),
      jsxs('div', {
        style: { position: 'absolute', left: 0, right: 0, top: 13, textAlign: 'center', pointerEvents: 'none' },
        children: [
          jsx('span', {
            style: {
              display: 'block',
              fontFamily: '"Segoe Script", "Brush Script MT", "Snell Roundhand", "Lucida Handwriting", cursive',
              fontWeight: 700,
              fontSize: 26,
              lineHeight: 1.0,
              letterSpacing: '-0.01em',
              color: 'color-mix(in srgb, var(--ui-text-primary) 88%, var(--ui-accent) 12%)',
              // stamped, not printed: a dark lip under the stroke and a thin light one above
              textShadow: lit
                ? '0 1px 0 rgba(0,0,0,0.6), 0 -1px 0 rgba(255,255,255,0.10), 0 0 14px color-mix(in srgb, var(--ui-accent) 45%, transparent)'
                : '0 1px 0 rgba(0,0,0,0.55), 0 -1px 0 rgba(255,255,255,0.08)',
              transition: 'text-shadow 700ms ease'
            },
            children: 'Amphead'
          }),
          jsx('span', {
            className: 'mt-0.5 block text-[0.4375rem] font-semibold uppercase',
            style: { letterSpacing: '0.34em', color: 'var(--ui-text-tertiary)' },
            children: 'valve · 100'
          })
        ]
      }),
      // the rank of valves, standing on the bottom lip
      jsx('div', {
        style: {
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 4,
          padding: '0 12px',
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          opacity: lit ? 0.62 + level * 0.38 : 0.28,
          transition: 'opacity 700ms ease'
        },
        children: [0, 1, 2, 3, 4, 5].map((i) => jsx(Valve, { heat: level, delay: i * 0.34 }, `valve-${i}`))
      }),
      jsx(Screw, { style: { left: 5, top: 5 } }),
      jsx(Screw, { style: { right: 5, top: 5 } }),
      jsx(Screw, { style: { left: 5, bottom: 5 } }),
      jsx(Screw, { style: { right: 5, bottom: 5 } })
    ]
  })
}

// The take strip: one press records, and a take that already exists is a file the player can hear
// again and hand to anything else. The audio goes through the app's own `hermes-media://` scheme
// with Range support, so a 24-bit wav seeks like any player and never rides through the JSON API.
// The path is offered as a plain copy because the next thing a player does with a riff is paste it
// somewhere: into chat, into a DAW, into a script.
const mediaUrl = (path) => `hermes-media://stream/${encodeURIComponent(path)}`

function TakeStrip({ take, recording, busy, running, onRecord, onSay }) {
  const audio = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [copied, setCopied] = useState('')
  const saved = !recording && !!take.path

  useEffect(() => {
    setPlaying(false)
    if (audio.current) audio.current.pause()
  }, [take.path])

  function toggle() {
    const el = audio.current
    if (!el) return
    if (!el.paused) {
      el.pause()
      return
    }
    const started = el.play()
    if (started && started.catch) started.catch(() => onSay('cannot play the take here'))
  }

  async function copy(what) {
    try {
      await navigator.clipboard.writeText(what)
    } catch (_) {
      // A renderer without the async clipboard still has the old selection copy.
      const box = document.createElement('textarea')
      box.value = what
      document.body.appendChild(box)
      box.select()
      try { document.execCommand('copy') } finally { box.remove() }
    }
    setCopied('path copied')
    setTimeout(() => setCopied(''), 1800)
  }

  const line = recording
    ? `recording ${clock(take.seconds)} · what you hear is what is written`
    : saved
      ? `${clock(take.seconds)} · last take saved`
      : 'rec writes what you hear to a wav'

  const tiny = 'flex shrink-0 items-center rounded px-1.5 py-1 text-[0.5rem] font-semibold uppercase tracking-[0.08em]'

  return jsxs('div', {
    className: 'rounded-md px-2 py-1.5',
    style: { ...WELL },
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsxs('button', {
            type: 'button',
            disabled: busy || !running,
            onClick: onRecord,
            className: 'flex shrink-0 items-center gap-1.5 rounded px-2 py-1 text-[0.5625rem] font-bold uppercase tracking-[0.1em]',
            style: {
              color: recording ? '#fff' : 'var(--ui-text-secondary)',
              background: recording ? '#e5533d' : 'var(--ui-bg-quaternary)',
              border: '1px solid var(--ui-stroke-secondary)',
              opacity: running ? 1 : 0.4,
              cursor: running && !busy ? 'pointer' : 'default'
            },
            children: [
              jsx('span', {
                className: 'inline-block h-1.5 w-1.5 rounded-full',
                style: { background: recording ? '#fff' : '#e5533d' }
              }),
              jsx('span', { children: recording ? 'stop' : 'rec' })
            ]
          }),
          jsx('span', {
            className: 'min-w-0 flex-1 truncate text-[0.5rem] leading-tight',
            style: recording ? SECOND : MUTED,
            children: line
          })
        ]
      }),
      saved && jsxs('div', {
        className: 'mt-1.5 flex items-center gap-1.5',
        children: [
          jsx('audio', {
            ref: audio,
            src: mediaUrl(take.path),
            preload: 'metadata',
            onPlay: () => setPlaying(true),
            onPause: () => setPlaying(false),
            onEnded: () => setPlaying(false),
            style: { display: 'none' }
          }),
          jsx('button', {
            type: 'button',
            onClick: toggle,
            'aria-label': playing ? 'stop the take' : 'play the take',
            className: 'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.5rem]',
            style: {
              color: 'var(--ui-text-secondary)',
              background: 'var(--ui-bg-quaternary)',
              border: '1px solid var(--ui-stroke-secondary)',
              cursor: 'pointer'
            },
            children: playing ? '❚❚' : '▶'
          }),
          jsx('span', {
            className: 'min-w-0 flex-1 truncate font-mono text-[0.5rem] leading-tight',
            title: take.path,
            style: MUTED,
            children: take.name || take.path
          }),
          jsx('button', {
            type: 'button',
            onClick: () => copy(take.path),
            className: tiny,
            style: {
              color: copied ? 'var(--ui-accent)' : 'var(--ui-text-secondary)',
              background: 'var(--ui-bg-quaternary)',
              border: '1px solid var(--ui-stroke-secondary)',
              cursor: 'pointer'
            },
            children: copied || 'copy path'
          })
        ]
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
  const cabinet = useQuery({
    queryKey: ['amphead2', 'rigs'],
    queryFn: () => rest('/rigs'),
    staleTime: 5000
  })
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState('')
  const [rigName, setRigName] = useState('')
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
  // Calibration used to be called TUNING, which now belongs to the guitar tuner. The engine's
  // state word stays TUNING for compatibility; the face speaks the new name.
  const verdictText = verdict === 'TUNING' ? 'CALIBRATING' : verdict
  const armed = !!s.armed
  // How hot the valves look: the meter squashed to 0..1 over the top 40 dB, so quiet playing still
  // lights them and the very bottom of the range does not.
  const heat = Math.max(0, Math.min(1, (toDb(peak) + 40) / 40))
  // Only the engine saying it is open counts as open. A state that has not arrived yet, or a
  // route that failed, must leave the face offering SET UP: the safe press is the one that
  // opens a device and stays silent, never the one that arms an amp we cannot see.
  const running = s.running === true
  const tuning = s.tuning || {}
  const tuningNow = !!tuning.running
  // A tune stops the device on purpose. The page stays alive, but the knobs and the chain are
  // not measured yet, so every control waits instead of sending a verb the engine will refuse.
  const locked = busy || tuningNow
  const tone = s.tone || {}
  const toneName = s.tone_name || [s.capture, s.cab].filter(Boolean).join(' + ') || ''
  // An aim is a claim about a file, never about a record. The colour states how strong it is, so
  // "family" cannot be read as "this is the band".
  const aimConfidence = String(s.aim_confidence || '')
  const aimColor = aimConfidence === 'exact'
    ? 'var(--ui-accent)'
    : aimConfidence === 'family' ? SECOND.color : MUTED.color
  // A capture trained at one rate and played at another is not silence: it is the wrong tone,
  // and the fix lives in the interface's own panel. Say it where the capture is named.
  const mismatch = s.rate_mismatch || null
  const mismatchText = s.notice || (mismatch && mismatch.capture_hz && mismatch.stream_hz
    ? `${mismatch.capture_hz / 1000} kHz capture in a ${mismatch.stream_hz / 1000} kHz engine`
    : '')
  const files = library.data || {}
  // A take is the one thing here that leaves the amp as a file, so the face says where it went
  // and how long it is, in place. No tooltip: a player watching the light needs the sentence.
  const take = s.take || {}
  const recording = !!take.recording

  async function act(path, body) {
    setBusy(true)
    setSaid('')
    try {
      const out = await rest(path, { method: 'POST', body })
      setSaid(headline(out))
      client.invalidateQueries({ queryKey: ['amphead2'] })
      if (out && out.ok === false) host.notify({ kind: 'warning', message: headline(out) })
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
      if (answer && answer.ok === false) setSaid(headline(answer) || 'that knob would not move')
      return answer
    } catch (err) {
      setSaid(`that knob would not move: ${err && err.message ? err.message : err}`)
      return { ok: false }
    }
  }

  const lit = running && armed
  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-2.5',
    children: [
      jsx('style', { children: FACE_CSS }),
      jsxs('div', {
        style: {
          ...BOX,
          padding: 10,
          // an open, armed amp gets a warm edge; a closed one stays a plain box
          boxShadow: lit
            ? `${BOX.boxShadow}, 0 0 26px -8px color-mix(in srgb, var(--ui-accent) 55%, transparent)`
            : BOX.boxShadow
        },
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
                    children: `${verdictText}${s.reason ? ` · ${s.reason}` : ''}`
                  }),
                  // The diagnosis is the reason; this is the control. One imperative, on the
                  // face, so the player never has to translate "master parked" into a gesture.
                  s.next && verdict !== 'READY' && verdict !== 'CONNECTING'
                    ? jsx('div', {
                      className: 'mt-0.5 truncate text-[0.5rem] uppercase leading-tight tracking-[0.14em]',
                      style: { color: 'var(--ui-text-quaternary)' },
                      children: `next · ${s.next}`
                    })
                    : null
                ]
              }),
              jsx(PowerButton, {
                running,
                armed,
                busy: locked,
                tuning: tuningNow,
                onSetup: () => act('/setup', {}),
                // Open at the level the knob is showing, never at a fixed one: the knob
                // starts on the floor, so the amp cannot come up loud on the first press.
                onLive: () => act('/live', {
                  on: !armed,
                  master_db: Number(typeof s.master_db === 'number' ? s.master_db : MASTER_LOW)
                })
              })
            ]
          }),

          // ---- the front of a head: valves behind a grille, the script across them ----
          jsx(Grille, { lit, heat }),

          // ---- the engine is up: say so in words, and give the way back down ----
          // Setup opens the device and away it stays. Without this row the switch could only be
          // pressed once: the pane read GO LIVE for ever and a player looking for SET UP found a
          // face that seemed to have forgotten the control it started with.
          running
            ? jsxs('div', {
              className: 'flex items-center justify-between gap-2 rounded-md px-2 py-1',
              style: {
                background: 'color-mix(in srgb, var(--ui-text-primary) 4%, transparent)',
                border: '1px solid var(--ui-stroke-secondary)'
              },
              children: [
                jsx('span', {
                  className: 'text-[0.5rem] uppercase leading-none tracking-[0.1em]',
                  style: MUTED,
                  children: armed
                    ? 'engine open · live'
                    : `engine open · silent · master ${s.master_db === null || s.master_db === undefined ? '-' : Number(s.master_db).toFixed(0)} dB`
                }),
                jsx('button', {
                  type: 'button',
                  disabled: locked,
                  onClick: () => act('/teardown', {}),
                  className: 'shrink-0 text-[0.5rem] uppercase leading-none tracking-[0.1em] underline-offset-2 hover:underline',
                  // a real ghost button: the app's button defaults would paint a white box here
                  style: {
                    color: SECOND.color,
                    cursor: locked ? 'default' : 'pointer',
                    background: 'transparent',
                    border: 0,
                    padding: 0,
                    font: 'inherit'
                  },
                  children: 'close engine'
                })
              ]
            })
            : null,

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
                  // Round trip is the number to show: the driver buffer plus the block. The
                  // old line printed the driver half only, which made a slower build look fast.
                  jsx('span', {
                    children: (s.round_trip_ms || s.latency_ms)
                      ? `round trip ${Number(s.round_trip_ms || s.latency_ms).toFixed(1)} ms`
                      : ''
                  }),
                  // Measured, not guessed: the same probe the CLI runs, on whatever capture and
                  // cabinet are loaded, so a rig that got heavier can find its own block size.
                  // A real button, not another underlined word: this one changes the device.
                  jsx('button', {
                    type: 'button',
                    disabled: locked || !running,
                    onClick: () => act('/tune', {}),
                    title: running
                      ? 'Calibrate: measures buffer sizes, picks the smallest one that never misses, and reopens the amp by itself.'
                      : 'Calibrate: open the engine first.',
                    'aria-label': 'Calibrate buffer and round trip',
                    className: 'shrink-0 text-[0.5rem] uppercase leading-none tracking-[0.1em]',
                    style: {
                      color: SECOND.color,
                      cursor: locked || !running ? 'default' : 'pointer',
                      background: 'color-mix(in srgb, var(--ui-text-primary) 6%, transparent)',
                      border: '1px solid var(--ui-stroke-secondary)',
                      borderRadius: 4,
                      padding: '3px 5px',
                      font: 'inherit',
                      opacity: locked || !running ? 0.5 : 1
                    },
                    children: tuningNow ? 'measuring' : 'calibrate'
                  })
                ]
              }),
              // A running tune names its own phase and closed its own device, so the face must
              // say so in place. Without this line the switch reading TUNING is the only clue.
              tuningNow
                ? jsx('div', {
                  className: 'mt-1 text-[0.5rem] leading-tight',
                  style: MUTED,
                  children: `calibrating · ${tuning.phase || 'starting'} · ${Number(tuning.seconds || 0).toFixed(0)}s · the amp reopens by itself`
                })
                : null,
              // What the agent was asked to sound like, and how far the library actually got. The
              // line is the tool's own words, so the face cannot overstate the match.
              s.aim
                ? jsxs('div', {
                  className: 'mt-1 flex items-baseline gap-1.5 border-t pt-1',
                  style: { borderColor: 'var(--ui-stroke-secondary)' },
                  children: [
                    jsx('span', {
                      className: 'shrink-0 text-[0.4375rem] font-bold uppercase leading-none tracking-[0.12em]',
                      style: { color: aimColor },
                      children: aimConfidence || 'aim'
                    }),
                    jsx('span', {
                      className: 'truncate text-[0.5rem] leading-tight',
                      style: MUTED,
                      children: s.aim
                    })
                  ]
                })
                : null,
              mismatchText
                ? jsxs('div', {
                  className: 'mt-1 flex items-baseline gap-1.5 border-t pt-1',
                  style: { borderColor: 'var(--ui-stroke-secondary)' },
                  children: [
                    jsx('span', {
                      className: 'shrink-0 text-[0.4375rem] font-bold uppercase leading-none tracking-[0.12em]',
                      style: { color: '#e5533d' },
                      children: 'rate'
                    }),
                    jsx('span', {
                      className: 'truncate text-[0.5rem] leading-tight',
                      style: MUTED,
                      children: mismatchText
                    })
                  ]
                })
                : null
            ]
          }),

          // ---- the knobs, on one plate, the way a head groups them ----
          jsxs('div', {
            style: { ...PLATE, padding: '7px 6px 5px' },
            children: [
              // Six knobs in a 3 x 2 grid, in the order the signal travels: in, the amp's own
              // tone, then out and the room. A grid and not two flex rows, because six does not
              // divide four-and-two: the treble used to sit alone on a line of its own, which
              // read as a mistake in a 300px pane.
              jsx('div', {
                style: {
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  justifyItems: 'center',
                  rowGap: 6,
                  columnGap: 2
                },
                children: [
                  jsx(ChickenKnob, {
                    label: 'GATE',
                    size: 40,
                    // A threshold, not a level: the knob shows the point the amp starts
                    // hearing you, so the number is the one a player sets against hiss.
                    db: Number(typeof s.gate_db === 'number' ? s.gate_db : GATE_LOW),
                    min: GATE_LOW,
                    max: GATE_HIGH,
                    neutral: GATE_LOW,
                    locked,
                    disabled: !running,
                    onCommit: (db) => act('/gate', { db })
                  }, 'gate'),
                  jsx(ChickenKnob, {
                    label: 'IN',
                    size: 40,
                    db: Number(typeof s.input_gain_db === 'number' ? s.input_gain_db : 0),
                    min: GAIN_LOW,
                    max: GAIN_HIGH,
                    neutral: 0,
                    locked,
                    disabled: !running,
                    onCommit: (db) => act('/input_gain', { db })
                  }, 'in'),
                  ...TONE_NAMES.map((name) => jsx(ChickenKnob, {
                    label: TONE_LABELS[name],
                    size: 40,
                    db: Number(tone[name] || 0),
                    min: -TONE_RANGE,
                    max: TONE_RANGE,
                    neutral: 0,
                    locked,
                    disabled: !running,
                    onCommit: (db) => turnKnob(name, db)
                  }, name)),
                  jsx(ChickenKnob, {
                    label: 'OUT',
                    size: 40,
                    db: Number(typeof s.output_gain_db === 'number' ? s.output_gain_db : 0),
                    min: GAIN_LOW,
                    max: GAIN_HIGH,
                    neutral: 0,
                    locked,
                    disabled: !running,
                    onCommit: (db) => act('/output_gain', { db })
                  }, 'out'),
                  jsx(ChickenKnob, {
                    label: 'MASTER',
                    size: 40,
                    db: Number(typeof s.master_db === 'number' ? s.master_db : MASTER_LOW),
                    min: MASTER_LOW,
                    max: MASTER_HIGH,
                    neutral: -6,
                    locked,
                    disabled: !running,
                    onCommit: (db) => act('/master', { db })
                  }, 'master')
                ]
              }),
              jsx('div', {
                className: 'mt-1 text-center text-[0.5rem] leading-tight',
                style: MUTED,
                children: running
                  ? 'in drives the amp · out balances the cab · master is the room · drag, shift for fine'
                  : 'the engine is off: set up before the knobs answer'
              })
            ]
          }),

          // ---- the take, right under the knobs ----
          jsx(TakeStrip, {
            take,
            recording,
            locked,
            running,
            onRecord: () => act('/record', { on: !recording }),
            onSay: setSaid
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
              locked,
              kind: 'capture',
              rest,
              roots: files.capture_dirs || [],
              onAdopted: () => client.invalidateQueries({ queryKey: ['amphead2', 'library'] }),
              placeholder: 'narrow the amps: bugera, fender, lead',
              onClose: () => setOpenPicker(''),
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
              locked,
              kind: 'cab',
              rest,
              roots: files.cab_dirs || [],
              onAdopted: () => client.invalidateQueries({ queryKey: ['amphead2', 'library'] }),
              placeholder: 'narrow the cabinets: 212, gov, sm57',
              onClose: () => setOpenPicker(''),
              onPick: async (path) => {
                setOpenPicker('')
                await act('/cab', { path })
              }
            })
          }),

          // ---- the sounds that are worth keeping ----
          // A rig is eight fields and a name, so it gets a row of chips rather than a menu:
          // the press that matters is "put my sound back", and it should be one press.
          jsxs('div', {
            className: 'flex flex-wrap items-center gap-1.5 rounded-md px-2 py-1.5',
            style: { ...WELL },
            children: [
              jsx('span', {
                className: 'text-[0.5rem] uppercase tracking-[0.1em]',
                style: MUTED,
                children: 'rigs'
              }),
              (cabinet.data && cabinet.data.rigs && cabinet.data.rigs.length)
                ? cabinet.data.rigs.map((row) => jsx('button', {
                    type: 'button',
                    key: row.name,
                    disabled: locked,
                    title: `${row.summary}

${row.line}`,
                    onClick: () => act('/rig', { action: 'load', name: row.name }),
                    className: 'rounded px-1.5 py-0.5 text-[0.5625rem] font-semibold outline-none',
                    style: {
                      background: 'color-mix(in srgb, var(--ui-accent) 12%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--ui-accent) 35%, transparent)',
                      color: 'var(--ui-accent)',
                      cursor: locked ? 'default' : 'pointer'
                    },
                    children: row.name
                  }, row.name))
                : jsx('span', {
                    className: 'text-[0.5rem]',
                    style: MUTED,
                    children: running ? 'none yet: dial a sound in and name it' : 'none yet'
                  }),
              jsx('input', {
                type: 'text',
                value: rigName,
                placeholder: 'save as',
                'aria-label': 'name this sound',
                onChange: (event) => setRigName(event.target.value),
                onKeyDown: (event) => {
                  if (event.key === 'Enter' && rigName.trim()) act('/rig', { action: 'save', name: rigName })
                },
                className: 'ml-auto w-24 rounded px-1.5 py-0.5 text-[0.5625rem] outline-none',
                style: { ...WELL, color: 'var(--ui-text-primary)' }
              }),
              jsx('button', {
                type: 'button',
                disabled: locked || !rigName.trim(),
                onClick: () => act('/rig', { action: 'save', name: rigName }),
                className: 'rounded px-1.5 py-0.5 text-[0.5625rem] font-semibold uppercase tracking-[0.1em] outline-none',
                style: {
                  background: rigName.trim() ? 'color-mix(in srgb, var(--ui-accent) 18%, transparent)' : 'transparent',
                  border: '1px solid color-mix(in srgb, var(--ui-accent) 35%, transparent)',
                  color: rigName.trim() ? 'var(--ui-accent)' : MUTED.color,
                  cursor: locked || !rigName.trim() ? 'default' : 'pointer'
                },
                children: 'save'
              })
            ]
          })
        ]
      }),

      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('button', {
            type: 'button',
            disabled: locked,
            onClick: () => act('/panic', {}),
            className: 'rounded-md px-2 py-1 text-[0.5625rem] font-semibold uppercase tracking-[0.1em] outline-none',
            style: {
              background: 'color-mix(in srgb, #e5533d 16%, transparent)',
              border: '1px solid color-mix(in srgb, #e5533d 45%, transparent)',
              color: '#e5533d',
              cursor: locked ? 'default' : 'pointer'
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
