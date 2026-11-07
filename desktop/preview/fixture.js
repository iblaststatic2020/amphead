// A rig fixture shaped exactly like /state and /meter answer, so the harness exercises the same
// field names the real routes use. Values come from the saved amphead.rpp reading: NAM Universal
// on the guitar track, ASIO at 512 / 44100, 11.61 ms out, master parked at -18 dB while live.
//
// There are two rigs here because there are two faces: `?engine=reaper` is the contest build's rig
// and `?engine=amphead2` is the local engine. Each keeps its own chain, master and knob values, so
// a write through one face can never show up in the other. That is the property the parallel is
// supposed to have, and the harness is where it gets checked without REAPER.

const KNOBS = () => [
  { index: 0, name: 'Amplifier Input Gain', value: '20.55 dB', norm: 0.62 },
  { index: 1, name: 'Amplifier Bass', value: '4.78', norm: 0.48 },
  { index: 2, name: 'Amplifier Mid', value: '6.04', norm: 0.6 },
  { index: 3, name: 'Amplifier Treble', value: '8.44', norm: 0.74 },
  { index: 4, name: 'Amplifier Output Gain', value: '0.00 dB', norm: 0.5 },
  { index: 5, name: 'Input Gain', value: '-12.68 dB', norm: 0.36 },
  { index: 6, name: 'Output Gain', value: '0.00 dB', norm: 0.5 },
  { index: 7, name: 'Gate', value: '-80.0 dB', norm: 0.0 },
  // The host marks a parameter that reads On or Off, so the harness draws the switch the same way
  // the pane does rather than only ever showing knobs.
  { index: 8, name: 'Amplifier Bright', value: 'On', norm: 1, bool: true },
  { index: 9, name: 'Amplifier Cabinet', value: 'Off', norm: 0, bool: true }
]

const rigs = {
  reaper: {
    verdict: 'READY',
    reason: 'armed, monitoring, NAM Universal is passing signal',
    armed: true,
    master: -18,
    project: 'amphead.rpp',
    ui: 'visible',
    problems: [],
    chain: [{ name: 'VST3: NAM Universal (Neural Amp Modeler)', enabled: true }],
    knobs: KNOBS()
  },
  // The local engine answers in its own words: it is up, and it has no device open yet. A READY
  // here would be a lie, and avoiding exactly that lie is why the seam exists.
  amphead2: {
    verdict: 'STANDBY',
    reason: 'the local engine is up, no audio device open yet',
    armed: false,
    master: -60,
    project: 'local engine, no project file',
    ui: 'hidden',
    problems: [],
    chain: [{ name: 'NAM: Twin Reverb (local .nam)', enabled: true }],
    knobs: KNOBS()
  }
}

// ?demo=1 shows the canned rig the way a visitor with no DAW sees it. The switch is held here so
// the demo button in the face can actually turn it off and back on inside the harness.
let demoOverride = null
const demoOn = () => (demoOverride !== null
  ? demoOverride
  : new URLSearchParams(location.search).get('demo') === '1')

// A plucked string, roughly: two peaks per second that fall away, so the meter looks like an amp
// being played rather than a random walk.
function peak(rig) {
  if (!rig.armed) return 0
  const t = Date.now() / 1000
  const pluck = Math.max(0, Math.sin(t * 2.1)) ** 3
  return Math.min(0.92, 0.03 + pluck * (0.42 + 0.1 * Math.sin(t * 37)))
}

// Every route may carry `?engine=`. A call without it belongs to the rig the process was started
// against, which is what the shipped face and the tools do.
export function split(path) {
  const [bare, search] = String(path).split('?')
  const query = new URLSearchParams(search || '')
  const named = (query.get('engine') || 'reaper').toLowerCase()
  return { path: bare, query, engine: rigs[named] ? named : 'reaper' }
}

export const state = (engine) => {
  const rig = rigs[engine]
  if (demoOn()) return DEMO_STATE(engine)
  // ?verdict=RIG%20TAB%20CLOSED lets the harness show a broken rig on demand, so the red states can
  // be looked at without unplugging anything.
  const forced = new URLSearchParams(location.search).get('verdict')
  return {
    ok: true,
    demo: false,
    engine,
    verdict: forced || rig.verdict,
    reason: forced
      ? 'REAPER is in front of a project that is not the rig, so nothing here can be trusted'
      : rig.reason,
    armed: rig.armed,
    master_db: rig.master,
    project: rig.project,
    ui: rig.ui,
    device: { buffer: '512', rate: '44100', out_ms: 11.61, closed: false,
              driver: 'ASIO AudioBox ASIO Driver' },
    // Two sockets on this interface, so the socket control is a two-position switch.
    input: { count: 2, index: 1, name: 'IN 1', norm: 0, track: 1 },
    problems: forced ? ['the active project is not the rig'] : rig.problems,
    chain: rig.chain,
    knobs: rig.knobs,
    presets: [
      { key: 'clean', title: 'clean: input 12 dB, bass 3, mid 5, treble 6' },
      { key: 'crunch', title: 'crunch: input 20 dB, bass 5, mid 6, treble 8' },
      { key: 'lead', title: 'lead: input 26 dB, bass 6, mid 7, treble 9' }
    ]
  }
}

