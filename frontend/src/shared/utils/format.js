export function classNames(...parts) {
  return parts.filter(Boolean).join(' ')
}

export function formatNow() {
  return new Intl.DateTimeFormat('pt-AO', {
    hour: '2-digit',
    minute: '2-digit',
    day: '2-digit',
    month: '2-digit',
  }).format(new Date())
}

export function formatHumanTimestamp(value) {
  if (!value) {
    return 'Sem data'
  }

  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value)
  }

  const now = new Date()
  const isSameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()

  if (isSameDay) {
    return `Hoje, ${new Intl.DateTimeFormat('pt-AO', { hour: '2-digit', minute: '2-digit' }).format(date)}`
  }

  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const isYesterday =
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate()

  if (isYesterday) {
    return `Ontem, ${new Intl.DateTimeFormat('pt-AO', { hour: '2-digit', minute: '2-digit' }).format(date)}`
  }

  return new Intl.DateTimeFormat('pt-AO', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function clipText(text, max = 140) {
  if (!text) {
    return ''
  }
  if (text.length <= max) {
    return text
  }
  return `${text.slice(0, max).trim()}...`
}
