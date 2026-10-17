// 用戶細查 / OneID 入口（原型同名頁）：細查從用戶列表點進（需理由、審計），OneID 規則在用戶表管理；這頁說明路徑並提供直接以客戶代碼細查。設計系統元件組合。
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Anchor, Button, Group, List, Paper, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useAuth } from '@/auth/AuthContext';
import { RequireGrant } from '@/auth/RequireGrant';
import { PageHeader, SectionTitle } from '@/ds';

export function ProfileEntryPage() {
  return <RequireGrant module="profile" action="use"><Inner /></RequireGrant>;
}

function Inner() {
  const { can } = useAuth(); const nav = useNavigate();
  const [oid, setOid] = useState(''); const [reason, setReason] = useState('');
  const go = (e: FormEvent) => { e.preventDefault(); nav('/users', { state: { openProfile: { oneId: oid.trim(), reason: reason.trim() } } }); };
  return (
    <div className="page">
      <PageHeader title="用戶細查 / OneID" lead="單一客戶的畫像、行為序列與身分圖。細查屬高敏操作：每次需填理由並寫入審計，欄位依角色遮罩，L2 以上明文需申請揭露（走審批）。" />
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper component="form" onSubmit={go}>
          <Stack gap="sm">
            <SectionTitle title="直接細查" />
            <TextInput label="客戶代碼（one_id，v1_ 開頭）或代碼化識別" ff="monospace" value={oid} onChange={(e) => setOid(e.target.value)} placeholder="例：v1_1a2b3c…" required />
            <TextInput label="理由（4–200 字，寫入審計）" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例：客訴單 C-1001 查詢交易序列" minLength={4} maxLength={200} required />
            <Group gap="xs"><Button type="submit" variant="filled">開啟細查</Button><Text size="sm" c="dimmed">也可在<Anchor component={Link} to="/users">用戶列表</Anchor>任一列點「細查」。</Text></Group>
          </Stack>
        </Paper>
        <Stack gap="md">
          <Paper><SectionTitle title="OneID" />
            <List size="sm" spacing="xs" c="dimmed">
              <List.Item>識別類型優先序、合併策略與統計：<Anchor component={Link} to="/governance/users">用戶表管理 › OneID 規則</Anchor>{can('governance', 'view') ? '' : '（需資料治理檢視權）'}。</List.Item>
              <List.Item>單一客戶的身分圖（id_edges：登入 ID、手機／Email 雜湊、帳號、裝置）在細查抽屜的「身分」分頁。</List.Item>
              <List.Item>人工合併／拆分需資訊管理發起、系統管理員審批（第二階段提供工具頁）。</List.Item>
            </List></Paper>
          <Paper><SectionTitle title="個資與遮罩" />
            <List size="sm" spacing="xs" c="dimmed">
              <List.Item>姓名、身分證、手機、Email、完整帳號永不入倉；細查只看得到代碼化識別與分桶後的敏感欄位。</List.Item>
              <List.Item>遮罩欄位要看明文：細查抽屜內「申請揭露」→ 數位經營／系統管理員審批 → 限時顯示並審計。</List.Item>
            </List></Paper>
        </Stack>
      </SimpleGrid>
    </div>
  );
}
