export const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
export const ATTACHMENT_MAX_COUNT = 5;
export const ATTACHMENT_PREVIEW_BYTES = 64 * 1024;
export const ATTACHMENT_PREVIEW_CHARACTERS = 4000;
export const ATTACHMENT_ACCEPT = ".pdf,.txt,.md,.csv,.json";
export type AttachmentExtension = "pdf" | "txt" | "md" | "csv" | "json";

export interface LocalAttachment {
  id: string;
  name: string;
  size: number;
  lastModified: number;
  extension: AttachmentExtension;
  file: File;
  preview: string | null;
  previewTruncated: boolean;
}

export function attachmentFingerprint(
  file: Pick<File, "name" | "size" | "lastModified">,
): string {
  return JSON.stringify([file.name, file.size, file.lastModified]);
}

export function formatAttachmentSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : bytes >= 1024
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${bytes} B`;
}

export function validateAttachment(
  file: File,
  existing: readonly LocalAttachment[] = [],
): string | null {
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  if (!extension || !["pdf", "txt", "md", "csv", "json"].includes(extension))
    return "仅支持 PDF、TXT、MD、CSV、JSON 文件。";
  if (file.size === 0) return "文件为空，请选择有内容的文件。";
  if (file.size > ATTACHMENT_MAX_BYTES) return "单个附件不能超过 10 MB。";
  if (
    existing.some(
      (attachment) =>
        attachmentFingerprint(attachment.file) === attachmentFingerprint(file),
    )
  )
    return "该文件已在待发送附件中。";
  if (existing.length >= ATTACHMENT_MAX_COUNT)
    return "每条消息最多附加 5 个文件，请先发送或移除附件。";
  return null;
}

/** Reads only a bounded prefix. Text is returned as plain text and must never become HTML. */
export async function prepareLocalAttachments(
  files: readonly File[],
  existing: readonly LocalAttachment[] = [],
) {
  const accepted: LocalAttachment[] = [];
  const errors: string[] = [];
  for (const file of files) {
    const validation = validateAttachment(file, [...existing, ...accepted]);
    if (validation) {
      errors.push(`${file.name}：${validation}`);
      continue;
    }
    const extension = file.name
      .split(".")
      .at(-1)!
      .toLowerCase() as AttachmentExtension;
    try {
      let preview: string | null = null;
      let previewTruncated = false;
      if (extension === "pdf") {
        const header = new Uint8Array(await file.slice(0, 5).arrayBuffer());
        if (String.fromCharCode(...header) !== "%PDF-")
          throw new Error("文件内容不是有效的 PDF 文件头。");
      } else {
        const prefix = new Uint8Array(
          await file.slice(0, ATTACHMENT_PREVIEW_BYTES).arrayBuffer(),
        );
        const text = new TextDecoder("utf-8", { fatal: true }).decode(prefix, {
          stream: file.size > ATTACHMENT_PREVIEW_BYTES,
        });
        if (text.includes("\0"))
          throw new Error("检测到二进制或非 UTF-8 内容，请转换为 UTF-8 文本。");
        preview = text.slice(0, ATTACHMENT_PREVIEW_CHARACTERS);
        previewTruncated =
          text.length > ATTACHMENT_PREVIEW_CHARACTERS ||
          file.size > ATTACHMENT_PREVIEW_BYTES;
      }
      accepted.push({
        id:
          globalThis.crypto?.randomUUID?.() ||
          `local-file-${Date.now()}-${accepted.length}-${Math.random().toString(36).slice(2)}`,
        name: file.name,
        size: file.size,
        lastModified: file.lastModified,
        extension,
        file,
        preview,
        previewTruncated,
      });
    } catch (error) {
      errors.push(
        `${file.name}：${error instanceof TypeError ? "无法按 UTF-8 文本读取，请检查文件编码。" : error instanceof Error ? error.message : "读取失败，请重新选择文件。"}`,
      );
    }
  }
  return { accepted, errors };
}

/** Memory only: no uploads, IndexedDB or localStorage. Each workflow owns its own value. */
export class WorkflowLocalStore<T> {
  private values = new Map<string, T>();
  get(workflowId: string): T | undefined {
    return this.values.get(workflowId);
  }
  set(workflowId: string, value: T): void {
    this.values.set(workflowId, value);
  }
  delete(workflowId: string): void {
    this.values.delete(workflowId);
  }
  clear(): void {
    this.values.clear();
  }
}
