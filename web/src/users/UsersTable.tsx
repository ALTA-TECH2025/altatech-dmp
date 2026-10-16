// 用戶列表表格 + keyset 分頁（上一頁用游標堆疊）；遮罩欄位加提示；點列進細查（需 profile.use）。
import { useAuth } from '@/auth/AuthContext';
import { fmtNum } from '@/analytics/series';
import type { UserRow, UsersListResult } from './types';

export function UsersTable({ res, colCn, onPrev, onNext, canPrev, busy, onOpen }: { res: UsersListResult; colCn: (c: string) => string; onPrev: () => void; onNext: () => void; canPrev: boolean; busy: boolean; onOpen?: (oneId: string) => void }) {
  const { can } = useAuth();
  const openable = !!onOpen && can('profile', 'use');
  const fmt = (v: UserRow[string]) => (v === null || v === undefined ? '—' : typeof v === 'number' ? fmtNum(v) : typeof v === 'boolean' ? (v ? '是' : '否') : String(v));
  return (
    <div className="card flush">
      <div className="tbl-wrap"><table className="tbl">
        <thead><tr>{res.columns.map((c) => <th key={c}>{colCn(c)}{res.masked[c] && <span className="sub" title={res.masked[c]}>遮罩</span>}</th>)}{openable && <th />}</tr></thead>
        <tbody>{res.rows.map((r, i) => (
          <tr key={i} className={openable ? 'clickable' : ''} onClick={openable ? () => onOpen?.(String(r.one_id)) : undefined}>
            {res.columns.map((c) => <td key={c} className={typeof r[c] === 'number' ? 'num mono' : c === 'one_id' ? 'mono' : ''}>{fmt(r[c])}</td>)}
            {openable && <td className="num"><button className="btn-ghost sm">細查</button></td>}
          </tr>))}
          {!res.rows.length && <tr><td colSpan={res.columns.length + 1} className="sub" style={{ textAlign: 'center', padding: 24 }}>沒有符合的用戶</td></tr>}
        </tbody>
      </table></div>
      <div className="row" style={{ padding: '10px 12px', marginTop: 0 }}>
        <span className="sub">共 {res.total === null || res.total === undefined ? '—' : res.total.toLocaleString()} 人 · 本頁 {res.rows.length} 列{res.masked.one_id ? ' · one_id 已遮罩' : ''}</span>
        <div className="right"><button className="btn-ghost sm" disabled={!canPrev || busy} onClick={onPrev}>上一頁</button> <button className="btn-ghost sm" disabled={!res.next_cursor || busy} onClick={onNext}>下一頁</button></div>
      </div>
      {res.warnings.length > 0 && <p className="note" style={{ padding: '0 12px 10px' }}>{res.warnings.join('；')}</p>}
    </div>
  );
}
