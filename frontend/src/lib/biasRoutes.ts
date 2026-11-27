export type BiasFields = Record<string, string>;
export interface BiasConfirmation {
  resourceId: string;
  fields: BiasFields;
  machine: string;
  instrument: Record<string, unknown>;
  physicalRoute: Record<string, unknown>;
}
export interface BiasSheet { fields: BiasFields; confirmation?: BiasConfirmation }
export interface BiasDeployment {
  machine?: string;
  instrument?: Record<string, unknown>;
  routes?: Record<string, Record<string, unknown>>;
  biasRoutes?: Record<string, Record<string, unknown>>;
  biasRouteDrafts?: Record<string, BiasSheet>;
}

export const biasGroups = [
  {title: '物理连接', fields: [
    ['lineId', 'Z / Flux 线号'], ['stationIdentity', '仪器识别回执（*IDN?）'],
    ['board','LF 槽位'], ['channel','LF 通道'], ['polarity','电压极性'],
  ]},
  {title: '偏置设定', fields: [
    ['startSettingV','当前静态设定（V）'], ['endSettingV','结束回写值（V）'],
    ['minimumV','允许下限（V）'], ['maximumV','允许上限（V）'],
    ['maxStepV','单步上限（V）'], ['maxSlewVPerSecond','最大斜率（V/s）'],
    ['settleSeconds','每点稳定等待（s）'], ['maxRampSeconds','爬坡时限（s）'],
    ['commandTimeoutSeconds','指令时限（s）'], ['endTimeoutSeconds','结束回写时限（s）'],
  ]},
  {title: '成功回执', fields: [
    ['moduleReplies','选择板卡的成功回执'], ['channelReplies','选择通道的成功回执'],
    ['offsetReplies','设置偏置的成功回执'], ['replySource','回执来源（协议或原始记录）'],
  ]},
  {title: '现场核实依据', fields: [
    ['lineSource','接线与允许范围来源'], ['staticDcSource','静态 DC 状态依据'],
    ['startSource','起始设定来源'], ['endSource','结束回写值来源'],
  ]},
] as const;

export function biasConfirmation(deployment: BiasDeployment, resourceId: string, fields: BiasFields): BiasConfirmation {
  const snapshot = (value: Record<string, unknown> | undefined, keys: string[]) =>
    Object.fromEntries(keys.map(key => [key, value?.[key] ?? null]));
  return {resourceId, fields: {...fields}, machine: deployment.machine ?? '',
    instrument: snapshot(deployment.instrument, ['id','host','port','address']),
    physicalRoute: snapshot(deployment.routes?.[resourceId], ['xyPhysical','readoutPhysical','adcPhysical'])};
}

export function biasSheetComplete(fields: BiasFields) {
  return biasGroups.every(group => group.fields.every(([key]) => Boolean(fields[key]?.trim())));
}

export function biasSheetConfirmed(deployment: BiasDeployment, id: string, sheet: BiasSheet) {
  if (!sheet.confirmation) return false;
  const expected = biasConfirmation(deployment, id, sheet.fields);
  const sameFields = (a:Record<string, unknown>, b:Record<string, unknown>) =>
    Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every(key => Object.hasOwn(b, key) && JSON.stringify(a[key]) === JSON.stringify(b[key]));
  const c = sheet.confirmation;
  return expected.machine===c.machine && expected.resourceId===c.resourceId &&
    sameFields(expected.fields,c.fields) && sameFields(expected.instrument,c.instrument) &&
    sameFields(expected.physicalRoute,c.physicalRoute);
}

export function updateBiasSheet<T extends BiasDeployment>(deployment: T, id: string, sheet?: BiasSheet): T {
  const drafts = {...deployment.biasRouteDrafts};
  const routes = {...deployment.biasRoutes};
  if (sheet) drafts[id] = sheet;
  else delete drafts[id];
  delete routes[id];
  return {...deployment, biasRouteDrafts:drafts, biasRoutes:routes};
}
