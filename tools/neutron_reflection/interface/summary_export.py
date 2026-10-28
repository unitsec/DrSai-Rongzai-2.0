"""User-facing CSV: RUN identity and two performance columns; extended provenance stays in JSON."""
import csv
import json
import math
from pathlib import Path


def write_public_summary(path, rows):
    public, order = [], []
    for row in rows:
        if row.get("Type") != "reflection":
            continue
        m = float(row["m"])
        envelope = row.get("r_envelope(%)")
        if envelope is None:
            envelope = row["r(%)"]
        envelope = float(envelope)
        if not math.isfinite(m) or not math.isfinite(envelope) or m <= 0 or envelope <= 0:
            raise ValueError("不能将无效m或包络反射率写入用户汇总表")
        run = row.get("Run")
        if not isinstance(run, str) or not run.strip():
            raise ValueError("反射记录缺少RUN编号，不能生成无对应关系的汇总表")
        public.append({"Run": run, "m": m, "r_envelope(%)": envelope})
        order.append({"row": len(public), "group_id": row.get("group_id"),
                      "group_name": row.get("group_name"), "run": row.get("Run")})
    if not public:
        raise ValueError("没有可导出的反射RUN结果")
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Run", "m", "r_envelope(%)"])
        writer.writeheader()
        writer.writerows(public)
    path.with_name("summary_row_order.json").write_text(json.dumps(order, ensure_ascii=False, indent=2), encoding="utf-8")
