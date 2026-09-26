import fs from 'node:fs'
import pptxgen from 'pptxgenjs'

const [inputPath, outputPath] = process.argv.slice(2)
if (!inputPath || !outputPath) throw new Error('Usage: node render.mjs input.json output.pptx')
const pitch = JSON.parse(fs.readFileSync(inputPath, 'utf8'))
if (!Array.isArray(pitch.pitch_slides) || pitch.pitch_slides.length !== 5) throw new Error('Exactly five slides are required')

const pptx = new pptxgen()
pptx.layout = 'LAYOUT_WIDE'
pptx.author = 'Marsh Policy Intelligence'
pptx.subject = 'Evidence-led medical policy recommendation'
pptx.title = `${pitch.company_profile.company_name} policy recommendation`
pptx.company = 'Marsh Policy Intelligence'
pptx.lang = 'en-IN'
pptx.theme = {
  headFontFace: 'Aptos Display', bodyFontFace: 'Aptos', lang: 'en-IN',
}

const C = { navy: '071B2E', navy2: '0D2942', teal: '25C7C9', cyan: '77E6E6', ink: '163047', muted: '657A8D', line: 'D8E3E8', pale: 'F1F6F8', white: 'FFFFFF', amber: 'D89B34' }
const safe = value => String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, ' ').replace(/\.\.\.|…/g, '.').replace(/\s+/g, ' ').trim()
const clipped = (value, max = 190) => {
  const text = safe(value)
  if (text.length <= max) return text
  const complete = (text.match(/[^.!?]+[.!?]/g) || []).find(sentence => sentence.trim().length <= max)
  if (complete) return complete.trim()
  const boundary = text.slice(0, max).lastIndexOf(' ')
  return `${text.slice(0, boundary > max * .55 ? boundary : max).trim().replace(/[,:;.-]+$/, '')}.`
}
const addText = (slide, text, x, y, w, h, options = {}) => slide.addText(safe(text), { x, y, w, h, margin: 0, fontFace: 'Aptos', fontSize: 14, color: C.ink, breakLine: false, valign: 'middle', fit: 'shrink', ...options })

function base(slide, item) {
  slide.background = { color: C.white }
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 13.333, h: .12, fill: { color: C.teal }, line: { color: C.teal } })
  addText(slide, item.title, .55, .28, 10.6, .46, { fontFace: 'Aptos Display', fontSize: 25, bold: true, color: C.navy })
  addText(slide, `0${item.slide_number}`, 12.15, .3, .55, .36, { fontSize: 11, bold: true, color: C.teal, align: 'right' })
  addText(slide, item.key_message, .58, .82, 12.05, .42, { fontSize: 12, color: C.muted })
}

function footer(slide, item) {
  slide.addShape(pptx.ShapeType.line, { x: .55, y: 7.03, w: 12.2, h: 0, line: { color: C.line, width: 1 } })
  const refs = (item.visible_citations || []).slice(0, 4).map(r => `${r.document_name}, ${r.page_number ? `p.${r.page_number}` : r.section || 'section not established'}`)
  addText(slide, refs.length ? `Sources: ${refs.join('  •  ')}` : 'Sources: advisor-confirmed company profile and supplied policy documents', .58, 7.08, 11.65, .2, { fontSize: 7.5, color: C.muted })
  addText(slide, 'Marsh Policy Intelligence', 11.4, 7.08, 1.3, .2, { fontSize: 7.5, bold: true, color: C.navy, align: 'right' })
}

function addCard(slide, title, value, x, y, w = 2.75, h = 1.05, dark = false) {
  slide.addShape(pptx.ShapeType.roundRect, { x, y, w, h, rectRadius: .06, fill: { color: dark ? C.navy2 : C.pale }, line: { color: dark ? C.navy2 : C.line, width: 1 } })
  addText(slide, title.toUpperCase(), x + .18, y + .12, w - .36, .22, { fontSize: 8, bold: true, color: dark ? C.cyan : C.teal, charSpacing: 1 })
  addText(slide, clipped(value, 115), x + .18, y + .38, w - .36, h - .48, { fontSize: 13, bold: true, color: dark ? C.white : C.navy, valign: 'top' })
}

