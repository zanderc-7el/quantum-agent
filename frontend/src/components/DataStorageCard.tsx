import { useEffect, useState } from "react";
import { Database, HardDrive, RefreshCw } from "lucide-react";
import { readChatStorageStats, subscribeChatStorage } from "../lib/chatPersistence";
import { getWorkflowStorageStatus, readWorkflowStorageBytes, subscribeWorkflowStorage } from "../lib/workflowStorage";
import { formatAttachmentSize } from "../lib/attachments";

export default function DataStorageCard({ workflowCount }: { workflowCount: number }) {
  const [workflowStorage, setWorkflowStorage] = useState(getWorkflowStorageStatus);
  const [bytes, setBytes] = useState(readWorkflowStorageBytes);
  const [chat, setChat] = useState<Awaited<ReturnType<typeof readChatStorageStats>> | null>(null);
  const [checking, setChecking] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    const update = () => {
      setWorkflowStorage(getWorkflowStorageStatus()); setBytes(readWorkflowStorageBytes());
      setChecking(true);
      void readChatStorageStats().then((value) => { if (active) setChat(value); }).finally(() => { if (active) setChecking(false); });
    };
    update();
    const offWorkflow = subscribeWorkflowStorage(update), offChat = subscribeChatStorage(update);
    return () => { active = false; offWorkflow(); offChat(); };
  }, [revision]);
  return <section className="data-storage-card" aria-label="本机数据存储">
    <div className="data-storage-heading"><Database size={20}/><div><h3>本机数据存储</h3><p>刷新、重新登录后继续使用已有记录</p></div><button className="icon-button" aria-label="刷新存储状态" disabled={checking} onClick={() => setRevision((v) => v + 1)}><RefreshCw size={15}/></button></div>
    <div className="data-storage-grid">
      <div><small>工作流与实验参数</small><strong>{workflowCount}<em> 个任务</em></strong><span>{formatAttachmentSize(bytes)} / 5 MB</span></div>
      <div><small>对话记录</small><strong>{chat?.recordCount ?? "—"}<em> 个会话</em></strong><span>草稿与消息自动保存</span></div>
      <div><small>本地附件</small><strong>{chat?.attachmentCount ?? "—"}<em> 个文件</em></strong><span>{formatAttachmentSize(chat?.attachmentBytes ?? 0)} / 100 MB</span></div>
    </div>
    <div className="storage-health"><HardDrive size={14}/><span>{workflowStorage.status === "error" ? workflowStorage.error : workflowStorage.savedAt ? `工作流最近保存于 ${new Date(workflowStorage.savedAt).toLocaleTimeString("zh-CN", {hour12:false})}` : bytes ? "已读取本机工作流记录；下次操作时自动保存" : "当前为初始示例，操作后自动保存"}</span></div>
    {chat && chat.status !== "ready" && <p className="ws-error" role="alert">{chat.error || "对话存储不可用，请检查浏览器权限后重试。"}</p>}
    <p className="ws-note">工作流和设置保存在本浏览器；对话与原始附件使用本机数据库。不同网址、浏览器和设备不共享数据，清除站点数据会删除记录。未上传服务器。</p>
  </section>;
}
