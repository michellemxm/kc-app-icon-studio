/**
 * Icon Studio — two-pane layout, ported from the builtin Notes app.
 *
 * Left panel  = the library switcher + the list of icon requests in that library.
 * Right pane  = one request's workflow, the whole-library icon gallery, or a
 *               create form.
 *
 * Geometry, radii, and token usage are lifted from
 * website/src/apps/md-notebook/ so the two apps read as one product. Deviations
 * from Notes are marked DEVIATION with a reason.
 *
 * Element construction uses the `el()` helper rather than raw _jsx/_jsxs calls:
 * picking the wrong one of that pair by hand is a silent key/children bug, and
 * this file has far more nodes than the version it replaces.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'

const API = '/api/apps/icon-studio'
const POLL_MS = 15000

// --- tokens --------------------------------------------------------------------
// Notes uses bare var(--x) with no fallbacks. Fallbacks are kept here for the
// three newer tokens an older custom theme may not define, so the panel can
// never render as an unpainted rectangle.
const ACCENT = 'var(--accent)'
const ACCENT_BG = 'var(--accent-subtle)'
const ACCENT_FG = 'var(--accent-fg)'
const TEXT = 'var(--text)'
const MUTED = 'var(--muted)'
const BORDER = 'var(--border)'
// Notes' bottom-bar separator uses --border-strong; fall back to --border so an
// older custom theme that never defined it still paints a line.
const BORDER_STRONG = 'var(--border-strong, var(--border))'
const CARD = 'var(--card)'
const BG = 'var(--bg)'
const ELEVATED = 'var(--bg-elevated, var(--card))'
const HOVER = 'var(--bg-hover, var(--card))'
const FONT_BODY = 'var(--font-body)'

const PANEL_MIN_WIDTH = 180
const PANEL_MAX_WIDTH = 420
const PANEL_DEFAULT_WIDTH = 260
const COLUMN_MAX_WIDTH = 800
const COLUMN_PAD_X = 20

const LS = {
  panelWidth: 'icon-studio-panel-width',
  panelOpen: 'icon-studio-panel-open',
  library: 'icon-studio-library',
}

const STATUS = {
  queued: { label: 'Queued', tone: 'muted' },
  concepts: { label: 'Metaphors ready', tone: 'accent' },
  drawing: { label: 'Drawing', tone: 'accent' },
  proofing: { label: 'Proofing', tone: 'warn' },
  done: { label: 'Done', tone: 'ok' },
  failed: { label: 'Failed', tone: 'danger' },
}

/** The agent's five-step process, as a rail the user can watch advance. */
const STEPS = ['Brief', 'Metaphors', 'Draw', 'Proof', 'Done']
const STEP_OF = { queued: 0, concepts: 1, drawing: 2, proofing: 3, done: 5, failed: -1 }

// --- element helper ------------------------------------------------------------

function el(type, props, children, key) {
  const p = children === undefined ? props || {} : { ...(props || {}), children }
  return Array.isArray(children) ? _jsxs(type, p, key) : _jsx(type, p, key)
}

// --- scoped stylesheet ---------------------------------------------------------
// Inline styles cannot express :hover / :focus / ::placeholder. Same reason
// Notes ships MDNB_CSS; the left-panel rules below are its rules verbatim,
// renamed is- for mdnb-. Notably the row tint has NO transition in Notes -- it
// lands instantly -- and the icon buttons carry none either.
const CSS = `
.is-row:hover{background:${HOVER};color:${TEXT}}
.is-collapse{color:${MUTED};background:transparent}
.is-collapse:hover{color:${TEXT};background:${HOVER}}
.is-trigger:hover{background:${HOVER}}
.is-trigger:hover span{color:${TEXT}}
.is-act:hover{background:${HOVER};color:${TEXT}}
.is-field{transition:border-color .2s,box-shadow .2s}
.is-field:focus{outline:none;border-color:var(--ring,${ACCENT});box-shadow:0 0 0 3px ${ACCENT_BG}}
.is-field::placeholder{color:color-mix(in srgb,${MUTED} 50%,transparent)}
.is-tile{transition:border-color .12s,background .12s}
.is-tile:hover{border-color:${ACCENT};background:${HOVER}}
.is-primary:hover:not(:disabled){filter:brightness(1.08)}
.is-ghost:hover{background:${HOVER};color:${TEXT}}
`

function useStyleSheet() {
  useEffect(() => {
    const id = 'icon-studio-css'
    if (document.getElementById(id)) return
    const tag = document.createElement('style')
    tag.id = id
    tag.textContent = CSS
    document.head.appendChild(tag)
  }, [])
}

// --- icons ---------------------------------------------------------------------
// Hand-rolled: an external app cannot import the dashboard's lucide bundle.
// 24x24 viewBox, currentColor, 1.75 stroke — reads at 13-16px like lucide does.

function Svg({ size = 16, children, style, fill }) {
  return el(
    'svg',
    {
      width: size,
      height: size,
      viewBox: '0 0 24 24',
      fill: fill || 'none',
      stroke: fill ? 'none' : 'currentColor',
      strokeWidth: 1.75,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      'aria-hidden': 'true',
      style: { flexShrink: 0, ...(style || {}) },
    },
    children,
  )
}

/** Notes' own PanelLeft geometry, so the toggle is pixel-identical. */
function PanelLeftIcon({ open, size = 16 }) {
  return el('svg', {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': 'true',
    style: { flexShrink: 0 },
    children: [
      _jsx('rect', { x: 2.5, y: 3.25, width: 19, height: 17.5, rx: 3 }, 'frame'),
      _jsx(
        'rect',
        {
          x: 4.5,
          y: 5.25,
          width: open ? 6.5 : 2.4,
          height: 13.5,
          rx: open ? 1.4 : 1.2,
          fill: 'currentColor',
          fillOpacity: open ? 0.45 : 1,
          stroke: 'none',
        },
        'pane',
      ),
    ],
  })
}

const ChevronDown = (p) => el(Svg, p, _jsx('path', { d: 'M6 9l6 6 6-6' }))
const Plus = (p) => el(Svg, p, _jsx('path', { d: 'M12 5v14M5 12h14' }))
const Check = (p) => el(Svg, p, _jsx('path', { d: 'M20 6L9 17l-5-5' }))
const Grid = (p) =>
  el(Svg, p, [
    _jsx('rect', { x: 3, y: 3, width: 7, height: 7, rx: 1.5 }, 'a'),
    _jsx('rect', { x: 14, y: 3, width: 7, height: 7, rx: 1.5 }, 'b'),
    _jsx('rect', { x: 3, y: 14, width: 7, height: 7, rx: 1.5 }, 'c'),
    _jsx('rect', { x: 14, y: 14, width: 7, height: 7, rx: 1.5 }, 'd'),
  ])
const Refresh = (p) =>
  el(Svg, p, [
    _jsx('path', { d: 'M21 12a9 9 0 1 1-3-6.7' }, 'a'),
    _jsx('path', { d: 'M21 4v5h-5' }, 'b'),
  ])
const Folder = (p) =>
  el(
    Svg,
    p,
    _jsx('path', {
      d: 'M4 20a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2z',
    }),
  )
