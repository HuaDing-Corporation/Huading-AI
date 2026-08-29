import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const PRODUCTION_FILES = [
  "src/components/brand-voice/brand-voice-create.tsx",
  "src/components/brand-voice/brand-voice-list.tsx",
  "src/components/brand-voice/brand-voice-order-list.tsx",
  "src/components/workbench/new-video-form.tsx",
  "src/components/workbench/ecom-video-form.tsx",
  "src/components/workbench/ecom-image-cutout-form.tsx",
  "src/components/workbench/ecom-image-model-form.tsx",
  "src/lib/api/brand-voices.ts",
  "src/lib/api/brand-voice-orders.ts",
  "src/lib/api/scripts.ts",
  "src/lib/api/videos.ts",
  "src/lib/api/ecom-images.ts"
] as const;
const FORBIDDEN_NAMES = new Set([
  "DOUBAO_CLONE_CREDITS",
  "SCRIPT_GENERATE_CREDITS",
  "SCENE_PROMPT_CREDITS",
  "ECOM_IMAGE_CREDITS",
  "COSYVOICE_CHARACTER_RATE"
]);
const FORBIDDEN_VALUES = new Set(["1", "30", "80", "30000", "0.1"]);
const PRICE_TEXT = /(?:1|30|80|30000|0\.1)\s*积分/;

describe("production pricing authority", () => {
  it("contains no client-owned price constants or literal Chinese price copy", () => {
    const root = path.resolve(__dirname, "../../..");
    const failures: string[] = [];
    for (const relative of PRODUCTION_FILES) {
      const file = path.join(root, relative);
      if (!fs.existsSync(file)) continue;
      const sourceText = fs.readFileSync(file, "utf8");
      const source = ts.createSourceFile(file, sourceText, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      const visit = (node: ts.Node) => {
        if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
          if (FORBIDDEN_NAMES.has(node.name.text)) failures.push(`${relative}: forbidden identifier ${node.name.text}`);
          const initializer = node.initializer;
          if (initializer && (ts.isNumericLiteral(initializer) || ts.isPrefixUnaryExpression(initializer))) {
            const text = initializer.getText(source);
            if (FORBIDDEN_VALUES.has(text)) failures.push(`${relative}: price-like constant ${node.name.text}=${text}`);
          }
        }
        if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && PRICE_TEXT.test(node.text)) {
          failures.push(`${relative}: literal price copy ${JSON.stringify(node.text)}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(failures).toEqual([]);
  });
});
