/*
 * The runtime require keeps this suite outside the production Next.js bundle
 * while Playwright supplies the test and expect APIs during browser runs.
 */
const { test, expect } = require("@playwright/test") as {
  test: any;
  expect: any;
};

const REAL_DATA = process.env.TUTOR_E2E_REAL_DATA === "1";
const MINIMAX_SEARCH = process.env.TUTOR_E2E_MINIMAX_SEARCH === "1";
const USER_ID = "local-user";
const RECOVERY_SESSION = "sess_ebb5a8f5dfdb";
const API = "/api/v1";
const TERMINAL = new Set(["succeeded", "partial", "failed", "cancelled"]);
const FIXTURE_SESSION = "fixture-reliability-session";
const FIXTURE_PACKAGE = "fixture-reliability-package";
const FIXTURE_QUESTION = "fixture-code-question";
const FIXTURE_CHOICE_RESOURCE = "fixture-choice-exercise";
const FIXTURE_CHOICE_QUESTION = "fixture-choice-question";
const FIXTURE_VIDEO_RESOURCE = "fixture-failed-video";
const FIXTURE_REPAIR_JOB = "fixture-repair-child";

type JsonObject = Record<string, any>;

const FIXTURE_TIME = "2026-07-18T08:00:00.000Z";
const TINY_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR42mNkYPj/n4GBgYGJAQoAHgQCAf7JdVQAAAAASUVORK5CYII=";

function fixtureResource(overrides: JsonObject): JsonObject {
  return {
    resource_id: "fixture-document",
    type: "document",
    title: "Fixture reliability lesson",
    content: "刷新后仍然存在的本地回归课程内容。",
    format_specific: {},
    difficulty: 2,
    estimated_minutes: 8,
    prerequisites: [],
    generated_by: ["fixture-agent"],
    confidence_score: 0.98,
    topic: "reliability",
    tags: ["fixture"],
    created_at: FIXTURE_TIME,
    metadata: { package_id: FIXTURE_PACKAGE, package_persisted: true },
    ...overrides,
  };
}

function fixtureFailedVideo(formatOverrides: JsonObject = {}): JsonObject {
  return fixtureResource({
    resource_id: FIXTURE_VIDEO_RESOURCE,
    type: "video",
    title: "Fixture failed Manim video",
    content: "",
    format_specific: {
      render_status: "failed",
      render_error: "Missing required asset files: person_silhouette.svg",
      render_failure: {
        error_code: "MISSING_ASSET",
        summary: "缺少动画资源文件：person_silhouette.svg",
      },
      scene_class: "MainScene",
      manim_code:
        'from manim import *\nclass MainScene(Scene):\n    def construct(self):\n        self.add(SVGMobject("person_silhouette.svg"))\n',
      source_revision: 0,
      ...formatOverrides,
    },
  });
}

const FIXTURE_RESOURCES = [
  fixtureResource({}),
  fixtureResource({
    resource_id: "fixture-code-exercise",
    type: "exercise",
    title: "Fixture executable Python exercise",
    content: "Implement add(a, b).",
    format_specific: {
      questions: [
        {
          id: FIXTURE_QUESTION,
          type: "code",
          difficulty: 2,
          knowledge_point: "Python functions",
          question: "实现 add(a, b)，返回两数之和。",
          options: [],
          explanation: "返回 a + b。",
          code_spec: {
            language: "python",
            starter_code: "def add(a, b):\n    pass\n",
            time_limit_seconds: 5,
            test_count: 2,
          },
        },
      ],
    },
  }),
  fixtureResource({
    resource_id: FIXTURE_CHOICE_RESOURCE,
    type: "exercise",
    title: "Fixture choice exercise",
    content: "选择正确答案。",
    format_specific: {
      questions: [
        {
          id: FIXTURE_CHOICE_QUESTION,
          type: "single_choice",
          difficulty: 2,
          knowledge_point: "注意力",
          question: "自注意力中 Q、K、V 分别来自哪里？",
          options: [
            { label: "A", text: "三个不同的输入序列" },
            { label: "B", text: "同一输入的三种线性投影" },
            { label: "C", text: "三个独立的注意力头" },
            { label: "D", text: "编码器与解码器" },
          ],
          explanation: "Q、K、V 都是同一输入经过不同线性变换得到的。",
        },
      ],
    },
  }),
  fixtureResource({
    resource_id: "fixture-broken-mindmap",
    type: "mindmap",
    title: "Fixture broken mindmap",
    content: "",
    format_specific: {
      // A standalone quoted node is rejected by the Mermaid 11.14 mindmap
      // grammar; the viewer must fall back to the stored outline.
      mermaid_dsl:
        'mindmap\n  root((反向传播))\n    "前向传播"\n    "激活函数 a=σ(z)"',
      central_topic: "反向传播",
      outline: [
        { depth: 0, label: "反向传播" },
        { depth: 1, label: "前向传播" },
        { depth: 1, label: "激活函数 a=σ(z)" },
      ],
    },
  }),
  fixtureResource({
    resource_id: "fixture-numpy-code",
    type: "code",
    title: "Fixture NumPy text output",
    content: "import numpy as np\nprint(int(np.arange(5).sum()))",
    format_specific: {
      language: "python",
      code: "import numpy as np\nprint(int(np.arange(5).sum()))",
      output_kind: "text",
      execution_status: "succeeded",
      stdout: "10\n",
      stderr: "",
      artifacts: [],
    },
  }),
  fixtureResource({
    resource_id: "fixture-matplotlib-code",
    type: "code",
    title: "Fixture Matplotlib output",
    content: "import matplotlib.pyplot as plt\nplt.plot([0, 1], [0, 1])\nplt.savefig('fixture.png')\n",
    format_specific: {
      language: "python",
      code: "import matplotlib.pyplot as plt\nplt.plot([0, 1], [0, 1])\nplt.savefig('fixture.png')\n",
      execution_status: "succeeded",
      artifacts: [
        { name: "fixture.png", kind: "png", artifact_key: "code/fixture.png" },
      ],
    },
  }),
  fixtureFailedVideo(),
];

function fixturePackage(failedVideoOverrides?: JsonObject): JsonObject {
  const resources = failedVideoOverrides
    ? FIXTURE_RESOURCES.map((resource) =>
        resource.resource_id === FIXTURE_VIDEO_RESOURCE
          ? fixtureFailedVideo(failedVideoOverrides)
          : resource,
      )
    : FIXTURE_RESOURCES;
  return {
    package_id: FIXTURE_PACKAGE,
    topic: "Reliability fixture",
    resources,
    target_profile_snapshot: {},
    learning_path_summary: {},
    generated_by: ["fixture-agent"],
    metadata: { session_id: FIXTURE_SESSION },
    created_at: FIXTURE_TIME,
  };
}