const Pen = (p) =>
  el(Svg, p, [
    _jsx('path', { d: 'M12 20h9' }, 'a'),
    _jsx('path', { d: 'M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z' }, 'b'),
  ])

// --- primitives ----------------------------------------------------------------

const iconBtn = {
  width: '28px',
  height: '28px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: '8px',
  background: 'transparent',
  border: 'none',
  color: MUTED,
  cursor: 'pointer',
  flexShrink: 0,
}

const fieldStyle = {
  width: '100%',
  boxSizing: 'border-box',
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: '8px',
  padding: '7px 10px',
  fontSize: '12px',
  color: TEXT,
  fontFamily: FONT_BODY,
}

const labelStyle = {
  fontSize: '11px',
  color: MUTED,
  display: 'block',
  marginBottom: '4px',
}

const sectionLabel = {
  fontSize: '10px',
  textTransform: 'uppercase',
  letterSpacing: '.04em',
  color: MUTED,
  marginBottom: '8px',
}

/** Notes' primary pill: 28px, radius 12, accent fill. */
function Primary({ label, onClick, disabled, busy, icon }) {
  return el(
    'button',
    {
      type: 'button',
      className: 'is-primary',
      onClick,
      disabled: !!disabled,
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        height: '28px',
        padding: '0 14px',
        borderRadius: '12px',
        // FIX: the old build dropped background AND border when disabled, so the
        // page's main action rendered as bare grey text. A disabled primary now
        // keeps its outline and stays legible as a button.
        background: disabled ? 'transparent' : ACCENT,
        color: disabled ? MUTED : ACCENT_FG,
        border: disabled ? `1px solid ${BORDER}` : '1px solid transparent',
        fontSize: '11px',
        fontWeight: 500,
        fontFamily: FONT_BODY,
        cursor: disabled ? 'default' : 'pointer',
      },
    },
    [icon || null, el('span', {}, busy ? 'Working…' : label, 'l')],
  )
}

function Ghost({ label, onClick, icon, title }) {
  return el(
    'button',
    {
      type: 'button',
      className: 'is-ghost',
      onClick,
      title,
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        height: '28px',
        padding: '0 12px',
        borderRadius: '8px',
        background: 'transparent',
        border: `1px solid ${BORDER}`,
        color: TEXT,
        fontSize: '11px',
        fontWeight: 500,
        fontFamily: FONT_BODY,
        cursor: 'pointer',
      },
    },
    [icon || null, el('span', {}, label, 'l')],
  )
}

function Badge({ status }) {
  const meta = STATUS[status] || STATUS.queued
  const tone = {
    muted: { bg: CARD, fg: MUTED, br: BORDER },
    accent: { bg: ACCENT_BG, fg: ACCENT, br: ACCENT },
    warn: { bg: 'var(--warn-subtle)', fg: 'var(--warn)', br: 'var(--warn)' },
    ok: { bg: 'var(--ok-subtle, var(--card))', fg: 'var(--ok)', br: 'var(--ok)' },
    danger: { bg: 'var(--danger-subtle)', fg: 'var(--danger)', br: 'var(--danger)' },
  }[meta.tone]
  return el(
    'span',
    {
      style: {
        padding: '1px 6px',
        borderRadius: '4px',
        fontSize: '10px',
        fontWeight: 500,
        lineHeight: 1,
        border: '1px solid',
        display: 'inline-flex',
        alignItems: 'center',
        flexShrink: 0,
        background: tone.bg,
        color: tone.fg,
        borderColor: tone.br,
      },
    },
    meta.label,
  )
}

function Chip({ children }) {
  return el(
    'span',
    {
      style: {
        padding: '3px 8px',
        borderRadius: '6px',
        fontSize: '11px',
        color: MUTED,
        background: CARD,
        border: `1px solid ${BORDER}`,
        whiteSpace: 'nowrap',
      },
    },
    children,
  )
}

/** Inline SVG markup. Sanitized server-side (store.sanitize_svg) because this
 *  app mounts into the dashboard DOM, not an iframe. An <img> is not an option:
 *  house icons use currentColor, which an <img> cannot resolve. */
function IconArt({ svg, px }) {
  if (!svg) {
    return el('div', {
      style: {
        width: `${px}px`,
        height: `${px}px`,
        borderRadius: '3px',
        border: `1px dashed ${BORDER}`,
      },
    })
  }
  return el('span', {
    style: {
      display: 'inline-flex',
      width: `${px}px`,
      height: `${px}px`,
      color: TEXT,
      lineHeight: 0,
    },
    dangerouslySetInnerHTML: { __html: svg },
  })
}

// --- helpers -------------------------------------------------------------------

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

function relTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const days = (now - d) / 86400000
  if (days < 7) {
    return d.toLocaleDateString([], { weekday: 'short' })
  }
  const opts = { month: 'short', day: 'numeric' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  return d.toLocaleDateString([], opts)
}

const paramSummary = (p) =>
  p ? `${p.canvas}px · ${p.stroke}px · ${p.style} · ${p.keyline}` : ''

function loadPref(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : JSON.parse(raw)
  } catch {
    return fallback
  }
}

function savePref(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* private mode — preferences are cosmetic, never block on this */
  }
}

async function api(path, options) {
  const resp = await fetch(`${API}${path}`, options)
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.error || `request failed (${resp.status})`)
  return data
}

const jsonPost = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// --- left panel ----------------------------------------------------------------

function LibraryMenu({ libraries, activeId, onPick, onNew, onClose }) {
  return el(
    'div',
    {
      role: 'listbox',
      style: {
        position: 'absolute',
        top: '38px',
        left: '38px',
        minWidth: '180px',
        maxWidth: 'calc(100% - 46px)',
        maxHeight: '112px',
        overflowY: 'auto',
        background: ELEVATED,
        border: `1px solid ${BORDER}`,
        borderRadius: '8px',
        boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
        padding: '4px',
        zIndex: 20,
      },
    },
    [
      ...libraries.map((lib) =>
        el(
          'div',
          {
            role: 'option',
            'aria-selected': lib.id === activeId,
            tabIndex: 0,
            className: 'is-row',
            onClick: () => {
              onPick(lib.id)
              onClose()
            },
            onKeyDown: (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onPick(lib.id)
                onClose()
              }
            },
            style: {
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '5px 8px',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '13px',
              color: lib.id === activeId ? TEXT : MUTED,
            },
          },
          [
            el(
              'span',
              {
                style: {
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                },
              },
              lib.name,
              'n',
            ),
            lib.id === activeId ? el(Check, { size: 14, style: { color: ACCENT } }, undefined, 'k') : null,
          ],
          lib.id,
        ),
      ),
      el('div', { style: { height: '1px', background: BORDER, margin: '4px 0' } }, undefined, 'sep'),
      el(
        'div',
        {
          role: 'option',
          tabIndex: 0,
          className: 'is-row',
          onClick: () => {
            onNew()
            onClose()
          },
          onKeyDown: (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onNew()
              onClose()
            }
          },
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '5px 8px',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '13px',
            color: MUTED,
          },
        },
        'New library…',
        'new',
      ),
    ],
  )
}