function addBullets(slide, items, x, y, w, h, fontSize = 12) {
  const runs = items.slice(0, 7).map((item, index) => ({ text: clipped(item, 205), options: { bullet: { indent: 13 }, hanging: 3, breakLine: index < items.length - 1, paraSpaceAfterPt: 8 } }))
  slide.addText(runs, { x, y, w, h, margin: .05, fontFace: 'Aptos', fontSize, color: C.ink, valign: 'top', breakLine: false, fit: 'shrink' })
}

function addTable(slide, table, y = 1.38) {
  const cols = table.columns.length
  const header = table.columns.map(value => ({ text: clipped(value, 35), options: { bold: true, color: C.white, fill: C.navy, align: cols > 4 ? 'center' : 'left' } }))
  const rows = [header, ...table.rows.slice(0, 7).map((row, ri) => row.map((value, ci) => ({ text: clipped(value, ci === 0 ? 45 : 145), options: { color: C.ink, fill: ri % 2 ? C.white : C.pale, bold: ci === 0 } })))]
  const first = cols > 4 ? 1.75 : 2.55
  const widths = [first, ...Array(cols - 1).fill((12.2 - first) / (cols - 1))]
  slide.addTable(rows, { x: .56, y, w: 12.2, h: 5.25, colW: widths, rowH: cols > 4 ? .96 : .85, margin: .08, border: { type: 'solid', color: C.line, pt: .6 }, fontFace: 'Aptos', fontSize: cols > 4 ? 9.5 : 10.5, valign: 'middle', autoFit: false })
}

function addBarChart(slide, chart, x, y, w, h) {
  const values = Object.values(chart.series || {})[0] || []
  const seriesName = Object.keys(chart.series || {})[0] || 'Points'
  slide.addChart(pptx.ChartType.bar, [{ name: seriesName, labels: chart.categories, values }], {
    x, y, w, h,
    showTitle: true, title: chart.title,
    showLegend: false, showValue: true,
    chartColors: [C.teal],
    catAxisLabelFontFace: 'Aptos', catAxisLabelFontSize: 10,
    valAxisLabelFontFace: 'Aptos', valAxisLabelFontSize: 8,
    valAxisMinVal: 0, valGridLine: { color: C.line, width: 1 },
    showCatName: false, showSerName: false,
    border: { color: C.line, width: 1 },
  })
}

