#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List

from run_incremental_steam import read_jsonl, steam_number, write_jsonl_atomic


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge disjoint Steam query shards.")
    parser.add_argument(
        "--input", action="append", nargs=2, metavar=("MODEL", "JSONL"),
        required=True, help="Model name and its completed shard JSONL.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    args = parser.parse_args()

    merged: Dict[str, Dict[str, str]] = {}
    assignments: Dict[str, str] = {}
    per_model: Dict[str, int] = {}
    for model, path_value in args.input:
        path = Path(path_value)
        rows = read_jsonl(path)
        per_model.setdefault(model, 0)
        for row in rows:
            data_id = str(row.get("data_id") or "")
            query = row.get("query")
            if not data_id.startswith("steam_") or not isinstance(query, str) or not query:
                raise ValueError(f"Invalid query row in {path}: {data_id!r}")
            if data_id in merged:
                if assignments[data_id] != model:
                    raise ValueError(
                        f"Duplicate data_id across different model shards: {data_id}"
                    )
                if merged[data_id]["query"] != query:
                    raise ValueError(
                        f"Conflicting duplicate data_id within {model} shards: {data_id}"
                    )
                continue
            merged[data_id] = {"data_id": data_id, "query": query}
            assignments[data_id] = model
            per_model[model] += 1

    if len(merged) != args.expected_count:
        raise ValueError(
            f"Expected {args.expected_count} merged rows, found {len(merged)}"
        )
    ordered_ids: List[str] = sorted(merged, key=steam_number)
    write_jsonl_atomic(args.output, (merged[data_id] for data_id in ordered_ids))
    write_jsonl_atomic(
        args.assignments,
        (
            {"data_id": data_id, "model": assignments[data_id]}
            for data_id in ordered_ids
        ),
    )
    write_json_atomic(args.summary, {
        "status": "completed",
        "total": len(merged),
        "unique_data_ids": len(merged),
        "per_model": per_model,
        "output": str(args.output.resolve()),
        "assignments": str(args.assignments.resolve()),
    })
    print(json.dumps({
        "status": "completed",
        "total": len(merged),
        "per_model": per_model,
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
