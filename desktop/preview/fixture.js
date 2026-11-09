// A rig fixture shaped exactly like /state answers for the local engine, so the harness exercises
// the same field names the real route uses. The numbers are copied from a real `/state` read
// (PEAVEY 6506 clean capture into a Vintage 30 4x12 at 44100 / 256, 15.8 ms round trip, master
// parked at -60 dB), so the face is drawn against a payload it will actually receive.
//
// `?off=1` answers the way the route answers before Setup has opened a device: running false,
// ENGINE OFF, nothing loaded. That is the state a player sees on a fresh start, and the one where
// the face has to offer SET UP rather than GO LIVE.

const CAPTURES = [
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Bugera 6262\\Phillipe P Bugera6262-Lead-M77-ESR0,020.nam', name: 'Phillipe P Bugera6262-Lead-M77-ESR0,020', folder: 'Bugera 6262' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - CLEAN - B0 - HIGH - G4.0.nam', name: 'PEAVEY - 6506 - CLEAN - B0 - HIGH - G4.0', folder: 'Peavey 6505' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - CLEAN - B1 - NORMAL - G4.0.nam', name: 'PEAVEY - 6506 - CLEAN - B1 - NORMAL - G4.0', folder: 'Peavey 6505' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - CRUNCH - B0 - HIGH - G4.0.nam', name: 'PEAVEY - 6506 - CRUNCH - B0 - HIGH - G4.0', folder: 'Peavey 6505' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - CRUNCH - B1 - NORMAL - G6.0.nam', name: 'PEAVEY - 6506 - CRUNCH - B1 - NORMAL - G6.0', folder: 'Peavey 6505' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - RED - B0 - HIGH - G4.0.nam', name: 'PEAVEY - 6506 - RED - B0 - HIGH - G4.0', folder: 'Peavey 6505' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM\\Peavey 6505\\PEAVEY - 6506 - RED - B1 - NORMAL - G6.0.nam', name: 'PEAVEY - 6506 - RED - B1 - NORMAL - G6.0', folder: 'Peavey 6505' }
]

const CABS = [
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\IRs\\Aurora\\AURORA_212_GOV.wav', name: 'AURORA_212_GOV', folder: 'Aurora' },
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\IRs\\Celestion\\Vintage 30 4x12 close.wav', name: 'Vintage 30 4x12 close', folder: 'Celestion' }
]

const off = () => new URLSearchParams(location.search).get('off') === '1'

// `?bare=1` answers the way a machine with no library does: configured folders, no files in them.
// That is the state the empty picker is written for, and the state where the press that searches
// has to appear, so the harness can show the button and prove it is the only thing that searches.
const bare = () => new URLSearchParams(location.search).get('bare') === '1'
const adopted = { capture: false, cab: false }

// Folders the pretend search turns up. Two of them, because picking is the point: a list of one
// hides whether the row that was pressed is the row that got adopted.
const FOUND = [
  { path: 'C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM', captures: 214, cabs: 3 },
  { path: 'C:\\Users\\Chris\\Downloads\\NAM packs', captures: 38, cabs: 0 }
]

// One rig, one chain: a capture, the tone stack, a cabinet. Same three stages the engine runs.
const rig = {
  running: true,
  armed: false,
  master_db: -60,
  // The two trims either side of the amp, as the engine reports them.
  input_gain_db: 0,
  output_gain_db: 0,
  // Noise gate threshold in front of the amp: the bottom of the range is off.
  gate_db: -80,
  // A take already saved, so the preview shows the row that plays it and offers the path.
  take: { recording: false, seconds: 37, saved: true,
          name: 'take 2026-11-08 091500.wav',
          path: 'C:/Users/Chris/AppData/Local/hermes/recordings/take 2026-11-08 091500.wav' },
  tone: { bass: 0, mid: 0, treble: 0 },
  capture: 'PEAVEY - 6506 - CLEAN - B0 - HIGH - G4.0',
  capture_path: CAPTURES[1].path,
  cab: 'Vintage 30 4x12 close',
  cab_path: CABS[1].path,
  // What an aim looks like on the face: the tool's words, and the strength of the claim.
  aim: 'aimed at AC/DC: cab matched: Greenback; no capture in the library answers that rig',
  aim_confidence: 'family',
  stages: [
    { i: 0, name: 'Capture', enabled: true },
    { i: 1, name: 'Tone', enabled: true },
    { i: 2, name: 'Cabinet', enabled: true }
  ]
}

