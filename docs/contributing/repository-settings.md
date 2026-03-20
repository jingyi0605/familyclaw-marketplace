# 市场仓库 GitHub 设置说明

## 这份说明解决什么问题

自动化脚本写对了，不等于流程就真的能跑起来。

如果目标市场仓库的 GitHub 设置没开，结果就是：

- Issue 能提，但机器人不能稳定打标签
- PR 能建，但不能自动请求审核或开启自动合并
- 你以为有审批流程，实际上只是摆设

所以这份说明只干一件事：把真实市场仓库必须打开的设置写清楚。

## 必开设置

### 1. Actions 工作流权限

仓库路径：

`Settings -> Actions -> General -> Workflow permissions`

至少要打开：

- `Read and write permissions`
- `Allow GitHub Actions to create and approve pull requests`

第一项解决“机器人有没有写权限”。
第二项解决“机器人能不能创建 PR，以及在允许的前提下提交审批相关动作”。

## 2. Pull Requests 设置

仓库路径：

`Settings -> General -> Pull Requests`

建议打开：

- `Allow auto-merge`

不开这个，审核通过以后 workflow 也没法把 PR 切到自动合并状态。

## 3. 默认分支保护

仓库路径：

`Settings -> Branches -> Branch protection rules`

对默认分支 `main` 至少要开这些：

- `Require a pull request before merging`
- `Require approvals`
- `Require review from Code Owners`

建议顺手再开：

- `Dismiss stale pull request approvals when new commits are pushed`

这个选项能防止“旧审批误伤新提交”。

## 4. CODEOWNERS 必须对应真人

当前骨架里的 `CODEOWNERS` 已经写了默认维护者。

如果真实市场仓库的审批人不是 `@jingyi0605`，你必须同步改这两个地方：

- `.github/CODEOWNERS`
- `.github/workflows/plugin-submission.yml` 里的 `reviewers`

只改一处，流程就是半残。

## 5. 标签不用手工预建

这套骨架已经在 workflow 里补了“自动创建和更新标签”的步骤。

也就是说，下面这些标签不需要你提前去 GitHub 后台手工点一遍：

- `plugin-submission`
- `auto-generated`
- `status:submitted`
- `status:validating`
- `status:needs-author-fix`
- `status:pr-opened`
- `status:awaiting-review`
- `status:changes-requested`
- `status:approved`
- `status:merged`
- `status:closed`
- `status:system-error`
- `status:rejected`

## 上线前最短检查清单

把骨架复制进真实市场仓库后，按这个顺序检查：

1. 开 `Read and write permissions`
2. 开 `Allow GitHub Actions to create and approve pull requests`
3. 开 `Allow auto-merge`
4. 给 `main` 配分支保护
5. 确认 `CODEOWNERS` 和 `reviewers` 指向真实维护者
6. 提一条测试 Issue，看是否能自动建 PR、请求审核、同步标签

## 一句实话

流程最容易死的地方，不是脚本，而是仓库设置没开全。

别等到第一次真实收录时才发现机器人根本没有权限。
