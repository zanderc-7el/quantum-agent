export async function getZeeLinAppKey(): Promise<string | null> {
  try {
    const res = await fetch('/api/api-providers')
    if (!res.ok) return null
    const providers = (await res.json()) as Array<{ id: string; api_key: string }>
    const zeelin = providers.find((p) => p.id === 'zeelin')
    if (!zeelin?.api_key) return null
    return zeelin.api_key
  } catch {
    return null
  }
}

export async function saveZeeLinAppKey(appKey: string): Promise<void> {
  await fetch('/api/api-providers/zeelin', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: appKey }),
  })
}

export async function installSkill(skillId: string, licenseKey: string): Promise<void> {
  const res = await fetch('/api/skill-license/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skillId, licenseKey }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({})) as { error?: string }
    throw new Error(data.error || `安装失败 HTTP ${res.status}`)
  }
}
