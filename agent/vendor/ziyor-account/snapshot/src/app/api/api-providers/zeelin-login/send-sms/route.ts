import { NextRequest, NextResponse } from 'next/server'

const SEND_SMS_URL = 'https://skill.zeelin.cn/api/zeelin/send_sms'

interface SendSmsBody {
  mobile?: string
}

export async function POST(req: NextRequest) {
  let body: SendSmsBody
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ code: 400, message: 'Invalid JSON body' }, { status: 400 })
  }

  const mobile = (body.mobile || '').trim()
  if (!/^1[3-9]\d{9}$/.test(mobile)) {
    return NextResponse.json({ code: 400, message: '手机号格式不正确' }, { status: 400 })
  }

  try {
    const upstream = await fetch(SEND_SMS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mobile }),
      signal: AbortSignal.timeout(15000),
    })

    const data = await upstream.json().catch(() => ({ code: upstream.status, message: `HTTP ${upstream.status}` }))

    if (!upstream.ok) {
      return NextResponse.json(
        {
          code: (data as { code?: number }).code || upstream.status,
          message: (data as { message?: string }).message || `HTTP ${upstream.status}`,
        },
        { status: upstream.status }
      )
    }

    return NextResponse.json(data)
  } catch (error) {
    const message = error instanceof Error ? error.message : '发送验证码失败'
    return NextResponse.json({ code: 500, message }, { status: 500 })
  }
}
