// 審批單與匯出狀態的顯示文字（純常數／函式，有單元測試）
import type { Approval, ApprovalStatus, Export, ExportStatus } from '@/users/types';

export const APPROVAL_TYPE_CN: Record<Approval['type'], string> = { export: '匯出', segment: '分群生效', push: '名單推送', tag: '標籤生效', reveal_l2: '欄位揭露（L2）', reveal_l3: '欄位揭露（L3）', agent_proposal: 'agent 提議', role_change: '角色變更（雙人複核）', break_glass: '臨時提權（雙人複核）', output_task: '資料輸出任務' };
export const APPROVAL_STATUS_CN: Record<ApprovalStatus, string> = { pending: '待審批', approved: '已核可', rejected: '已退回', expired: '已逾期' };
export const APPROVAL_PILL: Record<ApprovalStatus, string> = { pending: 'amber', approved: 'blue', rejected: 'red', expired: 'gray' };
export const EXPORT_STATUS_CN: Record<ExportStatus, string> = { pending_approval: '待審批', approved: '已核可，可產檔', rejected: '已退回', generated: '已產檔，可下載', downloaded: '已下載', expired: '已過期' };
export const EXPORT_PILL: Record<ExportStatus, string> = { pending_approval: 'amber', approved: 'blue', rejected: 'red', generated: 'blue', downloaded: 'gray', expired: 'gray' };

/** 匯出單下一步：產檔／下載／等待／無。 */
export function exportNext(e: Pick<Export, 'status'>): 'run' | 'download' | 'wait' | 'none' {
  if (e.status === 'approved') return 'run';
  if (e.status === 'generated' || e.status === 'downloaded') return 'download';
  if (e.status === 'pending_approval') return 'wait';
  return 'none';
}

/** 到期倒數文字（天／小時）；null = 無。 */
export function expiresText(iso: string | null, now = new Date()): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - now.getTime();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return '已到期';
  const h = Math.floor(ms / 3600000);
  return h >= 48 ? `${Math.floor(h / 24)} 天後到期` : `${Math.max(1, h)} 小時後到期`;
}
