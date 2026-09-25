export const API = 'http://localhost:8000'

function errorMessage(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (Array.isArray(value)) {
    const messages = value.map(item => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') {
        const location = 'loc' in item && Array.isArray(item.loc) ? item.loc.join('.') : ''
        return location ? `${location}: ${item.msg}` : item.msg
      }
      return ''
    }).filter(Boolean)
    return messages.length ? messages.join('; ') : undefined
  }
  return undefined
}

export async function responseError(response: Response, path: string): Promise<Error> {
  const body = await response.text().catch(() => '')
  let message: string | undefined
  if (body) {
    try {
      const data: unknown = JSON.parse(body)
      if (data && typeof data === 'object') {
        const error = data as Record<string, unknown>
        message = errorMessage(error.detail) || errorMessage(error.message) || errorMessage(error.error)
      }
    } catch {
      if (response.headers.get('content-type')?.startsWith('text/plain')) message = body.trim()
    }
  }
  return new Error(message || `Request failed (${response.status}) at ${path}`)
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(API + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) throw await responseError(response, path)
  return response.json()
}

export async function apiForm<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(API + path, { method: 'POST', body })
  if (!response.ok) throw await responseError(response, path)
  return response.json()
}
