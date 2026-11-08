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
  'ENGINE OFF': FACE_DIM,
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
  'ENGINE OFF': 'engine off',
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

// The volume the player actually hears. Boot and Live both leave the master at the floor, so this
// is the control that makes the rig audible, and it says so before it is touched: a sliver of
// movement on a hot amp model is already loud, so the caption asks for steps, not a yank.
function MasterRamp({ db, busy, armed, onCommit, ceiling = 0 }) {
  const top = Number.isFinite(ceiling) ? ceiling : 0
  const [value, setValue] = useState(typeof db === 'number' ? db : -60)
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    if (!dragging && typeof db === 'number') setValue(db)
  }, [db, dragging])

  // A drag can end two ways (mouse up, key up) and the refetch in between can hand back a reading
  // that is not a number. Every screen and every commit uses `level`, so neither gesture can post a
  // NaN at the rig and turn the master into a value nothing can read.
  const level = Number.isFinite(value) ? Math.max(-60, Math.min(top, value)) : -60
  const silent = level <= -60
  const commit = () => {
    setDragging(false)
    onCommit(Math.round(level))
  }

  return jsxs('div', {
    className: 'rounded-md px-2 py-1.5',
    style: { background: WELL, border: '1px solid rgba(255,255,255,0.05)' },
    children: [
      jsxs('div', {
        className: 'mb-0.5 flex items-baseline justify-between',
        children: [
          jsx('span', {
            className: 'text-[0.5625rem] font-semibold uppercase tracking-[0.14em]',
            style: { color: FACE, opacity: 0.8 },
            children: 'volume'
          }),
          jsx('span', {
            className: 'text-[0.625rem] tabular-nums',
            style: { color: silent ? FACE_DIM : FACE },
            children: dragging ? `${Math.round(level)} dB` : silent ? 'silent' : `${Math.round(level)} dB`
          })
        ]
      }),
      jsx('input', {
        type: 'range',
        min: -60,
        max: top,
        step: 1,
        value: level,
        disabled: busy || !armed,
        'aria-label': 'master volume',
        onPointerDown: () => setDragging(true),
        onPointerUp: commit,
        onKeyUp: commit,
        onChange: (event) => setValue(Number(event.target.value)),
        className: 'w-full',
        style: { accentColor: 'var(--ui-accent)' }
      }),
      jsx('div', {
        className: 'text-[0.5rem] leading-snug',
        style: { color: FACE_DIM },
        children: armed
          ? 'starts silent, on purpose: drag up in steps and listen'
          : 'arm the rig first, then ramp the volume here'
      })
    ]
  })
}

// Segmented meter, like the LED ladder on a hardware amp: the segment count is the reading, and
// the number beside it and the CLIP light are the same reading spelled out.
const SEG_DB = [0, -3, -6, -9, -12, -15, -18, -21, -24, -27, -30, -33, -36, -39, -42, -45, -50, -60]