function fixtureJob(
  jobId: string,
  status: string,
  overrides: JsonObject = {},
): JsonObject {
  const terminal = TERMINAL.has(status);
  return {
    job_id: jobId,
    user_id: USER_ID,
    session_id: FIXTURE_SESSION,
    capability: "resource_generation",
    status,
    message_preview: "Fixture controlled resource generation",
    language: "zh",
    event_count: terminal ? 8 : 3,
    created_at: FIXTURE_TIME,
    started_at: FIXTURE_TIME,
    finished_at: terminal ? "2026-07-18T08:00:03.000Z" : null,
    duration_seconds: terminal ? 3 : null,
    has_result: terminal,
    error: null,
    children: [],
    background_status: null,
    ...overrides,
  };
}

function fixtureJobs(settled: boolean): JsonObject[] {
  const children = settled
    ? [
        {
          job_id: "fixture-child-code",
          capability: "resource_generation",
          status: "succeeded",
          parent_job_id: "fixture-parent",
          task_kind: "code_generation",
          dedupe_key: "fixture:code",
          error: null,
        },
        {
          job_id: "fixture-child-video",
          capability: "resource_generation",
          status: "failed",
          parent_job_id: "fixture-parent",
          task_kind: "video_rendering",
          dedupe_key: "fixture:video",
          error: "Missing required asset files",
        },
      ]
    : [
        {
          job_id: "fixture-child-code",
          capability: "resource_generation",
          status: "running",
          parent_job_id: "fixture-parent",
          task_kind: "code_generation",
          dedupe_key: "fixture:code",
          error: null,
        },
        {
          job_id: "fixture-child-video",
          capability: "resource_generation",
          status: "pending",
          parent_job_id: "fixture-parent",
          task_kind: "video_rendering",
          dedupe_key: "fixture:video",
          error: null,
        },
      ];
  const parent = fixtureJob("fixture-parent", settled ? "partial" : "running", {
    children,
    background_status: settled ? "failed" : "running",
  });
  return [
    parent,
    ...children.map((child) =>
      fixtureJob(child.job_id, child.status, {
        parent_job_id: child.parent_job_id,
        task_kind: child.task_kind,
        dedupe_key: child.dedupe_key,
        error: child.error,
      }),
    ),
  ];
}

function fixtureConversation(workflow?: JsonObject): JsonObject {
  const messages: JsonObject[] = [
    {
      id: "fixture-message-user",
      role: "user",
      content: "请生成一组可靠性学习资源。",
      job_id: "fixture-parent",
      capability: "resource_generation",
      created_at: FIXTURE_TIME,
      metadata: {},
    },
    {
      id: "fixture-message-assistant",
      role: "assistant",
      content: "Fixture resource generation completed and persisted.",
      job_id: "fixture-parent",
      capability: "resource_generation",
      created_at: "2026-07-18T08:00:03.000Z",
      metadata: {},
    },
  ];
  if (workflow) {
    messages.push({
      id: `fixture-message-workflow-${workflow.job_id}`,
      role: "assistant",
      content: "",
      job_id: workflow.job_id,
      capability: "resource_generation",
      created_at: "2026-07-18T08:00:04.000Z",
      metadata: {
        kind: "workflow_timeline",
        job_id: workflow.job_id,
        client_message_id: `workflow:${workflow.job_id}`,
        workflow,
      },
    });
  }
  return {
    session_id: FIXTURE_SESSION,
    user_id: USER_ID,
    title: "Fixture reliability conversation",
    message_count: messages.length,
    last_message_preview: "资源已生成并持久化。",
    web_search_enabled: false,
    created_at: FIXTURE_TIME,
    updated_at: FIXTURE_TIME,
    messages,
  };
}