const toneName = () => `${rig.capture || 'no capture'} / ${rig.cab || 'no cabinet'}`

export const state = () => {
  if (off()) {
    return {
      ok: true, engine: 'amphead2', verdict: 'ENGINE OFF',
      reason: 'the local engine is not running: press Setup',
      project: 'AmpHead 2', ui: 'hidden', running: false, armed: false, monitor: false,
      master_db: null, input_gain_db: null, output_gain_db: null, gate_db: null, demo: false, problems: [], chain: [], knobs: [], tone: null,
      take: { recording: false },
      tone_name: '', capture: '', capture_path: '', cab: '', cab_path: '', aim: '', aim_confidence: '',
      samplerate: 0, blocksize: 0, latency_ms: 0, block_ms: 0, round_trip_ms: 0, mode: '',
      device: { driver: '', buffer: '', rate: '', out_ms: 0, closed: true }
    }
  }
  const armed = rig.armed
  return {
    ok: true, engine: 'amphead2', demo: false,
    verdict: armed ? 'READY' : 'STANDBY',
    reason: armed ? 'armed, monitoring, 15.8 ms round trip' : 'the amp is open and silent, flip LIVE to play',
    project: 'AmpHead 2', ui: 'hidden', running: true, armed, monitor: armed,
    master_db: rig.master_db,
    input_gain_db: rig.input_gain_db,
    output_gain_db: rig.output_gain_db,
    gate_db: rig.gate_db,
    take: rig.take,
    // A stage silence switched out is a stage the face must not pretend is playing.
    problems: rig.stages.some((stage) => !stage.enabled) ? ['a stage is switched out of the path'] : [],
    chain: rig.stages.map((stage) => ({ name: stage.name, enabled: stage.enabled })),
    stages: rig.stages, knobs: [], tone: { ...rig.tone },
    tone_name: toneName(),
    capture: rig.capture, capture_path: rig.capture_path,
    cab: rig.cab, cab_path: rig.cab_path,
    // Let the harness show what an aim looks like without a live agent turn.
    aim: rig.aim || '', aim_confidence: rig.aim_confidence || '',
    samplerate: 44100, blocksize: 256, latency_ms: 9.98, block_ms: 5.8, round_trip_ms: 15.8,
    mode: 'wdm-ks-exclusive',
    device: { driver: 'WDM-KS', buffer: 512, rate: 44100, out_ms: 10.0, closed: false }
  }
}

export const meter = () => {
  if (off() || !rig.armed) return { l: 0, r: 0, clip: false, live: false }
  // A plucked string, roughly: so the meter looks like an amp being played, not a random walk.
  const t = Date.now() / 1000
  const pluck = Math.max(0, Math.sin(t * 2.1)) ** 3
  const p = Math.min(0.92, 0.03 + pluck * (0.42 + 0.1 * Math.sin(t * 37)))
  return { l: p, r: p * 0.93, clip: p > 0.9, live: true }
}

export const library = () => {
  const empty = bare() && !adopted.capture
  return {
    ok: true,
    captures: empty ? [] : CAPTURES,
    cabs: bare() && !adopted.cab ? [] : CABS,
    capture: rig.capture_path,
    cab: rig.cab_path,
    // The folders already configured: the empty picker names them, so a player can tell "you looked
    // in the wrong place" from "there is nothing there".
    capture_dirs: ['C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\NAM'],
    cab_dirs: ['C:\\Users\\Chris\\Documents\\Guitar Amp Sims\\IRs']
  }
}

