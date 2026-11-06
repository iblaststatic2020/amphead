// AmpHead amp face: the pane that proves the rig is alive while you play.
// Everything here comes from /api/plugins/amphead/state and /meter, which read REAPER directly,
// so the pane carries every number REAPER would show while REAPER itself stays on the taskbar.

import { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

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
  'NO REAPER INSTALL': 'var(--ui-danger, var(--ui-text))'
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

function Meter({ peak, clip }) {
  const db = toDb(peak)
  const width = Math.max(0, Math.min(100, ((db + 60) / 60) * 100))
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsxs('div', {
        className: 'flex items-baseline justify-between',
        children: [
          jsx('span', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'input' }),
          jsx('span', {
            className: 'text-xs tabular-nums',
            style: clip ? VERDICT_COLOR.CLIPPING : SECOND,
            children: `${db.toFixed(1)} dB${clip ? '  CLIPPED' : ''}`
          })
        ]
      }),
      jsx('div', {
        className: 'h-2 w-full overflow-hidden rounded-full',
        style: { background: 'var(--ui-stroke-secondary)' },
        children: jsx('div', {
          className: 'h-full rounded-full transition-[width] duration-75',
          style: { width: `${width}%`, background: clip ? 'var(--ui-danger, var(--ui-accent))' : 'var(--ui-accent)' }
        })
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

  async function act(path, body) {
    setBusy(true)
    setSaid('')
    try {
      const out = await rest(path, { method: 'POST', body })
      setSaid(String(out && out.output ? out.output : 'done').split('\n')[0])
      client.invalidateQueries({ queryKey: ['amphead'] })
      if (out && out.ok === false) host.notify({ kind: 'warning', message: String(out.output).split('\n')[0] })
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

  const liveLabel = armed
    ? 'STANDBY: disarm and park the master at -60 dB'
    : 'LIVE: arm, monitor and play at -18 dB'

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

      jsx(Meter, { peak, clip }),

      jsx('button', {
        type: 'button',
        disabled: busy,
        onClick: () => act('/live', { on: !armed, master_db: -18 }),
        className: 'w-full rounded-md px-3 py-2 text-xs font-medium disabled:opacity-50',
        style: armed
          ? { background: 'var(--ui-stroke-secondary)', color: 'var(--ui-text)' }
          : { background: 'var(--ui-accent)', color: 'var(--ui-accent-foreground, #fff)' },
        children: busy ? 'working' : liveLabel
      }),

      jsxs('button', {
        type: 'button',
        disabled: busy,
        onClick: () => act('/panic', silenced ? { restore: true, master_db: -18 } : {}),
        className: 'w-full rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50',
        style: silenced
          ? { background: 'var(--ui-stroke-secondary)', color: 'var(--ui-text)' }
          : { background: '#8f1d20', color: '#fff' },
        children: busy
          ? 'working'
          : silenced
            ? 'RESTORE: un-bypass the chain and lift the master'
            : 'PANIC: silence the rig now'
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'chain' }),
          (s.chain || []).length === 0
            ? jsx('div', { className: 'text-xs', style: MUTED, children: 'no plugins loaded on the guitar track' })
            : (s.chain || []).map((fx, i) =>
                jsx(Row, {
                  key: `${i}-${fx.name}`,
                  label: `${i + 1}. ${fx.enabled ? 'on' : 'bypassed'}`,
                  children: fx.name
                }))
        ]
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'text-[0.6875rem] uppercase tracking-wide', style: MUTED, children: 'amp' }),
          (s.knobs || []).length === 0
            ? jsx('div', { className: 'text-xs', style: MUTED, children: 'no amp loaded yet' })
            : (s.knobs || []).map((k) => jsx(Row, { key: k.name, label: k.name, children: k.value }))
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
        function Chip() {
          const meter = useQuery({ queryKey: ['amphead', 'chip'], queryFn: () => ctx.rest('/meter'), refetchInterval: 400 })
          const peak = Math.max((meter.data && meter.data.l) || 0, (meter.data && meter.data.r) || 0)
          return jsx('span', {
            className: 'px-1.5 text-[0.6875rem] tabular-nums',
            style: meter.data && meter.data.live ? ACCENT : MUTED,
            children: `${toDb(peak).toFixed(0)} dB`
          })
        }
        return jsx(Chip, {})
      }
    })
  }
}
