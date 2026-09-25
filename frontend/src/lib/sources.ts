import type { Message } from '../types'

// Also suppress incorrectly attached sources in messages saved by older versions.
export function citedSources(message: Message) {
  const cited = new Set(Array.from(message.content.matchAll(/\[Source\s+(\d+)\]/gi), match => Number(match[1])))
  return (message.sources || []).map((source, index) => ({ ...source, source_number: source.source_number ?? index + 1 }))
    .filter(source => cited.has(source.source_number))
}
