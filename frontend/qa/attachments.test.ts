import test from "node:test";
import assert from "node:assert/strict";
import {
  ATTACHMENT_MAX_BYTES,
  ATTACHMENT_PREVIEW_CHARACTERS,
  formatAttachmentSize,
  prepareLocalAttachments,
  validateAttachment,
  WorkflowLocalStore,
} from "../src/lib/attachments.ts";

test("supported local text attachments preserve original File objects and plain-text previews", async () => {
  const source = '<script>alert("unsafe")</script>\n# 实验资料\nT1 = 50.84';
  const files = ["txt", "MD", "csv", "json"].map(
    (extension) =>
      new File([source], `paper.${extension}`, { lastModified: 123 }),
  );
  const result = await prepareLocalAttachments(files);
  assert.equal(result.accepted.length, 4);
  assert.deepEqual(result.errors, []);
  result.accepted.forEach((attachment, index) => {
    assert.equal(attachment.file, files[index]);
    assert.equal(attachment.preview, source);
    assert.equal(attachment.previewTruncated, false);
    assert.equal(attachment.lastModified, 123);
  });
});

test("PDF attachments are local binary references with no fabricated text extraction", async () => {
  const file = new File(["%PDF-1.7\n% fixture"], "paper.pdf");
  const result = await prepareLocalAttachments([file]);
  assert.equal(result.accepted.length, 1);
  assert.equal(result.accepted[0].preview, null);
  assert.equal(result.accepted[0].file, file);
  assert.equal(result.accepted[0].extension, "pdf");
  const invalid = await prepareLocalAttachments([
    new File(["not a PDF"], "fake.pdf"),
  ]);
  assert.equal(invalid.accepted.length, 0);
  assert.match(invalid.errors[0], /不是有效的 PDF/);
});

test("extensions, empty files, 10 MB limits and five-attachment limits are enforced", async () => {
  assert.match(
    validateAttachment(new File(["content"], "script.html"))!,
    /仅支持/,
  );
  assert.match(validateAttachment(new File([], "empty.txt"))!, /为空/);
  assert.equal(
    validateAttachment(
      new File([new Uint8Array(ATTACHMENT_MAX_BYTES)], "exact.txt"),
    ),
    null,
  );
  assert.match(
    validateAttachment(
      new File([new Uint8Array(ATTACHMENT_MAX_BYTES + 1)], "oversize.txt"),
    )!,
    /10 MB/,
  );
  const files = Array.from(
    { length: 6 },
    (_, index) => new File(["text"], `file${index}.txt`),
  );
  const result = await prepareLocalAttachments(files);
  assert.equal(result.accepted.length, 5);
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0], /最多附加 5/);
});

test("duplicate selection is rejected while failed files do not occupy attachment slots", async () => {
  const file = new File(["valid"], "same.txt", { lastModified: 456 });
  const first = await prepareLocalAttachments([file]);
  const second = await prepareLocalAttachments(
    [new File(["x"], "bad.exe"), file, new File(["valid"], "new.txt")],
    first.accepted,
  );
  assert.equal(second.accepted.length, 1);
  assert.equal(second.accepted[0].name, "new.txt");
  assert.equal(second.errors.length, 2);
  assert.match(second.errors.join(" "), /已在待发送/);
});

test("UTF-8 text previews are bounded and binary or invalid encodings return errors", async () => {
  const long = await prepareLocalAttachments([
    new File(["中".repeat(50000)], "long.md"),
  ]);
  assert.equal(long.accepted[0].preview?.length, ATTACHMENT_PREVIEW_CHARACTERS);
  assert.equal(long.accepted[0].previewTruncated, true);
  const invalid = await prepareLocalAttachments([
    new File([new Uint8Array([0xff, 0xfe, 0, 0x41])], "utf16.txt"),
    new File(["hello\0world"], "binary.txt"),
  ]);
  assert.equal(invalid.accepted.length, 0);
  assert.equal(invalid.errors.length, 2);
  assert.ok(invalid.errors.every((error) => /UTF-8/.test(error)));
});

test("workflow-local stores isolate attachments and permit independent removal and clearing", async () => {
  const files = await prepareLocalAttachments([
    new File(["context"], "paper.txt"),
  ]);
  const store = new WorkflowLocalStore<{
    attachments: typeof files.accepted;
    messages: { attachments: typeof files.accepted }[];
  }>();
  store.set("workflow-a", { attachments: files.accepted, messages: [] });
  store.set("workflow-b", { attachments: [], messages: [] });
  const messageAttachments = [...store.get("workflow-a")!.attachments];
  store.set("workflow-a", {
    attachments: [],
    messages: [{ attachments: messageAttachments }],
  });
  assert.equal(
    store.get("workflow-a")!.messages[0].attachments[0].file,
    files.accepted[0].file,
  );
  assert.deepEqual(store.get("workflow-b"), { attachments: [], messages: [] });
  store.delete("workflow-a");
  assert.equal(store.get("workflow-a"), undefined);
  assert.ok(store.get("workflow-b"));
  store.clear();
  assert.equal(store.get("workflow-b"), undefined);
  assert.equal(formatAttachmentSize(1024), "1.0 KB");
  assert.equal(formatAttachmentSize(1024 * 1024), "1.0 MB");
});
