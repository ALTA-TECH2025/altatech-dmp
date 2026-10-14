# 安全與弱點管理

## 回報弱點
請寄 security@altatech.example（原型階段：GitHub Security Advisories）。72 小時內回覆，Critical 7 天內修補。

## 相依與映像
- 所有相依鎖版本；容器映像以 digest 固定；基底 UBI 9 minimal / distroless。
- Renovate 每週更新；安全更新即時開 PR。
- CI：Semgrep（SAST）、gitleaks（秘密）、pip-audit / pnpm audit / govulncheck（相依）、Syft（SBOM）、Grype + Trivy（映像）、授權白名單檢查。Critical / High 阻擋發佈。
- 映像以 cosign 簽章；GKE Binary Authorization 只接受簽章映像。

## 修補 SLA
| 嚴重度 | 修補時限 |
|---|---|
| Critical | 7 天 |
| High | 30 天 |
| Medium | 90 天 |
| LTS 元件升版 | 每季 |
| Kubernetes | 每半年 |

## 執行期
非 root、唯讀檔案系統、Pod Security `restricted`、NetworkPolicy 預設拒絕、Workload Identity（無金鑰檔）、CMEK、審計日誌匯出至 WORM。

## 外部驗證
每季弱點掃描、年度滲透測試；提供 SBOM（SPDX）給客戶資安單位。
