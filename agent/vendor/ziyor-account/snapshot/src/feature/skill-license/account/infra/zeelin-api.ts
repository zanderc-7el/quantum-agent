import type { ZeeLinUserInfo, AcquireLicenseResponse } from '../../domain/types'

const ZEELIN_API_BASE = 'https://skill.zeelin.cn'

export async function callZeelinUserInfo(appKey: string): Promise<ZeeLinUserInfo | null> {
  const res = await fetch('/api/skill-zeelin/proxy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      endpoint: '/api/zeelin/user_info',
      body: { app_key: appKey },
    }),
  })
  if (!res.ok) return null
  const data = await res.json()
  if (data.code !== 200 || !data.data) return null
  return data.data as ZeeLinUserInfo
}

export async function callAcquireLicense(
  appKey: string,
  skillId: string,
  planId: number,
): Promise<AcquireLicenseResponse> {
  const res = await fetch('/api/skill-license/acquire-license', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_key: appKey, skill_id: skillId, plan_id: planId }),
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return data as AcquireLicenseResponse
}
