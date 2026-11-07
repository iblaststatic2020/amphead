// A rig fixture shaped exactly like /state and /meter answer, so the harness exercises the same
// field names the real routes use. Values come from the saved amphead.rpp reading: NAM Universal
// on the guitar track, ASIO at 512 / 44100, 11.61 ms out, master parked at -18 dB while live.

let live = true
// ?demo=1 shows the canned rig the way a visitor with no DAW sees it. The switch is held here so
// the demo button in the face can actually turn it off and back on inside the harness.
let demoOverride = null
const demoOn = () => (demoOverride !== null
  ? demoOverride
  : new URLSearchParams(location.search).get('demo') === '1')
let master = -18
let chain = [{ name: 'VST3: NAM Universal (Neural Amp Modeler)', enabled: true }]

const knobs = [
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

// A plucked string, roughly: two peaks per second that fall away, so the meter looks like an amp
// being played rather than a random walk.
function peak() {
  if (!live) return 0
  const t = Date.now() / 1000
  const pluck = Math.max(0, Math.sin(t * 2.1)) ** 3
  return Math.min(0.92, 0.03 + pluck * (0.42 + 0.1 * Math.sin(t * 37)))
}

export const state = () => {
  if (demoOn()) {
    const demo = DEMO_STATE()
    return demo
  }
  // ?verdict=RIG%20TAB%20CLOSED lets the harness show a broken rig on demand, so the red states can
  // be looked at without unplugging anything.
  const forced = new URLSearchParams(location.search).get('verdict')
  return {
    ok: true,
    demo: false,
    verdict: forced || (live ? 'READY' : 'STANDBY'),
    reason: forced
      ? 'REAPER is in front of a project that is not the rig, so nothing here can be trusted'
      : live
        ? 'armed, monitoring, NAM Universal is passing signal'
        : 'the track is not armed, flip LIVE to play',
    armed: live,
    master_db: master,
    project: 'amphead.rpp',
    ui: 'visible',
    device: { buffer: '512', rate: '44100', out_ms: 11.61, closed: false,
              driver: 'ASIO AudioBox ASIO Driver' },
    // Two sockets on this interface, so the socket control is a two-position switch.
    input: { count: 2, index: 1, name: 'IN 1', norm: 0, track: 1 },
    problems: forced ? ['the active project is not the rig'] : [],
    chain,
    knobs,
    presets: [
      { key: 'clean', title: 'clean: input 12 dB, bass 3, mid 5, treble 6' },
      { key: 'crunch', title: 'crunch: input 20 dB, bass 5, mid 6, treble 8' },
      { key: 'lead', title: 'lead: input 26 dB, bass 6, mid 7, treble 9' }
    ]
  }
}

// The demo rig as the backend sends it: a made-up session, shaped exactly like a real one.
function DEMO_STATE() {
  return {
    ok: true,
    demo: true,
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
    knobs,
    presets: [
      { key: 'crunch', title: 'a mid-forward crunch' },
      { key: 'clean', title: 'a bright clean' },
      { key: 'doom', title: 'a doom stack' }
    ]
  }
}

export const meter = () => {
  // A forced broken verdict means no rig, so the meter must read like no rig: zeros, not live.
  const forced = new URLSearchParams(location.search).get('verdict')
  if (forced) return { l: 0, r: 0, clip: false, live: false }
  const p = peak()
  return { l: p, r: p * (0.9 + Math.random() * 0.1), clip: p > 0.9, live }
}

export async function rest(path, options) {
  const body = options && options.body ? options.body : {}
  if (path === '/state') return state()
  if (path === '/meter') return meter()
  if (path === '/live') {
    live = body.on !== undefined ? !!body.on : !live
    master = body.master_db !== undefined ? body.master_db : live ? -18 : -60
    return { ok: true, output: live ? 'live: armed, monitor on, master -18 dB' : 'standby: disarmed, master parked' }
  }
  if (path === '/panic') {
    if (body.restore) {
      chain = chain.map((fx) => ({ ...fx, enabled: true }))
      master = body.master_db !== undefined ? body.master_db : -18
      return { ok: true, output: 'restored: chain un-bypassed, master -18 dB' }
    }
    chain = chain.map((fx) => ({ ...fx, enabled: false }))
    return { ok: true, output: 'panicked: chain bypassed, master untouched' }
  }
  if (path === '/focus') return { ok: true, output: 'brought plugin 1 forward in REAPER' }
  if (path === '/tweak') {
    const knob = knobs[body.param]
    if (knob) knob.norm = body.value
    return { ok: true, output: `set ${knob ? knob.name : 'knob'} to ${body.value.toFixed(3)}` }
  }
  if (path === '/preset') return { ok: true, output: `applied ${body.query}` }
  if (path === '/demo') {
    demoOverride = !!body.on
    return { ok: true, demo: demoOverride, on: demoOverride,
             output: demoOverride ? 'demo rig on: a made-up session, not your hardware' : 'demo rig off: back to REAPER' }
  }
  return { ok: false, output: `unknown route ${path}` }
}
