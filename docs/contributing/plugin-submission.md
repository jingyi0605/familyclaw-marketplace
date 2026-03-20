# 插件收录说明

## 入口规则

官方市场不接受“直接改主分支”的提交方式。

正确流程是：

1. 提交“插件收录申请” Issue
2. 机器人自动校验插件仓库
3. 机器人生成或更新市场条目 PR
4. 机器人自动请求审核并同步状态
5. 维护者审核后合并

## 作者要准备什么

- 一个公开可访问的 GitHub 插件仓库
- 可读取的 `manifest.json`
- 可读取的 README
- 可读取的 `requirements.txt`
- 清楚的维护者信息

## 机器人会帮你做什么

- 解析 Issue Form
- 检查仓库、manifest、README、requirements
- 生成 `plugins/<plugin_id>/entry.json`
- 创建或更新机器人 PR
- 自动补齐流程里需要的 GitHub 标签
- 审核通过后自动同步 PR / Issue 状态
- 在仓库允许自动合并时，尝试开启自动合并

## 机器人不会替你做什么

- 不会直接把条目写进 `main`
- 不会替维护者决定你的插件一定该收录
- 不会自动改 FamilyClaw 实例里的市场源配置
- 不会绕过 `CODEOWNERS` 和分支保护直接把未审内容塞进正式市场

## 重跑方式

如果你补充了 Issue 内容，直接在 Issue 下评论：

```text
/rerun-submission
```

## 审核阶段会发生什么

当机器人 PR 创建出来以后，后续链路是这样的：

1. PR 自动带上 `plugin-submission` 和审核状态标签
2. `CODEOWNERS` 指定的维护者会收到审核请求
3. 审核人要求修改时，Issue 会自动切回“等待作者补充”
4. 审核通过时，Issue 和 PR 都会自动切到“已批准”
5. 如果仓库开启了 Auto-merge，机器人会尝试把已批准 PR 设成自动合并

真正决定能不能进市场的，不是机器人说了算，而是：

- 分支保护
- Code Owner 审核
- 维护者的最终批准

如果你是在新仓库第一次启用这套流程，先看 `docs/contributing/repository-settings.md`，把 GitHub 权限和分支保护开对了再跑。

## 一句实话

这个流程的目标不是折腾作者，而是把“提申请”和“正式进入市场”之间的边界守住。
