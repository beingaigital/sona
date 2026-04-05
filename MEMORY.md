# MEMORY.md - Sona记忆系统

## 概述
Sona的记忆系统分为两层：短期记忆（STM）和长期记忆（LTM）。

## STM（短期记忆）

### 位置
- `memory/STM/`

### 存储内容
- 当前会话的对话历史
- 已生成的artifacts摘要
- 用户修改点和确认状态
- 当前工作流状态
- 任务进度

### 数据格式
```json
{
  "session_id": "uuid",
  "created_at": "timestamp",
  "user_query": "用户原始输入",
  "workflow_state": {
    "current_node": "search_plan",
    "completed_nodes": [],
    "pending_nodes": []
  },
  "artifacts": [
    {
      "type": "search_plan",
      "path": "path/to/file.json",
      "summary": "摘要"
    }
  ],
  "user_confirmations": []
}
```

### 生命周期
- 会话创建时初始化
- 会话结束时可选归档到LTM
- 默认会话结束7天后清除

## LTM（长期记忆）

### 存储位置
- Neo4j图数据库（图谱关联）
- 本地索引（快速检索）
- 文件系统（原始数据）

### 实体类型

#### EventCase（事件案例）
```json
{
  "event_id": "uuid",
  "event_name": "事件名称",
  "domain": "领域",
  "time_range": "时间范围",
  "search_words": ["关键词"],
  "total_count": 1000,
  "key_findings": ["关键发现"],
  "risk_level": "low/medium/high",
  "report_path": "报告路径",
  "created_at": "timestamp",
  "user_feedback": "用户反馈"
}
```

#### HotTopic（热点话题）
```json
{
  "topic_id": "uuid",
  "topic_name": "话题名称",
  "category": "分类",
  "heat_score": 85,
  "trend": "rising/falling/stable",
  "first_seen": "首次发现时间",
  "last_updated": "最后更新时间",
  "related_events": ["关联事件ID"]
}
```

#### MonitorTopic（监测专题）
```json
{
  "monitor_id": "uuid",
  "keywords": ["关键词"],
  "risk_thresholds": {...},
  "last_fetch_time": "最后抓取时间",
  "alert_count": 5,
  "status": "active/paused"
}
```

#### UserPreference（用户偏好）
```json
{
  "user_id": "uuid",
  "preferences": {
    "report_length": "中篇",
    "risk_thresholds": {...},
    "notification_channels": ["cli"]
  },
  "modification_history": [
    {
      "field": "threshold",
      "old_value": 0.6,
      "new_value": 0.7,
      "reason": "误报太多",
      "timestamp": "时间"
    }
  ]
}
```

## 记忆读写策略

### 读取时机
- 工作流开始时：读取用户偏好 + 相似历史案例
- 专题监测时：读取历史阈值参照
- 态势感知时：读取热点趋势

### 写入时机
- 事件分析完成：写入EventCase
- 热点事件识别：写入HotTopic
- 专题创建/更新：写入MonitorTopic
- 用户调整偏好：写入UserPreference

### 保留策略
- EventCase: 保留2年
- HotTopic: 保留1年
- MonitorTopic: 实时更新
- UserPreference: 实时更新

## 与现有系统的对接

### 现有组件
- `utils/session_manager.py` - 已有STM实现
- Neo4j - 图数据库（需确认部署）
- 文件系统 - sandbox目录

### 扩展计划
1. 阶段1：基于文件的LTM（快速实现）
2. 阶段2：Neo4j集成（图谱查询）
3. 阶段3：向量化检索（语义搜索）