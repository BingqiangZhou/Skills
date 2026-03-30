#!/usr/bin/env python3
"""
将 podcast_rss_list.md 转换为 JSON 格式
"""

import re
import json
from pathlib import Path

def parse_markdown_table(content: str) -> list[dict]:
    """解析 markdown 表格，提取播客信息"""
    podcasts = []
    current_category = None

    lines = content.split('\n')

    for i, line in enumerate(lines):
        # 检测分类标题 (### 休闲 (175个))
        category_match = re.match(r'^### (.+?) \((\d+)个\)', line)
        if category_match:
            current_category = category_match.group(1)
            continue

        # 解析表格行
        # 格式: | 排名 | 名称 | 作者 | 节目数 | RSS/小宇宙 | 简介 |
        table_match = re.match(
            r'^\|\s*(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(\d+)\s*\|\s*\[(RSS|小宇宙)\]\(([^)]+)\)\s*\|\s*([^|]*)\s*\|',
            line
        )

        if table_match:
            rank = int(table_match.group(1))
            name = table_match.group(2).strip()
            author = table_match.group(3).strip().rstrip(',')
            episode_count = int(table_match.group(4))
            link_type = table_match.group(5)  # "RSS" 或 "小宇宙"
            url = table_match.group(6)
            description = table_match.group(7).strip()

            podcasts.append({
                "rank": rank,
                "name": name,
                "author": author,
                "episode_count": episode_count,
                "link_type": link_type.lower(),  # "rss" 或 "xiaoyuzhou"
                "url": url,
                "description": description,
                "category": current_category
            })

    return podcasts


def main():
    # 读取 markdown 文件
    md_path = Path(__file__).parent.parent / "references" / "podcast_rss_list.md"
    content = md_path.read_text(encoding='utf-8')

    # 解析并转换为 JSON
    podcasts = parse_markdown_table(content)

    # 统计信息
    rss_count = sum(1 for p in podcasts if p["link_type"] == "rss")
    xiaoyuzhou_count = sum(1 for p in podcasts if p["link_type"] == "xiaoyuzhou")

    output = {
        "metadata": {
            "source": "xyzrank.com",
            "total_count": len(podcasts),
            "rss_count": rss_count,
            "xiaoyuzhou_count": xiaoyuzhou_count
        },
        "podcasts": podcasts
    }

    # 写入 JSON 文件
    json_path = Path(__file__).parent.parent / "references" / "podcasts.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"转换完成！")
    print(f"- 总数: {len(podcasts)}")
    print(f"- RSS 链接: {rss_count}")
    print(f"- 小宇宙链接: {xiaoyuzhou_count}")
    print(f"- 输出文件: {json_path}")


if __name__ == "__main__":
    main()
