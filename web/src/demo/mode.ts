// 示範模式（VITE_DEMO=1 建置）：不打後端，fetch 走 fixtures；角色由 localStorage 切換。正式建置時 isDemo=false、這裡的程式不會被用到。
export const isDemo = import.meta.env.VITE_DEMO === '1';
export const DEMO_ROLES: [string, string][] = [['admin', '系統管理員'], ['marketing', '行銷'], ['analyst', '數據分析'], ['ops', '數位經營'], ['it', '資訊管理']];
export function demoRole(): string { try { return localStorage.getItem('dmp_demo_role') || 'admin'; } catch { return 'admin'; } }
export function setDemoRole(r: string): void { try { localStorage.setItem('dmp_demo_role', r); } catch { /* 無法存就每次都是 admin */ } }
