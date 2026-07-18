import { chromium } from "@playwright/test";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const mermaidRuntime = resolve(process.cwd(), "../node_modules/mermaid/dist/mermaid.min.js");

describe("Mermaid 11.14 mindmap runtime", () => {
  it("parses and renders the normalized reported DSL in Chromium", async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.setContent("<div id=\"diagram\"></div>");
      await page.addScriptTag({ path: mermaidRuntime });
      const dsl = [
        "mindmap",
        "  root((反向传播))",
        '    node_3["前向传播"]',
        '    node_4["激活函数 a=σ(z)"]',
        '    node_5["计算损失 C"]',
      ].join("\n");

      const svg = await page.evaluate(async (source) => {
        const runtime = (window as typeof window & { mermaid: any }).mermaid;
        runtime.initialize({ startOnLoad: false, securityLevel: "loose" });
        await runtime.parse(source);
        return (await runtime.render("runtime_mindmap", source)).svg;
      }, dsl);

      expect(svg).toContain("<svg");
    } finally {
      await browser.close();
    }
  }, 30_000);
});
