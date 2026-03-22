# Publish 阶段 — 详细参考

## 概述

```
generated.json → [审批] → approval.json → [发布] → publish_results.json
```

两步流程：先审批确认，再自动发布到抖音。

## 模块文件

| 文件 | 职责 |
|------|------|
| `modules/publish/feishu_approval.py` | 审批卡片推送 + approval.json 管理 |
| `modules/publish/douyin_publisher.py` | 构建 Manifest + 调用 distribute.ts |
| `modules/publish/distributor.py` | 分发流程编排 |
| `modules/publish/scheduler.py` | APScheduler 定时任务 |
| `modules/publish/runner.py` | 入口（approve/publish/status/full） |

## 命令

```bash
# ── 审批 ──
python3 scripts/run_single.py approve --no-feishu       # 创建队列
python3 scripts/run_single.py approve --ids 0,1          # 批准指定条目
python3 scripts/run_single.py status                     # 查看审批状态

# ── 发布 ──
python3 scripts/run_single.py publish --preview          # 预览模式（不点发布）
python3 scripts/run_single.py publish                    # 正式发布到抖音
python3 scripts/run_single.py publish --force-publish    # 跳过审批检查

# ── 飞书互动卡片审批（推荐）──
python3 scripts/check_feishu_config.py
python3 scripts/test_feishu_lark_approval.py --open-id ou_xxxxx
```

## 审批方式 A：CLI / 文件（轻量）

1. `run_single.py approve` → 创建 approval.json
2. `run_single.py approve --ids 0,1` → 批准指定条目
3. 或编辑 `data/daily/YYYY-MM-DD/approval_reply.json`: `{"approved_ids": [0, 1], "rejected_ids": [2]}`

## 审批方式 B：飞书互动卡片 + 长链接（推荐）

通过飞书开放平台应用发送带按钮的互动卡片，WebSocket 长链接接收回调，**无需公网 IP**。

### 前置配置（一次性）

1. `pip3 install lark-oapi`
2. 飞书开发者后台 (https://open.feishu.cn/app) 配置：
   - 权限：`im:message.p2p_msg:readonly` + `im:message:send_as_bot`
   - 事件订阅：「使用长连接接收事件」→ `im.message.receive_v1`
   - 回调配置：「使用长连接接收回调」→ `card.action.trigger`
   - 发布版本
3. 获取 open_id：`python3 scripts/check_feishu_config.py` 或 `python3 scripts/get_feishu_openid.py`
4. 写入 .env：`FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_RECEIVE_OPEN_ID`

⚠️ 必须先在本地启动脚本建立 WebSocket 连接，再去后台保存长连接配置。

### 技术要点

- 单 ws.Client 架构：全程只创建一个 WebSocket 连接（lark-oapi 模块级 asyncio event loop 限制）
- 卡片使用 schema 1.x 兼容格式，按钮 value 携带 `{"action": "approve"/"skip", "index": "N"}`
- 回调 3 秒内必须响应

## approval.json 结构

```json
{
  "created_at": "2026-03-22T16:05:16",
  "publish_time": "18:00",
  "item_count": 2,
  "items": [
    {
      "index": 0,
      "idea_id": "萌宠-20241120-1",
      "title": "萌宠界的"超级赛事"",
      "strategy": "combine",
      "status": "approved",
      "review_note": "feishu_ou_xxx_16:06",
      "has_video": true,
      "has_copy": true,
      "has_cover": true
    }
  ]
}
```

status 取值：`pending` / `approved` / `skipped` / `rejected`

## 抖音发布

`douyin_publisher.py` 构建 Manifest JSON → 调用 `vendor/distribute/distribute.ts`（bun 运行）→ Chrome CDP 自动化：

1. `findPublishButtonCenterJson` — 遍历 button 元素匹配「发布」文案
2. `waitAndClickPublish` — 轮询最多 120 秒等待按钮出现
3. `tryClickConfirmIfPresent` — 处理二次确认弹窗
4. `verifyPublishSuccess` — 点击后轮询 60 秒验证（URL 变化/成功文字/表单消失）

### Manifest 格式

```json
{
  "version": "1.0",
  "source": "crawmedia-daily",
  "title": "萌宠界的超级赛事",
  "outputs": {
    "douyin": {
      "video": "/absolute/path/to/video.mp4",
      "copy": {
        "title": "萌宠界的超级赛事",
        "description": "以热点赛事的形式展现萌宠的可爱瞬间...",
        "tags": ["#萌宠", "#猫咪", "#铲屎官"]
      }
    }
  }
}
```

## 前置条件

- Chrome 浏览器（CDP 自动化）
- bun（`brew install bun`，运行 distribute.ts）
- 首次需手动扫码登录抖音创作者平台（之后 Cookie 持久化）
