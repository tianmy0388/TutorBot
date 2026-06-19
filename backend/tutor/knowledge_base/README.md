# 课程知识库

此目录存放 Tutor 系统的初始课程知识库。每门课程一个独立子目录。

## 当前课程

- `ai_introduction/` — 人工智能导论（本科生入门课，7 章节）

## 添加新课程

1. 在此目录下新建 `<course_name>/`
2. 写 `metadata.json`、`knowledge_graph.yaml`
3. 在 `raw/` 下放 Markdown 讲义
4. （可选）在 `questions/` 下放 JSON 题库
5. 重启服务，系统自动发现并初始化

详见 `docs/knowledge-base.md`。
