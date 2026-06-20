/**
 * Tests for the masked config form (Task 7).
 *
 * Security invariants:
 * - Existing key is rendered as a mask ("sk-…ab12" or similar), never
 *   placed in an input value.
 * - Saving with an empty key field omits ``api_key`` from the patch
 *   (so the server keeps the existing key).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { ServiceConfigSection } from "./ServiceConfigSection";

afterEach(() => {
  cleanup();
});

const sampleApiKey = { configured: true, preview: "sk-…ab12" };
const missingApiKey = { configured: false, preview: "" };

describe("ServiceConfigSection", () => {
  it("renders an existing key as a mask, never as an input value", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({ ok: true, latency_ms: 100, message: "ok" });
    render(
      <ServiceConfigSection
        title="LLM"
        description="大型语言模型"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    const preview = screen.getByTestId("LLM-key-preview");
    expect(preview.textContent).toBe("sk-…ab12");
    // No input value should contain the masked preview
    const inputs = screen.getAllByRole("textbox");
    for (const input of inputs) {
      expect((input as HTMLInputElement).value).not.toContain("sk-…ab12");
    }
  });

  it("renders an unconfigured key as 未配置", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({ ok: false, latency_ms: 0, message: "no" });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={missingApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    const preview = screen.getByTestId("LLM-key-preview");
    expect(preview.textContent).toContain("未配置");
  });

  it("omits api_key from the patch when the key input is left blank", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({ ok: true, latency_ms: 1, message: "ok" });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    const saveBtn = screen.getByTestId("LLM-save");
    fireEvent.click(saveBtn);
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const patch = onSave.mock.calls[0][0] as Record<string, unknown>;
    expect(patch).not.toHaveProperty("api_key");
    expect(patch).not.toHaveProperty("clear_api_key");
  });

  it("includes api_key when the user types a new value", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({ ok: true, latency_ms: 1, message: "ok" });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    // Open the key input
    fireEvent.click(screen.getByTestId("LLM-toggle-key"));
    const newKey = screen.getByTestId("LLM-new-key") as HTMLInputElement;
    fireEvent.change(newKey, { target: { value: "sk-new-key-abc" } });
    fireEvent.click(screen.getByTestId("LLM-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const patch = onSave.mock.calls[0][0] as Record<string, unknown>;
    expect(patch.api_key).toBe("sk-new-key-abc");
  });

  it("toggles clear_api_key when the user clicks 清除", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({ ok: true, latency_ms: 1, message: "ok" });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    const clearBtn = screen.getByTestId("LLM-clear-key");
    fireEvent.click(clearBtn);
    fireEvent.click(screen.getByTestId("LLM-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const patch = onSave.mock.calls[0][0] as Record<string, unknown>;
    expect(patch.clear_api_key).toBe(true);
  });

  it("disables the test button and shows latency on success", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({
      ok: true,
      latency_ms: 234,
      message: "ok",
    });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    fireEvent.click(screen.getByTestId("LLM-test"));
    await waitFor(() => expect(onTest).toHaveBeenCalled());
    const result = screen.getByTestId("LLM-test-result");
    expect(result.textContent).toContain("234");
    expect(result.textContent).toContain("成功");
  });

  it("renders the stable error code on failure", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTest = vi.fn().mockResolvedValue({
      ok: false,
      latency_ms: 50,
      message: "401 invalid api key",
      code: "AUTH_ERROR",
    });
    render(
      <ServiceConfigSection
        title="LLM"
        description="x"
        provider="openai"
        model="gpt-4o-mini"
        baseUrl="https://api.openai.com/v1"
        apiKey={sampleApiKey}
        onSave={onSave}
        onTest={onTest}
      />,
    );
    fireEvent.click(screen.getByTestId("LLM-test"));
    await waitFor(() => expect(onTest).toHaveBeenCalled());
    const result = screen.getByTestId("LLM-test-result");
    expect(result.textContent).toContain("AUTH_ERROR");
    expect(result.textContent).toContain("失败");
  });
});
