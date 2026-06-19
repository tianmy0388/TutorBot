"""Tutor service — session history + RAG context retrieval.

Public API:

    svc = get_tutor_service()
    context = await svc.retrieve_context(question, concepts)
    svc.record_interaction(user_id, question, understanding, answer)
    history = svc.get_history(user_id, limit=10)
"""

from tutor.services.tutor.service import (
    TutorService,
    TutorSession,
    TutorTurn,
    get_tutor_service,
    reset_tutor_service,
)

__all__ = [
    "TutorService",
    "TutorSession",
    "TutorTurn",
    "get_tutor_service",
    "reset_tutor_service",
]
