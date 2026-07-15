"use client";

/**
 * /settings — runtime configuration page (Task 7).
 *
 * Four groups: appearance, LLM, Embedding, Web Search. The appearance
 * group is a small switcher that piggybacks on the existing theme
 * store. The three AI service groups use :class:`ServiceConfigSection`
 * to render masked key previews and live connection tests.
 */

import { useEffect, useState } from "react";
import { Moon, Sun, Loader2, RefreshCw } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import {
  getRuntimeConfig,
  testEmbeddingConnection,
  testLLMConnection,
  testWebSearchConnection,
  updateEmbeddingConfig,
  updateLLMConfig,
  updateWebSearchConfig,
} from "@/lib/api";
import type {
  ConfigTestResult,
  EmbeddingConfig,
  LLMConfig,
  RuntimeConfig,
  WebSearchConfig,
} from "@/lib/types";
import { ServiceConfigSection } from "@/components/settings/ServiceConfigSection";

const LLM_PROVIDERS = [
  "openai",
  "anthropic",
  "deepseek",
  "azure_openai",
  "ollama",
  "custom",
];
const EMBED_PROVIDERS = [
  "openai",
  "openrouter",
  "azure_openai",
  "ollama",
  "custom",
  "zhipu",
  "zhipuai",
];
const WEB_PROVIDERS = ["duckduckgo", "searxng", "bing", "mcp"];

export default function SettingsPage() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const theme = useTutorStore((s) => s.theme);
  const setTheme = useTutorStore((s) => s.setTheme);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getRuntimeConfig();
      setConfig(c);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-12 text-fg-muted">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> 正在加载配置…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 text-sm text-red-400">
        加载配置失败：{error}
        <button
          className="btn-secondary text-sm h-8 ml-3"
          onClick={refresh}
        >
          重试
        </button>
      </div>
    );
  }

  if (!config) return null;

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">设置</h1>
          <p className="text-xs text-fg-muted mt-1">
            配置 AI 服务、密钥与外观。密钥以掩码形式回显，原值不会通过网络暴露。
          </p>
        </div>
        <button
          className="btn-secondary text-sm h-9"
          onClick={refresh}
          title="从服务器刷新"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </header>

      {/* 外观 */}
      <section className="rounded-xl border border-fg/10 bg-bg-panel p-5">
        <h3 className="text-base font-semibold">外观</h3>
        <p className="text-xs text-fg-muted mt-1">主题颜色；立即生效。</p>
        <div className="grid grid-cols-2 gap-2 mt-3 max-w-md">
          <ThemeOption
            label="深色"
            icon={Moon}
            selected={theme === "dark"}
            onClick={() => setTheme("dark")}
          />
          <ThemeOption
            label="浅色"
            icon={Sun}
            selected={theme === "light"}
            onClick={() => setTheme("light")}
          />
        </div>
      </section>

      <ServiceConfigSection
        title="LLM"
        description="对话与生成所用的大模型。"
        provider={config.llm.provider}
        model={config.llm.model}
        baseUrl={config.llm.base_url}
        extraFields={[
          { label: "Temperature", field: "temperature", value: config.llm.temperature, type: "number" },
          { label: "Max Tokens", field: "max_tokens", value: config.llm.max_tokens, type: "number" },
          { label: "Timeout (s)", field: "timeout", value: config.llm.timeout, type: "number" },
        ]}
        apiKey={config.llm.api_key}
        providerOptions={LLM_PROVIDERS}
        providerHelp={{
          deepseek:
            "DeepSeek 在本项目中只作为 LLM 生成/对话 provider；知识库向量检索仍需要单独配置 Embedding provider 和 key。",
        }}
        onSave={async (patch) => {
          const next = await updateLLMConfig(patch as any);
          setConfig(next);
        }}
        onTest={async () => {
          const r = await testLLMConnection();
          return r as ConfigTestResult;
        }}
      />

      <ServiceConfigSection
        title="Embedding"
        description="知识库索引与检索所用的向量模型。"
        provider={config.embedding.provider}
        model={config.embedding.model}
        baseUrl={config.embedding.base_url}
        extraFields={[
          { label: "Dimensions", field: "dimensions", value: config.embedding.dimensions, type: "number" },
        ]}
        apiKey={config.embedding.api_key}
        providerOptions={EMBED_PROVIDERS}
        providerHelp={{
          openrouter:
            "OpenRouter 可作为 OpenAI 兼容向量端点使用，需填写独立的 Embedding API Key 和 Base URL。",
          zhipu:
            "智谱 embedding-3 可用于国产向量模型演示，需使用智谱 API Key。",
          zhipuai:
            "智谱 embedding-3 可用于国产向量模型演示，需使用智谱 API Key。",
          ollama:
            "Ollama 本地向量模型通常不需要云端 API Key，但需要本机服务和模型已启动。",
        }}
        onSave={async (patch) => {
          const next = await updateEmbeddingConfig(patch as any);
          setConfig(next);
        }}
        onTest={async () => {
          const r = await testEmbeddingConnection();
          return r as ConfigTestResult;
        }}
      />

      <ServiceConfigSection
        title="WebSearch"
        description="事实核查与外部资料检索所使用的搜索引擎。"
        provider={config.web_search.provider}
        model="n/a"
        baseUrl=""
        extraFields={[
          { label: "Max Results", field: "max_results", value: config.web_search.max_results, type: "number" },
        ]}
        apiKey={config.web_search.api_key}
        providerOptions={WEB_PROVIDERS}
        onSave={async (patch) => {
          // Web-search "enabled" is not exposed in the form yet; merge it.
          const next = await updateWebSearchConfig({
            ...(patch as any),
            enabled: config.web_search.enabled,
          });
          setConfig(next);
        }}
        onTest={async () => {
          const r = await testWebSearchConnection();
          return r as ConfigTestResult;
        }}
      />
    </div>
  );
}

function ThemeOption({
  label,
  icon: Icon,
  selected,
  onClick,
}: {
  label: string;
  icon: any;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "p-3 rounded-lg border text-left transition-all flex items-center gap-2",
        selected
          ? "border-brand-500 bg-brand-500/10 text-brand-200"
          : "border-fg/10 hover:border-fg/20 hover:bg-bg-card text-fg-muted hover:text-fg",
      )}
    >
      <Icon
        className={cn(
          "w-4 h-4 shrink-0",
          selected ? "text-brand-300" : "text-fg-muted",
        )}
      />
      <span className="text-sm font-medium">{label}</span>
    </button>
  );
}
