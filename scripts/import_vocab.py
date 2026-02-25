#!/usr/bin/env python3
"""
词表导入脚本
用法: python scripts/import_vocab.py [--lists N] [--words-per-list M]

该脚本用于初始化或更新词表进度数据文件（data/vocab_progress.json）。
已有进度会被保留，只新增不存在的词表。
"""
import json
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_FILE = BASE_DIR / "data" / "vocab_progress.json"


def import_vocab(num_lists: int = 12, words_per_list: int = 25):
    """初始化或合并词表进度数据"""
    existing_lists = {}
    existing_current = None

    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                existing = json.load(f)
            existing_lists = existing.get("lists", {})
            existing_current = existing.get("current_list")
            print(f"发现已有词表数据，将保留已有进度。")
        except Exception as e:
            print(f"读取现有数据失败，将重新创建：{e}")

    lists = {}
    for i in range(1, num_lists + 1):
        name = f"List {i}"
        if name in existing_lists:
            lists[name] = existing_lists[name]
            pct = int(lists[name]["mastered"] / max(lists[name]["total"], 1) * 100)
            print(f"  {name}: 保留现有进度 {lists[name]['mastered']}/{lists[name]['total']} ({pct}%)")
        else:
            lists[name] = {"total": words_per_list, "mastered": 0}
            print(f"  {name}: 新建，共 {words_per_list} 词，进度 0%")

    # 当前列表 = 第一个未完成的列表，若全完成则为最后一个
    auto_current = f"List {num_lists}"
    for i in range(1, num_lists + 1):
        name = f"List {i}"
        if lists[name]["mastered"] < lists[name]["total"]:
            auto_current = name
            break

    current_list = existing_current if existing_current in lists else auto_current

    data = {
        "lists": lists,
        "current_list": current_list,
        "last_updated": datetime.now().isoformat(),
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(v["total"] for v in lists.values())
    mastered = sum(v["mastered"] for v in lists.values())
    cur = lists[current_list]
    cur_pct = int(cur["mastered"] / max(cur["total"], 1) * 100)

    print(f"\n✅ 词表导入完成！")
    print(f"   共 {num_lists} 个词表，每表 {words_per_list} 词，合计 {total} 词")
    print(f"   整体进度：{mastered}/{total} 词已掌握")
    print(f"   当前词表：{current_list}（已掌握 {cur_pct}%）")
    print(f"\n数据保存至: {DATA_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="词表导入脚本")
    parser.add_argument("--lists", type=int, default=12, help="词表数量（默认 12）")
    parser.add_argument("--words-per-list", type=int, default=25, help="每表单词数（默认 25）")
    args = parser.parse_args()
    import_vocab(args.lists, args.words_per_list)
