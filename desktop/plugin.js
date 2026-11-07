// AmpHead amp face: the pane that proves the rig is alive while you play.
// Everything here comes from /api/plugins/amphead/state and /meter, which read REAPER directly,
// so the pane carries every number REAPER would show while REAPER itself stays on the taskbar.
//
// The face is laid out like the front of an amp, because that is the thing being controlled: a
// pilot lamp and the verdict, a segmented input meter, the power switch, the signal path across
// the top, the amp's own knobs grouped the way a panel groups them, a footswitch row for tones,
// and a readout strip for the numbers that are not knobs. Every number is labelled where it sits,
// so nothing here needs a tooltip to be read.

import { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const MUTED = { color: 'var(--ui-text-quaternary)' }
const ACCENT = { color: 'var(--ui-accent)' }
const FACE = '#f0e2c8'
const FACE_DIM = 'rgba(240, 226, 200, 0.55)'
const TOLEX = 'linear-gradient(180deg, #1b1714 0%, #12100d 100%)'
const PLATE = 'linear-gradient(180deg, #2b2620 0%, #211d18 100%)'
const WELL = '#0c0a08'

// Amber is the amp being ready, red is something broken, dim cream is the rig standing by. The
// words themselves come from verdict.py, so the pane cannot invent a state the tools do not know.
const VERDICT_COLOR = {
  READY: '#ffb347',
  STANDBY: FACE_DIM,
  'NO SIGNAL': '#ffd08a',
  'NO INPUT': '#ffd08a',
  'NO CHAIN': '#ffd08a',
  CLIPPING: '#ff6b5a',
  'DEVICE CLOSED': '#ff6b5a',
  'REAPER NOT RUNNING': '#ff6b5a',
  'REAPER BUSY': '#ff6b5a',
  'NO REAPER INSTALL': '#ff6b5a',
  'BRIDGE GONE': '#ff6b5a',
  'TWO REAPER': '#ff6b5a',
  'RIG TAB CLOSED': '#ff6b5a',
  'DEVICE UNPLUGGED': '#ff6b5a',
  MUTED: '#ffd08a',
  'REOPENED DEVICE': '#ffd08a',
  CONNECTING: FACE_DIM
}

// A visitor installs the plugin on a machine with no DAW and would otherwise stare at a dead pane.
// These are the verdicts where a demo is the useful next click, not an insult to their setup.
const DEMO_OFFER = new Set([
  'REAPER NOT RUNNING',
  'NO REAPER INSTALL',
  'BRIDGE GONE',
  'REAPER BUSY',
  'TWO REAPER',
  'RIG TAB CLOSED',
  'CONNECTING'
])

const toDb = (peak) => (peak <= 0.000001 ? -60 : Math.max(-60, 20 * Math.log10(peak)))

// A dB number is a claim about a level, so the chip only shows one when the rig is actually live.
// With REAPER closed the meter reads zero and -60 dB looks like a real, quiet rig instead of no
// rig at all, so the chip says the state in words the same way the face does.
const CHIP_WORD = {
  READY: 'quiet',
  STANDBY: 'standby',
  'REAPER NOT RUNNING': 'reaper off',
  'NO REAPER INSTALL': 'no reaper',
  'REAPER BUSY': 'reaper busy',
  'BRIDGE GONE': 'bridge gone',
  'TWO REAPER': 'two reapers',
  'RIG TAB CLOSED': 'no rig tab',
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

// The host exposes the amp's knobs with an "Amplifier " prefix and its own gain knobs without one,
// so "Amplifier Input Gain" and "Input Gain" are two different knobs and must never look alike.
// Grouping them under AMP and HOST makes that readable; the prefix can then be dropped inside the
// amp group without creating a false twin.
const isAmpKnob = (name) => /^amplifier\s/i.test(name || '')
const shortName = (name) => String(name || 'knob').replace(/^amplifier\s+/i, '')
// The plugin's own halves, in the plugin's own order. `section` comes from the backend, which reads
// it off the parameter names the plugin publishes, so the pane groups knobs the way the VST does
// instead of inventing "amp" and "host" from a regex of its own.
function groups(knobs, plugin) {
  const named = plugin && plugin.name ? plugin.name : 'the plugin'
  const pick = (section) => knobs.filter((k) => (k.section || (isAmpKnob(k.name) ? 'amp' : 'other')) === section)
  const amp = pick('amp')
  const pedal = pick('pedal')
  const other = pick('other')
  return [
    amp.length ? { key: 'amp', title: 'amp', caption: `the model in ${named}`, knobs: amp } : null,
    pedal.length ? { key: 'pedal', title: 'pedal', caption: 'in front of the amp', knobs: pedal } : null,
    other.length ? { key: 'other', title: 'plugin', caption: `the rest of ${named}`, knobs: other } : null
  ].filter(Boolean)
}

// A pilot lamp: the one thing on the panel readable from across the room.
function Lamp({ verdict, armed }) {
  const color = VERDICT_COLOR[verdict] || FACE_DIM
  const lit = verdict === 'READY' || verdict === 'CLIPPING'
  return jsx('span', {
    className: 'shrink-0 rounded-full',
    style: {
      width: 13,
      height: 13,
      background: `radial-gradient(circle at 35% 30%, ${color}, ${lit ? color : 'rgba(240,226,200,0.12)'} 70%)`,
      boxShadow: lit ? `0 0 9px ${color}, inset 0 0 3px rgba(0,0,0,0.6)` : 'inset 0 0 3px rgba(0,0,0,0.8)',
      border: '1px solid rgba(0,0,0,0.6)',
      opacity: armed || lit ? 1 : 0.7
    }
  })
}

function Panel({ title, caption, children, className }) {
  return jsxs('div', {
    className: `rounded-md p-2 ${className || ''}`,
    style: { background: PLATE, border: '1px solid rgba(255,255,255,0.06)' },
    children: [
      jsxs('div', {
        className: 'mb-1.5 flex items-baseline justify-between gap-2',
        children: [
          jsx('span', {
            className: 'text-[0.5625rem] font-semibold uppercase tracking-[0.14em]',
            style: { color: FACE, opacity: 0.8 },
            children: title
          }),
          caption
            ? jsx('span', { className: 'text-[0.5625rem]', style: { color: FACE_DIM }, children: caption })
            : null
        ]
      }),
      children
    ]
  })
}

// Segmented meter, like the LED ladder on a hardware amp: the segment count is the reading, and
// the number beside it and the CLIP light are the same reading spelled out.
const SEG_DB = [0, -3, -6, -9, -12, -15, -18, -21, -24, -27, -30, -33, -36, -39, -42, -45, -50, -60]

function Meter({ peak, clip }) {
  const db = toDb(peak)
  const lit = SEG_DB.filter((floor) => db >= floor).length
  return jsxs('div', {
    className: 'rounded-md px-2 py-1.5',
    style: { background: WELL, border: '1px solid rgba(255,255,255,0.05)' },
    children: [
      jsxs('div', {
        className: 'mb-1 flex items-center justify-between',
        children: [
          jsx('span', {
            className: 'text-[0.5625rem] font-semibold uppercase tracking-[0.14em]',
            style: { color: FACE, opacity: 0.8 },
            children: 'input'
          }),
          jsxs('span', {
            className: 'flex items-center gap-1.5',
            children: [
              jsx('span', {
                className: 'rounded-sm px-1 text-[0.5rem] font-semibold uppercase tracking-[0.12em]',
                style: {
                  color: clip ? '#1a1512' : 'rgba(240,226,200,0.25)',
                  background: clip ? '#ff6b5a' : 'rgba(255,255,255,0.06)'
                },
                children: 'clip'
              }),
              jsx('span', {
                className: 'text-[0.625rem] tabular-nums',
                style: { color: clip ? '#ff6b5a' : FACE },
                children: `${db.toFixed(1)} dB`
              })
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'flex gap-[2px]',
        children: SEG_DB.map((floor, i) => {
          const on = i < lit
          const hot = i >= SEG_DB.length - 3
          const warm = i >= SEG_DB.length - 7
          const color = hot ? '#ff6b5a' : warm ? '#ffb347' : '#8fd97a'
          const dark = hot ? 'rgba(255,107,90,0.12)' : warm ? 'rgba(255,179,71,0.12)' : 'rgba(240,226,200,0.09)'
          return jsx('span', {
            key: floor,
            className: 'h-2 flex-1 rounded-[1px]',
            style: { background: on ? color : dark, boxShadow: on && hot ? `0 0 5px ${color}` : 'none' }
          })
        })
      }),
      jsxs('div', {
        className: 'mt-0.5 flex justify-between text-[0.5rem] tabular-nums',
        style: { color: 'rgba(240,226,200,0.35)' },
        children: [
          jsx('span', { children: '-60' }),
          jsx('span', { children: '-24' }),
          jsx('span', { children: '-12' }),
          jsx('span', { children: '0 dB' })
        ]
      })
    ]
  })
}

const MIN_ANGLE = -135
const MAX_ANGLE = 135

// A knob is the amp's knob: drag up or down to turn it, hold shift for a fifth of the travel,
// arrow keys to nudge it. A turn is sent every 80 ms while the finger moves and once more when it
// lifts, and the answer carries the number the plugin reads back in its own units, so the reading
// under the knob is the plugin's number and not a percentage this pane invented.
//
// The poll is held off while a finger is down and for a moment after a turn lands. That is the
// whole bug: the 1.5 s poll used to arrive mid-gesture or straight after release and put the old
// value back, which read as a sticky knob that would not stay where it was put.
const TRAVEL = 130   // pixels for the full range
const SEND_MS = 80

function Knob({ knob, busy, onTurn, size }) {
  const [value, setValue] = useState(typeof knob.norm === 'number' ? knob.norm : null)
  const [shown, setShown] = useState(knob.value)
  const [dragging, setDragging] = useState(false)
  const cur = useRef(typeof knob.norm === 'number' ? knob.norm : 0)
  const finger = useRef(0)
  const sent_at = useRef(0)
  const flight = useRef({ busy: false, queued: null })
  const settled = useRef(0)
  const moved = useRef(false)

  useEffect(() => {
    if (dragging || Date.now() < settled.current) return
    if (typeof knob.norm === 'number') {
      setValue(knob.norm)
      cur.current = knob.norm
    }
    setShown(knob.value)
  }, [knob.norm, knob.value, dragging])

  if (value === null) {
    return jsxs('div', {
      className: 'flex items-baseline justify-between gap-2 py-0.5',
      children: [
        jsx('span', {
          className: 'text-[0.625rem]',
          style: { color: FACE, opacity: 0.85 },
          children: shortName(knob.name)
        }),
        jsx('span', {
          className: 'text-[0.625rem] tabular-nums',
          style: { color: FACE_DIM },
          children: `${knob.value} (no reading)`
        })
      ]
    })
  }

  const diameter = size === 'lg' ? 50 : 42
  const angle = MIN_ANGLE + value * (MAX_ANGLE - MIN_ANGLE)

  // One write at a time per knob: a drag makes more positions than the bridge can answer, and
  // queueing every one would land the knob somewhere the finger never was. The newest wins.
  const push = (next, final) => {
    const order = flight.current
    if (order.busy) {
      order.queued = { next, final }
      return
    }
    order.busy = true
    sent_at.current = Date.now()
    Promise.resolve(onTurn(knob, next, final)).then((answer) => {
      if (answer && answer.ok) {
        settled.current = Date.now() + 1500
        if (answer.value) setShown(answer.value)
        if (final && typeof answer.norm === 'number') {
          cur.current = answer.norm
          setValue(answer.norm)
        }
      }
    }).catch(() => {}).finally(() => {
      order.busy = false
      const queued = order.queued
      order.queued = null
      if (queued) push(queued.next, queued.final)
    })
  }

  // An On/Off parameter is a knob with two places to be, not a switch beside the knobs, and the
  // input socket is a knob with one place per socket. A stepped knob snaps to its nearest stop as
  // the finger moves, so it can never be left at 0.47 with no meaning, and a tap without a drag
  // steps to the next one, which is what a footswitch does.
  const isBool = knob.bool === true
  const steps = isBool ? 2 : (Number(knob.steps) > 1 ? Math.round(Number(knob.steps)) : 0)
  const snap = (value) => (steps > 1 ? Math.round(value * (steps - 1)) / (steps - 1) : value)
  const nextStop = () => (steps > 1 ? ((Math.round(cur.current * (steps - 1)) + 1) % steps) / (steps - 1) : 0)
  const move = (next, final) => {
    const landed = snap(Math.min(1, Math.max(0, next)))
    cur.current = landed
    setValue(landed)
    if (final || Date.now() - sent_at.current >= SEND_MS) push(landed, final)
  }

  return jsxs('div', {
    className: 'flex flex-col items-center gap-1',
    children: [
      jsx('div', {
        role: 'slider',
        tabIndex: 0,
        'aria-label': `${knob.name}, ${knob.value}`,
        'aria-valuemin': 0,
        'aria-valuemax': 1,
        'aria-valuenow': Number(value.toFixed(3)),
        'aria-valuetext': knob.value,
        onPointerDown: (event) => {
          if (busy) return
          event.currentTarget.setPointerCapture(event.pointerId)
          finger.current = event.clientY
          moved.current = false
          setDragging(true)
        },
        onPointerMove: (event) => {
          if (!dragging) return
          // Step from the pixel delta, not from a running total, so pressing shift mid-drag
          // tightens the rest of the turn instead of teleporting the knob.
          const dy = finger.current - event.clientY
          finger.current = event.clientY
          if (dy !== 0) moved.current = true
          move(cur.current + dy / (event.shiftKey ? TRAVEL * 5 : TRAVEL), false)
        },
        onPointerUp: () => {
          if (!dragging) return
          setDragging(false)
          // A tap steps a stepped knob to its next stop; a drag already landed on one, so it writes that.
          if (steps > 1 && !moved.current) {
            move(nextStop(), true)
            return
          }
          move(cur.current, true)
        },
        onPointerCancel: () => {
          if (!dragging) return
          setDragging(false)
          move(cur.current, true)
        },
        onKeyDown: (event) => {
          if (busy) return
          // One stop per press on a stepped knob, a nudge on a continuous one.
          const step = steps > 1 ? 1 / (steps - 1) : (event.shiftKey ? 0.1 : 0.02)
          if (event.key === 'ArrowUp' || event.key === 'ArrowRight') {
            event.preventDefault()
            move(cur.current + step, true)
          } else if (event.key === 'ArrowDown' || event.key === 'ArrowLeft') {
            event.preventDefault()
            move(cur.current - step, true)
          } else if (steps > 1 && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault()
            move(nextStop(), true)
          }
        },
        className: 'relative shrink-0 select-none rounded-full outline-none',
        style: {
          width: diameter,
          height: diameter,
          cursor: busy ? 'default' : 'ns-resize',
          touchAction: 'none',
          background: 'radial-gradient(circle at 36% 28%, #575047 0%, #322c26 45%, #17130f 78%, #0b0908 100%)',
          boxShadow: 'inset 0 -1px 2px rgba(255,255,255,0.18), inset 0 2px 3px rgba(0,0,0,0.7), 0 1px 2px rgba(0,0,0,0.6)',
          border: '1px solid rgba(0,0,0,0.75)'
        },
        children: jsx('span', {
          className: 'absolute inset-0',
          style: { transform: `rotate(${angle}deg)` },
          children: jsx('span', {
            className: 'absolute left-1/2',
            style: {
              top: 4,
              width: 2,
              height: diameter * 0.32,
              marginLeft: -1,
              borderRadius: 1,
              background: dragging ? '#ffb347' : FACE,
              boxShadow: dragging ? '0 0 4px #ffb347' : 'none'
            }
          })
        })
      }),
      jsx('span', {
        className: 'w-full truncate text-center text-[0.5625rem] leading-tight',
        style: { color: FACE, opacity: 0.75 },
        children: shortName(knob.name)
      }),
      jsx('span', {
        className: 'max-w-full truncate text-center text-[0.5rem] tabular-nums',
        style: { color: dragging ? '#ffb347' : FACE_DIM },
        children: shown
      })
    ]
  })
}

function KnobGrid({ group, busy, onTurn, size }) {
  return jsxs('div', {
    // Three across, like a panel: tone stack in one row, the gains under it. A wrapping flex row
    // would put four knobs on a line in the pane and read as an arbitrary heap instead of a panel.
    className: 'grid grid-cols-3 justify-items-center gap-x-1 gap-y-2.5',
    children: group.knobs.map((k) => jsx(Knob, { knob: k, busy, onTurn, size }, k.index ?? k.name))
  })
}

// The signal path as it actually is: input, the plugins in order, the master. Clicking a plugin
// brings it forward in REAPER, which only works while REAPER is on screen, so the row says what it
// will do before it is clicked instead of doing nothing.
function SignalPath({ chain, ui, onFocus }) {
  const [note, setNote] = useState('')
  const clickable = ui === 'visible'
  const arrow = jsx('span', {
    className: 'text-[0.625rem]',
    style: { color: 'rgba(240,226,200,0.3)' },
    children: '›'
  })
  return jsxs('div', {
    className: 'rounded-md px-2 py-1.5',
    style: { background: WELL, border: '1px solid rgba(255,255,255,0.05)' },
    children: [
      jsx('div', {
        className: 'mb-1 text-[0.5625rem] font-semibold uppercase tracking-[0.14em]',
        style: { color: FACE, opacity: 0.8 },
        children: 'signal path'
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-1',
        children: [
          jsx('span', {
            className: 'rounded-sm px-1 py-0.5 text-[0.5625rem] font-semibold uppercase tracking-[0.1em]',
            style: { color: FACE_DIM, background: 'rgba(255,255,255,0.05)' },
            children: 'in'
          }),
          ...(chain.length === 0
            ? [
                arrow,
                jsx('span', {
                  className: 'text-[0.625rem]',
                  style: { color: FACE_DIM },
                  children: 'nothing loaded on the guitar track'
                }, 'empty')
              ]
            : chain.map((fx, i) => jsxs('span', {
              key: `${i}-${fx.name}`,
              className: 'flex items-center gap-1',
              children: [
                arrow,
                jsxs('button', {
                  type: 'button',
                  onClick: async () => {
                    if (!clickable) {
                      setNote('un-minimize REAPER first, then this click brings the plugin forward')
                      return
                    }
                    setNote((await onFocus(i)) || '')
                  },
                  className: 'rounded-sm px-1 py-0.5 text-[0.625rem]',
                  style: {
                    color: fx.enabled ? FACE : 'rgba(240,226,200,0.45)',
                    background: fx.enabled ? 'rgba(255,179,71,0.14)' : 'rgba(255,255,255,0.05)',
                    border: `1px solid ${fx.enabled ? 'rgba(255,179,71,0.45)' : 'rgba(255,255,255,0.07)'}`,
                    cursor: clickable ? 'pointer' : 'default',
                    textDecoration: fx.enabled ? 'none' : 'line-through'
                  },
                  children: [
                    jsx('span', { className: 'mr-1 tabular-nums', style: { opacity: 0.7 }, children: `${i + 1}` }),
                    jsx('span', {
                      children: String(fx.name || '').replace(/^VST3:\s*/, '').replace(/\s*\(.*\)$/, '')
                    }),
                    jsx('span', {
                      className: 'ml-1 uppercase',
                      style: { opacity: 0.7, fontSize: '0.5rem' },
                      children: fx.enabled ? 'on' : 'byp'
                    })
                  ]
                })
              ]
            }))),
          arrow,
          jsx('span', {
            className: 'rounded-sm px-1 py-0.5 text-[0.5625rem] font-semibold uppercase tracking-[0.1em]',
            style: { color: FACE_DIM, background: 'rgba(255,255,255,0.05)' },
            children: 'out'
          })
        ]
      }),
      note
        ? jsx('div', { className: 'mt-1 text-[0.5625rem] leading-snug', style: { color: FACE_DIM }, children: note })
        : null
    ]
  })
}

// LIVE is the amp's power switch: on means armed, monitoring, and the master out of the parked
// floor. The caption changes with the direction of the action, so the switch explains itself.
function PowerSwitch({ armed, busy, onToggle }) {
  return jsxs('button', {
    type: 'button',
    disabled: busy,
    onClick: onToggle,
    className: 'flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 disabled:opacity-50',
    style: {
      background: armed ? 'rgba(255,179,71,0.14)' : 'rgba(255,255,255,0.05)',
      border: `1px solid ${armed ? 'rgba(255,179,71,0.5)' : 'rgba(255,255,255,0.09)'}`
    },
    children: [
      jsxs('span', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', {
            className: 'relative inline-block rounded-full',
            style: {
              width: 30,
              height: 16,
              background: armed ? '#ffb347' : 'rgba(240,226,200,0.18)',
              transition: 'background 120ms'
            },
            children: jsx('span', {
              className: 'absolute rounded-full',
              style: {
                top: 2,
                left: armed ? 16 : 2,
                width: 12,
                height: 12,
                background: armed ? '#1a1512' : '#8a8074',
                transition: 'left 120ms'
              }
            })
          }),
          jsx('span', {
            className: 'text-[0.6875rem] font-semibold uppercase tracking-[0.14em]',
            style: { color: armed ? '#ffb347' : FACE, opacity: armed ? 1 : 0.8 },
            children: armed ? 'live' : 'standby'
          })
        ]
      }),
      jsx('span', {
        className: 'max-w-[62%] text-right text-[0.5625rem] leading-tight',
        style: { color: FACE_DIM },
        children: busy ? 'working' : armed ? 'armed, monitor on, master -18 dB' : 'click to arm and play'
      })
    ]
  })
}

function PanicButton({ silenced, busy, onClick }) {
  return jsxs('button', {
    type: 'button',
    disabled: busy,
    onClick,
    className: 'flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 disabled:opacity-50',
    style: {
      background: silenced ? 'rgba(255,255,255,0.05)' : 'rgba(255,107,90,0.16)',
      border: `1px solid ${silenced ? 'rgba(255,255,255,0.09)' : 'rgba(255,107,90,0.5)'}`
    },
    children: [
      jsx('span', {
        className: 'text-[0.625rem] font-semibold uppercase tracking-[0.14em]',
        style: { color: silenced ? FACE : '#ff6b5a' },
        children: silenced ? 'restore' : 'panic'
      }),
      jsx('span', {
        className: 'max-w-[66%] text-right text-[0.5625rem] leading-tight',
        style: { color: FACE_DIM },
        children: busy
          ? 'working'
          : silenced
            ? 'un-bypass the chain, master back to -18 dB'
            : 'silence the rig now, nothing else changes'
      })
    ]
  })
}

// The demo rig is a canned session: same face, same judge, no hardware. It is labelled while it
// runs, because a moving meter that is not the player's own rig is a lie unless it says so.
function DemoRow({ demo, busy, onToggle }) {
  return jsxs('div', {
    className: 'mt-1 flex items-center justify-between gap-2 rounded px-1.5 py-1',
    style: {
      background: demo ? 'rgba(255,179,71,0.12)' : 'rgba(255,255,255,0.03)',
      border: `1px solid ${demo ? 'rgba(255,179,71,0.4)' : 'rgba(255,255,255,0.08)'}`
    },
    children: [
      jsx('span', {
        className: 'text-[0.5rem] uppercase tracking-[0.14em]',
        style: { color: demo ? '#ffb347' : FACE_DIM },
        children: demo ? 'demo rig, not your hardware' : 'no amp to play with here?'
      }),
      jsx('button', {
        type: 'button',
        disabled: busy,
        onClick: onToggle,
        className: 'shrink-0 rounded px-1.5 py-0.5 text-[0.5rem] font-semibold uppercase tracking-[0.12em] disabled:opacity-50',
        style: { color: demo ? FACE_DIM : '#ffb347', background: 'rgba(0,0,0,0.28)' },
        children: busy ? '...' : demo ? 'leave demo' : 'see it work'
      })
    ]
  })
}

// Tones are footswitches: the key is on the switch, and the description of the tone that was last
// pressed is printed under the row, in place, instead of hiding in a tooltip.
function ToneRow({ presets, busy, onPick }) {
  const [picked, setPicked] = useState('')
  return jsxs('div', {
    className: 'rounded-md p-2',
    style: { background: PLATE, border: '1px solid rgba(255,255,255,0.06)' },
    children: [
      jsx('div', {
        className: 'mb-1.5 text-[0.5625rem] font-semibold uppercase tracking-[0.14em]',
        style: { color: FACE, opacity: 0.8 },
        children: 'tones'
      }),
      jsx('div', {
        className: 'flex flex-wrap gap-1.5',
        children: presets.length === 0
          ? jsx('span', {
            className: 'text-[0.625rem]',
            style: { color: FACE_DIM },
            children: 'no presets for this rig yet'
          })
          : presets.map((p) => jsx('button', {
            key: p.key,
            type: 'button',
            disabled: busy,
            onClick: () => {
              setPicked(p.title || p.key)
              onPick(p.key)
            },
            className: 'rounded-sm px-2 py-1 text-[0.625rem] uppercase tracking-[0.1em] disabled:opacity-50',
            style: {
              color: picked === (p.title || p.key) ? '#1a1512' : FACE,
              background: picked === (p.title || p.key) ? '#ffb347' : 'rgba(255,255,255,0.06)',
              border: '1px solid rgba(255,255,255,0.12)'
            },
            children: p.key
          }, p.key))
      }),
      jsx('div', {
        className: 'mt-1 text-[0.5625rem] leading-snug',
        style: { color: FACE_DIM },
        children: picked || 'a tone sets every knob at once, in one undo step'
      })
    ]
  })
}

// The strip is for the numbers that are not knobs. Each one is labelled, so it reads as a row of
// facts about the rig rather than as an unlabelled status bar.
function Strip({ device, master, project }) {
  const cells = [
    ['device', device.closed ? 'closed' : `${device.buffer || '?'} smp`],
    ['rate', device.closed ? '—' : `${device.rate || '?'} Hz`],
    ['out', device.closed ? '—' : `${device.out_ms || 0} ms`],
    ['master', master === null || master === undefined ? 'unknown' : `${master} dB`]
  ]
  return jsxs('div', {
    className: 'rounded-md px-2 py-1.5',
    style: { background: WELL, border: '1px solid rgba(255,255,255,0.05)' },
    children: [
      jsx('div', {
        className: 'flex justify-between gap-1',
        children: cells.map(([label, value]) => jsxs('span', {
          key: label,
          className: 'flex flex-col items-center',
          children: [
            jsx('span', {
              className: 'text-[0.5rem] uppercase tracking-[0.1em]',
              style: { color: 'rgba(240,226,200,0.35)' },
              children: label
            }),
            jsx('span', {
              className: 'text-[0.625rem] tabular-nums',
              style: { color: FACE },
              children: value
            })
          ]
        }))
      }),
      jsx('div', {
        className: 'mt-1 truncate border-t pt-1 text-center text-[0.5rem]',
        style: { color: 'rgba(240,226,200,0.35)', borderColor: 'rgba(255,255,255,0.06)' },
        children: `project ${project || 'unnamed'}`
      })
    ]
  })
}

function AmpFace({ rest }) {
  const client = useQueryClient()
  const state = useQuery({ queryKey: ['amphead', 'state'], queryFn: () => rest('/state'), refetchInterval: 1500 })
  const meter = useQuery({ queryKey: ['amphead', 'meter'], queryFn: () => rest('/meter'), refetchInterval: 150 })
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState('')
  const clipped = useRef(false)

  const s = state.data || {}
  const reading = meter.data || {}
  const peak = Math.max(reading.l || 0, reading.r || 0)
  useEffect(() => {
    // Latch the clip light like hardware: it stays lit until the input has been quiet for a moment.
    if (reading.clip) clipped.current = true
    else if (toDb(peak) < -6) clipped.current = false
  }, [reading.clip, peak])
  const clip = clipped.current || !!reading.clip

  const verdict = s.verdict || 'CONNECTING'
  const armed = !!s.armed
  const device = s.device || {}
  const chain = s.chain || []
  const silenced = chain.length > 0 && chain.every((fx) => !fx.enabled)
  const problems = s.problems || []
  const plugin = s.plugin || {}
  const [ampGroup, pedalGroup, hostGroup] = groups(s.knobs || [], plugin)

  // The socket is not a plugin parameter, but it is the control that decides whether anything
  // reaches the amp at all, so it is a knob like the rest: one stop per socket on the interface,
  // labelled with the interface's own name for it, and it never pretends to be a track number.
  const socket = s.input || {}
  const socketKnob = socket.count > 0
    ? { name: 'input', value: socket.name || socket.value || 'unknown',
        norm: typeof socket.norm === 'number' ? socket.norm : 0, steps: socket.count }
    : null

  // A knob turn is its own path: cheap, answered in the plugin's units, and deliberately not
  // wrapped in `act`, because `act` sets `busy` for the whole face and refetches the state. That
  // was the other half of the sticky knob: releasing one knob greyed out every other control.
  async function turnKnob(knob, value, final) {
    try {
      const answer = await rest('/knob', { method: 'POST', body: { param: knob.index, value, mode: 'norm' } })
      if (answer && answer.ok === false) {
        setSaid(String(answer.output || 'that knob would not move').split('\n')[0])
      } else if (final) {
        client.invalidateQueries({ queryKey: ['amphead', 'state'] })
      }
      return answer
    } catch (err) {
      const line = `that knob would not move: ${err && err.message ? err.message : err}`
      setSaid(line)
      return { ok: false, output: line }
    }
  }

  // A socket turn is its own route: the plugin's knobs cannot move the record input, and the
  // record input cannot be written as a parameter, so this is a knob with its own way to REAPER.
  async function turnInput(knob, value, final) {
    const count = socket.count || 0
    const number = Math.max(1, Math.min(count, Math.round(value * (count - 1)) + 1))
    try {
      const answer = await rest('/input', { method: 'POST', body: { input: number } })
      if (answer && answer.ok === false) {
        setSaid(String(answer.output || 'that socket would not take'))
      } else if (final) {
        setSaid(String((answer && answer.output) || `track 1 records from ${answer.name}`))
        client.invalidateQueries({ queryKey: ['amphead', 'state'] })
      }
      return answer
    } catch (err) {
      const line = `that socket would not take: ${err && err.message ? err.message : err}`
      setSaid(line)
      return { ok: false, output: line }
    }
  }

  async function act(path, body) {
    setBusy(true)
    setSaid('')
    try {
      const out = await rest(path, { method: 'POST', body })
      setSaid(String(out && out.output ? out.output : 'done').split('\n')[0])
      client.invalidateQueries({ queryKey: ['amphead'] })
      if (out && out.ok === false) host.notify({ kind: 'warning', message: String(out.output).split('\n')[0] })
      return out
    } catch (err) {
      const line = `failed: ${err && err.message ? err.message : err}`
      setSaid(line)
      host.notify({ kind: 'error', message: line })
    } finally {
      setBusy(false)
    }
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 overflow-y-auto p-2.5',
    style: { background: TOLEX, borderTop: '2px solid rgba(255,179,71,0.18)' },
    children: [
      // Verdict block: lamp, the word, then the reason in the verdict's own sentence.
      jsxs('div', {
        className: 'rounded-md px-2.5 py-2',
        style: { background: PLATE, border: '1px solid rgba(255,255,255,0.07)' },
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Lamp, { verdict, armed }),
              jsx('span', {
                className: 'text-[0.9375rem] font-semibold uppercase leading-none tracking-[0.1em]',
                style: { color: VERDICT_COLOR[verdict] || FACE },
                children: verdict
              })
            ]
          }),
          jsx('div', {
            className: 'mt-1 text-[0.625rem] leading-snug',
            style: { color: FACE_DIM },
            children: s.reason || 'reading the rig'
          }),
          // The reason is one sentence; problems are the separate things wrong at the same time, so
          // they get their own lines instead of being crammed into that sentence.
          problems.length
            ? jsx('ul', {
              className: 'mt-1 space-y-0.5',
              children: problems.map((p, i) => jsxs('li', {
                className: 'text-[0.5625rem] leading-snug',
                style: { color: VERDICT_COLOR[verdict] || FACE_DIM, opacity: 0.85 },
                children: ['– ', p]
              }, i))
            })
            : null,
          s.demo || DEMO_OFFER.has(verdict)
            ? jsx(DemoRow, { demo: !!s.demo, busy, onToggle: () => act('/demo', { on: !s.demo }) })
            : null
        ]
      }),

      jsx(Meter, { peak, clip }),

      jsxs('div', {
        className: 'flex flex-col gap-1.5',
        children: [
          jsx(PowerSwitch, {
            armed,
            busy,
            onToggle: () => act('/live', { on: !armed, master_db: -18 })
          }),
          jsx(PanicButton, {
            silenced,
            busy,
            onClick: () => act('/panic', silenced ? { restore: true, master_db: -18 } : {})
          })
        ]
      }),

      jsx(SignalPath, {
        chain,
        ui: s.ui,
        onFocus: async (which) => {
          const answer = await act('/focus', { fx: which })
          return answer && answer.output
        }
      }),

      socketKnob
        ? jsx(Panel, {
          title: 'input',
          // Said in place, because the numbers are the thing that gets confused: a track is a lane
          // in the project, an input is a socket on the interface, and they share no arithmetic.
          caption: `the socket on ${device.driver || 'the interface'}, not the track number`,
          children: jsxs('div', {
            className: 'flex flex-col items-center gap-1',
            children: [
              jsx(Knob, { knob: socketKnob, busy, size: 'lg', onTurn: turnInput }),
              jsx('div', {
                className: 'text-[0.5625rem] leading-snug',
                style: { color: FACE_DIM },
                children: `track ${socket.track || 1} records from ${socket.name || socket.value} ` +
                          `of ${socket.count} socket${socket.count === 1 ? '' : 's'}`
              })
            ]
          })
        })
        : null,

      ampGroup
        ? jsx(Panel, {
          title: ampGroup.title,
          caption: ampGroup.caption,
          children: jsx(KnobGrid, {
            group: ampGroup,
            busy,
            size: 'lg',
            onTurn: turnKnob
          })
        })
        : jsx(Panel, {
          title: 'amp',
          caption: plugin.name ? `${plugin.name}, no model loaded` : 'not loaded',
          children: jsx('div', {
            className: 'text-[0.625rem]',
            style: { color: FACE_DIM },
            children: 'no amp in the chain yet'
          })
        }),

      pedalGroup
        ? jsx(Panel, {
          title: pedalGroup.title,
          caption: pedalGroup.caption,
          children: jsx(KnobGrid, {
            group: pedalGroup,
            busy,
            onTurn: turnKnob
          })
        })
        : null,

      hostGroup
        ? jsx(Panel, {
          title: hostGroup.title,
          caption: hostGroup.caption,
          children: jsx(KnobGrid, {
            group: hostGroup,
            busy,
            onTurn: turnKnob
          })
        })
        : null,

      jsx(ToneRow, {
        presets: s.presets || [],
        busy,
        onPick: (key) => act('/preset', { query: key })
      }),

      jsx(Strip, { device, master: s.master_db, project: s.project }),

      said
        ? jsx('div', { className: 'text-[0.5625rem] leading-snug', style: { color: FACE_DIM }, children: said })
        : null,
      jsx('div', {
        className: 'mt-auto text-center text-[0.5rem] leading-snug',
        style: { color: 'rgba(240,226,200,0.3)' },
        children: 'REAPER stays on the taskbar'
      })
    ]
  })
}

