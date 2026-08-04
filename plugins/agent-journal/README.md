# agent-journal

Agent 每日自我回顾插件 —— 单 skill、单层、agent 自主完成全流程。

agent 自己重建当天发生了什么（看 git、看文件、回忆当前对话、必要时问
用户），用**中文**写一段回顾，并以**中英双语**的诗意收尾，输出到
`workspaces/journals/YYYY-MM-DD/journal_HH-MM.md`。

## 为什么没有采集脚本

数据源不该被预定死 —— agent 本来就能直接访问 git、文件、对话上下文。
固定一层预采集脚本只会限制它（非 git 目录、纯对话型的一天都会失真）。
所以这里把"今天发生了什么"交给 agent 自己判断，skill 只规定"怎么写"：

- **怎么写**（`references/reflection-prompts.md`）：
  - §A 中文回顾：3-6 条工作主线，每条一句事实 + 可选一句观察
  - §B 静默日分支：1-2 句温和陈述，不硬造
  - §C 中英双语诗意收尾：中文一段 + 英文一段，意境呼应而非逐字互译

## 适用

OpenClaw / Hermes 等任何 agent 的**定时自我回顾**。固定时间的调度不由
本插件承担（由 agent 框架的定时能力或调度器负责唤起），插件本身只定义
"被唤起后做什么"。

## 安装

```text
/plugin marketplace add BingqiangZhou/Skills
/plugin install daily-digest@agent-journal
```

## 使用

安装后用自然语言触发，或由框架在固定时间唤起：

- 「每日回顾」「今日回顾」「写今日手记」「今天做了什么」→ agent-journal
- 回顾主体为中文，尾声固定是一段中文 + 一段英文的**中英双语诗意收尾**
- 输出到当前项目（cwd）的 `workspaces/journals/YYYY-MM-DD/journal_HH-MM.md`

## License

MIT