// Saved sounds, kept in memory: the harness can save one and load it back without a file.
export const SAVED = [{ name: 'lead', capture: 'Bugera 6262 Lead', cab: 'AURORA 212 GOV',
                        bass: 3, mid: -1, treble: 2, input_gain_db: -6, output_gain_db: -3,
                        gate_db: -58 }]

const rigLine = (row) =>
  `amphead:${row.name} | capture=${row.capture};cab=${row.cab};bass=${row.bass};` +
  `mid=${row.mid};treble=${row.treble};in=${row.input_gain_db};out=${row.output_gain_db};` +
  `gate=${row.gate_db}`

export const rigs = () => ({
  ok: true,
  rigs: SAVED.map((row) => ({
    name: row.name,
    summary: `${row.capture} into ${row.cab}, bass ${row.bass >= 0 ? '+' : ''}${row.bass}`,
    line: rigLine(row)
  })),
  current: { capture: rig.capture, cab: rig.cab, bass: 0, mid: 0, treble: 0,
             input_gain_db: rig.input_gain_db || 0, output_gain_db: rig.output_gain_db || 0,
             gate_db: rig.gate_db || -80 }
})

// Every call the face makes. The harness reads this: a click that never reaches a route, or one
// that reaches the wrong one, is visible without the app.
export const calls = []

