"""Evaluate the bundled Computer Network RAG corpus.

The A3 statement asks teams to provide data/cases that support the
system's depth. This lightweight evaluator checks whether 30 course
queries retrieve the expected lecture file and reports hit rate,
citation coverage, and latency. It intentionally evaluates retrieval,
not final LLM prose quality; use it as the first line of evidence before
manual answer/citation-faithfulness review.

Usage:
    .venv\\Scripts\\python scripts\\evaluate_computer_network_rag.py
    .venv\\Scripts\\python scripts\\evaluate_computer_network_rag.py --write docs/rag-evaluation-computer-network.md
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@dataclass(frozen=True)
class EvalCase:
    query: str
    expected_docs: tuple[str, ...]
    concept: str


CASES: tuple[EvalCase, ...] = (
    EvalCase("计算机网络为什么要分层？OSI 和 TCP/IP 有什么区别？", ("01_网络体系结构与分层模型.md",), "network_architecture"),
    EvalCase("封装和解封装分别发生在哪些方向？", ("01_网络体系结构与分层模型.md",), "network_architecture"),
    EvalCase("物理层主要解决什么问题？", ("02_物理层与数据链路层.md",), "physical_layer"),
    EvalCase("数据链路层为什么需要成帧？", ("02_物理层与数据链路层.md",), "data_link"),
    EvalCase("交换机根据什么转发以太网帧？", ("02_物理层与数据链路层.md",), "data_link"),
    EvalCase("IP 地址和 MAC 地址的职责有什么不同？", ("03_网络层IP与路由.md", "02_物理层与数据链路层.md"), "network_ip"),
    EvalCase("路由器转发 IP 分组时会修改源 IP 吗？", ("03_网络层IP与路由.md",), "network_ip"),
    EvalCase("CIDR 前缀长度代表什么？", ("03_网络层IP与路由.md",), "network_ip"),
    EvalCase("TCP 三次握手的目标是什么？", ("04_传输层TCP与UDP.md",), "transport_tcp"),
    EvalCase("为什么 TCP 不是两次握手？", ("04_传输层TCP与UDP.md",), "transport_tcp"),
    EvalCase("SYN 报文为什么会消耗一个序列号？", ("04_传输层TCP与UDP.md",), "transport_tcp"),
    EvalCase("TCP 如何通过确认号实现可靠传输？", ("04_传输层TCP与UDP.md",), "transport_tcp"),
    EvalCase("UDP 适合哪些应用场景？", ("04_传输层TCP与UDP.md",), "transport_udp"),
    EvalCase("流量控制和拥塞控制有什么区别？", ("04_传输层TCP与UDP.md",), "transport_tcp"),
    EvalCase("DNS 解析的基本过程是什么？", ("05_应用层协议DNS_HTTP与邮件.md",), "application_dns"),
    EvalCase("HTTP 请求和响应分别包含哪些部分？", ("05_应用层协议DNS_HTTP与邮件.md",), "application_http"),
    EvalCase("HTTPS 相比 HTTP 增加了什么安全能力？", ("05_应用层协议DNS_HTTP与邮件.md", "06_网络安全基础.md"), "application_http"),
    EvalCase("电子邮件为什么通常涉及 SMTP 和 POP3 或 IMAP？", ("05_应用层协议DNS_HTTP与邮件.md",), "application_mail"),
    EvalCase("对称加密和非对称加密的差异是什么？", ("06_网络安全基础.md",), "network_security"),
    EvalCase("数字证书解决了什么信任问题？", ("06_网络安全基础.md",), "network_security"),
    EvalCase("防火墙通常根据哪些信息过滤流量？", ("06_网络安全基础.md",), "network_security"),
    EvalCase("网络性能常看哪些指标？", ("07_网络性能与故障排查.md",), "network_performance"),
    EvalCase("排查网络故障时为什么要从链路到应用逐层检查？", ("07_网络性能与故障排查.md",), "troubleshooting"),
    EvalCase("ping 和 traceroute 分别适合验证什么？", ("07_网络性能与故障排查.md",), "troubleshooting"),
    EvalCase("Wireshark 中如何过滤 TCP 端口 80 的报文？", ("08_Wireshark抓包实践.md",), "wireshark"),
    EvalCase("抓包观察三次握手时要看哪些 TCP 字段？", ("08_Wireshark抓包实践.md", "04_传输层TCP与UDP.md"), "wireshark"),
    EvalCase("为什么抓包实验要记录过滤条件和观察结论？", ("08_Wireshark抓包实践.md",), "wireshark"),
    EvalCase("课程学习路径应该先学网络层还是传输层？", ("00_课程大纲与学习路径.md", "03_网络层IP与路由.md"), "course_path"),
    EvalCase("计算机网络课程最终应能完成哪些实践任务？", ("00_课程大纲与学习路径.md", "08_Wireshark抓包实践.md"), "course_path"),
    EvalCase("如果 HTTP 访问很慢，应该如何结合 DNS、TCP 和链路层排查？", ("07_网络性能与故障排查.md", "05_应用层协议DNS_HTTP与邮件.md"), "troubleshooting"),
)


async def _prepare_seed_data() -> None:
    from tutor.services.knowledge_base.service import (
        KnowledgeBaseService,
        seed_default_libraries,
    )
    from tutor.services.courses import get_course_service, seed_default_courses

    kb_service = KnowledgeBaseService()
    seed_default_libraries(kb_service)
    seed_default_courses(get_course_service())


async def _evaluate() -> dict[str, object]:
    from tutor.services.retrieval import RAGContext, get_retrieval_service

    await _prepare_seed_data()
    svc = get_retrieval_service()
    rows: list[dict[str, object]] = []
    latencies: list[float] = []
    hits = 0
    with_citations = 0

    for case in CASES:
        start = time.perf_counter()
        result = await svc.retrieve(
            query=case.query,
            scope="course:computer_network",
            user_id="rag-eval",
        )
        latency_ms = (time.perf_counter() - start) * 1000
        latencies.append(latency_ms)
        ctx = RAGContext.from_result(result, query=case.query)
        docs = [c.document_name for c in ctx.chunks]
        hit = any(doc in case.expected_docs for doc in docs)
        if hit:
            hits += 1
        if ctx.chunks:
            with_citations += 1
        rows.append(
            {
                "query": case.query,
                "concept": case.concept,
                "status": ctx.status,
                "expected": ", ".join(case.expected_docs),
                "top_docs": ", ".join(docs[:3]),
                "hit": hit,
                "latency_ms": round(latency_ms, 1),
            }
        )

    avg = statistics.fmean(latencies) if latencies else 0.0
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    return {
        "case_count": len(CASES),
        "hit_rate": hits / len(CASES),
        "citation_coverage": with_citations / len(CASES),
        "avg_latency_ms": avg,
        "p95_latency_ms": p95,
        "rows": rows,
    }


def _to_markdown(result: dict[str, object]) -> str:
    rows = result["rows"]
    assert isinstance(rows, list)
    lines = [
        "# 《计算机网络》RAG 检索量化评测",
        "",
        "该报告由 `scripts/evaluate_computer_network_rag.py` 生成，评估对象为课程知识库检索命中情况。",
        "",
        f"- 样本数：{result['case_count']}",
        f"- 预期文档 Top-K 命中率：{float(result['hit_rate']) * 100:.1f}%",
        f"- Citation 覆盖率：{float(result['citation_coverage']) * 100:.1f}%",
        f"- 平均延迟：{float(result['avg_latency_ms']):.1f} ms",
        f"- P95 延迟：{float(result['p95_latency_ms']):.1f} ms",
        "",
        "| # | 概念 | 命中 | 状态 | 延迟(ms) | 期望文档 | Top 文档 | 问题 |",
        "|---:|---|---|---|---:|---|---|---|",
    ]
    for idx, row in enumerate(rows, 1):
        lines.append(
            "| {idx} | {concept} | {hit} | {status} | {latency} | {expected} | {top_docs} | {query} |".format(
                idx=idx,
                concept=row["concept"],
                hit="是" if row["hit"] else "否",
                status=row["status"],
                latency=row["latency_ms"],
                expected=str(row["expected"]).replace("|", "\\|"),
                top_docs=str(row["top_docs"]).replace("|", "\\|"),
                query=str(row["query"]).replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "说明：该脚本只衡量检索阶段。正式答辩建议继续人工抽查最终回答的正确率、引用忠实度和未验证声明比例。",
        ]
    )
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", type=Path, help="Optional markdown report path")
    args = parser.parse_args()

    result = await _evaluate()
    markdown = _to_markdown(result)
    if args.write:
        path = args.write if args.write.is_absolute() else ROOT / args.write
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"wrote {path}")
    print(markdown)


if __name__ == "__main__":
    asyncio.run(main())
