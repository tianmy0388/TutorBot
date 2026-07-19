import type { Resource, ResourcePackage } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clean(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function hasUsableExerciseOptions(resource: Resource): boolean {
  const formatSpecific = resource.format_specific;
  if (!isRecord(formatSpecific) || !Array.isArray(formatSpecific.questions)) {
    return false;
  }

  return formatSpecific.questions.every((question) => {
    if (
      !isRecord(question) ||
      clean(question.id) === "" ||
      clean(question.type) === "" ||
      clean(question.question) === ""
    ) {
      return false;
    }
    if (!Object.prototype.hasOwnProperty.call(question, "options")) {
      return true;
    }
    return (
      Array.isArray(question.options) &&
      question.options.every(
        (option: unknown) =>
          isRecord(option) &&
          clean(option.label) !== "" &&
          clean(option.text) !== "" &&
          clean(option.label) !== "[TRUNCATED]" &&
          clean(option.text) !== "[TRUNCATED]",
      )
    );
  });
}

/** Guards data received directly from a stream before it reaches the store. */
export function isUsableStreamedResource(value: unknown): value is Resource {
  if (
    !isRecord(value) ||
    clean(value.resource_id) === "" ||
    clean(value.type) === ""
  ) {
    return false;
  }

  return value.type !== "exercise" || hasUsableExerciseOptions(value as unknown as Resource);
}

/** Guards complete streamed result packages without narrowing legal resource variants. */
export function isUsableResourcePackage(value: unknown): value is ResourcePackage {
  return (
    isRecord(value) &&
    clean(value.package_id) !== "" &&
    Array.isArray(value.resources) &&
    value.resources.every(isUsableStreamedResource)
  );
}
