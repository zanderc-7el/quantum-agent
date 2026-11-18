import { NextRequest, NextResponse } from 'next/server'

const LOGIN_URL = 'https://skill.zeelin.cn/api/zeelin/login'

interface LoginBody {
  mobile?: string
  code?: string
  invitationCode?: string
}

export async function POST(req: NextRequest) {
  let body: LoginBody
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ code: 400, message: 'Invalid JSON body' }, { status: 400 })
  }

  const mobile = (body.mobile || '').trim()
  const code = (body.code || '').trim()
  const invitationCode = (body.invitationCode || '').trim()

  if (!/^1[3-9]\d{9}$/.test(mobile)) {
    return NextResponse.json({ code: 400, message: '手机号格式不正确' }, { status: 400 })
  }

  if (!code) {
    return NextResponse.json({ code: 400, message: '验证码不能为空' }, { status: 400 })
  }

  try {
    const upstream = await fetch(LOGIN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mobile,
        code,
        invitation_code: invitationCode || undefined,
      }),
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
    const message = error instanceof Error ? error.message : '登录失败'
    return NextResponse.json({ code: 500, message }, { status: 500 })
  }
}
