const MOJIBAKE_MARKERS = ['Ã', 'Â', 'â€', '�']

function mojibakeScore(text) {
  return MOJIBAKE_MARKERS.reduce((score, marker) => score + (text.includes(marker) ? 1 : 0), 0)
}

export function normalizeDisplayText(text) {
  if (!text) return ''
  let updated = String(text)

  if (mojibakeScore(updated) > 0) {
    const candidates = [updated]
    try {
      candidates.push(decodeURIComponent(escape(updated)))
    } catch {
      // ignore
    }
    const best = candidates
      .filter(Boolean)
      .reduce((currentBest, candidate) => (
        mojibakeScore(candidate) < mojibakeScore(currentBest) ? candidate : currentBest
      ), updated)
    updated = best
  }

  updated = updated
    .replace(/[\u241b-\u241f]/g, '')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
    .replace(/[\x1b-\x1f]/g, '')
    .replace(/Âº/g, 'º')
    .replace(/Âª/g, 'ª')
    .replace(/Â°/g, 'º')
    .replace(/\bN\.\s*[º°ª]/gi, 'n.º')
    .replace(/\bArt(?:igo)?\.\s*(\d+)\.\s*[º°]/gi, 'Art. $1.º')
    .replace(/\bArt(?:igo)?\s+(\d+)\.\s*[º°]/gi, 'Art. $1.º')
    .replace(/\bArt(?:igo)?\.?\s+(\d{3})([1-9])\b/gi, 'Art. $1.º, n.º $2')
    .replace(/\b(Art\. \d+\.º)(?=(?:da|do|de|das|dos)\b)/gi, '$1 ')
    .replace(/(\d+)\.\s*[º°]/g, '$1.º')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t\f\v]{2,}/g, ' ')

  return updated
}

export function formatArticleLabel(article) {
  if (!article) return ''
  const normalized = normalizeDisplayText(article).replace(/\s+/g, ' ').trim()
  const stripped = normalized.replace(/^Art(?:igo)?\.?\s*/i, '')
  if (!stripped) return 'Art.'
  const compactParagraph = stripped.match(/^(\d{3})([1-9])$/)
  if (compactParagraph) {
    return `Art. ${compactParagraph[1]}.º, n.º ${compactParagraph[2]}`
  }
  const compactDigits = stripped.replace(/[^\d]/g, '')
  if (/^\d{4}$/.test(compactDigits)) {
    return `Art. ${compactDigits.slice(0, 3)}.º, n.º ${compactDigits.slice(3)}`
  }
  if (/^\d{1,3}(?:\s*,\s*\d{1,3})*$/.test(stripped)) {
    return `Art. ${stripped.split(',').map((item) => `${item.trim()}.º`).join(', ')}`
  }
  const canonical = stripped
    .replace(/\b(\d+)\.\s*[º°]/g, '$1.º')
    .replace(/\b(\d+)\s*[º°]/g, '$1.º')
  return `Art. ${canonical}`
}

