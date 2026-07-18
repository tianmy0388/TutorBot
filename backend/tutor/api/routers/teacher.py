"""Teacher analytics endpoints.

The competition statement asks for learning-effect tracking and dynamic
resource adjustment. This router exposes a lightweight teacher view over
the same LearningEventStore used by AssessmentCapability, so class-level
intervention is based on real learner events instead of demo-only JSON.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from tutor.services.courses import get_course_service
from tutor.services.knowledge_graph.service import get_knowledge_graph_service
from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.store import get_learning_event_store

router = APIRouter()


@router.get("/teacher/courses/{course_id}/analytics")
async def course_teacher_analytics(
    course_id: str,
    window_hours: int = Query(168, ge=1, le=24 * 365),
    limit_users: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Aggregate class-level learning evidence for teacher intervention.

    ``course_id`` accepts either the persistent course id
    (``course_computer_network``) or the knowledge graph id
    (``computer_network``), matching the RAG scope compatibility layer.
    """
    course = _resolve_course(course_id)
    graph_id = course.get("knowledge_graph_id") or course_id
    concept_aliases = _course_concept_aliases(graph_id)
    store = get_learning_event_store()
    await store.init()

    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    users = (await store.list_users())[:limit_users]

    per_user: list[dict[str, Any]] = []
    all_events: list[LearningEvent] = []
    weak_scores: dict[str, list[float]] = defaultdict(list)
    by_type: Counter[str] = Counter()
    resource_types: Counter[str] = Counter()

    for user_id in users:
        events = await store.query(user_id, since=since, limit=5000)
        events = [
            ev for ev in events
            if _event_matches_course(ev, graph_id, concept_aliases)
        ]
        if not events:
            continue
        all_events.extend(events)
        by_type.update(ev.event_type.value for ev in events)
        stats = _summarise_user(user_id, events)
        per_user.append(stats)
        for ev in events:
            rtype = str((ev.metadata or {}).get("resource_type") or "")
            if rtype:
                resource_types[rtype] += 1
            if (
                ev.event_type in {EventType.EXERCISE_ATTEMPTED, EventType.EXERCISE_COMPLETED}
                and ev.score is not None
                and ev.concept_id
            ):
                weak_scores[ev.concept_id].append(float(ev.score))

    weak_concepts = []
    for concept, scores in weak_scores.items():
        avg = sum(scores) / len(scores)
        attempts = len(scores)
        risk = max(0.0, min(1.0, (1.0 - avg) * min(1.0, attempts / 5)))
        if avg < 0.75 or attempts >= 3:
            weak_concepts.append(
                {
                    "concept": concept,
                    "label": _concept_label(graph_id, concept),
                    "avg_score": round(avg, 3),
                    "attempts": attempts,
                    "risk": round(risk, 3),
                }
            )
    weak_concepts.sort(key=lambda item: (item["risk"], item["attempts"]), reverse=True)

    viewed = by_type.get(EventType.RESOURCE_VIEWED.value, 0)
    completed = by_type.get(EventType.RESOURCE_COMPLETED.value, 0)
    exercise_scores = [
        float(ev.score)
        for ev in all_events
        if ev.event_type == EventType.EXERCISE_COMPLETED and ev.score is not None
    ]
    avg_exercise_score = (
        round(sum(exercise_scores) / len(exercise_scores), 3)
        if exercise_scores
        else None
    )

    return {
        "course": course,
        "window_hours": window_hours,
        "active_users": len(per_user),
        "event_count": len(all_events),
        "by_type": dict(by_type),
        "completion_rate": round(completed / viewed, 3) if viewed else 0.0,
        "exercise_score_avg": avg_exercise_score,
        "resource_type_distribution": dict(resource_types),
        "weak_concepts": weak_concepts[:10],
        "students": sorted(
            per_user,
            key=lambda item: (item["risk_score"], -item["event_count"]),
            reverse=True,
        )[:20],
        "recommendations": _teacher_recommendations(
            weak_concepts=weak_concepts,
            avg_exercise_score=avg_exercise_score,
            completion_rate=(completed / viewed if viewed else 0.0),
            resource_types=resource_types,
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _resolve_course(course_id: str) -> dict[str, Any]:
    svc = get_course_service()
    course = svc.get_course(course_id)
    if course is None:
        for item in svc.list_courses():
            if item.knowledge_graph_id == course_id:
                course = item
                break
    if course is None:
        return {
            "id": course_id,
            "name": course_id,
            "knowledge_graph_id": course_id,
            "found": False,
        }
    data = course.model_dump(mode="json")
    data["found"] = True
    return data


def _course_concept_aliases(graph_id: str) -> set[str]:
    aliases = {graph_id}
    try:
        kg = get_knowledge_graph_service()
        if kg.has_course(graph_id):
            model = kg.get_model(graph_id)
            aliases.update(node.id for node in model.nodes)
    except Exception:  # noqa: BLE001
        pass
    # Demo and exercise-level aliases used by the Computer Network fixture.
    if graph_id == "computer_network":
        aliases.update(
            {
                "network_overview",
                "network_architecture",
                "link_layer",
                "data_link",
                "physical_layer",
                "network_layer",
                "network_ip",
                "transport_layer",
                "transport_tcp",
                "transport_udp",
                "application_layer",
                "application_http",
                "application_dns",
                "application_mail",
                "network_security",
                "performance_troubleshooting",
                "network_performance",
                "troubleshooting",
                "packet_analysis",
                "wireshark",
                "socket_programming",
                "protocol_project",
                "final_review",
                "teacher_intervention",
            }
        )
    return aliases


def _event_matches_course(
    event: LearningEvent,
    graph_id: str,
    concept_aliases: set[str],
) -> bool:
    metadata = event.metadata or {}
    course = str(metadata.get("course") or metadata.get("knowledge_graph_id") or "")
    if course in {graph_id, f"course_{graph_id}"}:
        return True
    if event.concept_id and event.concept_id in concept_aliases:
        return True
    target = str(event.target_id or "")
    return target.startswith(graph_id) or target.startswith(f"course_{graph_id}")


def _summarise_user(user_id: str, events: list[LearningEvent]) -> dict[str, Any]:
    by_type = Counter(ev.event_type.value for ev in events)
    exercise = [
        ev for ev in events
        if ev.event_type == EventType.EXERCISE_COMPLETED and ev.score is not None
    ]
    avg_score = (
        sum(float(ev.score) for ev in exercise) / len(exercise)
        if exercise
        else None
    )
    viewed = by_type.get(EventType.RESOURCE_VIEWED.value, 0)
    completed = by_type.get(EventType.RESOURCE_COMPLETED.value, 0)
    weak = sorted(
        {
            ev.concept_id
            for ev in events
            if ev.concept_id
            and ev.event_type in {EventType.EXERCISE_ATTEMPTED, EventType.EXERCISE_COMPLETED}
            and ev.score is not None
            and float(ev.score) < 0.7
        }
    )
    risk = 0.0
    if avg_score is not None:
        risk += max(0.0, 0.75 - avg_score)
    if viewed and completed / viewed < 0.5:
        risk += 0.2
    if weak:
        risk += min(0.3, 0.1 * len(weak))
    return {
        "user_id": user_id,
        "event_count": len(events),
        "by_type": dict(by_type),
        "completion_rate": round(completed / viewed, 3) if viewed else 0.0,
        "exercise_score_avg": round(avg_score, 3) if avg_score is not None else None,
        "weak_concepts": weak,
        "last_event_at": max(ev.created_at for ev in events).isoformat(),
        "risk_score": round(min(1.0, risk), 3),
    }


def _concept_label(graph_id: str, concept: str) -> str:
    try:
        kg = get_knowledge_graph_service()
        if kg.has_course(graph_id):
            node = kg.get_node(graph_id, concept)
            if node is not None:
                return node.name
    except Exception:  # noqa: BLE001
        pass
    labels = {
        "transport_tcp": "TCP 可靠传输",
        "transport_udp": "UDP 与实时通信",
        "wireshark": "Wireshark 抓包",
        "network_ip": "IP 与路由",
        "application_http": "HTTP/HTTPS",
        "socket_programming": "Socket 编程",
        "protocol_project": "协议设计项目",
    }
    return labels.get(concept, concept)


def _teacher_recommendations(
    *,
    weak_concepts: list[dict[str, Any]],
    avg_exercise_score: float | None,
    completion_rate: float,
    resource_types: Counter[str],
) -> list[str]:
    recs: list[str] = []
    if weak_concepts:
        top = weak_concepts[0]
        recs.append(
            f"优先干预「{top['label']}」：安排 10 分钟短讲、3-5 道诊断题和一项可复现实验。"
        )
    if avg_exercise_score is not None and avg_exercise_score < 0.7:
        recs.append("练习均分低于 70%，建议降低下一轮资源难度并增加基础题解析。")
    if completion_rate < 0.5:
        recs.append("资源完成率偏低，建议缩短单资源时长并增加图示/互动任务。")
    if resource_types and resource_types.get("exercise", 0) == 0:
        recs.append("近期缺少练习事件，建议推送章节小测以形成评估闭环。")
    if not recs:
        recs.append("班级学习状态稳定，建议继续每周导出画像、路径、资源证据和评估报告。")
    return recs


__all__ = ["router"]