async function installFixtureApi(
  page: any,
  options: {
    running?: boolean;
    workflow?: JsonObject;
    failedVideoOverrides?: JsonObject;
    repair?: "ready" | "failed";
  } = {},
) {
  let settled = !options.running;
  const attempts: JsonObject[] = [];
  const drafts = new Map<string, JsonObject>();
  const packageSnapshot = fixturePackage(options.failedVideoOverrides);
  const repairState = options.repair
    ? {
        outcome: options.repair,
        status:
          options.failedVideoOverrides?.repair_status === "running" ||
          options.failedVideoOverrides?.repair_status === "pending"
            ? "running"
            : "idle",
      }
    : null;
  const withVideo = (videoResource: JsonObject): JsonObject => ({
    ...packageSnapshot,
    resources: packageSnapshot.resources.map((resource: JsonObject) =>
      resource.resource_id === FIXTURE_VIDEO_RESOURCE ? videoResource : resource,
    ),
  });
  const repairedVideoResource = () =>
    fixtureResource({
      resource_id: FIXTURE_VIDEO_RESOURCE,
      type: "video",
      title: "Fixture failed Manim video",
      content: "",
      format_specific: {
        render_status: "ready",
        repair_status: "ready",
        repair_job_id: FIXTURE_REPAIR_JOB,
        source_revision: 1,
        scene_class: "MainScene",
        manim_code:
          "from manim import *\nclass MainScene(Scene):\n    def construct(self):\n        dot = Dot()\n        self.play(FadeIn(dot), run_time=0.1)\n",
        video_url: "/static/manim/fixture-repaired.mp4",
        artifact_key: "manim_videos/fixture-repaired.mp4",
        duration_seconds: 1,
        repair_history: [
          { job_id: FIXTURE_REPAIR_JOB, failed_revision: 0, status: "ready" },
        ],
      },
    });
  const failedRepairVideoResource = () =>
    fixtureFailedVideo({
      repair_status: "failed",
      repair_job_id: FIXTURE_REPAIR_JOB,
      repair_history: [
        {
          job_id: FIXTURE_REPAIR_JOB,
          failed_revision: 0,
          status: "failed",
          error_code: "process_exit",
          summary: "修复代码渲染失败：Manim 退出码 1",
          log_artifact_key: `manim_logs/${FIXTURE_REPAIR_JOB}/attempt-01.log`,
        },
      ],
    });
  const currentPackage = (): JsonObject => {
    if (!repairState) return packageSnapshot;
    if (repairState.status === "succeeded") return withVideo(repairedVideoResource());
    if (repairState.status === "failed") return withVideo(failedRepairVideoResource());
    if (repairState.status === "running") {
      return withVideo(
        fixtureFailedVideo({
          repair_status: "running",
          repair_job_id: FIXTURE_REPAIR_JOB,
        }),
      );
    }
    return packageSnapshot;
  };
  const repairChildSummary = (status: string): JsonObject => ({
    job_id: FIXTURE_REPAIR_JOB,
    capability: "video_repair_render",
    status,
    parent_job_id: "fixture-parent",
    task_kind: "video_repair_render",
    dedupe_key: `video-repair:${FIXTURE_PACKAGE}:${FIXTURE_VIDEO_RESOURCE}:0:1`,
    metadata: {
      package_id: FIXTURE_PACKAGE,
      resource_id: FIXTURE_VIDEO_RESOURCE,
      failed_revision: 0,
    },
    error: status === "failed" ? "Video repair failed" : null,
  });
  const json = (route: any, body: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  // Keep fixture runs hermetic: the application opens its unified socket on
  // mount, so terminate that route in-browser instead of letting Next proxy
  // retries to an intentionally absent real backend.
  await page.routeWebSocket("**/api/v1/ws", (socket: any) => {
    socket.onMessage(() => {});
  });

  await page.route("**/api/v1/**", async (route: any) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const jobs = fixtureJobs(settled);

    if (
      method === "GET" &&
      path === `${API}/conversations/${FIXTURE_SESSION}/aggregate`
    ) {
      return json(route, {
        conversation: fixtureConversation(options.workflow),
        jobs,
        packages: [currentPackage()],
        profile_summary: {},
        path_summary: {},
        recovery_warnings: [],
      });
    }
    if (method === "GET" && path === `${API}/conversations`) {
      const conversation = fixtureConversation(options.workflow);
      delete conversation.messages;
      return json(route, {
        items: [conversation],
        total: 1,
        limit: 50,
        offset: 0,
        has_more: false,
      });
    }
    if (method === "GET" && path === `${API}/jobs/${USER_ID}/stats`) {
      return json(route, {
        job_count: jobs.length,
        active_count: jobs.filter((job) => !TERMINAL.has(job.status)).length,
        by_status: jobs.reduce((counts: JsonObject, job: JsonObject) => {
          counts[job.status] = (counts[job.status] ?? 0) + 1;
          return counts;
        }, {}),
        by_capability: { resource_generation: jobs.length },
        first_at: FIXTURE_TIME,
        last_at: FIXTURE_TIME,
      });
    }
    if (method === "GET" && path === `${API}/jobs/${USER_ID}`) {
      return json(route, {
        user_id: USER_ID,
        items: jobs,
        total: jobs.length,
        limit: 50,
        offset: 0,
      });
    }
    if (method === "GET" && path.startsWith(`${API}/jobs/${USER_ID}/`)) {
      const jobId = decodeURIComponent(path.slice(`${API}/jobs/${USER_ID}/`.length));
      if (jobId === FIXTURE_REPAIR_JOB) {
        if (!repairState || repairState.status === "idle") {
          return json(route, { detail: "job not found" }, 404);
        }
        const childJob = fixtureJob(FIXTURE_REPAIR_JOB, repairState.status, {
          parent_job_id: "fixture-parent",
          capability: "video_repair_render",
          task_kind: "video_repair_render",
          dedupe_key: `video-repair:${FIXTURE_PACKAGE}:${FIXTURE_VIDEO_RESOURCE}:0:1`,
          error: repairState.status === "failed" ? "Video repair failed" : null,
        });
        return json(route, {
          ...childJob,
          message: "Fixture intelligent video repair",
          metadata: {
            package_id: FIXTURE_PACKAGE,
            resource_id: FIXTURE_VIDEO_RESOURCE,
            failed_revision: 0,
          },
          result: null,
          events: [],
          children: [],
        });
      }
      const job = jobs.find((candidate) => candidate.job_id === jobId);
      if (job && jobId === "fixture-parent" && repairState && repairState.status !== "idle") {
        const children = [
          ...(Array.isArray(job.children) ? job.children : []).filter(
            (child: JsonObject) => child.job_id !== FIXTURE_REPAIR_JOB,
          ),
          repairChildSummary(repairState.status),
        ];
        return json(route, {
          ...job,
          children,
          message: job.message_preview,
          metadata: {},
          result: null,
          events: [],
        });
      }
      return json(
        route,
        job
          ? { ...job, message: job.message_preview, metadata: {}, result: null, events: [] }
          : { detail: "job not found" },
        job ? 200 : 404,
      );
    }
    if (
      method === "GET" &&
      path === `${API}/exercises/${FIXTURE_PACKAGE}/resources/${FIXTURE_CHOICE_RESOURCE}/responses`
    ) {
      const questionId = url.searchParams.get("question_id") ?? "";
      return json(route, {
        draft: drafts.get(questionId) ?? null,
        submissions: [],
      });
    }
    if (
      method === "PUT" &&
      path ===
        `${API}/exercises/${FIXTURE_PACKAGE}/resources/${FIXTURE_CHOICE_RESOURCE}/questions/${FIXTURE_CHOICE_QUESTION}/draft`
    ) {
      const payload = request.postDataJSON();
      drafts.set(FIXTURE_CHOICE_QUESTION, {
        user_id: USER_ID,
        package_id: FIXTURE_PACKAGE,
        resource_id: FIXTURE_CHOICE_RESOURCE,
        question_id: FIXTURE_CHOICE_QUESTION,
        question_type: "single_choice",
        answer_json: payload.answer_json,
        updated_at: FIXTURE_TIME,
      });
      return json(route, { ok: true });
    }
    if (
      method === "POST" &&
      path ===
        `${API}/resources/packages/${USER_ID}/${FIXTURE_PACKAGE}/resources/${FIXTURE_VIDEO_RESOURCE}/retry-video`
    ) {
      if (!repairState || repairState.status !== "idle") {
        return json(route, { detail: "video is not retryable" }, 409);
      }
      repairState.status = "running";
      return json(route, {
        job_id: FIXTURE_REPAIR_JOB,
        parent_job_id: "fixture-parent",
        package_id: FIXTURE_PACKAGE,
        resource_id: FIXTURE_VIDEO_RESOURCE,
        status: "pending",
        child: repairChildSummary("pending"),
        resource: fixtureFailedVideo({
          repair_status: "pending",
          repair_job_id: FIXTURE_REPAIR_JOB,
        }),
      });
    }
    if (
      path === `${API}/exercises/${FIXTURE_PACKAGE}/${FIXTURE_QUESTION}/attempts`
    ) {
      if (method === "GET") {
        return json(route, { items: attempts, total: attempts.length, limit: 20, offset: 0 });
      }
      if (method === "POST") {
        const payload = request.postDataJSON();
        const attempt = {
          attempt_id: `fixture-attempt-${attempts.length + 1}`,
          client_attempt_id: payload.client_attempt_id,
          user_id: USER_ID,
          session_id: FIXTURE_SESSION,
          package_id: FIXTURE_PACKAGE,
          question_id: FIXTURE_QUESTION,
          source_code: payload.source_code,
          status: "passed",
          passed_tests: 2,
          total_tests: 2,
          test_results: [
            { name: "positive", passed: true, actual_json: 3 },
            { name: "zero", passed: true, actual_json: 0 },
          ],
          stdout: "fixture tests passed",
          stderr: "",
          duration_seconds: 0.02,
          created_at: FIXTURE_TIME,
          error_code: null,
        };
        attempts.unshift(attempt);
        return json(route, attempt);
      }
    }
    if (
      method === "GET" &&
      path.endsWith("/resources/fixture-matplotlib-code/artifacts/fixture.png")
    ) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(TINY_PNG, "base64"),
      });
    }
    if (method === "GET" && path === `${API}/resources/packages/${USER_ID}`) {
      return json(route, {
        user_id: USER_ID,
        items: [
          {
            package_id: FIXTURE_PACKAGE,
            topic: packageSnapshot.topic,
            resource_count: packageSnapshot.resources.length,
            total_minutes: 32,
            types: packageSnapshot.resources.map((resource: JsonObject) => resource.type),
            avg_confidence: 0.98,
            created_at: FIXTURE_TIME,
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      });
    }
    if (
      method === "GET" &&
      path === `${API}/resources/packages/${USER_ID}/${FIXTURE_PACKAGE}`
    ) {
      return json(route, currentPackage());
    }
    if (method === "GET" && path === `${API}/resources/packages/${USER_ID}/stats`) {
      return json(route, {
        package_count: 1,
        resource_count: packageSnapshot.resources.length,
        total_minutes: 32,
        avg_confidence: 0.98,
        topics: [packageSnapshot.topic],
      });
    }
    if (method === "GET" && path === `${API}/kg/courses`) {
      return json(route, { courses: ["ai_introduction"] });
    }
    if (method === "GET" && path === `${API}/courses`) {
      return json(route, { items: [], total: 0 });
    }
    if (method === "GET" && path === `${API}/knowledge-bases`) {
      return json(route, { items: [], total: 0 });
    }
    if (
      method === "GET" &&
      (path === `${API}/learning/profile/${USER_ID}` ||
        path === `${API}/learning/path/${USER_ID}` ||
        path.includes("/recommend-next"))
    ) {
      return json(route, { detail: "fixture intentionally empty" }, 404);
    }
    return json(route, { detail: `Unhandled fixture endpoint: ${method} ${path}` }, 404);
  });

  return {
    settle() {
      settled = true;
    },
    attempts,
    drafts,
    repair: repairState
      ? {
          finish() {
            repairState.status =
              repairState.outcome === "ready" ? "succeeded" : "failed";
          },
        }
      : null,
  };
}

