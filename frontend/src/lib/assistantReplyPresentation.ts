/** Display only. Stored replies and explicit technical/code passages stay intact. */
export function technicalReplyRequested(history: readonly {role: string; text?: string; content?: string}[], index: number) {
  for (let i = index - 1; i >= 0; i--) {
    if (history[i].role === 'user')
      return /字段|标识符|枚举|键名|源码|原始(?:回复|参数名|键|代码)|\b(?:API|JSON|SQL|contrast_snr|selection|verification|acquisitionState)\b/i.test(history[i].text ?? history[i].content ?? '');
  }
  return false;
}

export function assistantReplyPresentation(original: string, technical = false): { text: string; changed: boolean } {
  if (technical) return { text: original, changed: false };
  let fence: string | undefined;
  const text = original.split(/(\r?\n)/).map(line => {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = undefined;
      return line;
    }
    if (fence || /^\s{4}/.test(line) || !/[\u3400-\u9fff]/.test(line)
      || /字段|枚举|代码|源码|变量|函数|键名|属性|正则|\b(?:API|JSON|SQL)\b/.test(line)) return line;
    return line.split(/(`+[^`\n]*`+)/).map((part, index) => {
      if (index % 2) return part;
      return part
        .replace(/((?:首测|初测|首次采集|选点采集|独立复测|复测|对比度信噪比|信噪比))\s*[（(](selection|verification|contrast_snr)[）)]/g,
          (all, label: string, key: string) => (
            key === 'selection' && /首测|初测|首次采集|选点采集/.test(label)
            || key === 'verification' && /复测/.test(label)
            || key === 'contrast_snr' && /信噪比/.test(label)) ? (key === 'contrast_snr' ? '对比度信噪比' : label) : all)
        .replace(/[（(]selection[）)]/g, '（首测）')
        .replace(/[（(]verification[）)]/g, '（独立复测）')
        .replace(/[（(]contrast_snr[）)]/g, '（对比度信噪比）')
        .replace(/([\u3400-\u9fff])\s+(selection|verification)\s*操作/g,
          (_all, before: string, key: string) => before + (key === 'selection' ? '首测操作' : '独立复测操作'))
        .replace(/\bselection\b\s*操作/g, '首测操作')
        .replace(/\bverification\b\s*操作/g, '独立复测操作')
        .replace(/\bacquisitionState\b\s*快照/g, '采集状态快照')
        .replace(/((?:节点)?状态\s*(?:为|是|[:：=])?\s*)\bverified\b/g, (_all, prefix: string) => prefix.trimEnd() + '已验证')
        .replace(/质量(?:门槛|状态)?\s*(?:为|是|[:：=])?\s*\bpassed\b/g, '诊断门槛已通过')
        .replace(/((?:操作)?回执)\s*(仍)?\s*(?:为|是|[:：=])?\s*\bpending\b/g, '$1$2待完成');
    }).join('');
  }).join('');
  return { text, changed: text !== original };
}