function parseBrackets(text) {
  if (!text) return text
  let out = normalizeDisplayText(text)
    // Double brackets → single brackets (keep the citation wrapped)
    .replace(/\[\[(.*?)\]\]/g, '[$1]')
    .replace(/\[\[([^\]\n]{3,120}?)\]/g, '[$1]')
    .replace(/\[\[(?=Art)/g, '[')
    // Keep single-bracket citations intact for LegalMarkdown to process
    .replace(/\(\(/g, '(')
    .replace(/\)\)/g, ')')
  return out
}

function decodeJsonStringFragment(fragment) {
  if (!fragment) return ''
  const safeFragment = String(fragment)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
  try {
    return JSON.parse(`"${safeFragment.replace(/(?<!\\)"/g, '\\"')}"`)
  } catch {
    return safeFragment
      .replace(/\\r\\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '\t')
      .replace(/\\"/g, '"')
      .replace(/\\\//g, '/')
      .replace(/\\\\/g, '\\')
  }
}

function unwrapRichPayload(payload) {
  if (!payload || typeof payload !== 'object') return ''

  let current = payload
  if (current.json_object && typeof current.json_object === 'object') {
    current = current.json_object
  }
  if (current.json && typeof current.json === 'object') {
    current = current.json
  }

  for (const key of ['rich_content', 'answer', 'response', 'direct_answer', 'simple_explanation']) {
    const value = current[key]
    if (typeof value === 'string' && value.trim()) return value
    if (value && typeof value === 'object') {
      const nested = unwrapRichPayload(value)
      if (nested) return nested
    }
  }

  return ''
}

function extractPartialJsonString(text, key) {
  const keyMatch = new RegExp(`"${key}"\\s*:\\s*"`, 's').exec(text)
  if (!keyMatch) return ''

  const start = keyMatch.index + keyMatch[0].length
  let escaped = false
  let end = text.length

  for (let index = start; index < text.length; index += 1) {
    const char = text[index]
    if (escaped) {
      escaped = false
      continue
    }
    if (char === '\\') {
      escaped = true
      continue
    }
    if (char === '"') {
      const tail = text.slice(index + 1, index + 80)
      if (/^\s*(,|})/.test(tail) || /^\s*"(?:cited_articles|cited_diplomas|suggested_actions|confidence|answer_mode)"/.test(tail)) {
        end = index
        break
      }
    }
  }

  let fragment = text.slice(start, end)
  fragment = fragment
    .replace(/\\?","\s*"(?:cited_articles|cited_diplomas|suggested_actions|confidence|answer_mode)[\s\S]*$/m, '')
    .replace(/\n\s*"\s*,?\s*$/m, '')
    .replace(/\s*}\s*$/m, '')
    .trimEnd()

  return decodeJsonStringFragment(fragment)
}

function extractRichContent(raw) {
  if (!raw) return ''
  const text = String(raw).trim()
  if (!text.includes('"rich_content"') && !text.includes('"direct_answer"') && !text.includes('"simple_explanation"')) {
    return ''
  }

  try {
    const parsed = JSON.parse(text)
    const unwrapped = unwrapRichPayload(parsed)
    if (unwrapped) return unwrapped
  } catch {
    // Partial or malformed JSON; regex fallback below.
  }

  const match = text.match(/"(rich_content|answer|response|direct_answer|simple_explanation)"\s*:\s*"((?:\\.|[^"\\])*)"/s)
  if (!match) {
    for (const key of ['rich_content', 'answer', 'response', 'direct_answer', 'simple_explanation']) {
      const partial = extractPartialJsonString(text, key)
      if (partial) return partial
    }
    return ''
  }

  return decodeJsonStringFragment(match[2])
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeAttribute(text) {
  return text.replace(/"/g, '&quot;')
}

function restoreSafeTags(text) {
  return text
    .replace(/&lt;b&gt;/gi, '<b>')
    .replace(/&lt;\/b&gt;/gi, '</b>')
    .replace(/&lt;i&gt;/gi, '<i>')
    .replace(/&lt;\/i&gt;/gi, '</i>')
}

function sanitizeUrl(rawUrl) {
  if (!rawUrl) {
    return ''
  }

  let url = rawUrl.trim()
  url = url
    .replace(/<\/?code>/gi, '')
    .replace(/%3C\/?code%3E/gi, '')
    .replace(/&lt;\/?code&gt;/gi, '')

  return url
}

function replaceMarkdownLinks(text) {
  let output = ''
  let cursor = 0

  while (cursor < text.length) {
    const start = text.indexOf('[', cursor)
    if (start < 0) {
      output += escapeHtml(text.slice(cursor))
      break
    }

    output += escapeHtml(text.slice(cursor, start))
    const closeLabel = text.indexOf(']', start + 1)
    if (closeLabel < 0 || text[closeLabel + 1] !== '(') {
      output += escapeHtml(text[start])
      cursor = start + 1
      continue
    }

    const label = text.slice(start + 1, closeLabel)
    let urlIndex = closeLabel + 2
    let depth = 0
    let closeParen = -1
    while (urlIndex < text.length) {
      const char = text[urlIndex]
      if (char === '(') {
        depth += 1
      } else if (char === ')') {
        if (depth === 0) {
          closeParen = urlIndex
          break
        }
        depth -= 1
      }
      urlIndex += 1
    }

    if (closeParen < 0) {
      output += escapeHtml(text.slice(start, closeLabel + 1))
      cursor = closeLabel + 1
      continue
    }

    const url = sanitizeUrl(text.slice(closeLabel + 2, closeParen))
    if (!/^https?:\/\//i.test(url)) {
      output += escapeHtml(text.slice(start, closeParen + 1))
      cursor = closeParen + 1
      continue
    }

    const cleanLabel = label
      .replace(/^\[/, '')
      .replace(/\]$/, '')
      .replace(/\s+\[/g, ' ')
      .trim()

    output += `<a href="${escapeAttribute(url)}" target="_blank" rel="noreferrer">${escapeHtml(cleanLabel)}</a>`
    cursor = closeParen + 1
  }

  return output
}

function renderInline(text, sourceRefs = []) {
  let output = normalizeDisplayText(text)
  output = output.replace(/(^|\s)([^[\]\n]+)]\((https?:\/\/[^\s]+)\)/g, '$1[$2]($3)')
  output = replaceMarkdownLinks(output)

  if (sourceRefs && sourceRefs.length > 0) {
    output = output.replace(/(^|[\s(\[*_"'])(Art(?:igo|s|\.)?\.?\s*(?:n\.?º?\s*)?(\d+(?:\.?[º°ª])?(?:[-‑]?[a-zA-Z])?)(?:\s*[,;]\s*(?:n\.?º?\s*)?(\d+(?:\.?[º°ª])?))?)/gi, (match, prefix, _, num, num2) => {
      const cleanNum = num.replace(/[^\d]/g, '')
      const sourceIndex = sourceRefs.findIndex(s => {
        if (!s.article_number) return false
        const parts = String(s.article_number).split(/[^\d]+/).filter(Boolean)
        return parts.includes(cleanNum)
      })

      const citationLabel = formatArticleLabel(`${num}${num2 ? `, ${num2}` : ''}`)
      let html = `${prefix}<span class="text-[color:var(--accent)] font-bold italic">${escapeHtml(citationLabel)}</span>`

      if (sourceIndex >= 0) {
        const refIdx = sourceIndex + 1
        const src = sourceRefs[sourceIndex]
        html += `<button type="button" class="inline-ref align-super ml-1.5 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-[color:var(--accent)] px-1.5 text-[9px] font-bold text-white shadow-[var(--shadow-xs)] transition-all hover:bg-[color:var(--accent-hover)] hover:scale-105" data-ref-index="${refIdx}" title="${escapeAttribute(normalizeDisplayText(src.title || ''))}${src.page ? ' · pag. ' + src.page : ''}">${refIdx}</button>`
      }
      return html
    })
  }

  output = output.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  output = output.replace(/\*(?!\*)(.+?)\*/g, '<em>$1</em>')
  output = output.replace(/_(.+?)_/g, '<em>$1</em>')
  output = output.replace(/\[\^(\d+)\]/g, '<button type="button" class="inline-ref" data-ref-index="$1"><sup>[$1]</sup></button>')
  output = restoreSafeTags(output)
  return output
}

export function toSimpleMarkdownHtml(markdown, sourceRefs = []) {
  if (!markdown) {
    return ''
  }

  const lines = markdown.split(/\r?\n/)
  const html = []
  let inUnorderedList = false
  let inOrderedList = false

  const closeLists = () => {
    if (inUnorderedList) {
      html.push('</ul>')
      inUnorderedList = false
    }
    if (inOrderedList) {
      html.push('</ol>')
      inOrderedList = false
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()

    if (!line) {
      closeLists()
      continue
    }

    // Headers support (#, ##, ###)
    const headerMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headerMatch) {
      closeLists()
      const level = headerMatch[1].length
      html.push(`<h${level} class="chat-h${level}">${renderInline(headerMatch[2].trim(), sourceRefs)}</h${level}>`)
      continue
    }

    if (line.startsWith('- ') || line.startsWith('* ')) {
      if (inOrderedList) {
        html.push('</ol>')
        inOrderedList = false
      }
      if (!inUnorderedList) {
        html.push('<ul>')
        inUnorderedList = true
      }
      html.push(`<li>${renderInline(line.slice(2).trim(), sourceRefs)}</li>`)
      continue
    }

    const orderedMatch = line.match(/^(\d+)\.\s+(.+)$/)
    if (orderedMatch) {
      if (inUnorderedList) {
        html.push('</ul>')
        inUnorderedList = false
      }
      if (!inOrderedList) {
        html.push('<ol>')
        inOrderedList = true
      }
      html.push(`<li>${renderInline(orderedMatch[2].trim(), sourceRefs)}</li>`)
      continue
    }

    closeLists()

    const INLINE_NOTA_RE = /\[nota:\s*(.+?)\]/gi
    if (INLINE_NOTA_RE.test(line)) {
      INLINE_NOTA_RE.lastIndex = 0
      const parts = line.split(INLINE_NOTA_RE)
      for (let i = 0; i < parts.length; i++) {
        if (i % 2 === 0) {
          const text = parts[i].trim()
          if (text) html.push(`<p>${renderInline(text, sourceRefs)}</p>`)
        } else {
          html.push(`<aside class="disclaimer-note">[Nota: ${renderInline(parts[i], sourceRefs)}]</aside>`)
        }
      }
      continue
    }

    if (/^base legal(?: de apoio)?\s*:/i.test(line)) {
      html.push(`<p class="key-line">${renderInline(line, sourceRefs)}</p>`)
      continue
    }
    if (/^em termos simples\s*:/i.test(line) || /^passos pr[aá]ticos\s*:/i.test(line)) {
      html.push(`<p class="key-line">${renderInline(line, sourceRefs)}</p>`)
      continue
    }

    html.push(`<p>${renderInline(line, sourceRefs)}</p>`)
  }

  closeLists()
  return html.join('')
}

export function splitAnswerAndSources(answer) {
  if (!answer) {
    return { body: '', sources: [] }
  }

  const marker = /\n\s*Fontes consultadas:\s*/i
  const match = marker.exec(answer)
  if (!match) {
    return { body: answer, sources: [] }
  }

  const body = answer.slice(0, match.index).trim()
  const rawSources = answer.slice(match.index + match[0].length).trim()
  const matches = [...rawSources.matchAll(/-\s+([\s\S]+?)(?=\n-\s+|$)/g)]
  const sources = matches.map((match) => match[1].replace(/\s+/g, ' ').trim())

  return { body, sources }
}

export function splitBodyAndBaseLegal(body) {
  if (!body) {
    return { main: '', baseLegal: '' }
  }

  const normalizedBody = body.replace(/([^\n])\s+(Base legal(?: de apoio)?\s*:)/i, '$1\n\n$2')
  const match = /(^|\n)\s*base legal(?: de apoio)?\s*:\s*/i.exec(normalizedBody)
  if (!match) {
    return { main: normalizedBody.trim(), baseLegal: '' }
  }

  const main = normalizedBody.slice(0, match.index).trim()
  const baseLegal = normalizedBody.slice(match.index).trim()
  return { main, baseLegal }
}

/**
 * Strips noisy/redundant LLM-generated sections from the answer body
 * so the chat bubble only shows the core conversational answer.
 * Structured metadata (sources, confidence) is shown via UI components instead.
 */
export function cleanAnswerBody(raw) {
  if (!raw) return ''

  let text = extractRichContent(raw) || String(raw)

  // Strip unit separator chars/symbols the LLM may accidentally output
  text = text.replace(/[\x1b-\x1f\u241b-\u241f]/g, '')

  const extractedAfterCleanup = extractRichContent(text)
  if (extractedAfterCleanup) text = extractedAfterCleanup

  text = parseBrackets(text)

  // Normalize noisy line breaks and punctuation artifacts produced by LLM output.
  text = text
    .replace(/\r\n?/g, '\n')
    .replace(/^\s*[.•·]+\s*$/gm, '')
    .replace(/\n\s*([,.;:])/g, '$1')
    .replace(/([,.;:])\s*\n\s*([\])])/g, '$1 $2')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  const hasMarkdownStructure = /(^|\n)\s{0,3}(#{1,6}\s|- |\d+\.\s)/m.test(text)
  if (!hasMarkdownStructure && text.length > 280) {
    text = text
      // Give the answer room to breathe when it comes as a single wall of text.
      .replace(/:\s*(?=(?:O|A|As|Os|Se|Quando|Contudo|No entanto|Além|Em seguida|Para|Por isso|Assim|Base legal|O que fazer|Passos|Nota prudencial))/g, ':\n\n')
      .replace(/;\s*(?=(?:O|A|As|Os|Se|Quando|Contudo|No entanto|Além|Em seguida|Para|Por isso|Assim))/g, ';\n\n')
      .replace(/\.\s+(?=(?:###|Base legal|O que fazer|Passos|Em termos simples|Nota prudencial|Limites|Conclusão))/g, '.\n\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  const markers = [
    // Sections that start with newline + keyword (may or may not have colon)
    /\n\s*(?:\*\*)?Base (?:confirmada|parcial|legal)(?: de apoio)?(?:\*\*)?(?:\s*:)?\s*$/im,
    /\n\s*(?:\*\*)?Base (?:Legal|confirmada|parcial)(?: de apoio)?(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Nota prudencial(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Confian[çc]a\s+(?:da resposta|baixa|média|alta|muito alta)(?:\*\*)?(?:\s*[·•]\s*\d+%)?/i,
    /\n\s*(?:\*\*)?Confian[çc]a(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Fontes consultadas(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Distin[çc]ões importantes(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Limites da [Rr]esposta(?:\*\*)?\s*:/i,
    /\n\s*(?:\*\*)?Limita[çc][õo]es(?:\*\*)?\s*:/i,
    // Separator-only markers (confidence / base as standalone lines without colon)
    /\n\s*Base confirmada\s*\n/i,
    /\n\s*Base parcial\s*\n/i,
    /\n\s*Confiança\s+(?:baixa|média|alta|muito alta)\s*[·•]?\s*\d*%?\s*\n*/i,
  ]

  let earliestIndex = text.length

  for (const marker of markers) {
    const match = marker.exec(text)
    if (match && match.index < earliestIndex) {
      earliestIndex = match.index
    }
  }

  if (earliestIndex < text.length && earliestIndex > 10) {
    return text
      .slice(0, earliestIndex)
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  return text.trim()
}
