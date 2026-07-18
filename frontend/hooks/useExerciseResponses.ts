"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getExerciseResponseState,
  putExerciseDraft,
  submitExerciseResponse,
} from "@/lib/api";
import type {
  ExerciseResponseAnswer,
  ExerciseSubmission,
} from "@/lib/types";

const DRAFT_DEBOUNCE_MS = 400;
const FLUSH_TIMEOUT_MS = 1_500;

export interface ExerciseResponseIdentity {
  userId: string;
  packageId: string | null;
  resourceId: string;
  sessionId: string;
}

interface ResponseEntry {
  draft?: ExerciseResponseAnswer;
  submission?: ExerciseSubmission;
}

interface PendingDraft {
  answer: ExerciseResponseAnswer;
  identity: ExerciseResponseIdentity;
  version: number;
  timer?: ReturnType<typeof setTimeout>;
}

function keyOf(identity: ExerciseResponseIdentity) {
  return `${identity.userId}\u0000${identity.packageId ?? ""}\u0000${identity.resourceId}`;
}

function abortError(reason: unknown) {
  return typeof reason === "object" && reason !== null && "name" in reason
    && (reason as { name?: string }).name === "AbortError";
}

function submissionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `response-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Keeps resource response state owner-scoped and optimistic. Each instance is
 * safe across navigation: old loads are aborted and cannot paint a new resource.
 */
export function useExerciseResponses(
  identity: ExerciseResponseIdentity,
  questionIds: readonly string[],
) {
  const identityKey = keyOf(identity);
  const questionKey = questionIds.join("\u0000");
  const [entries, setEntries] = useState<Record<string, ResponseEntry>>({});
  const entriesRef = useRef(entries);
  const activeKeyRef = useRef(identityKey);
  const versionsRef = useRef(new Map<string, number>());
  const pendingRef = useRef(new Map<string, PendingDraft>());
  const draftRequestsRef = useRef(new Map<string, AbortController>());

  const replaceEntries = useCallback((next: Record<string, ResponseEntry>) => {
    entriesRef.current = next;
    setEntries(next);
  }, []);

  const flush = useCallback((questionId: string, pending: PendingDraft) => {
    const queued = pendingRef.current.get(questionId);
    if (queued !== pending || !pending.identity.packageId) return;
    if (pending.timer) clearTimeout(pending.timer);
    pendingRef.current.delete(questionId);
    draftRequestsRef.current.get(questionId)?.abort();
    const controller = new AbortController();
    draftRequestsRef.current.set(questionId, controller);
    const timeout = setTimeout(() => controller.abort(), FLUSH_TIMEOUT_MS);
    void putExerciseDraft(
      pending.identity.packageId,
      pending.identity.resourceId,
      questionId,
      { user_id: pending.identity.userId, answer_json: pending.answer },
      controller.signal,
    ).catch((reason: unknown) => {
      // A failed save must never erase the optimistic editor value.
      if (!abortError(reason)) return;
    }).finally(() => {
      clearTimeout(timeout);
      if (draftRequestsRef.current.get(questionId) === controller) {
        draftRequestsRef.current.delete(questionId);
      }
    });
  }, []);

  const queueDraft = useCallback((questionId: string, answer: ExerciseResponseAnswer) => {
    const current = activeKeyRef.current;
    if (current !== identityKey) return;
    const previous = pendingRef.current.get(questionId);
    if (previous?.timer) clearTimeout(previous.timer);
    draftRequestsRef.current.get(questionId)?.abort();
    const version = (versionsRef.current.get(questionId) ?? 0) + 1;
    versionsRef.current.set(questionId, version);
    const pending: PendingDraft = { answer, identity: { ...identity }, version };
    pending.timer = setTimeout(() => flush(questionId, pending), DRAFT_DEBOUNCE_MS);
    pendingRef.current.set(questionId, pending);
  }, [flush, identity, identityKey]);

  useEffect(() => {
    const requestKey = identityKey;
    activeKeyRef.current = requestKey;
    replaceEntries({});
    const load = new AbortController();
    const loadVersions = new Map(versionsRef.current);
    if (!identity.packageId || !identity.userId || !identity.resourceId || !questionIds.length) {
      return () => { load.abort(); };
    }
    void Promise.all(questionIds.map(async (questionId) => ({
      questionId,
      state: await getExerciseResponseState(
        identity.packageId!, identity.resourceId, questionId, identity.userId, load.signal,
      ),
    }))).then((loaded) => {
      if (load.signal.aborted || activeKeyRef.current !== requestKey) return;
      const next = { ...entriesRef.current };
      for (const { questionId, state } of loaded) {
        if ((versionsRef.current.get(questionId) ?? 0) !== (loadVersions.get(questionId) ?? 0)) continue;
        const submission = state.submissions[0];
        if (state.draft?.answer_json !== null && state.draft?.answer_json !== undefined) {
          next[questionId] = { draft: state.draft.answer_json, submission };
        } else if (submission) {
          next[questionId] = { submission };
        } else {
          delete next[questionId];
        }
      }
      replaceEntries(next);
    }).catch((reason: unknown) => {
      if (!abortError(reason)) return;
    });
    return () => {
      load.abort();
      for (const [questionId, pending] of pendingRef.current) {
        if (keyOf(pending.identity) !== requestKey) continue;
        flush(questionId, pending);
      }
    };
  }, [identity.packageId, identity.resourceId, identity.userId, identityKey, questionKey, replaceEntries, flush]);

  const setDraft = useCallback((questionId: string, answer: ExerciseResponseAnswer) => {
    if (activeKeyRef.current !== identityKey) return;
    replaceEntries({ ...entriesRef.current, [questionId]: { ...entriesRef.current[questionId], draft: answer } });
    queueDraft(questionId, answer);
  }, [identityKey, queueDraft, replaceEntries]);

  const resetDraft = useCallback((questionId: string) => {
    const next = { ...entriesRef.current };
    if (next[questionId]?.submission) next[questionId] = { submission: next[questionId].submission };
    else delete next[questionId];
    replaceEntries(next);
    queueDraft(questionId, null);
  }, [queueDraft, replaceEntries]);

  const submit = useCallback(async (
    questionId: string,
    options: {
      answer?: ExerciseResponseAnswer;
      linkedCodeAttemptId?: string;
      keepDraft?: boolean;
    } = {},
  ) => {
    if (!identity.packageId || activeKeyRef.current !== identityKey) return undefined;
    const pending = pendingRef.current.get(questionId);
    if (pending?.timer) clearTimeout(pending.timer);
    pendingRef.current.delete(questionId);
    draftRequestsRef.current.get(questionId)?.abort();
    const answer = options.answer === undefined ? entriesRef.current[questionId]?.draft : options.answer;
    if (answer === undefined) return undefined;
    const version = (versionsRef.current.get(questionId) ?? 0) + 1;
    versionsRef.current.set(questionId, version);
    const requestKey = identityKey;
    try {
      const saved = await submitExerciseResponse(identity.packageId, identity.resourceId, questionId, {
        user_id: identity.userId,
        session_id: identity.sessionId,
        answer_json: answer,
        client_submission_id: submissionId(),
        linked_code_attempt_id: options.linkedCodeAttemptId,
      });
      if (activeKeyRef.current !== requestKey) return undefined;
      const newerDraft = (versionsRef.current.get(questionId) ?? 0) !== version;
      const draft = newerDraft
        ? entriesRef.current[questionId]?.draft
        : options.keepDraft ? answer : undefined;
      replaceEntries({
        ...entriesRef.current,
        [questionId]: { ...(draft === undefined ? {} : { draft }), submission: saved },
      });
      if (options.keepDraft && !newerDraft) queueDraft(questionId, answer);
      return saved;
    } catch {
      return undefined;
    }
  }, [identity, identityKey, queueDraft, replaceEntries]);

  const visible = activeKeyRef.current === identityKey ? entries : {};
  return useMemo(() => ({
    drafts: Object.fromEntries(Object.entries(visible).flatMap(([id, entry]) =>
      entry.draft === undefined || entry.draft === null ? [] : [[id, entry.draft]],
    )) as Record<string, Exclude<ExerciseResponseAnswer, null>>,
    submissions: Object.fromEntries(Object.entries(visible).flatMap(([id, entry]) =>
      entry.submission ? [[id, entry.submission]] : [],
    )) as Record<string, ExerciseSubmission>,
    setDraft,
    submit,
    resetDraft,
  }), [visible, setDraft, submit, resetDraft]);
}
