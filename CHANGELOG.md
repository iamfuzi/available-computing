# 更新日志 (Changelog)

所有值得注意的项目更改都将记录在此文件中。

## [未发布]

### 新增 (Added)
- **候选厂商详细视图功能** - 候选厂商页面新增可点击统计卡片和详细筛选功能
  - 四种筛选类型：可继续审核、OpenAI 兼容候选、准入排除、抓取来源
  - 动态候选列表筛选和平滑滚动交互
  - 性能优化：使用 useMemo 缓存筛选结果
  - 完整的 TypeScript 类型支持和无障碍属性

### 改进 (Improved)
- 候选厂商管理页面用户体验优化
- 统计卡片可交互，支持点击筛选查看不同类型的候选厂商

### 技术细节 (Technical Details)
- 新增 `DetailView` 类型定义用于管理筛选状态
- 实现 `candidateCounts` 计算逻辑和 `visibleCandidates` 筛选逻辑  
- 添加 `showDetail` 函数处理交互和平滑滚动
- 使用 React Hooks 最佳实践：useMemo、useRef、useState

---

## 版本历史

### v0.1.0 (2026-07-31) - 候选厂商管理增强
- 重构候选厂商页面UI，新增详细视图和筛选功能
- 代码变更：+201/-59 行，主要在 `frontend/src/pages/Candidates.tsx`

### 早期版本
- Personal V1 基础功能实现（统一代理、调用日志、代理 Key 策略等）
- 三层健康监控和自动回退机制
- 候选池基础功能
