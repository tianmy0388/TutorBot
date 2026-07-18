import { describe, expect, it } from "vitest";

import { createJobState, reduceJobEvent } from "./job-reducer";
import { buildWorkflowSnapshot } from "./workflow-snapshot";

describe("buildWorkflowSnapshot", () => {
  it("keeps completed stages after a terminal transition", () => {
    let state = createJobState("job-1", "tutoring");
    state = reduceJobEvent(state, {
      type: "stream",
      job_id: "job-1",
      event: {
        type: "stage_start", source: "agent", stage: "intent", content: "",
        metadata: { job_id: "job-1" }, session_id: "s", turn_id: "",
        seq: 1, timestamp: 1, event_id: "start-intent",
      },
    });
    state = reduceJobEvent(state, {
      type: "stream",
      job_id: "job-1",
      event: {
        type: "stage_end", source: "agent", stage: "intent", content: "",
        metadata: { job_id: "job-1" }, session_id: "s", turn_id: "",
        seq: 2, timestamp: 2, event_id: "end-intent",
      },
    });

    expect(buildWorkflowSnapshot(state.jobsById["job-1"], "succeeded"))
      .toMatchObject({ stages: [{ name: "intent", status: "completed" }] });
  });
});
