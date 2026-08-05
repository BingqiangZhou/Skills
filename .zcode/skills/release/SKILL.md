---
name: release
version: "1.0"
description: 项目发布工具。分析 git 历史、检测 Skill 变更、通过 git-cliff 生成 CHANGELOG.md、同步 plugin.json 版本号、创建 git tag 并推送。推送后 GitHub Action 自动创建 Release。基于语义化版本号（如 v1.0.0，详见 Step 1 规则）。**触发场景**：用户提到"发布""release""发版""打 tag""生成 changelog""更新版本""发布新版本""创建 release"，或需要将当前项目状态发布为新版本时使用。
---

# Release — 项目发布

一键发布流程：分析变更 → 生成 CHANGELOG → 同步版本号 → 创建 tag → 推送 → GitHub Action 自动创建 Release。

## 前置工具

| 工具 | 用途 | 安装 |
|------|------|------|
| `git-cliff` | 生成 CHANGELOG | `winget install git-cliff` |

GitHub Release 由 `.github/workflows/release.yml` 自动创建，无需本地安装 `gh` CLI。

## 工作流

按顺序执行以下步骤。**Step 1 和 Step 2 完成后展示摘要，等待用户确认再继续。**

### Step 0: 前置检查

逐项验证，失败则中止并提示用户处理：

1. **git-cliff**: 运行 `git cliff --version`，未安装则提示 `winget install git-cliff`，中止
2. **工作树干净**: 运行 `git status --porcelain`，有输出则提示用户先 commit 或 stash，中止

### Step 1: 确定版本号

采用语义化版本 `v<MAJOR>.<MINOR>.<PATCH>`（如 `v1.0.0`）。

1. **获取上一个版本号**：从最新 tag 读取
   ```bash
   git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1
   ```
   如果**没有任何 tag**（首次发布），从 plugin manifest 读取版本号：
   ```bash
   grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' plugins/daily-digest/.claude-plugin/plugin.json | grep -o '"[0-9][^"]*"$' | tr -d '"'
   ```
   该版本号（当前为 `1.0.0`）即作为首次发布版本号 `v1.0.0`，覆盖全部历史。

2. **按变更规模递增**（参考本次待发布内容的 Step 2 分析）：
   - **PATCH（v1.0.x → v1.0.x+1）**：仅修 bug、文档、chore、配置同步等维护性变更
   - **MINOR（v1.x.* → v1.x+1.0）**：新增 skill、新增功能脚本、能力扩展
   - **MAJOR（vx.*.* → v+1.0.0）**：架构级重构、不兼容的工作流变更（本项目极少触发）

3. 检查 tag 是否已存在：
   ```bash
   git tag -l "v<新版本号>"
   ```
   若已存在（极少见），在 PATCH 末位继续 +1 直到不冲突

4. 展示版本号及递增依据（说明为何是 PATCH/MINOR/MAJOR）

### Step 2: 预览变更

1. **找到上一个 tag**：
   ```bash
   git tag --sort=-creatordate | head -1
   ```
   如果没有任何 tag，使用初始 commit：
   ```bash
   git rev-list --max-parents=0 HEAD
   ```

2. **生成 changelog 预览**（输出到终端，不写入文件）：
   ```bash
   git cliff --tag <VERSION> --unreleased
   ```

3. **统计 commits 数量**：
   ```bash
   git rev-list <PREV_TAG>..HEAD --count
   ```
   首次发布（PREV 是初始 commit）则统计到初始 commit（含），命令为 `git rev-list <INIT_COMMIT>..HEAD --count`

4. **分析 Skill 变更**（skill 位于 `plugins/daily-digest/skills/`）：
   - 列出 prev tag 时的 skills：
     ```bash
     git ls-tree -d --name-only <PREV_TAG> plugins/daily-digest/skills/ 2>/dev/null || echo "no-skills"
     ```
   - 列出当前的 skills：
     ```bash
     ls plugins/daily-digest/skills/
     ```
   - 新增的 skills（在当前存在但 prev tag 不存在）：读取其 SKILL.md frontmatter 的 `name` 和 `description`
   - 删除的 skills（在 prev tag 存在但当前不存在）
   - 更新的 skills（两边都存在的）：检查变更
     ```bash
     git diff <PREV_TAG>..HEAD --stat -- 'plugins/daily-digest/skills/<name>/'
     ```