async function checkedJson(response: any, label: string): Promise<JsonObject> {
  if (!response.ok()) {
    throw new Error(`${label} returned ${response.status()}: ${await response.text()}`);
  }
  return response.json();
}

async function getAggregate(request: any, sessionId = RECOVERY_SESSION) {
  return checkedJson(
    await request.get(
      `${API}/conversations/${encodeURIComponent(sessionId)}/aggregate?user_id=${USER_ID}`,
    ),
    `aggregate(${sessionId})`,
  );
}

async function openSession(page: any, sessionId: string) {
  // Establish the application origin before writing localStorage. An init
  // script can run against the initial opaque about:blank document on some
  // Chromium builds, where localStorage writes are rejected silently.
  await page.goto("/");
  await page.evaluate(
    ({ sid, userId }: { sid: string; userId: string }) => {
      const selectedSession =
        window.localStorage.getItem("tutor:e2eSessionOverride") ?? sid;
      window.localStorage.setItem("tutor:lastSessionId", selectedSession);
      window.localStorage.setItem("tutor-user-id", userId);
      window.localStorage.removeItem("tutor:user_id");
    },
    { sid: sessionId, userId: USER_ID },
  );
  const aggregate = page.waitForResponse(
    (response: any) =>
      response.request().method() === "GET" &&
      response.url().includes(`/conversations/${encodeURIComponent(sessionId)}/aggregate`),
    { timeout: 30_000 },
  );
  await page.reload();
  const response = await aggregate;
  if (!response.ok()) {
    throw new Error(`browser aggregate(${sessionId}) returned ${response.status()}`);
  }
  await expect(page.getByRole("heading", { name: "学习工作台" })).toBeVisible();
}

function latestPackage(aggregate: JsonObject) {
  const packages = Array.isArray(aggregate.packages) ? aggregate.packages : [];
  return packages[packages.length - 1] as JsonObject | undefined;
}

async function selectWorkspaceResource(page: any, title: string) {
  await page.getByRole("button", { name: /^资源(?:\s*\d+)?$/ }).click();
  await page.locator("main aside").getByText(title, { exact: true }).first().click();
}

async function allPackages(request: any) {
  const listed = await checkedJson(
    await request.get(`${API}/resources/packages/${USER_ID}?limit=100`),
    "resource package list",
  );
  const details: JsonObject[] = [];
  for (const item of listed.items ?? []) {
    details.push(
      await checkedJson(
        await request.get(
          `${API}/resources/packages/${USER_ID}/${encodeURIComponent(item.package_id)}`,
        ),
        `resource package ${item.package_id}`,
      ),
    );
  }
  return details;
}

async function openPackageResource(page: any, packageId: string, resourceId: string) {
  await page.getByTestId("nav-资源中心").click();
  await expect(page).toHaveURL(/\/resources$/);
  await page.getByTestId(`resource-card-${packageId}`).click();
  await expect(page.getByTestId("resource-package-preview")).toBeVisible();
  await page.getByTestId(`resource-list-item-${resourceId}`).click();
}

