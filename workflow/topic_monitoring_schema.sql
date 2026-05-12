-- Task 17+18: Supabase 数据库 Schema
-- 话题监控和告警系统所需的 6 张表

-- ============ 1. monitor_topics 监控话题表 ============
create table if not exists monitor_topics (
    id uuid default gen_random_uuid() primary key,
    name text not null,                      -- 话题名称
    domain text not null,                    -- 领域（如：科技、财经、社会）
    description text default '',              -- 描述
    owner text default 'system',               -- 拥有者
    is_active boolean default true,             -- 是否活跃
    config jsonb default '{}',               -- 配置（监控频率、阈值等）
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

-- ============ 2. topic_keywords 话题关键词表 ============
create table if not exists topic_keywords (
    id uuid default gen_random_uuid() primary key,
    topic_id uuid references monitor_topics(id) on delete cascade,
    keyword text not null,                   -- 关键词
    keyword_type text default 'include',        -- include/exclude
    weight float default 1.0,              -- 权重
    created_at timestamptz default now()
);

-- ============ 3. collected_posts 收集的帖子表 ============
create table if not exists collected_posts (
    id uuid default gen_random_uuid() primary key,
    topic_id uuid references monitor_topics(id) on delete cascade,
    post_id text,                           -- 原始帖子ID
    post_url text,                         -- 帖子链接
    platform text,                         -- 来源平台
    author text,                           -- 作者
    title text,                            -- 标题
    content text,                          -- 内容
    likes int default 0,                    -- 点赞数
    comments int default 0,                 -- 评论数
    shares int default 0,                   -- 分享数
    sentiment text,                         -- 情感倾向
    tags text[],                           -- 标签
    metadata jsonb default '{}',           -- 额外信息
    collected_at timestamptz default now()
);

-- ============ 4. topic_snapshots 话题快照表 ============
create table if not exists topic_snapshots (
    id uuid default gen_random_uuid() primary key,
    topic_id uuid references monitor_topics(id) on delete cascade,
    post_count int default 0,              -- 帖子数量
    engagement_sum int default 0,           -- 总互动量
    avg_sentiment float,                    -- 平均情感
    top_keywords text[],                   -- 热词
    volume_trend text,                     -- 趋势（up/down/stable）
    summary text,                          -- 摘要
    created_at timestamptz default now()
);

-- ============ 5. alerts 告警表 ============
create table if not exists alerts (
    id uuid default gen_random_uuid() primary key,
    topic_id uuid references monitor_topics(id) on delete cascade,
    alert_type text not null,               -- 告警类型
    title text not null,                   -- 告警标题
    message text,                          -- 告警消息
    severity text default 'info',          -- 严重级别：info/warning/critical
    metadata jsonb default '{}',          -- 额外信息
    is_resolved boolean default false,       -- 是否已解决
    resolved_at timestamptz,
    created_at timestamptz default now()
);

-- ============ 6. case_links 案例关联表 ============
create table if not exists case_links (
    id uuid default gen_random_uuid() primary key,
    topic_id uuid references monitor_topics(id) on delete cascade,
    case_title text not null,                -- 案例标题
    case_domain text,                      -- 案例领域
    case_url text,                        -- 案例链接
    relevance_score float default 1.0,      -- 相关度
    evidence text,                      -- 证据说明
    linked_at timestamptz default now()
);

-- ============ 索引 ============
create index idx_topic_keywords_topic_id on topic_keywords(topic_id);
create index idx_collected_posts_topic_id on collected_posts(topic_id);
create index idx_collected_posts_collected_at on collected_posts(collected_at desc);
create index idx_topic_snapshots_topic_id on topic_snapshots(topic_id);
create index idx_topic_snapshots_created_at on topic_snapshots(created_at desc);
create index idx_alerts_topic_id on alerts(topic_id);
create index idx_alerts_is_resolved on alerts(is_resolved);
create index idx_case_links_topic_id on case_links(topic_id);
create index idx_case_links_relevance on case_links(relevance_score desc);

-- ============ RLS 策略（可选） ============
-- 暂时禁用 RLS，方面开发调试
alter table monitor_topics disable row level security;
alter table topic_keywords disable row level security;
alter table collected_posts disable row level security;
alter table topic_snapshots disable row level security;
alter table alerts disable row level security;
alter table case_links disable row level security;