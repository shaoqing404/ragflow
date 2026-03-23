#!/usr/bin/env python3
#
# Export chat trace turns into JSONL records that can be fed into downstream
# training/data-cleaning pipelines.
#
import argparse
import json
from pathlib import Path

from api.db.db_models import init_database_tables
from api.db.services.chat_trace_service import ChatTraceTurnService


def parse_args():
    parser = argparse.ArgumentParser(description="Export chat trace turns to JSONL.")
    parser.add_argument("--output", required=True, help="Path to the output JSONL file.")
    parser.add_argument("--session-id", help="Filter by session_id.")
    parser.add_argument("--dialog-id", help="Filter by dialog_id.")
    parser.add_argument("--status", default="done", help="Filter by trace status. Default: done")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of rows to export. 0 means no limit.")
    return parser.parse_args()


def main():
    args = parse_args()
    init_database_tables()

    query = ChatTraceTurnService.query(reverse=False, order_by="create_time", status=args.status)
    if args.session_id:
        query = query.where(ChatTraceTurnService.model.session_id == args.session_id)
    if args.dialog_id:
        query = query.where(ChatTraceTurnService.model.dialog_id == args.dialog_id)
    if args.limit > 0:
        query = query.limit(args.limit)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output_path.open("w", encoding="utf-8") as fout:
        for row in query:
            fout.write(json.dumps(ChatTraceTurnService.to_training_record(row), ensure_ascii=False) + "\n")
            count += 1

    print(f"Exported {count} chat trace turns to {output_path}")


if __name__ == "__main__":
    main()