test.describe("TutorBot deterministic browser reliability fixtures", () => {
  test("@core restores persisted conversation and resources after refresh with no stale spinner", async ({
    page,
  }: {
    page: any;
  }) => {
    await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);

    await expect(
      page.getByText("Fixture resource generation completed and persisted.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Fixture reliability lesson", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/任务运行中/)).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("tutor:lastSessionId"))).toBe(
      FIXTURE_SESSION,
    );

    const aggregate = page.waitForResponse((response: any) =>
      response.url().includes(`/conversations/${FIXTURE_SESSION}/aggregate`),
    );
    await page.reload();
    expect((await aggregate).status()).toBe(200);
    await expect(
      page.getByText("Fixture resource generation completed and persisted.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Fixture reliability lesson", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
  });

  test("controlled resource generation settles every parent and child exactly once and survives refresh", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page, { running: true });
    await openSession(page, FIXTURE_SESSION);
    const trayButton = page.getByTitle("任务队列");
    await expect(trayButton).toContainText("3");
    await trayButton.click();
    await expect(page.getByText("3 运行中", { exact: true })).toBeVisible();

    fixture.settle();
    const refreshedJobs = page.waitForResponse(
      (response: any) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === `${API}/jobs/${USER_ID}`,
    );
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    expect((await refreshedJobs).status()).toBe(200);
    await expect(page.getByText("3 运行中", { exact: true })).toHaveCount(0);
    await expect(page.getByText("部分完成", { exact: true })).toBeVisible();
    await expect(page.getByText("已完成", { exact: true })).toBeVisible();
    await expect(page.getByText("失败", { exact: true })).toBeVisible();

    const terminalSnapshot = await page.evaluate(async (url: string) => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`fixture job list returned ${response.status}`);
      return response.json();
    }, `${API}/jobs/${USER_ID}?limit=50`);
    const children = terminalSnapshot.items.filter(
      (job: JsonObject) => job.parent_job_id === "fixture-parent",
    );
    expect(children).toHaveLength(2);
    expect(new Set(children.map((job: JsonObject) => job.job_id)).size).toBe(children.length);
    expect(children.every((job: JsonObject) => Boolean(job.task_kind))).toBe(true);
    expect(children.every((job: JsonObject) => TERMINAL.has(job.status))).toBe(true);
    expect(
      terminalSnapshot.items.filter((job: JsonObject) => job.job_id === "fixture-parent"),
    ).toHaveLength(1);
    expect(
      terminalSnapshot.items.every((job: JsonObject) => TERMINAL.has(job.status)),
    ).toBe(true);

    await page.reload();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/任务运行中/)).toHaveCount(0);
    await trayButton.click();
    await expect(page.getByText("3 运行中", { exact: true })).toHaveCount(0);
    await expect(page.getByText("部分完成", { exact: true })).toBeVisible();
  });

  test("uploads executable Python, submits it, and restores attempt history after refresh", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture executable Python exercise");
    const source = "def add(a, b):\n    return a + b\n";
    await page.getByLabel("上传 Python 文件").setInputFiles({
      name: "fixture_solution.py",
      mimeType: "text/x-python",
      buffer: Buffer.from(source, "utf8"),
    });
    await expect(page.getByLabel("Python 代码")).toHaveValue(source);
    const submitted = page.waitForResponse(
      (response: any) =>
        response.request().method() === "POST" &&
        response.url().includes(
          `/exercises/${FIXTURE_PACKAGE}/${FIXTURE_QUESTION}/attempts`,
        ),
    );
    await page.getByRole("button", { name: "运行并提交" }).click();
    expect((await submitted).status()).toBe(200);
    await expect(page.getByRole("region", { name: "本次运行结果" })).toContainText(
      "全部测试通过",
    );
    expect(fixture.attempts).toHaveLength(1);

    await page.reload();
    await selectWorkspaceResource(page, "Fixture executable Python exercise");
    const history = page.getByRole("region", { name: "历史尝试" });
    await expect(history).toBeVisible();
    await expect(history.getByText("fixture-attempt-1", { exact: true })).toBeVisible();
  });

  test("shows natural-size Matplotlib output and a terminal missing-asset Manim failure", async ({
    page,
  }: {
    page: any;
  }) => {
    await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture Matplotlib output");
    const opener = page.getByRole("button", { name: "查看 fixture.png" });
    await expect(opener).toBeVisible();
    await expect
      .poll(() =>
        opener
          .getByRole("img", { name: "fixture.png" })
          .evaluate((image: HTMLImageElement) => image.naturalWidth),
      )
      .toBeGreaterThan(0);
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "图片查看器" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "放大" }).click();
    await expect(dialog.getByText("125%", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("link", { name: "下载 fixture.png" })).toHaveAttribute(
      "href",
      new RegExp("/artifacts/fixture\\.png$"),
    );
    await dialog.getByRole("button", { name: "关闭图片查看器" }).click();

    await selectWorkspaceResource(page, "Fixture failed Manim video");
    await expect(page.getByText("渲染失败", { exact: true })).toBeVisible();
    await expect(page.getByText("缺少动画资源文件：person_silhouette.svg", { exact: true })).toBeVisible();
    await expect(page.getByText("视频渲染中…", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "查看源码" }).click();
    await expect(page.getByText(/person_silhouette\.svg/).first()).toBeVisible();
    await expect(page.getByText(/Traceback \(most recent call last\)/)).toHaveCount(0);
  });

  test("completed workflow stages remain visible after the job goes terminal and after refresh", async ({
    page,
  }: {
    page: any;
  }) => {
    await installFixtureApi(page, {
      workflow: {
        job_id: "fixture-parent",
        status: "succeeded",
        stages: [
          { name: "intent_understanding", status: "completed" },
          { name: "content_and_pedagogy", status: "completed" },
          { name: "package_assembly", status: "completed" },
        ],
      },
    });
    await openSession(page, FIXTURE_SESSION);

    await expect(page.getByText("工作流程 · 已完成", { exact: true })).toBeVisible();
    await expect(page.getByText("意图理解", { exact: true })).toBeVisible();
    await expect(page.getByText("内容生成", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("组装资源包", { exact: true })).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/任务运行中/)).toHaveCount(0);

    await page.reload();
    await expect(page.getByText("工作流程 · 已完成", { exact: true })).toBeVisible();
    await expect(page.getByText("意图理解", { exact: true })).toBeVisible();
    await expect(page.getByText("组装资源包", { exact: true })).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
  });

  test("exercise draft restores full options and the saved answer after unmount and refresh", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture choice exercise");

    // Full (non-truncated) options render from the canonical resource.
    await expect(page.getByText("自注意力中 Q、K、V 分别来自哪里？", { exact: true })).toBeVisible();
    await expect(page.getByText(/A\. 三个不同的输入序列/)).toBeVisible();
    await expect(page.getByText(/B\. 同一输入的三种线性投影/)).toBeVisible();
    await expect(page.getByText(/C\. 三个独立的注意力头/)).toBeVisible();
    await expect(page.getByText(/D\. 编码器与解码器/)).toBeVisible();
    await expect(page.getByText("[TRUNCATED]", { exact: true })).toHaveCount(0);

    const optionB = page.getByRole("radio", { name: /B\. 同一输入的三种线性投影/ });
    const saved = page.waitForResponse(
      (response: any) =>
        response.request().method() === "PUT" &&
        response.url().includes(`/questions/${FIXTURE_CHOICE_QUESTION}/draft`),
    );
    await optionB.click();
    expect((await saved).status()).toBe(200);
    await expect(optionB).toBeChecked();
    expect(fixture.drafts.get(FIXTURE_CHOICE_QUESTION)?.answer_json).toBe("B");

    // Unmount by switching resources: the restored draft still checks option B.
    await selectWorkspaceResource(page, "Fixture reliability lesson");
    await selectWorkspaceResource(page, "Fixture choice exercise");
    await expect(
      page.getByRole("radio", { name: /B\. 同一输入的三种线性投影/ }),
    ).toBeChecked();

    // Full refresh restores the durable draft from the backend state.
    await page.reload();
    await selectWorkspaceResource(page, "Fixture choice exercise");
    await expect(
      page.getByRole("radio", { name: /B\. 同一输入的三种线性投影/ }),
    ).toBeChecked();
  });

  test("Mermaid fallback shows the stored outline for an invalid quoted-node mindmap", async ({
    page,
  }: {
    page: any;
  }) => {
    await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture broken mindmap");

    await expect(page.getByText("思维导图暂时无法显示。", { exact: true })).toBeVisible();
    const outline = page.getByRole("list", { name: "思维导图文字版" });
    await expect(outline).toBeVisible();
    await expect(outline.getByText("反向传播", { exact: true })).toBeVisible();
    await expect(outline.getByText("前向传播", { exact: true })).toBeVisible();
    await expect(outline.getByText("激活函数 a=σ(z)", { exact: true })).toBeVisible();
    // The raw Mermaid parser blob is never surfaced to the user.
    await expect(page.getByText(/Parse error on line/)).toHaveCount(0);
  });

  test("text-only NumPy code shows stdout without a false image error", async ({
    page,
  }: {
    page: any;
  }) => {
    await installFixtureApi(page);
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture NumPy text output");

    await expect(page.getByText("运行结果", { exact: true })).toBeVisible();
    await expect(page.getByText("10", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/图片生成失败/)).toHaveCount(0);
    await expect(page.getByText(/产物 \(/)).toHaveCount(0);
  });

  test("intelligent video repair click-through shows progress, survives refresh, and finishes ready", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page, { repair: "ready" });
    // Keep the video request pending so the player shell stays mounted for
    // the deterministic src assertion (no decode error can flip the UI).
    await page.route("**/static/manim/fixture-repaired.mp4", async () => {});
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture failed Manim video");

    const repairButton = page.getByRole("button", { name: "智能修复并重新渲染" });
    await expect(repairButton).toBeEnabled();
    const requested = page.waitForResponse(
      (response: any) =>
        response.request().method() === "POST" &&
        response.url().includes(`/resources/${FIXTURE_VIDEO_RESOURCE}/retry-video`),
    );
    await repairButton.click();
    expect((await requested).status()).toBe(200);
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toBeVisible();
    await expect(repairButton).toBeDisabled();

    // A mid-repair refresh restores the active repair from canonical state.
    await page.reload();
    await selectWorkspaceResource(page, "Fixture failed Manim video");
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toBeVisible();

    fixture.repair?.finish();
    const source = page.locator("video source");
    await expect(source).toHaveAttribute("src", "/static/manim/fixture-repaired.mp4");
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toHaveCount(0);
    await expect(page.getByText("渲染失败", { exact: true })).toHaveCount(0);
  });

  test("intelligent video repair resumes polling from a persisted running repair without a click", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page, {
      repair: "ready",
      failedVideoOverrides: {
        repair_status: "running",
        repair_job_id: FIXTURE_REPAIR_JOB,
      },
    });
    await page.route("**/static/manim/fixture-repaired.mp4", async () => {});
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture failed Manim video");

    // The backend restart / browser refresh path: no click, polling resumes
    // from the canonical repair_job_id and the button stays disabled.
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "智能修复并重新渲染" })).toBeDisabled();

    fixture.repair?.finish();
    const source = page.locator("video source");
    await expect(source).toHaveAttribute("src", "/static/manim/fixture-repaired.mp4");
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toHaveCount(0);
  });

  test("intelligent video repair failure keeps the original failure and re-enables manual repair", async ({
    page,
  }: {
    page: any;
  }) => {
    const fixture = await installFixtureApi(page, { repair: "failed" });
    await openSession(page, FIXTURE_SESSION);
    await selectWorkspaceResource(page, "Fixture failed Manim video");

    const repairButton = page.getByRole("button", { name: "智能修复并重新渲染" });
    await repairButton.click();
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toBeVisible();

    fixture.repair?.finish();
    await expect(page.getByText("智能修复失败", { exact: true })).toBeVisible();
    await expect(page.getByText("修复代码渲染失败：Manim 退出码 1", { exact: true })).toBeVisible();
    // The original render failure and its diagnostic stay visible, and the
    // repair action is available again for the next manual attempt.
    await expect(page.getByText("渲染失败", { exact: true })).toBeVisible();
    await expect(
      page.getByText("缺少动画资源文件：person_silhouette.svg", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("正在生成修复代码并重新渲染…", { exact: true })).toHaveCount(0);
    await expect(repairButton).toBeEnabled();
  });
});

