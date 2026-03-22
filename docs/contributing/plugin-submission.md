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
- `manifest.json` 里已经声明宿主最低版本
- 清楚的维护者信息

推荐写法：

```json
{
  "compatibility": {
    "min_app_version": "0.1.0"
  }
}
```

机器人不会拿 Issue 文本给你补这个字段。插件仓库里没写，收录流程就会直接失败。

## 机器人会帮你做什么

- 解析 Issue Form
- 检查仓库、manifest、README、requirements
- 检查 `manifest.json` 是否声明 `min_app_version`
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
- 不会替你猜这个插件最低支持哪个宿主版本

## 兼容性字段为什么现在是硬门槛

FamilyClaw 现在会按市场条目里的 `versions[].min_app_version` 判断插件能不能安装。

如果市场条目缺这个字段，宿主只能把兼容性判定成“未知”，结果就是：

- 市场会显示“最新可兼容版本暂无”
- 安装按钮会被禁用
- 用户只能看到“当前不能安全判断宿主兼容性”

所以现在的规则很直接：

1. 插件仓库的 `manifest.json` 必须先声明宿主最低版本
2. 机器人只会从 manifest 读取这个值，再写进市场条目
3. manifest 没写，Issue 会校验失败，不会再生成一个“看起来能进市场、实际上不能安装”的条目

## 多版本条目怎么保存

官方市场不是“一个版本一个文件”，而是：

- 市场根清单放在 `market.json`
- 单个插件的真实条目固定放在 `plugins/<plugin_id>/entry.json`
- 这个插件的所有可安装版本都放在同一个 `entry.json` 的 `versions[]` 里

最小结构示例：

```json
{
  "plugin_id": "demo-plugin",
  "latest_version": "1.0.0",
  "versions": [
    {
      "version": "1.0.0",
      "git_ref": "refs/tags/v1.0.0",
      "artifact_type": "source_archive",
      "artifact_url": "https://github.com/demo/demo-plugin/archive/refs/tags/v1.0.0.zip",
      "checksum": "sha256:...",
      "published_at": "2026-03-20T12:00:00Z",
      "min_app_version": "0.1.0"
    },
    {
      "version": "0.9.0",
      "git_ref": "refs/tags/v0.9.0",
      "artifact_type": "source_archive",
      "artifact_url": "https://github.com/demo/demo-plugin/archive/refs/tags/v0.9.0.zip",
      "min_app_version": "0.1.0"
    }
  ]
}
```

死规矩：

1. `latest_version` 必须能在 `versions[]` 里找到
2. `latest_version` 必须指向当前最高版本，而不是随手挑一个
3. 想保留旧版本回滚，就继续把旧版本留在 `versions[]` 里，不要拆文件
4. 每个版本都必须带自己的 `min_app_version`，不能偷懒共用一份全局兼容说明

## tag / release 规则

这里以前写得不够死，现在补清楚：

1. 正式多版本条目必须来自 tag；`git_ref` 统一写成 `refs/tags/<tag>`
2. 推荐同时发 GitHub Release，但正式规则先卡 tag，不强制你一定发 release asset
3. 如果是 `release_asset`，`artifact_url` 必填；如果是 `source_archive`，可以显式写 `artifact_url`，也可以让宿主按 `git_ref` 推导
4. 如果仓库里根本没有 tag / release，机器人只会退化生成一个“单版本开发态条目”，引用你在 Issue 里填的 branch
5. 单版本 branch 兜底只是让开发阶段能跑，不是正式多版本发布方案

一句话说：想让市场长期保存多个版本，仓库里就必须真的有这些版本对应的 tag。

## 已收录插件后续怎么同步新版本

插件一旦已经进了市场，后面再发版本，不需要重新提一遍收录 Issue。

现在官方市场仓库会每 2 小时自动做一次轻量扫描：

1. 读取所有已收录条目的 `source_repo`
2. 只检查这些仓库有没有新的 release / tag
3. 只有发现新 tag 时，才去读取那个 tag 下的 `manifest.json`
4. 自动补写 `versions[]`、更新 `latest_version`，并创建或更新机器人 PR

这条定时任务故意保持保守：

- 只追加新版本，或者把同版本的 branch 记录收口为 tag 记录
- 一旦某个插件开始进入正式 tag 模式，旧的 branch 兜底记录会被移除
- 不会因为上游删了 tag，就自动删掉市场里已经存在的历史版本
- 如果新 tag 的 `manifest.version`、`compatibility.min_app_version` 不合法，这一轮会直接失败，不会生成脏 PR

一句话说：

- 第一次进市场：走收录 Issue
- 已经进市场后的新版本：靠定时扫描自动发现，再走自动 PR + 人工审核

## 发版本前作者要先做什么

如果你准备打一个新 tag，先把仓库里的事实改对，再来提市场 Issue：

1. 更新 `manifest.version`
2. 更新这个版本自己的 `compatibility.min_app_version`
3. 如果依赖、配置、安装说明变了，同步更新 `requirements.txt` 和 README
4. 确认 tag 名和 `manifest.version` 一致，再创建 tag / release

机器人会按每个 tag 分别读取对应版本的 `manifest.json`。

这意味着：

- 历史版本的最低宿主版本会各自保存
- 用户未来选安装 `v1.0.0` 还是 `v1.2.0`，看到的是各自真实兼容性
- 如果 tag 和 `manifest.version` 对不上，收录流程会直接失败

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
