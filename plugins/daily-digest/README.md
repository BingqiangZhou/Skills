# daily-digest

中文内容更新监控与每日摘要生成插件。包含三个监控 skill：

- **rss-monitor** — 微信公众号（~395）+ 科技博客（~26 RSS + Hacker News）+ 中文播客（~1000）统一监控，AI 摘要日报
- **github-monitor** — GitHub 仓库动态监控（新合并 PR + 新 issue），多仓库配置驱动
- **tool-update-monitor** — 13 个开发工具的新版本增量检测（GitHub Releases / npm / changelog）

## 安装

```text
/plugin marketplace add BingqiangZhou/Skills
/plugin install daily-digest@daily-digest
```

## 使用

安装后用自然语言触发即可，例如：

- 「公众号更新」「科技日报」「播客日报」→ rss-monitor
- 「GitHub 动态」「独立开发者项目更新」「阮一峰开源自荐」→ github-monitor
- 「工具更新」「版本更新」「有没有新版本」→ tool-update-monitor

日报输出到当前项目（cwd）的 `daily-digests/YYYY-MM-DD/` 目录，中间文件输出到 `workspaces/`。

## License

MIT