5. **展示发布摘要**，格式如下：
   ```
   📦 Release <VERSION> Summary
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Previous: <PREV_TAG>
   Commits:  <N>

   Skills:
     🆕 <name> — <description>
     🔄 <name> — <changed files summary>
     🗑️ <name> → removed

   Output:
     [x] CHANGELOG.md
     [x] plugin.json version → <VERSION without v>（插件全局版本）
     [x] SKILL.md version → 仅 bump 有改动的 skill（各自独立版本）
     [x] Git tag <VERSION>
     [x] Push to origin → GitHub Action auto-creates Release

   确认发布？(y/n)
   ```

6. **等待用户确认**。用户可以：
   - 确认继续
   - 修改版本号
   - 取消

### Step 3: 生成 CHANGELOG.md

1. 检查 CHANGELOG.md 是否已存在：
   ```bash
   test -f CHANGELOG.md && echo "exists" || echo "not-exists"
   ```

2. 如果**不存在**（首次），全量生成：
   ```bash
   git cliff --tag <VERSION> --unreleased -o CHANGELOG.md
   ```

3. 如果**已存在**，前置插入新版本：
   ```bash
   git cliff --tag <VERSION> --unreleased --prepend CHANGELOG.md
   ```

   > ⚠️ **切勿对已存在的 CHANGELOG.md 使用 `-o`（覆盖写入）！**
   > `-o` 会用新版本内容**整文件覆盖**，丢失所有历史版本。必须用 `--prepend`
   > 将新版本插入到文件头部，保留已有版本记录。

   > ⚠️ **`--unreleased` 不可省略。** git-cliff 2.13.1 在缺少 `--unreleased`
   > （或 `-u`）时直接报 `Error: ArgumentError("'-u' or '-l' is not specified")`
   > 中止，且**不会改动 CHANGELOG.md**。Step 2 的预览命令已自带，这里务必
   > 同步带上，否则 `--prepend` / `-o` 都不会执行。

4. **校验历史版本未丢失**（生成后必须执行）：
   ```bash
   # 统计 CHANGELOG 中的版本数，应等于 git tag 数
   echo "CHANGELOG 版本数: $(grep -c '^## v' CHANGELOG.md)"
   echo "Git tag 数:       $(git tag --list 'v*' | grep -c .)"
   ```
   > 避免用 `wc -l`：在 Git Bash 下 `wc` 常被 ugrep 别名劫持而报错；改用
   > `grep -c .` 数非空行，语义等价且无别名冲突。
   两个数字**必须相等**。若 CHANGELOG 版本数 < tag 数，说明历史被覆盖，
   立即用 `git checkout CHANGELOG.md` 恢复后重试本步。

5. **生成 AI 摘要**：git-cliff 模板中 `<!-- AI_SUMMARY -->` 是占位符，需要用 AI 生成的摘要替换。

   基于以下信息生成一段 2-3 句的自然语言摘要：
   - commits 总数和分组统计（从 Step 2 的 git-cliff 预览中提取）
   - Skill 变更（新增/更新/删除，从 Step 2 分析结果中提取）
   - 主要技术变更（如 skill 重构、监控源调整、目录结构变更等）

   摘要格式：
   ```markdown
   > <自然语言总结，2-3 句，概括本次发布最核心的变化>
   >
   > 共 <N> commits，其中 🚀 Features <N> | 🐛 Fixes <N> | 📝 Docs <N> | ...
   >
   > **[Full diff](https://github.com/BingqiangZhou/Skills/compare/<PREV_TAG>...<VERSION>)**
   ```
   （首次发布无 PREV_TAG 时，Full diff 链接为 `https://github.com/BingqiangZhou/Skills/commits/<VERSION>`）

   将生成的摘要文本替换 CHANGELOG.md 中对应版本段的 `<!-- AI_SUMMARY -->` 占位符。

6. 暂存（与 Step 3.5 一起提交）：
   ```bash
   git add CHANGELOG.md
   ```

### Step 3.5: 同步版本号

本仓库采用**两层版本**模型：

- **插件全局版本**：`plugins/daily-digest/.claude-plugin/plugin.json` 的 `"version"` 字段 + git tag（`vX.Y.Z`）+ CHANGELOG.md。每次发版必 bump，代表整个插件包的发版。
- **各 skill 独立版本**：`plugins/daily-digest/skills/*/SKILL.md` frontmatter 的 `version:` 字段。**只 bump 本次有改动的 skill**，未改动的 skill 保持原版本不动。skill 版本号独立演进，与插件全局版本**互不绑定**。

#### 3.5.1 插件全局版本 → plugin.json

