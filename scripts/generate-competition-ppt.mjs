import {
  AgentDocumentSchema,
  compileAgentDocument,
  PaperEngine,
  setDeterministicMode,
} from "@paperjsx/json-to-pptx";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const inputPath = resolve("submission/tutorbot-a3-presentation.json");
const outputPath = resolve("submission/TutorBot-A3-competition-deck.pptx");
const input = JSON.parse(readFileSync(inputPath, "utf8"));
const parsed = AgentDocumentSchema.parse(input);

setDeterministicMode(true);
const document = compileAgentDocument(parsed, { layoutValidation: "error" });
const buffer = await PaperEngine.render(document);
mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, buffer);

const size = statSync(outputPath).size;
if (size === 0) {
  throw new Error("Generated presentation is empty");
}
console.log(`Generated ${outputPath} (${size} bytes)`);
