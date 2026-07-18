import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function userFacingError(
  error: unknown,
  fallback = "服务暂时不可用，请稍后重试。",
): string {
  const raw =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : String(error ?? "");

  if (
    !raw ||
    /internal server error|failed to fetch|networkerror|\b50\d\b/i.test(raw)
  ) {
    return fallback;
  }

  return raw;
}
