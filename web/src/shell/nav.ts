// 側欄：與原型（prototype/js/app.js NAV）同構 — 首頁／分析／營運／資料／AI 洞察／數據治理／系統管理；名稱依實際功能微調，原型沒有的頁放到對應群組。
// 每項綁一個模組，用 /v1/iam/me 的 grants 過濾（看不到 ≠ 不能呼叫 API，後端另有 authorize）。
import type { Module } from '@/api/types';

export interface NavItem { path: string; cn: string; module: Module; stage?: 'phase2' | 'todo' }
export interface NavGroup { id: string; cn: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  { id: 'home', cn: '首頁', items: [{ path: '/overview', cn: '總覽／儀表板', module: 'overview' }, { path: '/bookmarks', cn: '書籤', module: 'overview' }] },
  { id: 'analysis', cn: '分析', items: [
    { path: '/events', cn: '事件分析', module: 'events' },
    { path: '/funnel', cn: '漏斗分析', module: 'funnel' },
    { path: '/retention', cn: '留存分析', module: 'events' },
    { path: '/path', cn: '行為路徑', module: 'path' },
    { path: '/attribution', cn: '歸因分析', module: 'attribution' },
    { path: '/users', cn: '用戶列表', module: 'users' },
    { path: '/sql', cn: '自定義 SQL', module: 'events' },
  ] },
  { id: 'ops', cn: '營運', items: [
    { path: '/segments', cn: '分群與標籤', module: 'segments' },
    { path: '/approvals', cn: '審批與匯出', module: 'users' },
    { path: '/triggers', cn: '即時觸發', module: 'triggers', stage: 'phase2' },
  ] },
  { id: 'data', cn: '資料', items: [
    { path: '/integration', cn: '數據整合', module: 'integration' },
    { path: '/profile', cn: '用戶細查 / OneID', module: 'profile' },
  ] },
  { id: 'ai', cn: 'AI 洞察', items: [
    { path: '/ai/insights', cn: '洞察串流', module: 'ai_ask', stage: 'phase2' },
    { path: '/ai/ask', cn: '問答', module: 'ai_ask', stage: 'phase2' },
    { path: '/ai/agents', cn: 'Agents', module: 'ai_admin', stage: 'phase2' },
    { path: '/ai/tools', cn: '工具目錄', module: 'ai_admin', stage: 'phase2' },
  ] },
  { id: 'gov', cn: '數據治理', items: [
    { path: '/governance', cn: '元事件管理', module: 'governance' },
    { path: '/governance/users', cn: '用戶表管理', module: 'governance' },
    { path: '/governance/items', cn: '維度表與字典', module: 'governance' },
    { path: '/migration', cn: '歷史資料遷移', module: 'governance' },
    { path: '/governance/pipeline', cn: '資料流與效能', module: 'governance', stage: 'phase2' },
  ] },
  { id: 'sys', cn: '系統管理', items: [
    { path: '/iam', cn: '權限中心', module: 'iam' },
    { path: '/audit', cn: '日誌統計', module: 'audit' },
    { path: '/settings', cn: '平台設定', module: 'iam' },
  ] },
];