function Meter({ peak, clip, live }) {
  const db = toDb(peak)
  const hot = !!clip && live
  const lit = live ? SEG_DB.filter((floor) => db >= floor).length : 0
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
                  color: hot ? '#1a1512' : 'rgba(240,226,200,0.25)',
                  background: hot ? '#ff6b5a' : 'rgba(255,255,255,0.06)'
                },
                children: 'clip'
              }),
              jsx('span', {
                className: 'text-[0.625rem] tabular-nums',
                style: { color: hot ? '#ff6b5a' : FACE },
                children: live ? `${db.toFixed(1)} dB` : 'not measuring'
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

  // A knob with a handful of places to be, like the input socket on a three-input interface,
  // snaps to its nearest stop as the finger moves so it can never be left at 0.47 with no meaning,
  // and a tap without a drag steps to the next one. A parameter with exactly two positions is not
  // a knob at all: it is a Switch, because two states read better as a lever than as an angle.
  const steps = Number(knob.steps) > 1 ? Math.round(Number(knob.steps)) : 0
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

// A parameter with two positions is a lever, not a knob with two stops. A switch says which of two
// states it is in from across the pane: the cap sits at the top for on, at the bottom for off, and
// the word under it is the reading the plugin itself gives. A range belongs on a knob.
function Switch({ knob, busy, onTurn }) {
  const [on, setOn] = useState(knob.norm >= 0.5)
  const [shown, setShown] = useState(knob.value)
  const [working, setWorking] = useState(false)
  const settled = useRef(0)

  // The face follows the rig, but not while a flip is in flight or has just landed: a poll that
  // arrives mid-write would drag the cap back under the finger.
  useEffect(() => {
    if (working || Date.now() < settled.current) return
    setOn(knob.norm >= 0.5)
    setShown(knob.value)
  }, [knob.norm, knob.value, working])

  const flip = async () => {
    if (busy || working) return
    const before = on
    const next = before ? 0 : 1
    // Move first, write second: a switch that waits for REAPER feels broken, and a refused write
    // puts the cap back where it was instead of leaving a lie on the panel.
    setOn(next === 1)
    setWorking(true)
    try {
      const answer = await onTurn(knob, next, true)
      if (answer && answer.ok) {
        settled.current = Date.now() + 1500
        if (answer.value) setShown(answer.value)
        if (typeof answer.norm === 'number') setOn(answer.norm >= 0.5)
      } else {
        setOn(before)
        if (answer && answer.output) setShown(String(answer.output))
      }
    } catch (err) {
      setOn(before)
      setShown(`no answer: ${err && err.message ? err.message : err}`)
    } finally {
      setWorking(false)
    }
  }

  return jsxs('div', {
    className: 'flex flex-col items-center gap-1',
    children: [
      jsx('button', {
        type: 'button',
        role: 'switch',
        'aria-checked': on,
        'aria-label': `${knob.name}, ${shown}`,
        disabled: busy || working,
        onClick: flip,
        onKeyDown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            flip()
          }
        },
        className: 'relative shrink-0 rounded-full outline-none disabled:opacity-60',
        style: {
          width: 28,
          height: 46,
          cursor: busy ? 'default' : 'pointer',
          background: 'linear-gradient(180deg, #100d0a 0%, #1d1915 100%)',
          border: '1px solid rgba(0,0,0,0.8)',
          boxShadow: 'inset 0 2px 5px rgba(0,0,0,0.85), 0 1px 1px rgba(255,255,255,0.06)',
          touchAction: 'none'
        },
        children: jsx('span', {
          className: 'absolute rounded-full',
          style: {
            left: 3,
            right: 3,
            height: 16,
            top: on ? 4 : 26,
            background: on
              ? 'linear-gradient(180deg, #ffcf8d 0%, #e08a2b 100%)'
              : 'linear-gradient(180deg, #6d6459 0%, #37312a 100%)',
            boxShadow: on
              ? '0 0 6px rgba(255,179,71,0.55), 0 1px 2px rgba(0,0,0,0.7)'
              : '0 1px 2px rgba(0,0,0,0.7)',
            transition: 'top 110ms ease, background 110ms ease'
          }
        })
      }),
      jsx('span', {
        className: 'w-full truncate text-center text-[0.5625rem] leading-tight',
        style: { color: FACE, opacity: 0.75 },
        children: shortName(knob.name)
      }),
      jsx('span', {
        className: 'max-w-full truncate text-center text-[0.5rem] tabular-nums',
        style: { color: working ? '#ffb347' : FACE_DIM },
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
    children: group.knobs.map((k) => (k.bool === true
      ? jsx(Switch, { knob: k, busy, onTurn }, k.index ?? k.name)
      : jsx(Knob, { knob: k, busy, onTurn, size }, k.index ?? k.name)))
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
function PowerSwitch({ armed, busy, onToggle, setup, onSetup }) {
  return jsxs('button', {
    type: 'button',
    disabled: busy,
    onClick: setup ? onSetup : onToggle,
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
            children: setup ? 'engine off' : armed ? 'live' : 'standby'
          })
        ]
      }),
      jsx('span', {
        className: 'max-w-[62%] text-right text-[0.5625rem] leading-tight',
        style: { color: FACE_DIM },
        children: busy
          ? 'working'
          : setup
            ? 'click to open the engine: silent until live'
            : armed
              ? 'armed, master silent until you raise it'
              : 'click to arm and play'
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
            ? 'un-bypass the chain, still silent at -60 dB'
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

// ---- meter cadence, one policy for every surface -------------------------------
// 150 ms is fast enough that a strum reaches the number before the eye notices the gap, and it is
// what six-plus bridge round trips a second costs. So: no polling at all when the pane is not on
// screen, 400 ms while the rig is parked in STANDBY where the only thing moving is the -60 dB
// floor, and 150 ms only when the player is actually live. The chip below stays at 400 ms; it is
// a glance, not a meter.
const METER_MS = 150
const METER_IDLE_MS = 400
const pageOnScreen = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'

function useOnScreen() {
  const [shown, setShown] = useState(pageOnScreen)
  useEffect(() => {
    // Without this listener the poll would stop at the first hidden tick and never restart:
    // react-query re-evaluates the interval on a render, and coming back to the tab is one.
    const change = () => setShown(pageOnScreen())
    document.addEventListener('visibilitychange', change)
    return () => document.removeEventListener('visibilitychange', change)
  }, [])
  return shown
}

const meterEvery = (armed) => (armed ? METER_MS : METER_IDLE_MS)

// --- Amp 2's own face, in the engine's own words ---------------------------------------
// A plug-in has parameters; the engine has three files and three knobs. Rather than dress the
// engine up as a VST and then show nothing, the face asks the engine what it is playing and
// offers exactly those choices: which capture, which cabinet, three tone knobs. The name of the
// chain is the two file names, because that is what a player calls "the tone".

function toneWords(tone) {
  const rows = [['bass', 'bass'], ['mid', 'mid'], ['treble', 'treble']]
  const moved = rows
    .map(([key, word]) => [word, Number(tone && tone[key] || 0)])
    .filter(([, db]) => Math.abs(db) >= 0.05)
  if (!moved.length) return 'flat: all three knobs at zero'
  return moved.map(([word, db]) => `${word} ${db > 0 ? '+' : ''}${db.toFixed(1)} dB`).join(', ')
}

function ChainPlate({ name, capture, cab, tone, rate, block, latency }) {
  const title = name || [capture, cab].filter(Boolean).join(' + ') || 'nothing loaded yet'
  const facts = [
    rate ? `${Math.round(rate / 100) / 10} kHz` : '',
    block ? `${block} frames` : '',
    latency ? `${Number(latency).toFixed(0)} ms round trip` : ''
  ].filter(Boolean).join(' · ')
  const row = (label, value) => jsxs('div', {
    className: 'flex items-baseline justify-between gap-2',
    children: [
      jsx('span', {
        className: 'text-[0.5rem] font-semibold uppercase tracking-[0.14em]',
        style: { color: FACE_DIM }, children: label
      }),
      jsx('span', {
        className: 'text-right text-[0.625rem] leading-snug', style: { color: FACE }, children: value
      })
    ]
  })
  return jsxs('div', {
    className: 'rounded-md px-2.5 py-2',
    style: { background: PLATE, border: '1px solid rgba(255,255,255,0.07)' },
    children: [
      jsx('div', {
        className: 'text-[0.8125rem] font-semibold leading-tight', style: { color: FACE }, children: title
      }),
      jsx('div', {
        className: 'mb-1 text-[0.5rem] leading-snug', style: { color: FACE_DIM },
        children: 'what is on the signal path right now'
      }),
      jsxs('div', { className: 'flex flex-col gap-0.5', children: [
        row('amp', capture || 'not loaded'),
        row('cabinet', cab || 'no cabinet: raw amp'),
        row('tone', toneWords(tone)),
        facts ? row('engine', facts) : null
      ]})
    ]
  })
}

function FileMenu({ title, caption, current, currentPath, items, busy, onPick }) {
  const list = items || []
  return jsx(Panel, {
    title,
    caption,
    children: jsxs('div', {
      className: 'flex flex-col gap-1',
      children: [
        jsx('div', {
          className: 'text-[0.6875rem] font-semibold leading-tight', style: { color: FACE },
          children: current || 'nothing loaded'
        }),
        list.length
          ? jsx('select', {
            className: 'w-full rounded-sm px-1 py-1 text-[0.625rem]',
            style: { background: WELL, color: FACE, border: '1px solid rgba(255,255,255,0.08)' },
            value: currentPath || '',
            disabled: busy,
            'aria-label': `choose the ${title}`,
            onChange: (event) => { if (event.target.value) onPick(event.target.value) },
            children: list.map((file) => jsx('option', {
              value: file.path, children: `${file.name} — ${file.folder}`
            }, file.path))
          })
          : jsx('div', {
            className: 'text-[0.5625rem] leading-snug', style: { color: FACE_DIM },
            children: 'no files in the usual folders; set AMPHEAD_CAPTURE_DIR or AMPHEAD_CAB_DIR'
          })
      ]
    })
  })
}

function TonePanel({ tone, busy, onTurn }) {
  const knobs = [['bass', 'Bass'], ['mid', 'Mid'], ['treble', 'Treble']].map(([key, label], index) => {
    const db = Number((tone && tone[key]) || 0)
    return {
      name: label,
      index,
      norm: Math.max(0, Math.min(1, (db + 12) / 24)),
      value: `${db > 0 ? '+' : ''}${db.toFixed(1)} dB`,
      steps: 0
    }
  })
  return jsx(Panel, {
    title: 'tone',
    caption: 'three knobs, ±12 dB; all three at zero is off',
    children: jsx('div', {
      className: 'grid grid-cols-3 place-items-center gap-2',
      children: knobs.map((knob) => jsx(Knob, { knob, busy, size: 'lg', onTurn }, knob.name))
    })
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
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState('')
  const clipped = useRef(false)

  const s = state.data || {}
  // Amp 2 answers with what it is actually playing, so the face can offer the files on this
  // machine. REAPER's face never asks: its amp is a plug-in with parameters, not a file.
  const engine = s.engine === 'amphead2'
  const library = useQuery({
    queryKey: ['amphead2', 'library'],
    queryFn: () => rest('/library'),
    enabled: engine,
    staleTime: 20000
  })
  const reading = meter.data || {}
  const peak = Math.max(reading.l || 0, reading.r || 0)
  // A dB number is a claim that something was measured. With no rig the meter reads zero, and
  // "-60.0 dB" reads as a quiet amp rather than no amp, so the panel says words instead.
  const live = !!reading.live
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
        client.invalidateQueries({ queryKey: ['amphead2', 'state'] })
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
        client.invalidateQueries({ queryKey: ['amphead2', 'state'] })
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
      client.invalidateQueries({ queryKey: ['amphead2'] })
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

  // One array of panels, built for whichever rig is behind the pane. The engine has files
  // and knobs it really owns; REAPER has a plug-in and its parameters. Neither is dressed up
  // as the other, and the face never shows a control that cannot answer.
  const extras = []
  if (engine) {
    extras.push(jsx(ChainPlate, {
      name: s.tone_name, capture: s.capture, cab: s.cab, tone: s.tone,
      rate: s.samplerate, block: s.blocksize, latency: s.latency_ms
    }))
    extras.push(jsx(FileMenu, {
      title: 'amp',
      caption: 'the capture: the amp model the engine runs',
      current: s.capture, currentPath: s.capture_path,
      items: (library.data || {}).captures || [], busy,
      onPick: (path) => act('/capture', { path })
    }))
    extras.push(jsx(FileMenu, {
      title: 'cabinet',
      caption: 'the speaker: the impulse response after the amp',
      current: s.cab, currentPath: s.cab_path,
      items: (library.data || {}).cabs || [], busy,
      onPick: (path) => act('/cab', { path })
    }))
    extras.push(jsx(TonePanel, { tone: s.tone, busy, onTurn: turnKnob }))
  } else {
    extras.push(...[
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
                // Two sockets is two positions, which is a switch; anything with more places to be
                // is a knob that steps. The control follows the count instead of hard-coding either.
                socket.count === 2
                  ? jsx(Switch, { knob: socketKnob, busy, onTurn: turnInput })
                  : jsx(Knob, { knob: socketKnob, busy, size: 'lg', onTurn: turnInput }),
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
    ].filter(Boolean))
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

      jsx(Meter, { peak, clip, live }),

      jsxs('div', {
        className: 'flex flex-col gap-1.5',
        children: [
          jsx(PowerSwitch, {
            armed,
            busy,
            setup: s.running === false,
            onToggle: () => act('/live', { on: !armed }),
            onSetup: () => act('/setup', {})
          }),
          jsx(PanicButton, {
            silenced,
            busy,
            onClick: () => act('/panic', silenced ? { restore: true } : {})
          })
        ]
      }),

      jsx(MasterRamp, {
        db: s.master_db,
        busy,
        armed,
        // The engine's own output gain goes above unity: a clean DI through a lead capture
        // still had room at 0 dB, which is why 'maxed out' sounded quiet. REAPER stays at 0.
        ceiling: engine ? 12 : 0,
        onCommit: (db) => act('/master', { db })
      }),

      ...extras,


      said
        ? jsx('div', { className: 'text-[0.5625rem] leading-snug', style: { color: FACE_DIM }, children: said })
        : null,
      jsx('div', {
        className: 'mt-auto text-center text-[0.5rem] leading-snug',
        style: { color: 'rgba(240,226,200,0.3)' },
        children: engine ? 'the engine is its own process: Hermes never touches a song' : 'REAPER stays on the taskbar'
      })
    ]
  })
}

// The page is the reliable door: a sidebar row and a route render in the workspace like any
// built-in view, so the amp is one click away and needs no pane dragging to be discovered.
function AmpPage({ rest, title = 'AmpHead', subtitle = 'the rig, without opening REAPER' }) {
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

// The two faces share one plugin process but not one backend. `?engine=` on every route names the
// rig that answers, so the original can stay on REAPER while the rebuild talks to the local one.
// Every write on the face is a POST with a body; the options ride along, not just the
// path, or the server reads a GET against a POST-only route and answers 404.
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
    // Two faces, two backends, one process. Amp is the face that shipped, asking for the REAPER
    // rig; Amp 2 is the rebuild, asking for the local engine. Neither one can hide the other.
    // The original keeps its page, its sidebar row, its palette entry and its own draggable pane.
    // It does not keep the status-bar chip: two chips would show two different rigs' levels in the
    // same corner with no way to tell which is which.
    LegacyPlugin.register(engineContext(ctx, 'reaper', [ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, 'panes']))
    const rest2 = engineContext(ctx, 'amphead2').rest
    ctx.registerMany([
      {
        id: 'page2',
        area: ROUTES_AREA,
        data: { path: '/amp2' },
        render: () => jsx(AmpPage, { rest: rest2, title: 'AmpHead 2', subtitle: 'the local engine, no REAPER at all' })
      },
      {
        id: 'nav2',
        area: SIDEBAR_NAV_AREA,
        order: 61,
        data: { codicon: 'pulse', label: 'Amp 2', path: '/amp2' }
      },
      {
        id: 'open2',
        area: PALETTE_AREA,
        data: {
          id: 'amphead.open2',
          label: 'AmpHead 2: open the local engine',
          keywords: ['amp', 'amphead', 'guitar', 'reaper', 'rig', 'tone'],
          run: () => host.navigate('/amp2')
        }
      }
    ])
    ctx.register({
      id: 'amp2-face',
      area: 'panes',
      title: 'amp 2',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(AmpFace, { rest: rest2 })
    })
    ctx.register({
      id: 'amp2-chip',
      area: 'statusBar.right',
      order: 120,
      render: () => {
        // The chip is the "is the amp alive" glance from any pane: a verdict dot plus the input level,
        // both from the same routes the page reads, so it cannot disagree with them.
        function Chip() {
          const shown = useOnScreen()
          const meter = useQuery({
            queryKey: ['amphead2', 'chip-meter'],
            queryFn: () => rest2('/meter'),
            refetchInterval: shown ? 400 : false
          })
          const state = useQuery({
            queryKey: ['amphead2', 'chip-state'],
            queryFn: () => rest2('/state'),
            refetchInterval: shown ? 4000 : false
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

// ---- the original face, kept so both builds are on screen at once ----
// Verbatim except for the meter cadence: its two useQuery calls read the same policy as the
// rebuild above (hidden pane stops, STANDBY 400 ms, live 150 ms), because the cost being paid
// there was a bridge round trip into REAPER, not a look.
const LegacyPlugin = (() => {
// AmpHead amp face: the pane that proves the rig is alive while you play.
// Everything here comes from /api/plugins/amphead/state and /meter, which read REAPER directly,
// so the pane carries every number REAPER would show while REAPER itself stays on the taskbar.


const MUTED = { color: 'var(--ui-text-quaternary)' }
const SECOND = { color: 'var(--ui-text-secondary)' }
const ACCENT = { color: 'var(--ui-accent)' }

const VERDICT_COLOR = {
  READY: 'var(--ui-accent)',
  STANDBY: 'var(--ui-text-secondary)',
  'NO SIGNAL': 'var(--ui-warning, var(--ui-text-secondary))',
  'NO INPUT': 'var(--ui-warning, var(--ui-text-secondary))',
  'NO CHAIN': 'var(--ui-warning, var(--ui-text-secondary))',
  CLIPPING: 'var(--ui-danger, var(--ui-text))',
  'DEVICE CLOSED': 'var(--ui-danger, var(--ui-text))',
  'REAPER NOT RUNNING': 'var(--ui-danger, var(--ui-text))',
  'REAPER BUSY': 'var(--ui-danger, var(--ui-text))',
  'NO REAPER INSTALL': 'var(--ui-danger, var(--ui-text))',
  'BRIDGE GONE': 'var(--ui-danger, var(--ui-text))',
  'TWO REAPER': 'var(--ui-danger, var(--ui-text))',
  'RIG TAB CLOSED': 'var(--ui-danger, var(--ui-text))',
  'DEVICE UNPLUGGED': 'var(--ui-danger, var(--ui-text))',
  MUTED: 'var(--ui-warning, var(--ui-text-secondary))',
  'REOPENED DEVICE': 'var(--ui-warning, var(--ui-text-secondary))'
}

const toDb = (peak) => (peak <= 0.000001 ? -60 : Math.max(-60, 20 * Math.log10(peak)))

function Row({ label, children }) {
  return jsxs('div', {
    className: 'flex items-baseline justify-between gap-3',
    children: [
      jsx('span', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: label }),
      jsx('span', { className: 'text-right text-xs', style: SECOND, children })
    ]
  })
}

function Meter({ peak, clip, live }) {
  const db = toDb(peak)
  const hot = !!clip && live
  const width = live ? Math.max(0, Math.min(100, ((db + 60) / 60) * 100)) : 0
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsxs('div', {
        className: 'flex items-baseline justify-between',
        children: [
          jsx('span', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'input' }),
          jsx('span', {
            className: 'text-xs tabular-nums',
            style: hot ? VERDICT_COLOR.CLIPPING : SECOND,
            children: live
              ? `${db.toFixed(1)} dB${hot ? '  CLIPPED' : ''}`
              : 'not measuring'
          })
        ]
      }),
      jsx('div', {
        className: 'h-2 w-full overflow-hidden rounded-full',
        style: { background: 'var(--ui-stroke-secondary)' },
        children: jsx('div', {
          className: 'h-full rounded-full transition-[width] duration-75',
          style: { width: `${width}%`, background: hot ? 'var(--ui-danger, var(--ui-accent))' : 'var(--ui-accent)' }
        })
      })
    ]
  })
}

// A slider is the amp's knob, not a copy of it: it starts at the normalized value the host reported,
// stays where the finger put it while dragging (the 1.5 s state poll must not fight the drag), and
// only then writes through /tweak, which resolves the knob by index and reads it back. A host that
// cannot report a normalized value gets the number row instead of a slider that would lie about
// where the knob sits.
function Knob({ knob, busy, onCommit }) {
  const [value, setValue] = useState(typeof knob.norm === 'number' ? knob.norm : null)
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    if (!dragging && typeof knob.norm === 'number') setValue(knob.norm)
  }, [knob.norm, dragging])

  if (value === null) {
    return jsx(Row, { label: knob.name, children: knob.value })
  }

  const commit = () => {
    setDragging(false)
    onCommit(knob, value)
  }

  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsxs('div', {
        className: 'flex items-baseline justify-between gap-3',
        children: [
          jsx('span', { className: 'text-[0.6875rem] leading-tight', style: MUTED, children: knob.name }),
          jsx('span', {
            className: 'text-xs tabular-nums',
            style: dragging ? MUTED : SECOND,
            children: dragging ? '...' : knob.value
          })
        ]
      }),
      jsx('input', {
        type: 'range',
        min: 0,
        max: 1,
        step: 0.001,
        value,
        disabled: busy,
        'aria-label': knob.name,
        onPointerDown: () => setDragging(true),
        onPointerUp: commit,
        onKeyUp: commit,
        onChange: (event) => setValue(Number(event.target.value)),
        className: 'w-full',
        style: { accentColor: 'var(--ui-accent)' }
      })
    ]
  })
}


// Clicking a chain row is only useful while REAPER is on screen, so the row says what it will do
// before it is clicked: with REAPER hidden the click answers in place instead of doing nothing.
function ChainRow({ fx, index, ui, onFocus }) {
  const [note, setNote] = useState('')
  const clickable = ui === 'visible'
  return jsxs('button', {
    type: 'button',
    onClick: async () => {
      if (!clickable) {
        setNote('un-minimize REAPER first, then this click brings the plugin forward')
        return
      }
      const said = await onFocus(index)
      setNote(said || '')
    },
    className: 'flex w-full items-baseline justify-between gap-3 bg-transparent px-0 py-0.5 text-left',
    style: { cursor: clickable ? 'pointer' : 'default' },
    children: [
      jsx('span', { className: 'text-[0.6875rem] leading-tight', style: MUTED, children: `${index + 1}. ${fx.enabled ? 'on' : 'bypassed'}` }),
      jsxs('span', { className: 'flex flex-col items-end', children: [
        jsx('span', { className: 'text-xs', style: clickable ? SECOND : MUTED, children: fx.name }),
        note ? jsx('span', { className: 'text-[0.625rem]', style: MUTED, children: note }) : null
      ] })
    ]
  })
}


function AmpFace({ rest }) {
  const client = useQueryClient()
  const shown = useOnScreen()
  const state = useQuery({
    queryKey: ['amphead', 'state'],
    queryFn: () => rest('/state'),
    refetchInterval: shown ? 1500 : false
  })
  const armedNow = !!(state.data && state.data.armed)
  const meter = useQuery({
    queryKey: ['amphead', 'meter'],
    queryFn: () => rest('/meter'),
    // 150 ms while the rig is live and the pane is on screen; 400 ms in STANDBY; nothing at all
    // when the pane is hidden. Each poll is a file round trip into REAPER.
    refetchInterval: shown ? meterEvery(armedNow) : false
  })
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState('')
  const clipped = useRef(false)

  const s = state.data || {}
  const reading = meter.data || {}
  const peak = Math.max(reading.l || 0, reading.r || 0)
  // A dB number is a claim that something was measured. With no rig the meter reads zero, and
  // "-60.0 dB" reads as a quiet amp rather than no amp, so the panel says words instead.
  const live = !!reading.live
  useEffect(() => {
    // Latch the clip light like hardware: it stays lit until the input has been quiet for a moment.
    if (reading.clip) clipped.current = true
    else if (toDb(peak) < -6) clipped.current = false
  }, [reading.clip, peak])
  const clip = clipped.current || !!reading.clip

  const verdict = s.verdict || 'CONNECTING'
  const armed = !!s.armed
  const device = s.device || {}

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

  // Panic is not a mode the daemon remembers: it is a visible fact about the chain, so the button
  // reads the chain it just fetched and offers the reverse action only when the reverse is true.
  const chain = s.chain || []
  const silenced = chain.length > 0 && chain.every((fx) => !fx.enabled)

  // The engine is neither REAPER nor installed by anyone else: if it is not running, nothing on
  // this face can succeed, so the first button opens it instead of asking it for a level.
  const running = s.running !== false
  const liveLabel = armed
    ? 'STANDBY: disarm and park the master at -60 dB'
    : 'LIVE: arm and monitor at the safe -6 dB, then use the ramp'
  const firstLabel = !running
    ? 'SET UP: open the engine, silent until Live'
    : liveLabel

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', {
            className: 'text-lg font-semibold leading-tight',
            style: { color: VERDICT_COLOR[verdict] || 'var(--ui-text)' },
            children: verdict
          }),
          jsx('div', { className: 'text-xs leading-snug', style: SECOND, children: s.reason || 'reading the rig' })
        ]
      }),

      jsx(Meter, { peak, clip, live }),

      jsx('button', {
        type: 'button',
        disabled: busy,
        onClick: () => (running ? act('/live', { on: !armed }) : act('/setup', {})),
        className: 'w-full rounded-md px-3 py-2 text-xs font-medium disabled:opacity-50',
        style: armed
          ? { background: 'var(--ui-stroke-secondary)', color: 'var(--ui-text)' }
          : { background: 'var(--ui-accent)', color: 'var(--ui-accent-foreground, var(--ui-text))' },
        children: busy ? 'working' : firstLabel
      }),

      jsxs('button', {
        type: 'button',
        disabled: busy || !running,
        onClick: () => act('/panic', silenced ? { restore: true } : {}),
        className: 'w-full rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50',
        style: silenced
          ? { background: 'var(--ui-stroke-secondary)', color: 'var(--ui-text)' }
          : { background: 'var(--ui-danger, var(--ui-stroke-secondary))', color: 'var(--ui-accent-foreground, var(--ui-text))' },
        children: busy
          ? 'working'
          : silenced
            ? 'RESTORE: un-bypass the chain and lift the master'
            : 'PANIC: silence the rig now'
      }),

      // Live arms at the floor on purpose, so without this the player is armed and inaudible and
      // the only honest word for that is broken. The ramp is the deliberate step that makes sound.
      jsx(MasterRamp, {
        db: s.master_db,
        busy,
        armed,
        onCommit: (db) => act('/master', { db })
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'chain' }),
          (s.chain || []).length === 0
            ? jsx('div', { className: 'text-xs', style: MUTED, children: 'no plugins loaded on the guitar track' })
            : (s.chain || []).map((fx, i) =>
                jsx(ChainRow, {
                  key: `${i}-${fx.name}`,
                  fx,
                  index: i,
                  ui: s.ui,
                  onFocus: async (which) => {
                    const answer = await act('/focus', { fx: which })
                    return answer && answer.output
                  }
                }))
        ]
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'amp' }),
          (s.knobs || []).length === 0
            ? jsx('div', { className: 'text-xs', style: MUTED, children: 'no amp loaded yet' })
            : (s.knobs || []).map((k) =>
                jsx(Knob, {
                  key: k.index ?? k.name,
                  knob: k,
                  busy,
                  onCommit: (knob, value) => act('/tweak', { param: knob.index, value, mode: 'norm' })
                }))
        ]
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1.5',
        children: [
          jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'tones' }),
          jsx('div', {
            className: 'flex flex-wrap gap-1.5',
            children: (s.presets || []).map((p) =>
              jsx('button', {
                key: p.key,
                type: 'button',
                disabled: busy,
                title: p.title,
                onClick: () => act('/preset', { query: p.key }),
                className: 'rounded-md px-2 py-1 text-[0.6875rem] disabled:opacity-50',
                style: { background: 'var(--ui-stroke-secondary)', color: 'var(--ui-text)' },
                children: p.key
              }))
          })
        ]
      }),

      jsx(Row, {
        label: 'latency',
        children: device.closed
          ? 'device closed'
          : `${device.out_ms || 0} ms out, ${device.buffer || '?'} samples at ${device.rate || '?'} Hz`
      }),
      jsx(Row, { label: 'master', children: s.master_db === null || s.master_db === undefined ? 'unknown' : `${s.master_db} dB` }),
      jsx(Row, { label: 'project', children: s.project || 'unnamed' }),

      said ? jsx('div', { className: 'text-xs leading-snug', style: SECOND, children: said }) : null,
      jsx('div', { className: 'mt-auto text-[0.625rem] leading-snug', style: MUTED, children: 'REAPER stays on the taskbar. Say "more gain" or click a tone.' })
    ]
  })
}