function RequestRow({ job, active, onClick }) {
  const names = job.params?.names || []
  return el(
    'div',
    {
      role: 'button',
      tabIndex: 0,
      className: 'is-row',
      onClick,
      onKeyDown: (e) => {
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onClick()
        }
      },
      style: {
        position: 'relative',
        padding: '8px 16px',
        borderRadius: '8px',
        cursor: 'pointer',
        ...(active ? { background: ACCENT_BG } : null),
      },
    },
    [
      // Notes wraps its title in this flex row to seat the pin glyph. Kept
      // even with nothing beside the title, so the two rows share one structure.
      el(
        'div',
        { style: { display: 'flex', alignItems: 'center', gap: '4px', minWidth: 0 } },
        el(
          'div',
          {
            style: {
              fontSize: '13px',
              fontWeight: 600,
              lineHeight: 1.375,
              color: TEXT,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            },
          },
          names.length ? names.join(', ') : job.id,
        ),
        't',
      ),
      el(
        'div',
        {
          style: {
            display: 'flex',
            gap: '6px',
            alignItems: 'center',
            marginTop: '2px',
            minWidth: 0,
          },
        },
        [
          el(Badge, { status: job.status }, undefined, 'b'),
          // FIX: was hardcoded "1 icons".
          el(
            'span',
            { style: { fontSize: '11px', fontWeight: 400, color: MUTED, flexShrink: 0 } },
            plural(names.length, 'icon'),
            'n',
          ),
          el(
            'span',
            { style: { fontSize: '11px', fontWeight: 400, color: MUTED, flexShrink: 0 } },
            '·',
            'd',
          ),
          el(
            'span',
            { style: { fontSize: '11px', fontWeight: 400, color: MUTED, flexShrink: 0 } },
            relTime(job.createdAt),
            'r',
          ),
        ],
        'm',
      ),
    ],
  )
}

// --- right pane views ----------------------------------------------------------

function EmptyState({ onCreate }) {
  return el(
    'div',
    {
      style: {
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '10px',
        padding: '24px',
        textAlign: 'center',
      },
    },
    [
      el(Grid, { size: 26, style: { color: MUTED, opacity: 0.55 } }, undefined, 'i'),
      el(
        'div',
        { style: { fontSize: '13px', fontWeight: 600, color: TEXT } },
        'No icons in this library yet',
        'h',
      ),
      el(
        'div',
        { style: { fontSize: '12px', color: MUTED, maxWidth: '320px', lineHeight: 1.5 } },
        'Describe the behaviours you need an icon for — not the pictures — and the designer agent brings back metaphors before it draws.',
        'b',
      ),
      el('div', { style: { marginTop: '4px' } }, el(Primary, { label: 'Create new icons', onClick: onCreate, icon: el(Plus, { size: 13 }) }), 'a'),
    ],
  )
}

function ParamGrid({ values, onChange }) {
  const field = (key, label, input) =>
    el('div', { style: { minWidth: 0 } }, [el('label', { style: labelStyle }, label, 'l'), input], key)

  const select = (key, options) =>
    el(
      'select',
      {
        className: 'is-field',
        value: values[key],
        onChange: (e) => onChange({ ...values, [key]: e.target.value }),
        style: { ...fieldStyle, padding: '6px 8px' },
      },
      options.map((o) => el('option', { value: o }, o, o)),
    )

  return el(
    'div',
    {
      // FIX: was `repeat(4, 1fr)`, which stretched a one-character stroke input
      // to ~430px. auto-fit with a 132px cap sizes each control to its content.
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(0, 132px))',
        gap: '10px',
      },
    },
    [
      field(
        'canvas',
        'Canvas',
        el('input', {
          className: 'is-field',
          type: 'number',
          min: 8,
          max: 512,
          value: values.canvas,
          onChange: (e) => onChange({ ...values, canvas: e.target.value }),
          style: { ...fieldStyle, padding: '6px 8px' },
        }),
      ),
      field(
        'stroke',
        'Stroke',
        el('input', {
          className: 'is-field',
          type: 'number',
          step: 0.25,
          min: 0.25,
          max: 8,
          value: values.stroke,
          onChange: (e) => onChange({ ...values, stroke: e.target.value }),
          style: { ...fieldStyle, padding: '6px 8px' },
        }),
      ),
      field('style', 'Style', select('style', ['outline', 'filled'])),
      field('keyline', 'Keyline', select('keyline', ['square', 'circle'])),
    ],
  )
}

function CreateView({ library, onSubmit, onEditParams, busy, error }) {
  const [names, setNames] = useState('')
  const [mode, setMode] = useState('concepts')
  const [notes, setNotes] = useState('')
  const count = names
    .replace(/,/g, '\n')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean).length

  return el('div', {}, [
    el(
      'div',
      { style: { marginBottom: '18px' } },
      [
        el('div', { style: sectionLabel }, 'Library parameters', 's'),
        el(
          'div',
          { style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } },
          [
            el(Chip, {}, `${library.params.canvas}px canvas`, 'a'),
            el(Chip, {}, `${library.params.stroke}px stroke`, 'b'),
            el(Chip, {}, library.params.style, 'c'),
            el(Chip, {}, `${library.params.keyline} keyline`, 'd'),
            el(
              'button',
              {
                type: 'button',
                className: 'is-ghost',
                onClick: onEditParams,
                style: {
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '3px 8px',
                  borderRadius: '6px',
                  background: 'transparent',
                  border: `1px dashed ${BORDER}`,
                  color: MUTED,
                  fontSize: '11px',
                  fontFamily: FONT_BODY,
                  cursor: 'pointer',
                },
              },
              [el(Pen, { size: 11 }, undefined, 'i'), el('span', {}, 'Edit', 'l')],
              'e',
            ),
          ],
          'c',
        ),
        el(
          'div',
          { style: { fontSize: '11px', color: MUTED, marginTop: '8px', lineHeight: 1.5 } },
          `Every request in "${library.name}" inherits these, which is what keeps its icons a set. Editing them affects new requests only.`,
          'h',
        ),
      ],
      'params',
    ),
    el(
      'div',
      { style: { marginBottom: '14px' } },
      [
        el('label', { style: labelStyle }, 'Behaviours to draw — one per line', 'l'),
        el('textarea', {
          className: 'is-field',
          value: names,
          onChange: (e) => setNames(e.target.value),
          rows: 5,
          placeholder: 'waiting on a human to approve\nrun finished but output was discarded\nthis setting only applies locally',
          style: { ...fieldStyle, resize: 'vertical', lineHeight: 1.6 },
        }, undefined, 't'),
        el(
          'div',
          { style: { fontSize: '11px', color: MUTED, marginTop: '6px' } },
          'Describe the behaviour, not the picture. "Waiting on approval" gets you a considered metaphor; "clock" gets you a clock.',
          'h',
        ),
      ],
      'names',
    ),
    el(
      'div',
      { style: { marginBottom: '14px', maxWidth: '320px' } },
      [
        el('label', { style: labelStyle }, 'Your involvement', 'l'),
        el(
          'select',
          {
            className: 'is-field',
            value: mode,
            onChange: (e) => setMode(e.target.value),
            style: fieldStyle,
          },
          [
            el('option', { value: 'concepts' }, 'Show me three metaphors first', 'c'),
            el('option', { value: 'ship' }, 'Just draw it — pick the best metaphor', 's'),
          ],
          's',
        ),
      ],
      'mode',
    ),
    el(
      'div',
      { style: { marginBottom: '18px' } },
      [
        el('label', { style: labelStyle }, 'Notes (optional)', 'l'),
        el('textarea', {
          className: 'is-field',
          value: notes,
          onChange: (e) => setNotes(e.target.value),
          rows: 2,
          placeholder: 'Anything the agent should know — references, things to avoid.',
          style: { ...fieldStyle, resize: 'vertical' },
        }, undefined, 't'),
      ],
      'notes',
    ),
    error
      ? el(
          'div',
          {
            style: {
              margin: '0 0 12px',
              padding: '8px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              background: 'var(--danger-subtle)',
              color: 'var(--danger)',
            },
          },
          error,
          'err',
        )
      : null,
    el(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '10px' } },
      [
        el(
          Primary,
          {
            label: count ? `Design ${plural(count, 'icon')}` : 'Design icons',
            disabled: busy || count === 0,
            busy,
            onClick: () => onSubmit({ names, mode, notes }),
          },
          undefined,
          'p',
        ),
        count > 0
          ? el('span', { style: { fontSize: '11px', color: MUTED } }, `${plural(count, 'name')} parsed`, 'c')
          : null,
      ],
      'actions',
    ),
  ])
}

