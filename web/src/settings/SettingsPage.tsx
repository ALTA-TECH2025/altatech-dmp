// 平台設定（唯讀）：/v1/settings 五區塊（身分與登入、權限、隱碼與揭露、Agent 預算、審計告警）；密鑰只顯示 secret_ref；線上修改（雙人複核）第二階段。
import { useEffect, useState } from 'react';
import { api, ApiError } from '@/api/client';
import type { SettingsView } from '@/api/types';
import { RequireGrant } from '@/auth/RequireGrant';

export function SettingsPage() {
  return <RequireGrant module="iam"><Inner /></RequireGrant>;
}

function Inner() {
  const [st, setSt] = useState<SettingsView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sec, setSec] = useState<string>('identity');
  useEffect(() => { api.get<SettingsView>('/v1/settings').then(setSt).catch((e) => setErr(e instanceof ApiError ? e.message : '讀取失敗')); }, []);
  if (err) return <div className="page"><div className="alert">{err}</div></div>;
  if (!st) return <div className="page"><div className="loading">載入中…</div></div>;
  const cur = st.sections.find((s) => s.key === sec) ?? st.sections[0];
  return (
    <div className="page">
      <div className="page-h"><div><h1>平台設定 <span className="pill gray">唯讀</span></h1><p>登入政策、session、角色與雙人複核、K 匿名、隱碼與揭露、匯出上限、agent 預算、審計告警。目前以設定檔與 PR 管理；密鑰只存 secret_ref。</p></div>
        <div className="right"><span className="pill amber">線上修改（雙人複核）：第二階段</span></div></div>
      <div className="seg" style={{ marginBottom: 12 }}>{st.sections.map((s) => <a key={s.key} href="#" className={s.key === cur.key ? 'on' : ''} onClick={(e) => { e.preventDefault(); setSec(s.key); }}>{s.cn}</a>)}</div>
      <div className="card"><div className="card-h"><h3>{cur.cn}</h3><span className="sub mono">{cur.source}</span></div>
        <table className="tbl"><thead><tr><th style={{ width: 180 }}>項目</th><th>值</th><th style={{ width: 260 }}>說明</th></tr></thead>
          <tbody>{cur.items.map((it) => <tr key={it.key}><td><b>{it.cn}</b><span className="sub mono">{it.key}</span></td><td><Value v={it.value} /></td><td className="sub">{it.note}</td></tr>)}</tbody></table>
      </div>
      <p className="note">{st.edit.note}</p>
    </div>
  );
}

function Value({ v }: { v: unknown }) {
  if (v == null || v === '') return <span className="sub">—</span>;
  if (typeof v === 'boolean') return <span className={`pill ${v ? 'blue' : 'gray'}`}>{v ? '開' : '關'}</span>;
  if (typeof v === 'number' || typeof v === 'string') return <span className="mono">{String(v)}</span>;
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x !== 'object')) return <span className="mono">{v.map(String).join('、')}</span>;
    const cols = Array.from(new Set(v.flatMap((x) => Object.keys(x as object))));
    return <div className="tbl-wrap"><table className="tbl" style={{ fontSize: 12 }}><thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>{v.map((x, i) => <tr key={i}>{cols.map((c) => <td key={c} className="mono" style={{ fontSize: 11.5 }}>{cell((x as Record<string, unknown>)[c])}</td>)}</tr>)}</tbody></table></div>;
  }
  return <dl className="kv">{Object.entries(v as Record<string, unknown>).map(([k, x]) => <><dt key={k + 'k'} className="mono">{k}</dt><dd key={k + 'v'} className="mono">{cell(x)}</dd></>)}</dl>;
}
const cell = (x: unknown): string => (x == null ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x));