export async function rest(path, options) {
  const [bare, search] = String(path).split('?')
  const query = new URLSearchParams(search || '')
  const engine = (query.get('engine') || '').toLowerCase()
  const body = (options && options.body) || {}
  calls.push(`${engine || 'no-engine'} ${bare}`)

  if (bare === '/state') return state()
  if (bare === '/meter') return meter()
  if (bare === '/library') return library()
  // The press: a walk that only happens because somebody asked for it. Nothing in the fixture is
  // read before this is called, which is what `window.__amphead.calls` lets a test check.
  if (bare === '/library/find') {
    return { ok: true, candidates: FOUND, scanned: ['C:\\Users\\Chris\\Documents', 'C:\\Users\\Chris\\Downloads'] }
  }
  if (bare === '/library/adopt') {
    const kind = body.kind === 'cab' ? 'cab' : 'capture'
    adopted[kind] = true
    return { ok: true, output: `added ${body.path} to ${kind}_dirs` }
  }
  if (bare === '/rigs') return rigs()
  if (bare === '/rig') {
    if (body.action === 'save') {
      const name = String(body.name || '').trim()
      if (!name) return { ok: false, output: 'NOT SAVED: a rig needs a name' }
      const had = SAVED.some((row) => row.name.toLowerCase() === name.toLowerCase())
      SAVED.push({ name, capture: rig.capture, cab: rig.cab, bass: rig.tone.bass,
                   mid: rig.tone.mid, treble: rig.tone.treble,
                   input_gain_db: rig.input_gain_db || 0,
                   output_gain_db: rig.output_gain_db || 0, gate_db: rig.gate_db || -80 })
      return { ok: true, output: `${had ? 'REPLACED' : 'SAVED'}: ${name}` }
    }
    const found = SAVED.find((row) => row.name.toLowerCase() === String(body.name || '').toLowerCase())
    if (!found) return { ok: false, output: `NOT FOUND: no rig called ${body.name || 'anything'}` }
    rig.capture = found.capture
    rig.cab = found.cab
    rig.tone = { bass: found.bass, mid: found.mid, treble: found.treble }
    return { ok: true, output: `LOADED: ${found.name}` }
  }
  if (bare === '/setup') {
    rig.running = true
    return { ok: true, output: 'engine up: silent until live (master -60 dB)' }
  }
  if (bare === '/capture') {
    const found = CAPTURES.find((row) => row.path === body.path)
    if (found) { rig.capture = found.name; rig.capture_path = found.path }
    return { ok: true, output: `capture ${found ? found.name : 'unchanged'}` }
  }
  if (bare === '/cab') {
    const found = CABS.find((row) => row.path === body.path)
    if (found) { rig.cab = found.name; rig.cab_path = found.path }
    return { ok: true, output: `cabinet ${found ? found.name : 'unchanged'}` }
  }
  // The three tone knobs answer in dB, because that is what the engine reports back.
  if (bare === '/knob') {
    const sent = String(body.param === undefined ? '' : body.param)
    // The face sends a knob index; a name works too. Both land on the same three values.
    const key = /^[0-9]+$/.test(sent) ? ['bass', 'mid', 'treble'][Number(sent)] : sent
    const db = Math.max(-12, Math.min(12, Number(body.value)))
    if (key && key in rig.tone) rig.tone[key] = db
    return { ok: true, norm: body.value, value: db, param_name: key,
             output: `${key} ${db > 0 ? '+' : ''}${db.toFixed(1)} dB` }
  }
  // The two trims either side of the amp: -24..+24, and they never touch the master.
  if (bare === '/input_gain' || bare === '/output_gain') {
    const key = bare === '/input_gain' ? 'input_gain_db' : 'output_gain_db'
    const asked = Number(body.db)
    const db = Number.isFinite(asked) ? Math.max(-24, Math.min(24, asked)) : rig[key]
    rig[key] = db
    return { ok: true, db, output: `${bare === '/input_gain' ? 'input' : 'output'} gain ${db} dB` }
  }
  // The gate: -80 means off, -20 is as high as the threshold goes.
  if (bare === '/gate') {
    const asked = Number(body.db)
    const db = Number.isFinite(asked) ? Math.max(-80, Math.min(-20, asked)) : rig.gate_db
    rig.gate_db = db
    return { ok: true, db, output: db <= -79.5 ? 'gate off' : `gate ${Math.round(db)} dB` }
  }
  if (bare === '/tune') {
    return { ok: true, blocksize: 128, changed: true, round_trip_ms: 12.9,
             output: '128 frames, 2.9 ms a block, 5 s with nothing dropped' }
  }
  // The ramp: -60 is silent, +12 is the engine's own ceiling (not a DAW's 0).
  if (bare === '/master') {
    const asked = Number(body.db)
    const db = Number.isFinite(asked) ? Math.max(-60, Math.min(12, asked)) : rig.master_db
    rig.master_db = db
    return { ok: true, db, output: db <= -60 ? 'silent; raise it in steps and listen' : `master at ${db} dB` }
  }
  if (bare === '/live') {
    rig.armed = body.on === undefined ? !rig.armed : !!body.on
    if (body.master_db !== undefined) rig.master_db = Number(body.master_db)
    // Arming is the moment a player asks to hear the chain again, so the stages silence closed
    // come back, exactly as the live route does it.
    const back = rig.stages.filter((stage) => !stage.enabled).map((stage) => stage.name)
    if (rig.armed) rig.stages.forEach((stage) => { stage.enabled = true })
    return { ok: true, on: rig.armed, reengaged: back,
             output: rig.armed
               ? `live=True | armed=True | master ${rig.master_db} dB${back.length ? ` | re-engaged=${back.join(', ')}` : ''}`
               : 'live=False | armed=False | master parked' }
  }
  if (bare === '/panic') {
    if (body.restore) {
      rig.stages.forEach((stage) => { stage.enabled = true })
      return { ok: true, output: 'restored: chain back in, still silent' }
    }
    rig.armed = false
    rig.master_db = -60
    rig.stages.forEach((stage) => { stage.enabled = false })
    return { ok: true, output: 'silenced: disarmed, master at -60 dB, stages out of the path' }
  }
  return { ok: false, output: `unknown route ${bare}` }
}