将 `"version"` 更新为 `<VERSION>`（**去掉 `v` 前缀**，如 tag 是 `v1.0.1` 则改为 `"1.0.1"`）：
```bash
# 只替换 version 行，保持 JSON 其余结构与缩进不变
sed -i 's/"version":[[:space:]]*"[0-9][^"]*"/"version": "<VERSION_WITHOUT_V>"/' plugins/daily-digest/.claude-plugin/plugin.json
```
（Windows Git Bash 下 `sed -i` 可用；若环境不支持，直接用编辑器手动改该行。）

校验改动只动了 version 行：
```bash
git diff plugins/daily-digest/.claude-plugin/plugin.json
```

#### 3.5.2 各 skill 独立版本 → 仅 bump 有改动的 skill

1. **检测本次改动的 skill**（对比上一个 tag，**含未提交改动**）：
   ```bash
   PREV_TAG=$(git tag --sort=-creatordate | head -1)
   # 已提交改动
   git diff --name-only "$PREV_TAG" HEAD -- 'plugins/daily-digest/skills/*' \
     | sed 's|plugins/daily-digest/skills/||;s|/.*||' | sort -u
   # 未提交改动（working tree，会捡到 untracked 之外的所有变更）
   git diff --name-only "$PREV_TAG" -- 'plugins/daily-digest/skills/*' \
     | sed 's|plugins/daily-digest/skills/||;s|/.*||' | sort -u
   ```
   两条命令的结果取并集，即为本次需 bump 的 skill 清单。
   （首次发布无 tag 时，对比初始 commit：`PREV_TAG=$(git rev-list --max-parents=0 HEAD)`。
   注意：全新 skill 若仍是 untracked，`git diff` 看不到——以 Step 2.4 的 `git status` 为准。）

2. **对每个有改动的 skill，按变更规模 bump 其 `version:` 字段**（语义与插件全局版本相同）：
   - **PATCH**：bug 修复、文档、配置调整
   - **MINOR**：新增功能脚本、能力扩展
   - **MAJOR**：不兼容的工作流/接口变更

   先读取该 skill 当前版本号：
   ```bash
   grep '^version:' "plugins/daily-digest/skills/<SKILL>/SKILL.md"
   ```
   再用 sed 替换为新版本号（**只改这一个 skill**，其余 skill 不动）：
   ```bash
   sed -i 's/^version:[[:space:]]*.*/version: "<NEW_SKILL_VERSION>"/' \
       "plugins/daily-digest/skills/<SKILL>/SKILL.md"
   ```

3. **列出本次 bump 的 skill 清单**（在下方 Summary / 完成状态中展示），例如：
   ```
   skill 版本变更:
     rss-monitor: 1.0.0 → 1.1.0
     (github-monitor / tool-update-monitor 未改动，版本不变)
   ```

> 注意：`version:` 字段不会自动出现——新增 skill 时需在 frontmatter 手动加上 `version: "1.0.0"`。

#### 3.5.3 暂存并提交

```bash
git add CHANGELOG.md plugins/daily-digest/.claude-plugin/plugin.json
# 只 add 本次实际改过 version 的 skill
git add plugins/daily-digest/skills/<CHANGED_SKILL_1>/SKILL.md plugins/daily-digest/skills/<CHANGED_SKILL_2>/SKILL.md
git commit -m "docs: release <VERSION>"
```

### Step 4: 创建 Tag 并推送

```bash
git tag -a <VERSION> -m "Release <VERSION>"
git push origin HEAD --tags
```

如果 push 失败，tag、CHANGELOG 与 plugin.json 已在本地，提示用户稍后手动推送：
```
⚠️ Push failed. 本地 tag、CHANGELOG 与版本号已就绪，稍后手动推送：
  git push origin HEAD --tags
```

### 完成

输出最终状态：
```
✅ Release <VERSION> 发布完成
   CHANGELOG.md: 已更新
   plugin.json:  version → <VERSION_WITHOUT_V>（插件全局版本）
   SKILL.md:     仅 bump 有改动的 skill（各自独立版本，未改不动）
   Git tag:      <VERSION>
   GitHub Release: 等待 GitHub Action 自动创建
   查看: https://github.com/BingqiangZhou/Skills/actions
```

## 错误处理

| 场景 | 处理 |
|------|------|
| git-cliff 未安装 | 中止，提示安装命令 |
| 工作树有未提交变更 | 中止，提示 commit 或 stash |
| tag 已存在 | 自动追加序号 `.1`, `.2`... |
| push 失败 | 提示手动推送 `git push origin HEAD --tags` |
| 没有上一个 tag | 从 plugin.json 读版本号作为首个发布版本，用初始 commit 作为基准全量生成 |
| 0 commits since last tag | 中止："No new commits since <PREV_TAG>. Nothing to release." |
