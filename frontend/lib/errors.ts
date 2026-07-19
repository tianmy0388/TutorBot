import type { StructuredError } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Convert legacy REST strings and canonical objects at one adapter boundary. */
export function normalizeStructuredError(
  value: unknown,
  fallbackCode = "JOB_FAILED",
): StructuredError | null {
  if (typeof value === "string") {
    const message = value.trim();
    return message ? { code: fallbackCode, message } : null;
  }
  if (!isRecord(value) || typeof value.message !== "string") return null;
  const message = value.message.trim();
  if (!message) return null;
  const code =
    typeof value.code === "string" && value.code.trim()
      ? value.code.trim()
      : fallbackCode;
  return {
    code,
    message,
    ...(Object.prototype.hasOwnProperty.call(value, "details")
      ? { details: value.details }
      : {}),
  };
}

export function formatStructuredError(error: StructuredError): string {
  return `[${error.code}] ${error.message}`;
}
