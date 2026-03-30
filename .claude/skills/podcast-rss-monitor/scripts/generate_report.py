#!/usr/bin/env python3
"""
生成播客更新汇总报告
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

def load_ad_keywords():
    """加载广告关键词，返回 (high_confidence, low_confidence) 两个列表"""
    script_dir = Path(__file__).parent
    ad_file = script_dir.parent / 'references' / 'ad_keywords.txt'

    high = []
    low = []
    current_level = 'high'
    if ad_file.exists():
        with open(ad_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line == '[high]':
                    current_level = 'high'
                elif line == '[low]':
                    current_level = 'low'
                else:
                    (high if current_level == 'high' else low).append(line)
    return high, low

def filter_ads(shownotes, ad_keywords):
    """过滤广告内容（支持双层级关键词）"""
    if not shownotes:
        return ''

    if isinstance(ad_keywords, tuple):
        high_keywords, low_keywords = ad_keywords
    else:
        high_keywords, low_keywords = ad_keywords, []

    paragraphs = re.split(r'\n\n+|\n', shownotes)

    filtered = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if any(kw in p for kw in high_keywords):
            continue
        if len(p) <= 50 and any(kw in p for kw in low_keywords):
            continue
        filtered.append(p)

    return '\n'.join(filtered)

def clean_html(text):
    """清理 HTML 标签"""
    if not text:
        return ''
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理多余空白
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def generate_summary(shownotes, max_length=150):
    """生成摘要（简化版，直接截取前 N 字符）"""
    if not shownotes:
        return '内容暂无'

    # 清理 HTML
    text = clean_html(shownotes)

    # 截取前 500 字符作为摘要基础
    if len(text) > 500:
        text = text[:500]

    # 简单摘要：提取关键信息
    # 这里可以用 AI 来生成更好的摘要，但为了速度，我们直接截取
    if len(text) > max_length:
        return text[:max_length] + '...'

    return text

def load_ai_summaries(summaries_path):
    """加载 AI 生成的摘要

    JSON 格式: { "episode_url": "一句话摘要", ... }
    """
    if summaries_path and Path(summaries_path).exists():
        with open(summaries_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def generate_report(json_path, output_path=None, summaries_path=None):
    """生成 Markdown 报告

    Args:
        json_path: latest_updates.json 路径
        output_path: 输出 Markdown 路径（可选）
        summaries_path: AI 摘要 JSON 路径（可选），格式: { "episode_url": "摘要", ... }
    """
    # 读取 JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metadata = data['metadata']
    updates = data['updates']

    # 加载广告关键词和 AI 摘要
    ad_keywords = load_ad_keywords()
    ai_summaries = load_ai_summaries(summaries_path)

    # 生成报告
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    lines = [
        f'# 播客更新汇总 - {date_str} {time_str}',
        '',
        f'> 共检查 {metadata["checked_count"]} 个播客，时间范围 {metadata["hours"]} 小时，发现 {metadata["update_count"]} 个更新',
        '',
        '---',
        ''
    ]

    for i, update in enumerate(updates, 1):
        podcast_name = update.get('podcast_name', '未知')
        rank = update.get('rank', 0)
        title = update.get('episode_title', '未知标题')
        url = update.get('episode_url', '')
        pub_date = update.get('pub_date', '未知')
        shownotes = update.get('shownotes', '')
        source = update.get('source', 'RSS')

        # 过滤广告
        filtered = filter_ads(shownotes, ad_keywords)

        # 优先使用 AI 摘要，无则 fallback 到截断式
        summary = ai_summaries.get(url) or ai_summaries.get(title)
        if not summary:
            summary = generate_summary(filtered)

        lines.append(f'## {i}. {podcast_name} (排名 {rank})')
        lines.append('')
        lines.append(f'**单集**: {title}')
        lines.append('')
        lines.append(f'**链接**: {url}')
        lines.append('')
        lines.append(f'**发布时间**: {pub_date}')
        lines.append('')
        lines.append(f'**来源**: {source}')
        lines.append('')
        lines.append(f'**摘要**: {summary}')
        lines.append('')
        lines.append('---')
        lines.append('')

    content = '\n'.join(lines)

    # 保存文件
    if output_path:
        output_file = Path(output_path)
    else:
        output_dir = Path(__file__).parent.parent.parent.parent / 'podcast-digests'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f'{date_str}_{time_str.replace(":", "-")}.md'

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'报告已生成: {output_file}')
    print(f'检查播客: {metadata["checked_count"]}')
    print(f'发现更新: {metadata["update_count"]}')

    return str(output_file)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='生成播客更新报告')
    parser.add_argument('--input', '-i', type=str, help='输入 JSON 文件路径')
    parser.add_argument('--output', '-o', type=str, help='输出 Markdown 文件路径')
    parser.add_argument('--summaries', '-s', type=str, help='AI 摘要 JSON 文件路径（格式: { "episode_url": "摘要" }）')
    args = parser.parse_args()

    # 设置 stdout 编码
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')

    if args.input:
        json_path = args.input
    else:
        # 默认使用最新更新文件
        script_dir = Path(__file__).parent
        json_path = Path.cwd() / 'podcast-workspace' / 'latest_updates.json'

    generate_report(json_path, args.output, args.summaries)