// The page is the reliable door: a sidebar row and a route render in the workspace like any
// built-in view, so the amp is one click away and needs no pane dragging to be discovered.
function AmpPage({ rest }) {
  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto',
    children: [
      jsxs('div', {
        className: 'flex items-baseline gap-2 border-b px-4 py-2',
        style: { borderColor: 'var(--ui-stroke-secondary)' },
        children: [
          jsx('span', { className: 'text-sm font-semibold', children: 'AmpHead' }),
          jsx('span', { className: 'text-[0.6875rem]', style: MUTED, children: 'the rig, without opening REAPER' })
        ]
      }),
      jsx('div', {
        className: 'mx-auto flex w-full max-w-[420px] flex-1 flex-col',
        children: jsx(AmpFace, { rest })
      })
    ]
  })
}

export default {
  id: 'amphead',
  name: 'AmpHead',
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/amphead' },
        render: () => jsx(AmpPage, { rest: ctx.rest })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'pulse', label: 'Amp', path: '/amphead' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'amphead.open',
          label: 'AmpHead: open the amp',
          keywords: ['amp', 'amphead', 'guitar', 'reaper', 'rig', 'tone'],
          run: () => host.navigate('/amphead')
        }
      }
    ])
    ctx.register({
      id: 'amp-face',
      area: 'panes',
      title: 'amp',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(AmpFace, { rest: ctx.rest })
    })
    ctx.register({
      id: 'amp-chip',
      area: 'statusBar.right',
      order: 120,
      render: () => {
        // The chip is the "is the amp alive" glance from any pane: a verdict dot plus the input level,
        // both from the same routes the page reads, so it cannot disagree with them.
        function Chip() {
          const meter = useQuery({
            queryKey: ['amphead', 'chip-meter'],
            queryFn: () => ctx.rest('/meter'),
            refetchInterval: 400
          })
          const state = useQuery({
            queryKey: ['amphead', 'chip-state'],
            queryFn: () => ctx.rest('/state'),
            refetchInterval: 4000
          })
          const peak = Math.max((meter.data && meter.data.l) || 0, (meter.data && meter.data.r) || 0)
          const verdict = (state.data && state.data.verdict) || 'CONNECTING'
          // A number only when there is something to number: with no rig, the meter reads zero and
          // "0 dB" or "-60 dB" would read as a quiet rig rather than no rig. The words carry it.
          const live = !!(meter.data && meter.data.live)
          const text = state.data && state.data.demo
            ? 'demo'
            : live
              ? `${toDb(peak).toFixed(0)} dB`
              : CHIP_WORD[verdict] || verdict.toLowerCase()
          return jsxs('span', {
            className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums',
            title: (state.data && state.data.reason) || verdict,
            children: [
              jsx('span', {
                className: 'inline-block h-1.5 w-1.5 rounded-full',
                style: { background: VERDICT_COLOR[verdict] || 'var(--ui-text-quaternary)' }
              }),
              jsx('span', {
                style: live ? ACCENT : MUTED,
                children: text
              })
            ]
          })
        }
        return jsx(Chip, {})
      }
    })
  }
}
