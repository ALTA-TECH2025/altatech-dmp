// 側欄：與原型 NAV 同構；每項綁一個模組，用 /v1/iam/me 的 grants 過濾（看不到 ≠ 不能呼叫 API，後端另有 authorize）。
import type { Module } from '@/api/types';

export interface NavItem { path: string; cn: string; module: Module }
export interface NavGroup { id: string; cn: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  { id: 'analysis', cn: '分析', items: [
    { path: '/overview', cn: '總覽', module: 'overview' },
    { path: '/events', cn: '事件分析', module: 'events' },
    { path: '/funnel', cn: '漏斗分析', module: 'funnel' },
    { path: '/retention', cn: '留存分析', module: 'events' },
    { path: '/path', cn: '行為路徑', module: 'path' },
    { path: '/attribution', cn: '歸因分析', module: 'attribution' },
    { path: '/sql', cn: '自定義 SQL', module: 'events' },
  ] },
  { id: 'users', cn: '用戶', items: [
    { path: '/users', cn: '用戶列表', module: 'users' },
    { path: '/segments', cn: '分群與標籤', module: 'segments' },
    { path: '/approvals', cn: '審批與匯出', module: 'users' },
    { path: '/triggers', cn: '即時觸發', module: 'triggers' },
  ] },
  { id: 'data', cn: '數據', items: [
    { path: '/integration', cn: '數據整合', module: 'integration' },
    { path: '/governance', cn: '元事件管理', module: 'governance' },
    { path: '/governance/users', cn: '用戶表管理', module: 'governance' },
    { path: '/governance/items', cn: '維度表與字典', module: 'governance' },
    { path: '/governance/pipeline', cn: '資料流與效能', module: 'governance' },
    { path: '/migration', cn: '神策遷移', module: 'governance' },
  ] },
  { id: 'sys', cn: '系統管理', items: [
    { path: '/iam', cn: '權限中心', module: 'iam' },
    { path: '/audit', cn: '日誌統計', module: 'audit' },
    { path: '/settings', cn: '平台設定', module: 'iam' },
  ] },
];