// The demo rig as the backend sends it: a made-up session, shaped exactly like a real one.
function DEMO_STATE(engine) {
  return {
    ok: true,
    demo: true,
    engine,
    verdict: 'READY',
    reason: 'armed, monitoring, 11.61 ms out, 1 plugin live',
    armed: true,
    master_db: -20,
    project: 'amphead.rpp (demo rig)',
    ui: 'hidden',
    device: { buffer: '512', rate: '44100', out_ms: 11.61, closed: false,
              driver: 'ASIO AudioBox ASIO Driver' },
    // Two sockets on this interface, so the socket control is a two-position switch.
    input: { count: 2, index: 1, name: 'IN 1', norm: 0, track: 1 },
    problems: [],
    chain: [{ name: 'VST3i: NAM Universal (Neural Amp Modeler)', enabled: true }],
    knobs: KNOBS(),
    presets: [
      { key: 'crunch', title: 'a mid-forward crunch' },
      { key: 'clean', title: 'a bright clean' },
      { key: 'doom', title: 'a doom stack' }
    ]
  }
}

export const meter = (engine) => {
  const rig = rigs[engine]
  // A forced broken verdict means no rig, so the meter must read like no rig: zeros, not live.
  const forced = new URLSearchParams(location.search).get('verdict')
  if (forced) return { l: 0, r: 0, clip: false, live: false }
  const p = peak(rig)
  return { l: p, r: p * (0.9 + Math.random() * 0.1), clip: p > 0.9, live: rig.armed }
}

// Every call the faces make, tagged with the engine that rode on it. The harness reads this, so a
// pane that forgets its `?engine=` shows up as a reaper call made by the rebuild.
export const calls = []

export async function rest(path, options) {
  const body = options && options.body ? options.body : {}
  const call = split(path)
  const rig = rigs[call.engine]
  calls.push(`${call.engine} ${call.path}`)
  if (call.path === '/state') return state(call.engine)
  if (call.path === '/meter') return meter(call.engine)
  if (call.path === '/live') {
    rig.armed = body.on !== undefined ? !!body.on : !rig.armed
    rig.master = body.master_db !== undefined ? body.master_db : rig.armed ? -18 : -60
    rig.verdict = rig.armed ? 'READY' : 'STANDBY'
    return { ok: true, engine: call.engine,
             output: rig.armed ? 'live: armed, monitor on, master -18 dB' : 'standby: disarmed, master parked' }
  }
  if (call.path === '/panic') {
    if (body.restore) {
      rig.chain = rig.chain.map((fx) => ({ ...fx, enabled: true }))
      rig.master = body.master_db !== undefined ? body.master_db : -18
      return { ok: true, engine: call.engine, output: 'restored: chain un-bypassed, master -18 dB' }
    }
    rig.chain = rig.chain.map((fx) => ({ ...fx, enabled: false }))
    return { ok: true, engine: call.engine, output: 'panicked: chain bypassed, master untouched' }
  }
  if (call.path === '/focus') return { ok: true, engine: call.engine, output: 'brought plugin 1 forward in REAPER' }
  // The rebuild turns knobs at /knob with a normalized value and reads the plugin's own number back;
  // the shipped face still posts /tweak. Both are served here, or the harness would only ever
  // exercise one of the two faces.
  if (call.path === '/knob' || call.path === '/tweak') {
    const knob = rig.knobs[body.param]
    if (knob) {
      knob.norm = body.value
      knob.value = `${Number(body.value).toFixed(2)} (turned)`
    }
    return { ok: true, engine: call.engine, norm: body.value, value: knob ? knob.value : '?',
             output: `set ${knob ? knob.name : 'knob'} to ${Number(body.value).toFixed(3)}` }
  }
  if (call.path === '/input') {
    const socket = { count: 2, index: body.input, name: `IN ${body.input}`, norm: body.input - 1, track: 1 }
    return { ok: true, engine: call.engine, ...socket, output: `track 1 records from ${socket.name}` }
  }
  if (call.path === '/preset') return { ok: true, engine: call.engine, output: `applied ${body.query}` }
  if (call.path === '/demo') {
    demoOverride = !!body.on
    return { ok: true, engine: call.engine, demo: demoOverride, on: demoOverride,
             output: demoOverride ? 'demo rig on: a made-up session, not your hardware' : 'demo rig off: back to REAPER' }
  }
  return { ok: false, engine: call.engine, output: `unknown route ${call.path}` }
}
