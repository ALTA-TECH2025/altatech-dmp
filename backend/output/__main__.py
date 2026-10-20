"""資料輸出的獨立自測（不用 ClickHouse）：python3 -m backend.output
1. 出件匣路徑：含 ..／絕對路徑／越界 prefix 的輸入在任何寫檔前被拒；符號連結指到目錄外也擋；合法樣板照常產檔，目錄外既有檔案不動。
2. 擁有者角色重算：deny／到期指派生效；沒有指派不預設任何角色。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..auth.mapping import overlay_assignments
from .transport import OutboxUploader, safe_relpath


def _transport() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "outbox"; up = OutboxUploader(root)
        src = Path(td) / "src.csv"; src.write_text("a,b\n1,2\n", encoding="utf-8")
        victim = Path(td) / "escaped_0001.csv"; victim.write_text("original", encoding="utf-8")
        dest = {"id": "dst_test", "config": {"path": "/dmp/out"}}
        # 合法樣板
        out = up.upload(dest, [(src, "ot_1/2026-10-16/ot_1_2026-10-16_0001.csv")])
        assert out == ["dst_test/dmp/out/ot_1/2026-10-16/ot_1_2026-10-16_0001.csv"] and (root / out[0]).read_text(encoding="utf-8") == "a,b\n1,2\n", out
        # 越界：.. 與絕對路徑；整批一個壞就都不寫
        for bad in ("../../escaped_0001.csv", "/etc/x_0001.csv", "a/../../../escaped_0001.csv", "./x/../../y.csv", ""):
            try:
                up.upload(dest, [(src, "ot_1/ok_0001.csv"), (src, bad)]); raise AssertionError(bad)
            except ValueError:
                pass
        assert victim.read_text(encoding="utf-8") == "original" and not (root / "dst_test" / "dmp" / "out" / "ot_1" / "ok_0001.csv").exists()
        for bad in ("../../escaped_{seq}.csv", "/abs/{seq}.csv", "a/./{seq}.csv"):
            try:
                safe_relpath(bad); raise AssertionError(bad)
            except ValueError:
                pass
        assert safe_relpath("{task}/{date}/{task}_{date}_{seq}.{ext}") == "{task}/{date}/{task}_{date}_{seq}.{ext}"
        # 目的地 prefix 越界
        for cfg in ({"path": "../../"}, {"prefix": "/../x"}):
            try:
                up.upload({"id": "dst_bad", "config": cfg}, [(src, "ot_1/x_0001.csv")]); raise AssertionError(cfg)
            except ValueError:
                pass
        assert not (Path(td) / "x_0001.csv").exists()
        # 符號連結：目的地目錄內的連結指向外面 → 擋
        base = root / "dst_test" / "dmp" / "out"; outside = Path(td) / "outside"; outside.mkdir()
        try:
            os.symlink(outside, base / "link")
            try:
                up.upload(dest, [(src, "link/leak_0001.csv")]); raise AssertionError("symlink")
            except ValueError:
                pass
            assert not (outside / "leak_0001.csv").exists()
        except OSError:
            pass                                                                 # 平台不支援符號連結就略過這一段


def _roles() -> None:
    now = 1_800_000_000.0
    asg = [{"id": "a1", "user_id": "entra:ana", "roles": ["data_analyst"]},
           {"id": "a2", "user_id": "entra:ana", "roles": ["data_analyst"], "deny": True},
           {"id": "a3", "user_id": "entra:bob", "roles": ["marketing"], "expires_at": now - 1},
           {"id": "a4", "user_id": "entra:cat", "roles": ["digital_ops"], "expires_at": now + 86400, "scope": {"projects": ["p1"]}}]
    assert overlay_assignments(("entra:ana",), ["data_analyst", "marketing"], asg, now)[0] == ["marketing"]          # deny 蓋過基底與指派
    assert overlay_assignments(("entra:bob",), [], asg, now)[0] == []                                              # 到期不算
    roles, scope, applied = overlay_assignments(("entra:cat",), [], asg, now); assert roles == ["digital_ops"] and scope == {"projects": ["p1"]} and applied == ["assign:a4"]
    assert overlay_assignments(("entra:nobody",), [], asg, now)[0] == []                                           # 沒指派＝沒有角色，不預設


if __name__ == "__main__":
    _transport(); _roles()
    print("output self-test ok: outbox path safety (.., absolute, prefix, symlink, all-or-nothing) + owner roles overlay (deny/expiry/no default)")
