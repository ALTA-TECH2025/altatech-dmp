"""批流一致性測試（docs/43 §3.6）：同一組合成列走批次 apply() 與 Flink（PyFlink UDF），逐列結果必須相同。

    python3 -m backend.streaming.parity                  # 沒裝 PyFlink：只跑純 Python 部分（單列 vs 整批、個資不外流、event_id 性質）
    python3 -m backend.streaming.parity --require-flink  # CI flink 工作：PyFlink 必須在（requirements-flink.txt），三種寫法都比
"""
from __future__ import annotations

import json
import os
import site
import sys
from pathlib import Path

from . import fixtures as fx
from . import rules

REPO = Path(__file__).resolve().parents[2]


def _classify_json(s: str) -> str:
    """Flink worker 端：每列一次，結果序列化成穩定 JSON 方便逐列比對。"""
    fx.install_synthetic_keys()
    out = rules.classify(fx.MODEL_PATH, json.loads(s), now=fx.FIXED_NOW, ingest_ms=fx.INGEST_MS)
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def check_python(labels: list[str], rows: list[dict]) -> list[dict]:
    fx.install_synthetic_keys()
    ref = rules.batch(fx.MODEL_PATH, rows, now=fx.FIXED_NOW, ingest_ms=fx.INGEST_MS)
    for leak in fx.LEAKS:
        bad = [lbl for lbl, r in zip(labels, ref, strict=True) if leak in r["payload"]]
        assert not bad, f"個資原值出現在輸出：{leak} → {bad}"
    ids = {lbl: r["event_id"] for lbl, r in zip(labels, ref, strict=True)}
    assert ids["ok_new"] == ids["dup_of_ok_new"] == ids["dup_other_cust"], "重送／身分欄不同時 event_id 應相同"
    assert len({ids["ok_new"], ids["time_plus_1s"], ids["updated_plus_1s"]}) == 3, "時間欄不同時 event_id 應不同"
    statuses = {r["status"] for r in ref}
    assert {"ok", "quarantined", "rejected"} <= statuses, f"合成資料應涵蓋三種結果：{statuses}"
    single = [json.loads(_classify_json(json.dumps(r, ensure_ascii=False))) for r in rows]
    _compare("python-單列", labels, ref, single)
    return ref


def _compare(name: str, labels: list[str], ref: list[dict], got: list[dict]) -> None:
    diffs = [(lbl, a, b) for lbl, a, b in zip(labels, ref, got, strict=True) if a != b]
    for lbl, a, b in diffs[:5]:
        print(f"  DIFF {lbl}\n    batch：{a}\n    {name}：{b}")
    assert not diffs and len(ref) == len(got), f"{name} 與批次結果不一致（{len(diffs)} 列）"
    print(f"[{name}] {len(got)} 列與批次逐列相同")


def check_flink(labels: list[str], rows: list[dict], ref: list[dict]) -> None:
    from pyflink.common import Types
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.functions import ProcessFunction
    from pyflink.table import DataTypes, EnvironmentSettings, TableEnvironment
    from pyflink.table.udf import udf

    # Python worker 要 import backend；thread 模式是嵌入式直譯器，看不到 venv，要把目前的 site-packages 也帶上
    paths = [str(REPO), *site.getsitepackages(), os.environ.get("PYTHONPATH", "")]
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
    data = [(i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(rows)]

    def via_table(mode: str) -> list[dict]:
        t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
        t_env.get_config().set("parallelism.default", "2")
        t_env.get_config().set("python.executable", sys.executable)
        t_env.get_config().set("python.execution-mode", mode)
        f = udf(_classify_json, result_type=DataTypes.STRING())
        t = t_env.from_elements(data, DataTypes.ROW([DataTypes.FIELD("idx", DataTypes.BIGINT()), DataTypes.FIELD("j", DataTypes.STRING())]))
        with t.select(t.idx, f(t.j)).execute().collect() as it:
            got = {r[0]: json.loads(r[1]) for r in it}
        return [got[i] for i in range(len(rows))]

    class Classify(ProcessFunction):
        def process_element(self, value, ctx):
            yield value[0], _classify_json(value[1])

    def via_datastream() -> list[dict]:
        env = StreamExecutionEnvironment.get_execution_environment()
        env.set_parallelism(2)
        env.set_python_executable(sys.executable)
        ds = env.from_collection(data, type_info=Types.TUPLE([Types.LONG(), Types.STRING()]))
        out = ds.process(Classify(), output_type=Types.TUPLE([Types.LONG(), Types.STRING()]))
        got = {i: json.loads(s) for i, s in out.execute_and_collect()}
        return [got[i] for i in range(len(rows))]

    _compare("flink-table-udf(process)", labels, ref, via_table("process"))
    _compare("flink-table-udf(thread)", labels, ref, via_table("thread"))
    _compare("flink-datastream", labels, ref, via_datastream())


def main(argv: list[str]) -> int:
    labelled = fx.labelled_rows()
    labels, rows = [lbl for lbl, _ in labelled], [r for _, r in labelled]
    ref = check_python(labels, rows)
    try:
        import pyflink  # noqa: F401
    except ImportError:
        if "--require-flink" in argv:
            print("::error::需要 PyFlink：pip install --no-deps -r backend/streaming/requirements-flink.txt（另需 JDK 17／21）")
            return 1
        print("略過 Flink 部分（未安裝 PyFlink）")
        return 0
    check_flink(labels, rows, ref)
    print("批流一致性：全部相同")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