for (const item of pitch.pitch_slides) {
  const slide = pptx.addSlide()
  base(slide, item)
  const blocks = (item.content_blocks || []).flatMap(block => block.items || (block.text ? [block.text] : []))
  if (item.slide_number === 1) {
    slide.addShape(pptx.ShapeType.roundRect, { x: .58, y: 1.42, w: 4.18, h: 2.48, fill: { color: C.navy }, line: { color: C.navy } })
    addText(slide, 'RECOMMENDED SHORTLIST LEADER', .86, 1.7, 3.6, .22, { fontSize: 9, bold: true, color: C.cyan, charSpacing: 1.2 })
    addText(slide, clipped(pitch.pitch_slides[0].title.split(' for ')[0], 60), .86, 2.08, 3.55, .55, { fontSize: 27, bold: true, color: C.white })
    addText(slide, 'Best documented fit against the advisor-confirmed priorities. Final placement remains subject to underwriting and policy terms.', .86, 2.85, 3.5, .7, { fontSize: 11.5, color: 'C4D7E4', valign: 'top' })
    if ((item.charts || []).length) addBarChart(slide, item.charts[0], 5.08, 1.4, 7.45, 2.52)
    const claims = item.claims.filter(c => c.benefit_name !== 'Final recommendation').slice(0, 3)
    claims.forEach((c, i) => addCard(slide, c.benefit_name || 'Verified benefit', clipped(c.presented_value, 150), .58 + i * 4.13, 4.3, 3.83, 1.92, false))
    addText(slide, 'Why it leads', .7, 4.02, 3.0, .22, { fontSize: 9, bold: true, color: C.teal, charSpacing: 1 })
  } else if (item.slide_number === 2) {
    const facts = item.content_blocks[0]?.items || []
    facts.slice(0, 4).forEach((fact, i) => { const [label, ...rest] = safe(fact).split(':'); addCard(slide, label, rest.join(':').trim(), .65, 1.45 + i * 1.18, 5.25, 1.0, i === 0) })
    slide.addShape(pptx.ShapeType.roundRect, { x: 6.28, y: 1.45, w: 6.02, h: 4.85, fill: { color: C.pale }, line: { color: C.line } })
    addText(slide, 'CONFIRMED DECISION PRIORITIES', 6.62, 1.76, 5.3, .26, { fontSize: 9, bold: true, color: C.teal, charSpacing: 1 })
    const priorityValues = item.content_blocks.slice(1).flatMap(b => b.items || [])
    priorityValues.slice(0, 5).forEach((value, i) => {
      slide.addShape(pptx.ShapeType.ellipse, { x: 6.65, y: 2.25 + i * .72, w: .34, h: .34, fill: { color: i === 0 ? C.teal : C.white }, line: { color: C.teal, width: 1.3 } })
      addText(slide, `${i + 1}`, 6.65, 2.25 + i * .72, .34, .34, { fontSize: 9, bold: true, align: 'center', color: i === 0 ? C.navy : C.teal })
      addText(slide, safe(value).replace(/^Priority:\s*/i, ''), 7.18, 2.19 + i * .72, 4.55, .46, { fontSize: 14, bold: i === 0, color: C.navy })
    })
    addText(slide, 'Advisor-confirmed requirements control scoring and recommendation logic.', .68, 6.47, 11.7, .28, { fontSize: 10, color: C.muted, italic: true })
  } else if ((item.comparison_tables || []).length) {
    addTable(slide, item.comparison_tables[0])
  } else if (item.slide_number !== 5) {
    addBullets(slide, blocks, .7, 1.5, 5.55, 4.95, 12)
  }
  if (item.slide_number === 5) {
    const advantages = blocks.filter(v => /^Advantage:/i.test(v)).slice(0, 3)
    const considerations = blocks.filter(v => /^Consideration:/i.test(v)).slice(0, 2)
    addText(slide, 'DOCUMENTED ADVANTAGES', .68, 1.48, 5.45, .24, { fontSize: 9, bold: true, color: C.teal, charSpacing: 1 })
    advantages.forEach((value, i) => addCard(slide, `0${i + 1}`, value.replace(/^Advantage:\s*/i, ''), .65, 1.82 + i * 1.24, 5.45, 1.04, i === 0))
    if (considerations.length) {
      addText(slide, 'PLACEMENT CONSIDERATION', .68, 5.68, 5.4, .22, { fontSize: 8.5, bold: true, color: C.amber, charSpacing: 1 })
      addText(slide, clipped(considerations[0].replace(/^Consideration:\s*/i, ''), 180), .68, 5.98, 5.42, .55, { fontSize: 10.5, color: C.ink, valign: 'top' })
    }
    slide.addShape(pptx.ShapeType.roundRect, { x: 6.6, y: 1.55, w: 5.7, h: 4.75, fill: { color: C.navy2, transparency: 1 }, line: { color: C.navy2 } })
    addText(slide, 'ADVISOR DECISION FRAME', 6.92, 1.82, 4.9, .24, { fontSize: 9, bold: true, color: C.cyan, charSpacing: 1 })
    addText(slide, 'Approve only after the exact pitch version receives a PASS audit.', 6.92, 2.18, 4.85, .75, { fontSize: 21, bold: true, color: C.white, valign: 'top' })
    addText(slide, 'Validate eligibility, policy variant, commercial terms, waiting periods and exclusions before placement.', 6.92, 3.25, 4.85, 1.0, { fontSize: 13, color: 'C4D7E4', valign: 'top' })
    addText(slide, `Pitch version ${pitch.pitch_version || 1}\n${clipped(pitch.pitch_hash || 'Hash assigned by server', 32)}`, 6.92, 5.35, 4.85, .52, { fontSize: 8.5, color: C.cyan })
  }
  footer(slide, item)
}

await pptx.writeFile({ fileName: outputPath })
