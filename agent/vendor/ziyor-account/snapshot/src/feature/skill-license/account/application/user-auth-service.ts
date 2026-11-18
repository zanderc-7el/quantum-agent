import type { ZeeLinUserInfo, LoginState } from '../../domain/types'
import { getZeeLinAppKey, saveZeeLinAppKey } from '../infra/api-providers-repo'
import { callZeelinUserInfo } from '../infra/zeelin-api'

export async function isLoggedIn(): Promise<boolean> {
  const key = await getZeeLinAppKey()
  return !!key
}

export async function fetchUserInfo(appKey?: string): Promise<ZeeLinUserInfo | null> {
  const key = appKey || (await getZeeLinAppKey())
  if (!key) return null
  return callZeelinUserInfo(key)
}

export async function getLoginState(): Promise<LoginState | null> {
  const info = await fetchUserInfo()
  if (!info) return null
  return { mobile: info.mobile, nickname: info.nickname, remain_calls: info.remain_calls }
}

export async function loginAndSave(appKey: string): Promise<void> {
  await saveZeeLinAppKey(appKey)
}
