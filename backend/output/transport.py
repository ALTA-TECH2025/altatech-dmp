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


def safe_relpath(rel: str, what: str = "路徑") -> str:
    """遠端相對路徑白名單：不得為絕對路徑、不得含 ..／空段落／反斜線；回正規化後的相對路徑。給檔名樣板、目的地 path／prefix 與上傳共用。"""
    p = str(rel).replace("\\", "/")
    if p.startswith("/") or ":" in p.split("/")[0] and len(p.split("/")[0]) <= 2:
        raise ValueError(f"{what}不能是絕對路徑：{rel}")
    parts = [x for x in p.split("/") if x != ""]
    if not parts or any(x in (".", "..") for x in parts):
        raise ValueError(f"{what}不能含 . 或 ..（不得越出目的地目錄）：{rel}")
    return "/".join(parts)


def _inside(base: Path, target: Path) -> bool:
    """resolve 後（含符號連結）target 仍在 base 之內。"""
    b = base.resolve(); t = target.resolve()
    return t == b or b in t.parents


class OutboxUploader:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _base(self, dest: dict[str, Any]) -> Path:
        cfg = dest.get("config") or {}
        prefix = str(cfg.get("path") or cfg.get("prefix") or "").strip("/")
        base = self.root / str(dest["id"])
        if prefix: base = base / safe_relpath(prefix, "目的地路徑")
        if not _inside(self.root, base): raise ValueError("目的地路徑越出出件匣")
        return base

    def upload(self, dest: dict[str, Any], files: list[tuple[Path, str]]) -> list[str]:
        base = self._base(dest); out = []
        plan: list[tuple[Path, Path, str]] = []
        for local, rel in files:                                                # 先全部檢查，任何一個越界就整批不寫
            safe = safe_relpath(rel, "輸出檔路徑"); target = base / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            if not _inside(base, target) or not _inside(base, target.parent) or target.is_symlink():
                raise ValueError(f"輸出檔路徑越出目的地目錄：{rel}")
            plan.append((local, target, safe))
        for local, target, _ in plan:
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
