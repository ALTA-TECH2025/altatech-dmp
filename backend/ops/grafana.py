"""由 backend/ops/panels.yaml 產生 Grafana 看板 JSON（deploy/helm/altatech-dmp/dashboards/dmp-overview.json）。
用法：python3 -m backend.ops.grafana [--check]   （--check：只比對不寫入，CI 用）"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
PANELS: dict[str, Any] = yaml.safe_load((Path(__file__).resolve().parent / "panels.yaml").read_text(encoding="utf-8"))
OUT = ROOT / "deploy" / "helm" / "altatech-dmp" / "dashboards" / "dmp-overview.json"
LAYOUT = {1: (0, 1, 8, 7), 2: (8, 1, 8, 7), 3: (16, 1, 4, 7), 4: (20, 1, 4, 7), 5: (0, 9, 8, 7), 6: (8, 9, 8, 7), 7: (16, 9, 8, 7), 8: (0, 16, 8, 7), 9: (8, 16, 8, 7), 10: (16, 16, 8, 7),
          11: (0, 24, 12, 8), 12: (12, 24, 6, 8), 13: (18, 24, 6, 8), 14: (0, 33, 8, 7), 15: (8, 33, 4, 7), 16: (12, 33, 8, 7), 17: (20, 33, 4, 7)}
ROW_Y = {"platform": 0, "pipeline": 8, "query": 23, "activation": 32}
COLORS = {0: "green", 1: "orange", 2: "red"}


def panel_json(p: dict[str, Any]) -> dict[str, Any]:
    x, y, w, h = LAYOUT[p["id"]]
    targets = [{"refId": "A", "expr": p["expr"], "legendFormat": str(p.get("legend", ""))}] + [{"refId": chr(66 + i), "expr": e["expr"], "legendFormat": str(e["legend"])} for i, e in enumerate(p.get("extra", []))]
    out: dict[str, Any] = {"id": p["id"], "type": p["kind"], "title": p["cn"], "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": {"type": "prometheus", "uid": "${ds}"}, "targets": targets,
                           "fieldConfig": {"defaults": {"unit": p["unit"], "color": {"mode": "palette-classic"}, "custom": {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never"}}, "overrides": []},
                           "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi"}}}
    th = p.get("thresholds")
    if th:
        steps = [{"color": "green", "value": None}] + [{"color": ("red" if i == len(th) - 1 else "orange") if th != [0, 1] else ("red" if i == 0 else "green"), "value": v} for i, v in enumerate(th)]
        out["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": steps}; out["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
    if p["kind"] == "stat":
        out["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "none", "textMode": "value"}
    return out


def build() -> dict[str, Any]:
    d = PANELS["dashboard"]; panels: list[dict[str, Any]] = []
    for i, sec in enumerate(PANELS["sections"]):
        panels.append({"id": 100 + i, "type": "row", "title": sec["cn"], "gridPos": {"x": 0, "y": ROW_Y[sec["key"]], "w": 24, "h": 1}, "collapsed": False})
        panels += [panel_json(p) for p in PANELS["panels"] if p["section"] == sec["key"]]
    return {"title": d["title"], "uid": d["uid"], "tags": ["dmp"], "timezone": "Asia/Taipei", "schemaVersion": 39, "version": 1, "editable": True, "refresh": d["refresh"], "time": {"from": d["range"], "to": "now"},
            "templating": {"list": [{"name": "ds", "type": "datasource", "query": "prometheus", "label": "Prometheus", "current": {}}]},
            "annotations": {"list": [{"name": "告警", "datasource": {"type": "prometheus", "uid": "${ds}"}, "enable": True, "expr": 'ALERTS{alertstate="firing", key=~"source_lag|sync_failed|events_stale|http_errors|delivery_dlq"}', "iconColor": "red", "titleFormat": "{{alertname}}"}]},
            "panels": panels}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    txt = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != txt:
            print(f"{OUT} 與 panels.yaml 不一致：請跑 python3 -m backend.ops.grafana"); return 1
        print("grafana dashboard up to date"); return 0
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(txt, encoding="utf-8")
    print(f"wrote {OUT} ({len(build()['panels'])} panels)"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