/** Library creation asks for a NAME only.
 *
 *  Parameters deliberately are not here. They default to the house set and are
 *  revisable later from the parameter editor, which can also redraw the whole
 *  set -- so committing to a canvas and stroke before a single icon exists
 *  would be asking the user to decide the thing they have least information
 *  about, at the moment they have least information. */
function NewLibraryView({ onCreate, busy, error, defaults }) {
  const [name, setName] = useState('')
  const submit = () => onCreate({ name })
  return el('div', {}, [
    el(
      'div',
      { style: { maxWidth: '360px' } },
      [
        el('label', { style: labelStyle }, 'Library name', 'l'),
        el('input', {
          className: 'is-field',
          value: name,
          onChange: (e) => setName(e.target.value),
          onKeyDown: (e) => {
            if (e.key === 'Enter' && name.trim() && !busy) submit()
          },
          placeholder: 'Product icons',
          autoFocus: true,
          style: fieldStyle,
        }, undefined, 'i'),
      ],
      'name',
    ),
    el(
      'div',
      { style: { fontSize: '11px', color: MUTED, marginTop: '10px', lineHeight: 1.55 } },
      defaults
        ? `Starts on the house spec — ${defaults}. Every request in the library inherits it, so the icons stay a set. You can change it later and redraw everything at the new spec.`
        : 'Every request in the library inherits one shared spec, so the icons stay a set.',
      'h',
    ),
    el(
      'div',
      { style: { fontSize: '11px', color: MUTED, marginTop: '6px', lineHeight: 1.55 } },
      'The icons are saved to a folder of their own under the Kiro Crew workspace. You can point that at any local folder afterwards.',
      'h2',
    ),
    error
      ? el(
          'div',
          {
            style: {
              margin: '12px 0 0',
              padding: '8px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              background: 'var(--danger-subtle)',
              color: 'var(--danger)',
            },
          },
          error,
          'err',
        )
      : null,
    el(
      'div',
      { style: { marginTop: '18px' } },
      el(Primary, {
        label: 'Create library',
        disabled: busy || !name.trim(),
        busy,
        onClick: submit,
      }),
      'a',
    ),
  ])
}

function StepRail({ status }) {
  const at = STEP_OF[status] ?? 0
  if (at < 0) {
    return el(
      'div',
      { style: { fontSize: '11px', color: 'var(--danger)', marginBottom: '14px' } },
      'This request failed before it finished.',
    )
  }
  return el(
    'div',
    {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        marginBottom: '18px',
        flexWrap: 'wrap',
      },
    },
    STEPS.map((label, i) => {
      const done = i < at
      const current = i === at
      return el(
        'div',
        { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
        [
          el(
            'span',
            {
              style: {
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                padding: '3px 9px',
                borderRadius: '9999px',
                fontSize: '11px',
                fontWeight: current ? 600 : 400,
                background: current ? ACCENT_BG : 'transparent',
                color: current ? ACCENT : done ? TEXT : MUTED,
                border: `1px solid ${current ? ACCENT : 'transparent'}`,
              },
            },
            [
              done ? el(Check, { size: 11 }, undefined, 'c') : null,
              el('span', {}, label, 'l'),
            ],
            'p',
          ),
          i < STEPS.length - 1
            ? el(
                'span',
                { style: { width: '10px', height: '1px', background: BORDER } },
                undefined,
                'd',
              )
            : null,
        ],
        label,
      )
    }),
  )
}

// HERO_PX is the largest size the tile renders. `single` collapses the tile to
// that one size only -- the gallery ("All icons") shows one size per icon; the
// request view keeps the multi-size row.
const HERO_PX = 32

function IconTile({ icon, canvas, single }) {
  return el(
    'div',
    {
      className: 'is-tile',
      style: {
        border: `1px solid ${BORDER}`,
        borderRadius: '8px',
        padding: '12px 10px',
        background: CARD,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '10px',
        minWidth: 0,
      },
    },
    [
      el(IconArt, { svg: icon.svg, px: HERO_PX }, undefined, 'hero'),
      single
        ? null
        : el(
            'div',
            { style: { display: 'flex', gap: '10px', alignItems: 'flex-end' } },
            [
              el(IconArt, { svg: icon.svg, px: 16 }, undefined, 'a'),
              el(IconArt, { svg: icon.svg, px: canvas === 24 ? 24 : 24 }, undefined, 'b'),
            ],
            'sizes',
          ),
      el(
        'div',
        {
          style: {
            fontSize: '11px',
            color: TEXT,
            textAlign: 'center',
            lineHeight: 1.4,
            // Mockup defect fixed: labels truncated to "approval pe…". Two lines
            // with a wrap instead of one line with an ellipsis.
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            wordBreak: 'break-word',
          },
        },
        icon.name,
        'n',
      ),
      icon.metaphor
        ? el(
            'div',
            { style: { fontSize: '10px', color: MUTED, textAlign: 'center' } },
            icon.metaphor,
            'm',
          )
        : null,
    ],
  )
}

function IconGrid({ icons, canvas, empty, single }) {
  if (!icons.length) {
    return el('div', { style: { fontSize: '12px', color: MUTED } }, empty)
  }
  return el(
    'div',
    {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(124px, 1fr))',
        gap: '10px',
      },
    },
    icons.map((ic, i) =>
      el(IconTile, { icon: ic, canvas, single }, undefined, `${ic.jobId || ''}-${ic.name}-${i}`),
    ),
  )
}

