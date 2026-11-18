import { NextRequest } from 'next/server'
import { normalizeZeeLinProxyHeaders, ZEE_LIN_MESSAGES_URL } from '@/lib/zeelin'
import { getDb } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const REQUEST_HEADERS_TO_SKIP = new Set([
  'connection',
  'content-length',
  'host',
  'transfer-encoding',
])

const RESPONSE_HEADERS_TO_SKIP = new Set([
  'connection',
  'content-encoding',
  'content-length',
  'transfer-encoding',
])

function copyRequestHeaders(req: NextRequest): Headers {
  const headers = new Headers()

  for (const [key, value] of req.headers.entries()) {
    if (!REQUEST_HEADERS_TO_SKIP.has(key.toLowerCase())) {
      headers.set(key, value)
    }
  }

  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }

  return headers
}

function copyResponseHeaders(headers: Headers): Headers {
  const nextHeaders = new Headers()

  for (const [key, value] of headers.entries()) {
    if (!RESPONSE_HEADERS_TO_SKIP.has(key.toLowerCase())) {
      nextHeaders.set(key, value)
    }
  }

  return nextHeaders
}

export async function POST(req: NextRequest) {
  try {
    const headers = normalizeZeeLinProxyHeaders(copyRequestHeaders(req))

    // 强制使用数据库中配置的 ZeeLin API key，避免请求携带其他提供商凭据。
    const db = getDb()
    const row = db.prepare("SELECT api_key FROM api_providers WHERE id = 'zeelin'").get() as { api_key: string } | undefined
    if (row?.api_key) {
      headers.set('x-api-key', row.api_key)
    }

    const upstream = await fetch(ZEE_LIN_MESSAGES_URL, {
      method: 'POST',
      headers,
      body: await req.text(),
      signal: req.signal,
    })

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: copyResponseHeaders(upstream.headers),
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to reach Ziyor upstream'
    return Response.json({ error: { message } }, { status: 502 })
  }
}
