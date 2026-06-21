"use client";

/**
 * ServiceConfigSection — masked config form for one AI service.
 *
 * The section renders a masked API key preview but NEVER puts the raw
 * key into an input value (security). Saving with an empty key field
 * omits the field from the PATCH body so the existing key is preserved;
 * a dedicated "清除" button sends ``clear_api_key: true`` to wipe it.
 *
 * The test button is a pure local-state machine:
 *   - clicking it sends a POST to ``/config/test/{section}``;
 *   - while in flight the button is disabled and shows a spinner;
 *   - on success the latency is shown;
 *   - on failure a stable error code + message is shown.
 */

import { useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, XCircle, Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ServiceConfigSectionProps {
  title: string;
  description: string;
  provider: string;
  model: string;
  baseUrl: string;
  extraFields?: Array<{
    label: string;
    value: string | number;
    field: string;
    type?: "text" | "number";
  }>;
  apiKey: {
    configured: boolean;
    preview: string;
    required?: boolean;
    hint?: string;
  };
  onSave: (patch: Record<string, unknown>) => Promise<void>;
  onTest: () => Promise<{ ok: boolean; latency_ms: number; message: string; code?: string }>;
  providerOptions?: string[];
}

export function ServiceConfigSection({
  title,
  description,
  provider,
  model,
  baseUrl,
  extraFields = [],
  apiKey,
  onSave,
  onTest,
  providerOptions = [],
}: ServiceConfigSectionProps) {
  const [draftProvider, setDraftProvider] = useState(provider);
  const [draftModel, setDraftModel] = useState(model);
  const [draftBaseUrl, setDraftBaseUrl] = useState(baseUrl);
  const [draftExtras, setDraftExtras] = useState<Record<string, string>>(
    () =>
      Object.fromEntries(extraFields.map((f) => [f.field, String(f.value ?? "")])),
  );
  const [draftKey, setDraftKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<
    | { ok: boolean; latency_ms: number; message: string; code?: string }
    | null
  >(null);

  const handleSave = async () => {
    setSaving(true);
    setSaveStatus(null);
    try {
      const patch: Record<string, unknown> = {
        provider: draftProvider,
        model: draftModel,
        base_url: draftBaseUrl,
        ...Object.fromEntries(
          extraFields.map((f) => [f.field, Number(draftExtras[f.field]) || draftExtras[f.field]]),
        ),
      };
      if (clearKey) {
        patch.clear_api_key = true;
      } else if (draftKey) {
        patch.api_key = draftKey;
      }
      await onSave(patch);
      setSaveStatus("已保存");
      setDraftKey("");
      setClearKey(false);
    } catch (e: any) {
      setSaveStatus(`保存失败: ${e?.message ?? String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await onTest();
      setTestResult(r);
    } catch (e: any) {
      setTestResult({
        ok: false,
        latency_ms: 0,
        message: e?.message ?? String(e),
        code: "EXCEPTION",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="rounded-xl border border-fg/10 bg-bg-panel p-5 space-y-4">
      <header>
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="text-xs text-fg-muted mt-1">{description}</p>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Provider">
          {providerOptions.length > 0 ? (
            <select
              data-testid={`${title}-provider`}
              className="input"
              value={draftProvider}
              onChange={(e) => setDraftProvider(e.target.value)}
            >
              {providerOptions.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              data-testid={`${title}-provider`}
              value={draftProvider}
              onChange={(e) => setDraftProvider(e.target.value)}
            />
          )}
        </Field>
        <Field label="Model">
          <input
            className="input"
            data-testid={`${title}-model`}
            value={draftModel}
            onChange={(e) => setDraftModel(e.target.value)}
          />
        </Field>
        <Field label="Base URL" fullWidth>
          <input
            className="input"
            data-testid={`${title}-base-url`}
            value={draftBaseUrl}
            onChange={(e) => setDraftBaseUrl(e.target.value)}
          />
        </Field>
        {extraFields.map((f) => (
          <Field key={f.field} label={f.label}>
            <input
              className="input"
              type={f.type ?? "text"}
              data-testid={`${title}-${f.field}`}
              value={draftExtras[f.field] ?? ""}
              onChange={(e) =>
                setDraftExtras((prev) => ({ ...prev, [f.field]: e.target.value }))
              }
            />
          </Field>
        ))}
      </div>

      <div>
        <label className="block text-xs font-semibold text-fg-muted mb-1">
          API Key
          {apiKey.required === false && (
            <span className="ml-2 text-[10px] font-normal text-fg-subtle">
              (本 Provider 不需要 Key)
            </span>
          )}
        </label>
        <div className="flex items-center gap-2">
          <code
            data-testid={`${title}-key-preview`}
            className="flex-1 px-3 py-2 rounded-lg border border-fg/10 bg-bg-card text-sm font-mono"
          >
            {apiKey.configured
              ? apiKey.preview || "••••••••"
              : apiKey.required === false
              ? "—"
              : "（未配置）"}
          </code>
          {apiKey.required !== false && (
            <>
              <button
                type="button"
                className="btn-secondary text-sm h-9"
                onClick={() => setShowKeyInput((v) => !v)}
                data-testid={`${title}-toggle-key`}
                title="修改 API Key"
              >
                {showKeyInput ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
              {apiKey.configured && (
                <button
                  type="button"
                  className="btn-secondary text-sm h-9"
                  onClick={() => setClearKey((v) => !v)}
                  data-testid={`${title}-clear-key`}
                  title="清除现有 API Key"
                  aria-pressed={clearKey}
                >
                  {clearKey ? "已选清除" : "清除"}
                </button>
              )}
            </>
          )}
        </div>
        {apiKey.required !== false && showKeyInput && (
          <input
            type="password"
            className="input mt-2"
            data-testid={`${title}-new-key`}
            value={draftKey}
            onChange={(e) => setDraftKey(e.target.value)}
            placeholder="留空表示不修改"
            autoComplete="off"
          />
        )}
        {apiKey.hint && (
          <p
            data-testid={`${title}-key-hint`}
            className="text-[11px] text-yellow-300/80 mt-1"
          >
            💡 {apiKey.hint}
          </p>
        )}
        {!apiKey.hint && (
          <p className="text-[11px] text-fg-subtle mt-1">
            出于安全考虑，原 Key 不会回显到界面。提交空 Key 表示保留旧值；点"清除"会移除当前 Key。
          </p>
        )}
      </div>

      <div className="flex items-center gap-2 pt-2 border-t border-fg/10">
        <button
          className="btn-primary text-sm h-9"
          onClick={handleSave}
          disabled={saving}
          data-testid={`${title}-save`}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : "保存"}
        </button>
        <button
          className="btn-secondary text-sm h-9"
          onClick={handleTest}
          disabled={testing}
          data-testid={`${title}-test`}
        >
          {testing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          <span className="ml-1">测试连接</span>
        </button>
        {saveStatus && (
          <span
            className={cn(
              "text-xs",
              saveStatus === "已保存" ? "text-green-400" : "text-red-400",
            )}
            data-testid={`${title}-save-status`}
          >
            {saveStatus}
          </span>
        )}
        {testResult && (
          <span
            data-testid={`${title}-test-result`}
            className={cn(
              "inline-flex items-center gap-1 text-xs",
              testResult.ok ? "text-green-400" : "text-red-400",
            )}
          >
            {testResult.ok ? (
              <CheckCircle2 className="w-3.5 h-3.5" />
            ) : (
              <XCircle className="w-3.5 h-3.5" />
            )}
            {testResult.ok
              ? `连接成功 (${testResult.latency_ms} ms)`
              : `连接失败：${testResult.message}${testResult.code ? ` [${testResult.code}]` : ""}`}
          </span>
        )}
      </div>
    </section>
  );
}

function Field({
  label,
  fullWidth,
  children,
}: {
  label: string;
  fullWidth?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={fullWidth ? "sm:col-span-2" : ""}>
      <label className="block text-xs font-semibold text-fg-muted mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}