function RequestView({ job, icons, canProve, onRender, rendering, proofStamp }) {
  const p = job.params || {}
  return el('div', {}, [
    el(StepRail, { status: job.status }, undefined, 'rail'),
    job.note
      ? el(
          'div',
          {
            style: {
              margin: '0 0 16px',
              padding: '8px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              background: 'var(--danger-subtle)',
              color: 'var(--danger)',
            },
          },
          job.note,
          'note',
        )
      : null,
    el(
      'div',
      { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '18px' } },
      [
        el(Chip, {}, plural((p.names || []).length, 'icon'), 'n'),
        el(Chip, {}, `${p.canvas}px canvas`, 'c'),
        el(Chip, {}, `${p.stroke}px stroke`, 's'),
        el(Chip, {}, p.style, 'y'),
        el(Chip, {}, `${p.keyline} keyline`, 'k'),
        el(
          Chip,
          {},
          p.kind === 'redraw'
            ? 'Redraw at new spec'
            : p.mode === 'ship'
              ? 'Drew straight away'
              : 'Metaphors first',
          'm',
        ),
      ],
      'chips',
    ),
    p.notes
      ? el(
          'div',
          { style: { marginBottom: '18px' } },
          [
            el('div', { style: sectionLabel }, "Designer's notes", 's'),
            el(
              'div',
              { style: { fontSize: '12px', color: TEXT, lineHeight: 1.6, whiteSpace: 'pre-wrap' } },
              p.notes,
              'b',
            ),
          ],
          'notes',
        )
      : null,
    el(
      'div',
      { style: { marginBottom: '20px' } },
      [
        el('div', { style: sectionLabel }, 'Icons', 's'),
        el(
          IconGrid,
          {
            icons,
            canvas: p.canvas,
            empty:
              job.status === 'failed'
                ? 'Nothing was shipped.'
                : 'The agent has not written any SVGs yet.',
          },
          undefined,
          'g',
        ),
      ],
      'icons',
    ),
    el(
      'div',
      {},
      [
        el(
          'div',
          { style: { ...sectionLabel, display: 'flex', alignItems: 'center', gap: '8px' } },
          [
            el('span', {}, 'Contact sheet', 'l'),
            el(
              'span',
              { style: { textTransform: 'none', letterSpacing: 0, fontSize: '10px' } },
              'the proof, at real size, on light and dark',
              'h',
            ),
          ],
          's',
        ),
        job.hasProof
          ? el('img', {
              src: `${API}/jobs/${job.id}/proof?t=${proofStamp}`,
              alt: `Contact sheet for ${job.id}`,
              style: {
                display: 'block',
                width: '100%',
                border: `1px solid ${BORDER}`,
                borderRadius: '8px',
                imageRendering: 'pixelated',
              },
            }, undefined, 'img')
          : el(
              'div',
              {
                style: {
                  padding: '18px',
                  border: `1px dashed ${BORDER}`,
                  borderRadius: '8px',
                  fontSize: '12px',
                  color: MUTED,
                },
              },
              canProve
                ? 'No contact sheet yet. Render one to check the icons at their real size.'
                : 'No renderer found, so this set cannot be proven at real size. Install one with: npx playwright install chromium',
              'ph',
            ),
        canProve
          ? el(
              'div',
              { style: { marginTop: '10px' } },
              el(Ghost, {
                label: rendering ? 'Rendering…' : job.hasProof ? 'Re-render sheet' : 'Render sheet',
                icon: el(Refresh, { size: 12 }),
                onClick: onRender,
              }),
              'btn',
            )
          : null,
      ],
      'proof',
    ),
  ])
}

// --- page ----------------------------------------------------------------------