test.describe("TutorBot end-to-end reliability @real-data", () => {
  test.skip(!REAL_DATA, "Set TUTOR_E2E_REAL_DATA=1 only after a verified local-data backup/migration.");

  test("@core restores sess_ebb5a8f5dfdb, resources and terminal jobs after refresh", async (
    { page, request }: { page: any; request: any },
    testInfo: any,
  ) => {
    const aggregate = await getAggregate(request);
    expect(aggregate.conversation.user_id).toBe(USER_ID);
    expect(aggregate.conversation.messages.length).toBeGreaterThan(0);
    expect(aggregate.packages.length).toBeGreaterThan(0);
    expect(aggregate.jobs.length).toBeGreaterThan(0);
    expect(
      aggregate.jobs.every((job: JsonObject) => TERMINAL.has(job.status)),
      "a restarted process must repair every historical running job to one terminal state",
    ).toBe(true);

    const message = aggregate.conversation.messages[0].content;
    const pkg = latestPackage(aggregate);
    expect(pkg?.resources?.length).toBeGreaterThan(0);
    if (!pkg) throw new Error("sess_ebb5a8f5dfdb has no migrated resource package");
    await openSession(page, RECOVERY_SESSION);

    expect(await page.title()).toContain("TutorBot");
    expect(await page.evaluate(() => window.innerWidth)).toBe(
      testInfo.project.name === "mobile-chromium" ? 390 : 1440,
    );
    await expect(page.getByText(message, { exact: false }).first()).toBeVisible();
    await expect(
      page.getByRole("heading", { name: pkg.resources[0].title, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/任务运行中/)).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("tutor:lastSessionId"))).toBe(
      RECOVERY_SESSION,
    );

    const refreshed = page.waitForResponse((response: any) =>
      response.url().includes(`/conversations/${RECOVERY_SESSION}/aggregate`),
    );
    await page.reload();
    expect((await refreshed).status()).toBe(200);
    await expect(page.getByText(message, { exact: false }).first()).toBeVisible();
    await expect(
      page.getByRole("heading", { name: pkg.resources[0].title, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("处理中", { exact: true })).toHaveCount(0);
  });

  test("opens a migrated Matplotlib artifact at natural size and supports zoom, pan, reset and download", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const pkg = latestPackage(await getAggregate(request));
    const resource = pkg?.resources?.find((candidate: JsonObject) =>
      (candidate.format_specific?.artifacts ?? []).some((artifact: JsonObject) =>
        /\.(?:png|jpe?g|svg)$/i.test(String(artifact.name ?? "")),
      ),
    );
    expect(resource, "sess_ebb5a8f5dfdb must retain its Matplotlib image resource").toBeTruthy();
    const artifact = resource.format_specific.artifacts.find((item: JsonObject) =>
      /\.(?:png|jpe?g|svg)$/i.test(String(item.name ?? "")),
    );

    await openSession(page, RECOVERY_SESSION);
    await selectWorkspaceResource(page, resource.title);
    const opener = page.getByRole("button", { name: `查看 ${artifact.name}` });
    await expect(opener).toBeVisible();
    const preview = opener.getByRole("img", { name: artifact.name });
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await opener.click();

    const dialog = page.getByRole("dialog", { name: "图片查看器" });
    await expect(dialog).toBeVisible();
    const image = dialog.getByRole("img", { name: artifact.name });
    await expect(dialog.getByText("100%", { exact: true })).toBeVisible();
    for (let index = 0; index < 4; index += 1) {
      await dialog.getByRole("button", { name: "放大" }).click();
    }
    await expect(dialog.getByText("200%", { exact: true })).toBeVisible();
    const beforePan = await image.evaluate((node: HTMLElement) => node.style.transform);
    const stage = page.getByTestId("image-lightbox-stage");
    const box = await stage.boundingBox();
    expect(box).toBeTruthy();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 35);
    await page.mouse.up();
    expect(await image.evaluate((node: HTMLElement) => node.style.transform)).not.toBe(beforePan);
    await dialog.getByRole("button", { name: "重置图片" }).click();
    await expect(dialog.getByText("100%", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("link", { name: `下载 ${artifact.name}` })).toHaveAttribute(
      "href",
      new RegExp(`/artifacts/${artifact.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`),
    );
  });

  test("shows the missing-SVG Manim resource as failed instead of rendering forever", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const pkg = latestPackage(await getAggregate(request));
    const video = pkg?.resources?.find(
      (candidate: JsonObject) =>
        candidate.type === "video" && candidate.format_specific?.render_status === "failed",
    );
    expect(video, "the migrated world-model package must retain the failed SVG video").toBeTruthy();
    expect(String(video.format_specific?.manim_code ?? "")).toMatch(/SVGMobject\(.+\.svg/);

    await openSession(page, RECOVERY_SESSION);
    await selectWorkspaceResource(page, video.title);
    await expect(page.getByText("渲染失败", { exact: true })).toBeVisible();
    await expect(page.getByText("视频渲染中…", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "查看源码" }).click();
    await expect(page.getByText(/person_silhouette\.svg|cup\.svg|brain_sketch\.svg/).first()).toBeVisible();
    await expect(page.getByText(/Traceback \(most recent call last\)/)).toHaveCount(0);
  });

  test("loads an existing minimal Manim MP4 through the proxied static route", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const packages = await allPackages(request);
    const pkg = packages.find((candidate) =>
      candidate.resources?.some(
        (resource: JsonObject) =>
          resource.type === "video" &&
          resource.format_specific?.render_status === "ready" &&
          resource.format_specific?.video_url,
      ),
    );
    const video = pkg?.resources?.find(
      (resource: JsonObject) =>
        resource.type === "video" && resource.format_specific?.render_status === "ready",
    );
    const sessionId = pkg?.metadata?.session_id;
    expect(pkg && video && sessionId, "a persisted minimal ready-video fixture is required").toBeTruthy();
    if (!pkg || !video || !sessionId) {
      throw new Error("a persisted minimal ready-video fixture is required");
    }

    await openSession(page, sessionId);
    await openPackageResource(page, pkg.package_id, video.resource_id);
    const source = page.locator("video source");
    await expect(source).toHaveAttribute("src", video.format_specific.video_url);
    await expect(page.getByText("视频渲染中…", { exact: true })).toHaveCount(0);
    const media = await request.get(video.format_specific.video_url);
    expect(media.status()).toBe(200);
    expect(media.headers()["content-type"]).toContain("video/mp4");
  });

  test("uploads a .py answer, submits it and restores the durable result after refresh", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const packages = await allPackages(request);
    let fixture: { pkg: JsonObject; resource: JsonObject; question: JsonObject } | null = null;
    for (const pkg of packages) {
      for (const resource of pkg.resources ?? []) {
        const question = (resource.format_specific?.questions ?? []).find(
          (candidate: JsonObject) =>
            candidate.type === "code" &&
            candidate.code_spec &&
            Number(candidate.code_spec.test_count) > 0,
        );
        if (resource.type === "exercise" && question && pkg.metadata?.session_id) {
          fixture = { pkg, resource, question };
          break;
        }
      }
      if (fixture) break;
    }
    test.skip(
      !fixture,
      "No migrated/generated exercise has a public code_spec yet; generate one executable code exercise first.",
    );
    if (!fixture) return;

    await openSession(page, fixture.pkg.metadata.session_id);
    await openPackageResource(page, fixture.pkg.package_id, fixture.resource.resource_id);
    const source = "def solution(*args):\n    return None\n";
    await page.getByLabel("上传 Python 文件").setInputFiles({
      name: "e2e_solution.py",
      mimeType: "text/x-python",
      buffer: Buffer.from(source, "utf8"),
    });
    await expect(page.getByLabel("Python 代码")).toHaveValue(source);
    const submitted = page.waitForResponse(
      (response: any) =>
        response.request().method() === "POST" &&
        response.url().includes(
          `/exercises/${fixture!.pkg.package_id}/${fixture!.question.id}/attempts`,
        ),
      { timeout: 90_000 },
    );
    await page.getByRole("button", { name: "运行并提交" }).click();
    const attemptResponse = await submitted;
    const attempt = await checkedJson(attemptResponse, "code exercise submission");
    await expect(page.getByRole("region", { name: "本次运行结果" })).toBeVisible();

    await page.reload();
    await page.getByTestId(`resource-card-${fixture.pkg.package_id}`).click();
    await page.getByTestId(`resource-list-item-${fixture.resource.resource_id}`).click();
    const history = page.getByRole("region", { name: "历史尝试" });
    await expect(history).toBeVisible();
    await expect(history.getByText(attempt.attempt_id, { exact: true })).toBeVisible();
  });

  test("builds a non-empty learner profile and version-bound learning path from scored events", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const previousProfileResponse = await request.get(`${API}/learning/profile/${USER_ID}`);
    expect([200, 404]).toContain(previousProfileResponse.status());
    const previousProfile = previousProfileResponse.ok()
      ? await previousProfileResponse.json()
      : null;
    const previousProfileVersion = Number(previousProfile?.version ?? 0);
    const previousPathResponse = await request.get(`${API}/learning/path/${USER_ID}`);
    expect([200, 404]).toContain(previousPathResponse.status());
    const previousPath = previousPathResponse.ok()
      ? await previousPathResponse.json()
      : null;

    const runId = `task15-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    for (let index = 1; index <= 5; index += 1) {
      const response = await request.post(`${API}/learning/events`, {
        data: {
          event_id: `${runId}-${index}`.slice(0, 64),
          user_id: USER_ID,
          session_id: RECOVERY_SESSION,
          event_type: "exercise_scored",
          target_id: `e2e-question-${index}`,
          concept_id: "backpropagation",
          duration_seconds: 30,
          score: 0.2 + index * 0.1,
          correct: index >= 3,
          course: "ai_introduction",
          metadata: { source: "playwright-acceptance", modality: "code" },
        },
      });
      expect(response.status()).toBe(202);
    }

    let profile: JsonObject = {};
    let path: JsonObject = {};
    await expect
      .poll(
        async () => {
          const [profileResponse, pathResponse] = await Promise.all([
            request.get(`${API}/learning/profile/${USER_ID}`),
            request.get(`${API}/learning/path/${USER_ID}`),
          ]);
          if (!profileResponse.ok() || !pathResponse.ok()) return "not-ready";
          const candidateProfile = await profileResponse.json();
          const candidatePath = await pathResponse.json();
          if (Number(candidateProfile.version) <= previousProfileVersion) {
            return "profile-not-rebuilt";
          }
          if (Number(candidatePath.profile_version) !== Number(candidateProfile.version)) {
            return "path-not-bound-to-current-profile";
          }
          profile = candidateProfile;
          path = candidatePath;
          return "version-bound";
        },
        { timeout: 120_000 },
      )
      .toBe("version-bound");
    if (profile.version == null || path.profile_version == null) {
      throw new Error("profile/path workflow never produced a bound revision");
    }
    expect(Number(profile.version)).toBeGreaterThan(previousProfileVersion);
    expect(Object.keys(profile.knowledge_map ?? {}).length).toBeGreaterThan(0);
    expect(path.nodes.length).toBeGreaterThan(0);
    expect(path.profile_version).toBe(profile.version);
    if (previousPath?.profile_version != null) {
      expect(Number(path.profile_version)).toBeGreaterThan(
        Number(previousPath.profile_version),
      );
    }

    await openSession(page, RECOVERY_SESSION);
    await page.getByRole("button", { name: /^画像$/ }).click();
    await expect(page.getByText("暂无画像数据", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /^路径(?:\s*\d+)?$/ }).click();
    await expect(page.getByText("学习路径", { exact: true })).toBeVisible();
    await expect(page.getByText(path.nodes[0].name, { exact: true })).toBeVisible();
  });

  test("keeps web search default-off and persists the switch independently per conversation", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const enabledSession = `e2e-search-a-${suffix}`.slice(0, 64);
    const disabledSession = `e2e-search-b-${suffix}`.slice(0, 64);
    try {
      const first = await checkedJson(
        await request.post(`${API}/conversations`, {
          data: { user_id: USER_ID, session_id: enabledSession, title: "E2E search A" },
        }),
        "create default-off conversation A",
      );
      const second = await checkedJson(
        await request.post(`${API}/conversations`, {
          data: { user_id: USER_ID, session_id: disabledSession, title: "E2E search B" },
        }),
        "create default-off conversation B",
      );
      expect(first.web_search_enabled).toBe(false);
      expect(second.web_search_enabled).toBe(false);

      await openSession(page, enabledSession);
      const toggle = page.getByRole("switch", { name: "联网搜索" });
      await expect(toggle).toHaveAttribute("aria-checked", "false");
      const saved = page.waitForResponse(
        (response: any) =>
          response.request().method() === "PATCH" &&
          response.url().includes(`/conversations/${enabledSession}/settings`),
      );
      await toggle.click();
      expect((await saved).status()).toBe(200);
      await expect(toggle).toHaveAttribute("aria-checked", "true");

      await page.evaluate((sid: string) => {
        localStorage.setItem("tutor:e2eSessionOverride", sid);
        localStorage.setItem("tutor:lastSessionId", sid);
      }, disabledSession);
      await page.reload();
      await expect(page.getByRole("switch", { name: "联网搜索" })).toHaveAttribute(
        "aria-checked",
        "false",
      );
      await page.evaluate((sid: string) => {
        localStorage.setItem("tutor:e2eSessionOverride", sid);
        localStorage.setItem("tutor:lastSessionId", sid);
      }, enabledSession);
      await page.reload();
      await expect(page.getByRole("switch", { name: "联网搜索" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    } finally {
      await request.delete(
        `${API}/conversations/${enabledSession}?user_id=${encodeURIComponent(USER_ID)}`,
      );
      await request.delete(
        `${API}/conversations/${disabledSession}?user_id=${encodeURIComponent(USER_ID)}`,
      );
    }
  });

  test("uses the configured MiniMax MCP provider and persists HTTP sources", async ({
    page,
    request,
  }: {
    page: any;
    request: any;
  }) => {
    test.skip(
      !MINIMAX_SEARCH,
      "Set TUTOR_E2E_MINIMAX_SEARCH=1 only for the explicit live MiniMax MCP smoke test.",
    );
    const config = await checkedJson(await request.get(`${API}/config`), "runtime config");
    expect(String(config.web_search?.provider ?? "").toLowerCase()).toBe("mcp");
    expect(
      config.web_search?.mcp_server,
      "GET /api/v1/config must expose the non-secret MiniMax MCP server binding",
    ).toBe("MiniMax");
    expect(
      config.web_search?.mcp_tool,
      "GET /api/v1/config must expose the non-secret MiniMax MCP tool binding",
    ).toBe("web_search");

    const sessionId = `e2e-minimax-${Date.now()}`.slice(0, 64);
    let jobId = "";
    try {
      await checkedJson(
        await request.post(`${API}/conversations`, {
          data: {
            user_id: USER_ID,
            session_id: sessionId,
            title: "E2E MiniMax MCP",
            web_search_enabled: true,
          },
        }),
        "create MiniMax MCP conversation",
      );
      await openSession(page, sessionId);
      await page.getByRole("button", { name: "即时答疑" }).first().click();
      const prompt = `MiniMax MCP acceptance ${Date.now()}: 查询当前 OpenAI 官方首页标题`;
      await page.getByPlaceholder(/请输入你想学的内容|例如:什么是注意力机制/).fill(prompt);
      await page.getByRole("button", { name: "发送" }).click();

      let aggregate: JsonObject = {};
      await expect
        .poll(
          async () => {
            aggregate = await getAggregate(request, sessionId);
            const job = aggregate.jobs?.find((item: JsonObject) =>
              String(item.message_preview ?? "").includes("MiniMax MCP acceptance"),
            );
            return job?.status ?? "missing";
          },
          { timeout: 180_000 },
        )
        .toMatch(/succeeded|partial/);
      const job = aggregate.jobs.find((item: JsonObject) =>
        String(item.message_preview ?? "").includes("MiniMax MCP acceptance"),
      );
      jobId = job.job_id;
      const detail = await checkedJson(
        await request.get(`${API}/jobs/${USER_ID}/${jobId}`),
        "MiniMax MCP job detail",
      );
      const resultEvent = [...(detail.events ?? [])]
        .reverse()
        .find((event: JsonObject) => event.type === "result");
      const persistedPayload = resultEvent?.content
        ? JSON.parse(resultEvent.content)
        : {};
      expect(persistedPayload.search_used).toBe(true);
      const sources = persistedPayload.sources ?? [];
      expect(sources.length).toBeGreaterThan(0);
      expect(sources.every((source: JsonObject) => /^https?:\/\//.test(source.url))).toBe(true);
    } finally {
      if (jobId) {
        await request.delete(`${API}/jobs/${USER_ID}/${jobId}`);
      }
      await request.delete(
        `${API}/conversations/${sessionId}?user_id=${encodeURIComponent(USER_ID)}`,
      );
    }
  });
});
