"""目的地傳輸：把產好的檔案送到 sftp／s3／gcs。API 節點不接觸明文密鑰，真正的 SFTP／S3 客戶端在執行節點以 secret_ref 取密鑰；
這裡定義介面與單機用的「出件匣」實作（檔案落到本機目錄，路徑照遠端路徑樣板），單機 quickstart 與自測用。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol


class Uploader(Protocol):
    def upload(self, dest: dict[str, Any], files: list[tuple[Path, str]]) -> list[str]:
        """(本機檔, 遠端相對路徑) → 實際遠端路徑清單。失敗丟例外（呼叫端記錄並重試）。"""
        ...

    def test(self, dest: dict[str, Any]) -> dict[str, Any]:
        """逐步診斷：dns→tcp→auth→寫入測試檔→刪除；回 {status: ok|failed, steps: [...]}。"""
        ...


class OutboxUploader:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _base(self, dest: dict[str, Any]) -> Path:
        cfg = dest.get("config") or {}
        prefix = str(cfg.get("path") or cfg.get("prefix") or "").strip("/")
        return self.root / str(dest["id"]) / prefix if prefix else self.root / str(dest["id"])

    def upload(self, dest: dict[str, Any], files: list[tuple[Path, str]]) -> list[str]:
        base = self._base(dest); out = []
        for local, rel in files:
            target = base / rel.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target); out.append(str(target.relative_to(self.root)))
        return out

    def test(self, dest: dict[str, Any]) -> dict[str, Any]:
        base = self._base(dest); steps = []
        for name in ("dns", "tcp", "auth"):
            steps.append({"step": name, "ok": True, "note": "單機出件匣（執行節點才連真實目的地）"})
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".dmp_probe"; probe.write_text("ok", encoding="utf-8"); probe.unlink()
            steps.append({"step": "write_probe", "ok": True, "note": str(base)})
            return {"status": "ok", "steps": steps}
        except OSError as e:
            steps.append({"step": "write_probe", "ok": False, "note": str(e)[:200]})
            return {"status": "failed", "steps": steps}