export default function IconStudio() {
  useStyleSheet()

  const [health, setHealth] = useState({ canProve: true })
  const [libraries, setLibraries] = useState(null)
  const [jobs, setJobs] = useState([])
  const [activeLib, setActiveLib] = useState(() => loadPref(LS.library, ''))
  // The gallery is the landing view: opening a library should show what is in it.
  const [view, setView] = useState('gallery')
  const [selected, setSelected] = useState('')
  const [galleryIcons, setGalleryIcons] = useState([])
  const [jobIcons, setJobIcons] = useState([])
  const [libSelOpen, setLibSelOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [rendering, setRendering] = useState(false)
  const [revealing, setRevealing] = useState(false)
  const [error, setError] = useState('')
  const [proofStamp, setProofStamp] = useState(() => Date.now())
  const [panelOpen, setPanelOpen] = useState(() => loadPref(LS.panelOpen, true))
  const [panelW, setPanelW] = useState(() => {
    const w = loadPref(LS.panelWidth, PANEL_DEFAULT_WIDTH)
    return typeof w === 'number' && w >= PANEL_MIN_WIDTH && w <= PANEL_MAX_WIDTH
      ? w
      : PANEL_DEFAULT_WIDTH
  })
  const panelWRef = useRef(panelW)
  panelWRef.current = panelW

  const load = useCallback(async () => {
    try {
      const data = await api('/state')
      setLibraries(data.libraries || [])
      setJobs(data.jobs || [])
    } catch (err) {
      setError(String(err.message || err))
      setLibraries([])
    }
  }, [])

  useEffect(() => {
    api('/health')
      .then(setHealth)
      .catch(() => setHealth({ canProve: false }))
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  // Settle on a library once state arrives: the stored one if it still exists.
  useEffect(() => {
    if (!libraries || !libraries.length) return
    if (!libraries.some((l) => l.id === activeLib)) {
      setActiveLib(libraries[0].id)
      savePref(LS.library, libraries[0].id)
    }
  }, [libraries, activeLib])

  const library = useMemo(
    () => (libraries || []).find((l) => l.id === activeLib) || null,
    [libraries, activeLib],
  )
  const libJobs = useMemo(
    () => jobs.filter((j) => j.libraryId === activeLib),
    [jobs, activeLib],
  )
  const job = useMemo(() => libJobs.find((j) => j.id === selected) || null, [libJobs, selected])

  // Icons for the open request come from the library feed, filtered — one
  // endpoint serves both the gallery and the request view.
  useEffect(() => {
    if (!library) return
    let alive = true
    api(`/libraries/${library.id}/icons`)
      .then((d) => {
        if (alive) setGalleryIcons(d.icons || [])
      })
      .catch(() => {
        if (alive) setGalleryIcons([])
      })
    return () => {
      alive = false
    }
  }, [library, jobs])

  useEffect(() => {
    setJobIcons(galleryIcons.filter((ic) => ic.jobId === selected))
  }, [galleryIcons, selected])

  // Reset the pane when the library changes — a request from another library
  // must not stay open, and switching lands on the new library's icons.
  useEffect(() => {
    setSelected('')
    setView('gallery')
  }, [activeLib])

  const startResize = useCallback((e) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = panelWRef.current
    const clamp = (x) =>
      Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, startW + (x - startX)))
    const move = (ev) => setPanelW(clamp(ev.clientX))
    const up = (ev) => {
      savePref(LS.panelWidth, clamp(ev.clientX))
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }, [])

  const togglePanel = () => {
    const next = !panelOpen
    setPanelOpen(next)
    savePref(LS.panelOpen, next)
  }

  /** Hand a created job to the designer agent in a background slot, so the user
   *  stays on this page. Shared by new requests and whole-set redraws. */
  const dispatch = async (data) => {
    const resp = await fetch('/api/chat?ws=1', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: data.brief,
        slot: `icon-studio-${data.job.id}`,
        agent: 'icon-designer',
      }),
    })
    // A silent dispatch failure would leave a queued request with no agent
    // behind it, which looks identical to "the agent is thinking".
    if (!resp.ok) {
      setError(
        `Request ${data.job.id} was created but the designer agent could not be started (${resp.status}). Nothing is drawing.`,
      )
    }
    await load()
    setSelected(data.job.id)
    setView('request')
  }

  const submitJob = async ({ names, mode, notes }) => {
    if (!library) return
    setBusy(true)
    setError('')
    try {
      const data = await api('/jobs', jsonPost({ libraryId: library.id, names, mode, notes }))
      await dispatch(data)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  const createLibrary = async ({ name }) => {
    setBusy(true)
    setError('')
    try {
      // Name only — parameters default to the house spec and are revisable
      // later, with a redraw available once there are icons to bring forward.
      const data = await api('/libraries', jsonPost({ name }))
      await load()
      setActiveLib(data.library.id)
      savePref(LS.library, data.library.id)
      // Land on the library's own page. setActiveLib also fires the
      // library-change effect, which sets this too -- stated explicitly here so
      // the landing view does not depend on that effect's ordering.
      setView('gallery')
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  const saveParams = async (params, redraw, outputPath) => {
    if (!library) return
    setBusy(true)
    setError('')
    try {
      const patch = { params }
      // Only send the path when it actually changed: a PATCH that repoints the
      // folder copies files, so resending the same value on every parameter save
      // would do filesystem work for nothing.
      if (typeof outputPath === 'string' && outputPath !== (library.outputPath || '')) {
        patch.outputPath = outputPath
      }
      await api(`/libraries/${library.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (redraw) {
        // Save first, then redraw: the redraw job inherits the library's params,
        // so the PATCH has to have landed or it would redraw at the old spec.
        const data = await api(`/libraries/${library.id}/redraw`, { method: 'POST' })
        await dispatch(data)
      } else {
        await load()
        setView('create')
      }
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  /** Open the library's output folder in Finder. Sends no path -- the backend
   *  derives it from the library id, so the button cannot be pointed elsewhere. */
  const revealFolder = async () => {
    if (!library || revealing) return
    setRevealing(true)
    setError('')
    try {
      await api(`/libraries/${library.id}/reveal`, { method: 'POST' })
    } catch (err) {
      // Worth surfacing rather than swallowing: the usual cause is a folder the
      // user moved or deleted, and the fix is to repoint it in Library parameters.
      setError(String(err.message || err))
    } finally {
      setRevealing(false)
    }
  }

  const renderSheet = async () => {
    if (!job) return
    setRendering(true)
    setError('')
    try {
      await api(`/jobs/${job.id}/render`, { method: 'POST' })
      setProofStamp(Date.now())
      await load()
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setRendering(false)
    }
  }

  // --- boot / first-run --------------------------------------------------------

  if (libraries === null) {
    return el(
      'div',
      {
        style: {
          padding: '24px',
          fontSize: '12px',
          fontFamily: FONT_BODY,
          color: error ? 'var(--danger)' : MUTED,
        },
      },
      error || 'Loading Icon Studio…',
    )
  }

  if (!libraries.length) {
    return el(
      'div',
      {
        style: {
          minHeight: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          boxSizing: 'border-box',
          fontFamily: FONT_BODY,
          color: TEXT,
          background: BG,
        },
      },
      el(
        'div',
        {
          style: {
            width: '100%',
            maxWidth: '440px',
            background: ELEVATED,
            border: `1px solid ${BORDER}`,
            borderRadius: '12px',
            padding: '20px',
          },
        },
        [
          el('div', { style: { fontSize: '18px', fontWeight: 600 } }, 'Name your first icon library', 'h'),
          el(
            'div',
            { style: { fontSize: '12px', color: MUTED, marginTop: '8px', lineHeight: 1.55 } },
            'A library is a set of icons that share one spec, so icons you design weeks apart still look like they belong together. It starts on the house spec — you can change it later and redraw the whole set at the new one.',
            'b',
          ),
          el(
            'div',
            { style: { marginTop: '18px' } },
            el(NewLibraryView, {
              onCreate: createLibrary,
              busy,
              error,
              defaults: '16px canvas, 1px stroke, outline, square keyline',
            }),
            'f',
          ),
        ],
      ),
    )
  }

  // --- titles -----------------------------------------------------------------

  // A request has no name of its own, so the title is synthesized. Joining every
  // behaviour produced a five-line 23px wall, so only the first is shown.
  const requestTitle = (j) => {
    if (!j) return 'Request'
    const names = j.params?.names || []
    if (!names.length) return j.id
    const first = names[0].length > 52 ? `${names[0].slice(0, 52)}…` : names[0]
    return names.length > 1 ? `${first}  +${names.length - 1} more` : first
  }

  const title = {
    create: 'Create new icons',
    'new-library': 'New icon library',
    'edit-params': 'Library parameters',
    // The gallery is the library's own page, so it carries the library's name
    // rather than a generic "All icons" label.
    gallery: library ? library.name : 'Icon Studio',
    request: requestTitle(job),
  }[view]

  const breadcrumb = {
    request: job ? `${library?.name || ''} · ${job.id}` : '',
    // No library name here — it is the title now. The folder stays: these files
    // exist on disk and the user's next move is usually to go get them.
    gallery: library
      ? `${plural(galleryIcons.length, 'icon')} · ${library.outputPath || ''}`
      : '',
    create: library ? library.name : '',
    'edit-params': library ? library.name : '',
  }[view]

  // --- panel ------------------------------------------------------------------

  const panel = el(
    'div',
    {
      style: {
        width: `${panelW}px`,
        flexShrink: 0,
        // Notes gets border-box from the dashboard's Tailwind preflight
        // (`*{box-sizing:border-box}`). Stated explicitly here because the
        // resize handle's 180-420px range is a TOTAL width: without it the two
        // 1px borders sit outside, the panel measures 262 against Notes' 260,
        // and every saved width is off by two.
        boxSizing: 'border-box',
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        margin: '0 0 8px',
        background: ELEVATED,
        border: `1px solid ${BORDER}`,
        borderRadius: '16px',
        boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
      },
    },
    [
      // header: library switcher + new request
      el(
        'div',
        {
          style: {
            height: '40px',
            marginTop: '2px',
            display: 'flex',
            alignItems: 'center',
            padding: '0 14px 0 8px',
            flexShrink: 0,
            position: 'relative',
          },
        },
        [
          el(
            'button',
            {
              type: 'button',
              className: 'is-trigger',
              onClick: () => setLibSelOpen((v) => !v),
              'aria-expanded': libSelOpen,
              // Notes carries both of these on its vault trigger; omitting them
              // left the control announcing itself as a plain button with no
              // hint that it opens a list.
              'aria-haspopup': 'listbox',
              'aria-label': 'Switch icon library',
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                marginLeft: '28px',
                minWidth: 0,
                background: 'transparent',
                border: 'none',
                padding: '4px 6px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontFamily: FONT_BODY,
              },
            },
            [
              el(
                'span',
                {
                  style: {
                    fontSize: '14px',
                    fontWeight: 500,
                    color: MUTED,
                    letterSpacing: '.04em',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  },
                },
                library ? library.name : 'Libraries',
                'n',
              ),
              el(
                ChevronDown,
                {
                  size: 13,
                  style: {
                    color: MUTED,
                    transform: libSelOpen ? 'rotate(180deg)' : 'none',
                    transition: 'transform .15s',
                  },
                },
                undefined,
                'c',
              ),
            ],
            'trigger',
          ),
          el(
            'button',
            {
              type: 'button',
              className: 'is-act',
              title: 'New icon request',
              'aria-label': 'New icon request',
              onClick: () => {
                setSelected('')
                setView('create')
              },
              style: { ...iconBtn, marginLeft: 'auto' },
            },
            el(Plus, { size: 16 }),
            'add',
          ),
          libSelOpen
            ? el(
                LibraryMenu,
                {
                  libraries,
                  activeId: activeLib,
                  onPick: (id) => {
                    setActiveLib(id)
                    savePref(LS.library, id)
                  },
                  onNew: () => setView('new-library'),
                  onClose: () => setLibSelOpen(false),
                },
                undefined,
                'menu',
              )
            : null,
        ],
        'head',
      ),
      // request list
      el(
        'div',
        { style: { flex: 1, overflowY: 'auto', padding: '8px' } },
        libJobs.length
          ? libJobs.map((j) =>
              el(
                RequestRow,
                {
                  job: j,
                  active: view === 'request' && j.id === selected,
                  onClick: () => {
                    setSelected(j.id)
                    setView('request')
                  },
                },
                undefined,
                j.id,
              ),
            )
          : el(
              'div',
              { style: { padding: '10px', fontSize: '11px', color: MUTED } },
              'No requests in this library yet.',
            ),
        'list',
      ),
      // Bottom bar, in the slot Notes gives its Settings row: a separator, then
      // one full-width primary control plus a 30x30 icon button -- the geometry of
      // Notes' search + sort pair, moved to the foot of the panel.
      //
      // paddingTop is 6px, not Notes' 8px, and that 2px is load-bearing. Notes
      // seats a 28px row here; these are 30px controls (the search row's height,
      // which you asked to match). Both cards share a grid row with the dashboard
      // left nav and both end 8px above the pane, so the budget below this border
      // has to equal Notes' for the separators to land on one baseline:
      // 6 + 30 + 8 == 8 + 28 + 8 == 44px of content. Measured against Notes' own
      // panel it reads 46px from separator to panel floor for both, the extra 2px
      // being this wrapper's border and the panel's own bottom border.
      el(
        'div',
        {
          style: {
            flexShrink: 0,
            marginTop: '4px',
            borderTop: `1px solid ${BORDER_STRONG}`,
            marginBottom: '8px',
            display: 'flex',
            gap: '6px',
            alignItems: 'center',
            padding: '6px 12px 0',
          },
        },
        [
          el(
            'button',
            {
              type: 'button',
              className: 'is-row',
              onClick: () => {
                setSelected('')
                setView('gallery')
              },
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                // flex:1 like Notes' search input, not width:100%, so it shares
                // the row with the folder button.
                flex: 1,
                minWidth: 0,
                height: '30px',
                boxSizing: 'border-box',
                padding: '0 10px',
                borderRadius: '8px',
                background: view === 'gallery' ? ACCENT_BG : CARD,
                border: `1px solid ${view === 'gallery' ? ACCENT : BORDER}`,
                color: view === 'gallery' ? TEXT : MUTED,
                fontSize: '12px',
                fontFamily: FONT_BODY,
                cursor: 'pointer',
                textAlign: 'left',
              },
            },
            [
              el(Grid, { size: 13 }, undefined, 'i'),
              el('span', { style: { flex: 1 } }, 'All icons', 'l'),
              el(
                'span',
                { style: { fontSize: '10px', color: MUTED } },
                String(library ? library.iconCount : 0),
                'c',
              ),
            ],
            'gallery',
          ),
          el(
            'button',
            {
              type: 'button',
              className: 'is-act',
              onClick: revealFolder,
              disabled: !library || revealing,
              // The path is in the title rather than the label: the button has to
              // stay 30px wide, and a truncated path tells you less than nothing.
              title: library
                ? `Open ${library.outputPath} in Finder`
                : 'Open the library folder',
              'aria-label': 'Open the library folder',
              style: {
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '30px',
                height: '30px',
                flexShrink: 0,
                boxSizing: 'border-box',
                borderRadius: '8px',
                background: CARD,
                border: `1px solid ${BORDER}`,
                color: MUTED,
                cursor: library && !revealing ? 'pointer' : 'default',
              },
            },
            el(Folder, { size: 14 }),
            'folder',
          ),
        ],
        'bottombar',
      ),
      el(
        'div',
        {
          onPointerDown: startResize,
          style: {
            position: 'absolute',
            top: 0,
            bottom: 0,
            right: '-3px',
            width: '5px',
            cursor: 'col-resize',
          },
        },
        undefined,
        'resize',
      ),
    ],
  )

  // --- right pane -------------------------------------------------------------

  const body = {
    create: library
      ? el(CreateView, {
          library,
          busy,
          error,
          onSubmit: submitJob,
          onEditParams: () => setView('edit-params'),
        })
      : null,
    'new-library': el(NewLibraryView, {
      onCreate: createLibrary,
      busy,
      error,
      defaults: '16px canvas, 1px stroke, outline, square keyline',
    }),
    'edit-params': library
      ? el('div', {}, [
          el(
            ParamEditor,
            {
              library,
              busy,
              error,
              iconCount: library.iconCount || 0,
              onSave: saveParams,
              onCancel: () => setView('create'),
            },
            undefined,
            'e',
          ),
        ])
      : null,
    gallery: galleryIcons.length
      ? el(
          'div',
          {},
          el(IconGrid, {
            icons: galleryIcons,
            canvas: library?.params?.canvas,
            single: true,
          }),
        )
      : // One empty state, not two. The gallery IS the library's landing page, so
        // "this library has no icons" and "this gallery is empty" were the same
        // sentence in two places -- the grid's one-liner said less and offered no
        // way out of it.
        el(EmptyState, { onCreate: () => setView('create') }),
    request: job
      ? el(RequestView, {
          job,
          icons: jobIcons,
          canProve: health.canProve,
          rendering,
          proofStamp,
          onRender: renderSheet,
        })
      : el('div', { style: { fontSize: '12px', color: MUTED } }, 'That request no longer exists.'),
  }[view]

  // Centre the empty state in the pane instead of seating it at the top of the
  // reading column. Keyed off the icons, not the request list: a library can
  // hold failed requests and still have nothing to show.
  const showEmptyCentered = view === 'gallery' && !galleryIcons.length

  const pane = el(
    'div',
    { style: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 } },
    [
      el(
        'div',
        { style: { position: 'relative', flexShrink: 0 } },
        [
          // action cluster
          el(
            'div',
            {
              style: {
                position: 'absolute',
                top: '24px',
                right: `${COLUMN_PAD_X}px`,
                zIndex: 2,
                display: 'flex',
                gap: '6px',
                alignItems: 'center',
              },
            },
            [
              !health.canProve
                ? el(
                    'span',
                    {
                      title: 'Install one with: npx playwright install chromium',
                      style: {
                        padding: '2px 8px',
                        borderRadius: '9999px',
                        fontSize: '10px',
                        fontWeight: 500,
                        background: 'var(--warn-subtle)',
                        color: 'var(--warn)',
                      },
                    },
                    'no renderer',
                    'w',
                  )
                : null,
              // No render button here: the proof section below owns that action,
              // and two controls doing one thing reads as two different things.
              view !== 'create' && view !== 'new-library'
                ? el(
                    Primary,
                    {
                      label: 'Create new icons',
                      icon: el(Plus, { size: 13 }),
                      onClick: () => {
                        setSelected('')
                        setView('create')
                      },
                    },
                    undefined,
                    'c',
                  )
                : null,
            ],
            'actions',
          ),
          // title block, in the reading column
          el(
            'div',
            {
              style: {
                margin: '0 auto',
                width: '100%',
                maxWidth: `${COLUMN_MAX_WIDTH}px`,
                boxSizing: 'border-box',
                padding: `24px ${COLUMN_PAD_X}px 14px`,
              },
            },
            [
              el(
                'div',
                {
                  style: {
                    fontSize: '23px',
                    fontWeight: 700,
                    lineHeight: 1.25,
                    // Always full-strength: this used to dim for the placeholder
                    // "Icon Studio" title, but the gallery now shows a real
                    // library name and a muted heading read as disabled.
                    color: TEXT,
                    // Room for the action cluster so a long title cannot slide
                    // under the buttons.
                    paddingRight: '220px',
                    overflowWrap: 'anywhere',
                  },
                },
                title,
                't',
              ),
              breadcrumb
                ? el(
                    'div',
                    { style: { fontSize: '11px', color: MUTED, marginTop: '2px' } },
                    breadcrumb,
                    'b',
                  )
                : null,
            ],
            'title',
          ),
          el(
            'div',
            { style: { borderTop: `1px solid ${BORDER}`, margin: `0 ${COLUMN_PAD_X}px` } },
            undefined,
            'rule',
          ),
        ],
        'header',
      ),
      showEmptyCentered
        ? body
        : el(
            'div',
            { style: { flex: 1, overflowY: 'auto', minHeight: 0 } },
            el(
              'div',
              {
                style: {
                  margin: '0 auto',
                  width: '100%',
                  maxWidth: `${COLUMN_MAX_WIDTH}px`,
                  boxSizing: 'border-box',
                  padding: `14px ${COLUMN_PAD_X}px 32px`,
                },
              },
              body,
            ),
            'scroll',
          ),
    ],
  )

  return el(
    'div',
    {
      style: {
        display: 'flex',
        height: '100%',
        minHeight: '520px',
        position: 'relative',
        fontFamily: FONT_BODY,
        color: TEXT,
        background: BG,
      },
    },
    [
      el(
        'button',
        {
          type: 'button',
          className: 'is-collapse',
          onClick: togglePanel,
          title: panelOpen ? 'Hide requests' : 'Show requests',
          'aria-label': panelOpen ? 'Hide requests' : 'Show requests',
          style: {
            position: 'absolute',
            top: '9px',
            left: '8px',
            zIndex: 10,
            width: '28px',
            height: '28px',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            border: 'none',
            // No inline `background` on purpose -- an inline value would outrank
            // the stylesheet's :hover rule and kill the tint. The rest state is
            // `.is-collapse{background:transparent}` instead. Notes omits it
            // entirely and leans on Tailwind preflight resetting button
            // backgrounds; without that the UA paints buttonface grey.
            transition: 'color .15s, background .15s',
          },
        },
        el(PanelLeftIcon, { open: panelOpen, size: 16 }),
        'collapse',
      ),
      panelOpen ? panel : null,
      pane,
    ],
  )
}

/** Parameter editor for an existing library. Separate component so its draft
 *  state resets whenever the pane is reopened. */
function ParamEditor({ library, onSave, onCancel, busy, error, iconCount }) {
  const [params, setParams] = useState(() => ({
    canvas: String(library.params.canvas),
    stroke: String(library.params.stroke),
    style: library.params.style,
    keyline: library.params.keyline,
  }))
  const [outputPath, setOutputPath] = useState(library.outputPath || '')
  const paramsChanged =
    String(library.params.canvas) !== String(params.canvas) ||
    String(library.params.stroke) !== String(params.stroke) ||
    library.params.style !== params.style ||
    library.params.keyline !== params.keyline
  const pathChanged = (outputPath || '').trim() !== (library.outputPath || '')
  const changed = paramsChanged || pathChanged
  const save = (redraw) => onSave(params, redraw, outputPath.trim())

  return el('div', {}, [
    el(ParamGrid, { values: params, onChange: setParams }, undefined, 'g'),
    el(
      'div',
      { style: { fontSize: '11px', color: MUTED, marginTop: '14px', lineHeight: 1.55 } },
      'Saving affects new requests only — the requests already in this library keep the parameters they were drawn with, because those are a record of what happened.',
      'h1',
    ),
    iconCount > 0
      ? el(
          'div',
          { style: { fontSize: '11px', color: MUTED, marginTop: '6px', lineHeight: 1.55 } },
          `To bring the existing ${plural(iconCount, 'icon')} up to the new spec, save and redraw. The agent reuses each icon's recorded metaphor rather than inventing a new one, so only the drawing changes — not what the icons mean.`,
          'h2',
        )
      : null,
    // The output folder lives here rather than on the create screen for the same
    // reason the parameters do: it is a decision worth revisiting once there are
    // icons to put somewhere, not a gate before the library exists.
    el(
      'div',
      { style: { marginTop: '22px' } },
      [
        el('div', { style: sectionLabel }, 'Output folder', 's'),
        el('input', {
          className: 'is-field',
          value: outputPath,
          onChange: (e) => setOutputPath(e.target.value),
          spellCheck: false,
          placeholder: library.defaultOutputPath || '',
          style: { ...fieldStyle, fontSize: '12px' },
        }, undefined, 'i'),
        el(
          'div',
          { style: { fontSize: '11px', color: MUTED, marginTop: '8px', lineHeight: 1.55 } },
          'Every icon in this library is written here as one SVG per name. Point it at any local folder — a design-system repo, say — and the icons land where you already work. Existing SVGs are copied across; the originals are left in place, never deleted.',
          'h3',
        ),
      ],
      'out',
    ),
    error
      ? el(
          'div',
          {
            style: {
              margin: '14px 0 0',
              padding: '8px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              background: 'var(--danger-subtle)',
              color: 'var(--danger)',
            },
          },
          error,
          'err',
        )
      : null,
    el(
      'div',
      { style: { marginTop: '18px', display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
      [
        el(
          Primary,
          {
            label: iconCount > 0 ? 'Save' : 'Save parameters',
            busy,
            disabled: busy,
            onClick: () => save(false),
          },
          undefined,
          'p',
        ),
        iconCount > 0
          ? el(
              Ghost,
              {
                label: `Save and redraw ${plural(iconCount, 'icon')}`,
                icon: el(Refresh, { size: 12 }),
                onClick: () => save(true),
              },
              undefined,
              'r',
            )
          : null,
        el(Ghost, { label: 'Cancel', onClick: onCancel }, undefined, 'c'),
        changed
          ? el('span', { style: { fontSize: '11px', color: 'var(--warn)' } }, 'unsaved changes', 'w')
          : null,
      ],
      'a',
    ),
  ])
}
