// 匯出申請小表單（用戶列表／分群成員共用）：用途、保存天數 → POST /v1/exports → 導到審批與匯出頁。
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/api/client';
import type { Export, ExportRequest } from './types';

export function ExportDialog({ base, onClose }: { base: Omit<ExportRequest, 'purpose' | 'retention_days'>; onClose: () => void }) {
  const nav = useNavigate();
  const [purpose, setPurpose] = useState(''); const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api.post<Export>('/v1/exports', { ...base, purpose: purpose.trim(), retention_days: days }); nav('/approvals'); }
    catch (e) { setErr(e instanceof ApiError ? e.message : '申請失敗'); } finally { setBusy(false); }
  };
  return (
    <div className="row" style={{ background: 'var(--soft)', padding: 10, borderRadius: 10 }}>
      <span className="sub">匯出需數位經營審批；檔案帶浮水印、欄位比畫面嚴一級，上限 10 萬列。</span>
      <input aria-label="匯出用途" placeholder="用途（必填，4–200 字）" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      <select aria-label="保存天數" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90].map((d) => <option key={d} value={d}>保存 {d} 天</option>)}</select>
      <button className="btn-primary" disabled={busy || purpose.trim().length < 4} onClick={() => void submit()}>送出申請</button><button className="btn-ghost" onClick={onClose}>取消</button>{err && <span className="alert">{err}</span>}
    </div>
  );
}
