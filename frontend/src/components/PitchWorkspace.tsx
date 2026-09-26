import { BarChart3, CheckCircle2, ChevronLeft, ChevronRight, FileText, ShieldCheck } from 'lucide-react'
import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { MarketingPitch, PitchSlide } from '../types'

const COLORS = ['#26d7c9', '#6d7cff', '#a98bff', '#f7b64a']

function NativeChart({ slide }: { slide: PitchSlide }) {
  const chart = slide.charts[0]
  const data = useMemo(() => {
    if (!chart) return []
    const [seriesName, values] = Object.entries(chart.series)[0] || ['', []]
    return chart.categories.map((category, index) => ({ category, value: values[index], seriesName }))
  }, [chart])

  if (!chart) return null
  return (
    <div className="chart-shell">
      <div className="chart-heading"><BarChart3 size={16} /> {chart.title}</div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} layout="vertical" margin={{ left: 12, right: 24 }}>
          <CartesianGrid horizontal={false} stroke="rgba(255,255,255,.06)" />
          <XAxis type="number" hide />
          <YAxis dataKey="category" type="category" width={132} tick={{ fill: '#9eb2c9', fontSize: 11 }} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} contentStyle={{ background: '#0d2742', border: '1px solid #214665', borderRadius: 12 }} />
          <Bar dataKey="value" radius={[0, 8, 8, 0]}>
            {data.map((_, index) => <Cell key={index} fill={COLORS[index % COLORS.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function DecisionSnapshot({ pitch, slide }: { pitch: MarketingPitch; slide: PitchSlide }) {
  const priorities = slide.content_blocks.flatMap((block) => block.items)
    .filter((item) => item.startsWith('Priority:'))
    .map((item) => item.replace('Priority:', '').trim())
    .slice(0, 4)
  const isRecommendationSlide = slide.slide_number === 5
  const checklist = isRecommendationSlide
    ? ['Confirm the chosen policy variant and sum insured', 'Confirm waiting periods, co-payments and deductibles', 'Confirm cashless-network access for employee locations']
    : ['Confirm the employee count and locations', 'Confirm the ranked coverage priorities', 'Confirm budget and cost-sharing tolerance']

  return (
    <aside className="decision-snapshot">
      <span className="snapshot-label">Advisor decision snapshot</span>
      <h4>{isRecommendationSlide ? 'Placement checklist' : 'Advisor validation checklist'}</h4>
      <p>{isRecommendationSlide ? <>Complete these commercial and eligibility checks before presenting the shortlist to <strong>{pitch.company_profile.company_name}</strong>.</> : <>Confirm these client inputs before comparing the selected policy documents for <strong>{pitch.company_profile.company_name}</strong>.</>}</p>
      <div className="snapshot-metrics">
        <div><b>{priorities.length || 0}</b><span>priorities mapped</span></div>
        <div><b>{slide.visible_citations.length}</b><span>source citations</span></div>
        <div><b>4</b><span>policies in scope</span></div>
      </div>
      <div className="snapshot-priorities"><strong>{isRecommendationSlide ? 'Complete before placement' : 'Confirm with the client'}</strong>{checklist.map((item) => <span key={item}><CheckCircle2 size={14} />{item}</span>)}</div>
      <div className="snapshot-boundary"><ShieldCheck size={16} /><span>Check the cited clauses, selected variant, waiting periods and cost-sharing before using this recommendation.</span></div>
    </aside>
  )
}

function SlideCompanion({ pitch, slide }: { pitch: MarketingPitch; slide: PitchSlide }) {
  const isProfile = slide.slide_number === 2
  const items = isProfile
    ? ['Workforce profile captured', 'Coverage priorities ranked', 'Inputs ready for policy comparison']
    : ['Compare equivalent benefits only', 'Review limits and qualifications', 'Use citations to validate each response']
  return (
    <aside className={`slide-companion ${isProfile ? 'profile-companion' : 'evidence-companion'}`}>
      <span>{isProfile ? 'Client input summary' : 'Evidence review lens'}</span>
      <h4>{isProfile ? 'What shapes the recommendation' : 'How to read this comparison'}</h4>
      <p>{isProfile ? <>Confirmed inputs for <strong>{pitch.company_profile.company_name}</strong> guide the shortlist and scoring.</> : 'Each response should be read with its cited source, eligibility rules and applicable limits.'}</p>
      <div className="companion-list">{items.map((item, index) => <div key={item}><b>{String(index + 1).padStart(2, '0')}</b><span>{item}</span></div>)}</div>
      <small>{slide.visible_citations.length ? `${slide.visible_citations.length} cited sources shown on this slide` : 'Advisor-confirmed inputs; no policy claim is made on this slide'}</small>
    </aside>
  )
}

function Comparison({ slide }: { slide: PitchSlide }) {
  const table = slide.comparison_tables[0]
  if (!table) return null
  return (
    <div className="comparison-scroll">
      <table className="comparison-table">
        <thead><tr>{table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={`${row[0]}-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td className={cell === 'Not established' ? 'muted-cell' : ''} key={`${cellIndex}-${cell.slice(0, 20)}`}>
                  {cellIndex === 0 ? <strong>{cell}</strong> : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface Props {
  pitch: MarketingPitch
  activeSlide: number
  onSlideChange: (slide: number) => void
}

export function PitchWorkspace({ pitch, activeSlide, onSlideChange }: Props) {
  const slide = pitch.pitch_slides[activeSlide - 1]
  return (
    <section className="workspace-card">
      <div className="workspace-topbar">
        <div>
          <span className="eyebrow">Live pitch workspace</span>
          <h2>{pitch.company_profile.company_name}</h2>
        </div>
        <span className="grounded-badge"><ShieldCheck size={15} /> Grounded in policy evidence</span>
      </div>

      <div className="slide-tabs" role="tablist" aria-label="Pitch slides">
        {pitch.pitch_slides.map((item) => (
          <button
            aria-selected={activeSlide === item.slide_number}
            className={activeSlide === item.slide_number ? 'active' : ''}
            key={item.slide_number}
            onClick={() => onSlideChange(item.slide_number)}
            role="tab"
            type="button"
          >
            <span>{String(item.slide_number).padStart(2, '0')}</span>
            {item.title}
          </button>
        ))}
      </div>

      <article className="slide-canvas">
        <div className="slide-kicker">SLIDE {activeSlide} / 5</div>
        <h3>{slide.title}</h3>
        <p className="slide-message">{slide.key_message}</p>
        {slide.comparison_tables.length > 0 ? <div className="comparison-slide-layout"><Comparison slide={slide} /><SlideCompanion pitch={pitch} slide={slide} /></div> : (
          <div className="slide-grid">
            <div className="narrative-panel">
              {slide.content_blocks.flatMap((block) => block.items).map((item) => (
                <div className="insight-row" key={item}>
                  <CheckCircle2 size={17} /> <span>{item}</span>
                </div>
              ))}
              {slide.content_blocks.length === 0 ? (
                <div className="evidence-note"><FileText size={18} /> Evidence is stored in the structured pitch and audit report.</div>
              ) : null}
            </div>
            {slide.charts.length ? <NativeChart slide={slide} /> : slide.slide_number === 5 ? <DecisionSnapshot pitch={pitch} slide={slide} /> : <SlideCompanion pitch={pitch} slide={slide} />}
          </div>
        )}
        {slide.visible_citations.length > 0 ? (
          <div className="citation-strip">
            {slide.visible_citations.slice(0, 4).map((source, index) => (
              <span key={`${source.document_id}-${source.page_number}-${index}`}>{source.document_name} · p.{source.page_number ?? '—'}</span>
            ))}
          </div>
        ) : null}
      </article>
      <div className="slide-navigation" aria-label="Slide navigation">
        <button disabled={activeSlide === 1} onClick={() => onSlideChange(activeSlide - 1)} type="button"><ChevronLeft size={15} /> Previous</button>
        <span>Slide {activeSlide} of {pitch.pitch_slides.length}</span>
        <button disabled={activeSlide === pitch.pitch_slides.length} onClick={() => onSlideChange(activeSlide + 1)} type="button">Next slide <ChevronRight size={15} /></button>
      </div>
    </section>
  )
}
