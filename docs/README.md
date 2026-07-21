# 文档索引

> 更新日期：2026-07-21

本文档目录区分“当前操作手册”和“历史设计基线”，避免把早期规划误认为未完成功能。

## 当前文档

| 文档 | 用途 |
|---|---|
| [03-architecture.md](./03-architecture.md) | Personal V1 当前代码架构、数据、路由与调度 |
| [05-deployment.md](./05-deployment.md) | Docker `8080`、源码开发 `5173/8002`、配置、备份与排障 |
| [06-integration.md](./06-integration.md) | `ac_` Key、Chat/Embedding/Rerank/Image 和诊断接口 |
| [07-personal-v1-upgrade.md](./07-personal-v1-upgrade.md) | 用户确认后的 V1 产品边界和验收结果 |
| [08-original-plan-progress.md](./08-original-plan-progress.md) | 对原始升级方案逐阶段的实施结论 |

## 历史基线

| 文档 | 定位 |
|---|---|
| [01-PRD.md](./01-PRD.md) | 2026-05-05 的最初需求基线；厂商与版本假设已被 V1 文档取代 |
| [02-product-design.md](./02-product-design.md) | 最初信息架构、线框和设计决策记录 |
| [04-mvp-tasks.md](./04-mvp-tasks.md) | V0.1 MVP 任务记录，文末附 Personal V1 增量任务 |

## 其他入口

- 项目概览、快速启动和路线图：[README.md](../README.md)
- 声明式厂商配置规范：[providers/README.md](../providers/README.md)
- 免费模型人工基线：[whitelist/providers.yaml](../whitelist/providers.yaml)

当文档与实际运行结果冲突时，先检查当前分支、`/api/status`、`/v1/ac/status` 和应用日志；免费政策以最新官方证据与实时探测为准。