// The page is the reliable door: a sidebar row and a route render in the workspace like any
// built-in view, so the amp is one click away and needs no pane dragging to be discovered.
function AmpPage({ rest, title = 'AmpHead', subtitle = 'the rig, without opening REAPER' }) {
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

const plugin = {
  id: 'amphead',
  name: 'AmpHead',
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/amp' },
        render: () => jsx(AmpPage, { rest: ctx.rest })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'pulse', label: 'Amp', path: '/amp' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'amphead.open',
          label: 'AmpHead: open the amp',
          keywords: ['amp', 'amphead', 'guitar', 'reaper', 'rig', 'tone'],
          run: () => host.navigate('/amp')
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
          const meter = useQuery({ queryKey: ['amphead', 'chip-meter'], queryFn: () => ctx.rest('/meter'), refetchInterval: 400 })
          const state = useQuery({ queryKey: ['amphead', 'chip-state'], queryFn: () => ctx.rest('/state'), refetchInterval: 4000 })
          const peak = Math.max((meter.data && meter.data.l) || 0, (meter.data && meter.data.r) || 0)
          const verdict = (state.data && state.data.verdict) || 'CONNECTING'
          return jsxs('span', {
            className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums',
            title: (state.data && state.data.reason) || verdict,
            children: [
              jsx('span', {
                className: 'inline-block h-1.5 w-1.5 rounded-full',
                style: { background: VERDICT_COLOR[verdict] || 'var(--ui-text-quaternary)' }
              }),
              jsx('span', {
                style: meter.data && meter.data.live ? ACCENT : MUTED,
                children: `${toDb(peak).toFixed(0)} dB`
              })
            ]
          })
        }
        return jsx(Chip, {})
      }
    })
  }
}

  return plugin
})()
