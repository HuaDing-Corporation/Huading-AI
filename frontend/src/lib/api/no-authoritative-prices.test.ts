import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const TASK_PRODUCTION_ROOTS = [
  "src/lib/api", "src/lib/billing", "src/lib/videos", "src/components/billing",
  "src/components/brand-voice", "src/components/workbench", "src/components/layout",
  "src/app/(app)/brand-voices", "src/app/(admin)/admin/brand-voice-orders",
  "src/app/(admin)/admin/voice-slots"
] as const;
const REQUIRED_ENTRY_POINTS = [
  "src/lib/api/billing.ts", "src/lib/api/hooks.ts", "src/lib/api/brand-voice-orders.ts",
  "src/lib/billing/use-billing-action.ts", "src/components/billing/pricing-confirm-dialog.tsx",
  "src/components/brand-voice/brand-voice-create.tsx",
  "src/components/brand-voice/brand-voice-order-list.tsx",
  "src/components/workbench/new-video-form.tsx", "src/lib/videos/tasks-context.tsx",
  "src/app/(admin)/admin/brand-voice-orders/page.tsx"
] as const;
const FORBIDDEN_NAMES = new Set([
  "DOUBAO_CLONE_CREDITS", "SCRIPT_GENERATE_CREDITS", "SCENE_PROMPT_CREDITS",
  "ECOM_IMAGE_CREDITS", "COSYVOICE_CHARACTER_RATE"
]);
const FORBIDDEN_VALUES = new Set(["1", "30", "80", "30000", "0.1"]);
const PRICE_BEARING_NAME = /(?:price|pricing|credit|cost|rate|amount|payable|settled|held|subtotal|fee)/i;
const PRICE_TEXT = /(?:1|30|80|30000|0\.1)\s*积分/;
const JSX_PRICE_TEXT = /(?:1|30|80|30000|0\.1)(?:\s|[{}<>"'`]){0,20}积分/;

interface SourceFixture { relative: string; sourceText: string }

function isProductionTypeScript(relative: string): boolean {
  const normalized = relative.replaceAll("\\", "/");
  return /\.tsx?$/.test(normalized) && !/(?:^|\/)src\/mocks\//.test(normalized) && !/\.(?:test|spec)\.tsx?$/.test(normalized);
}

function discoverProductionSources(root: string): { sources: SourceFixture[]; failures: string[] } {
  const failures: string[] = [];
  const found = new Map<string, SourceFixture>();
  for (const relativeRoot of TASK_PRODUCTION_ROOTS) {
    const absoluteRoot = path.join(root, relativeRoot);
    if (!fs.existsSync(absoluteRoot) || !fs.statSync(absoluteRoot).isDirectory()) {
      failures.push(`missing required production root ${relativeRoot}`);
      continue;
    }
    const pending = [absoluteRoot];
    while (pending.length > 0) {
      const current = pending.pop() as string;
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        const absolute = path.join(current, entry.name);
        if (entry.isDirectory()) pending.push(absolute);
        else {
          const relative = path.relative(root, absolute).replaceAll("\\", "/");
          if (isProductionTypeScript(relative)) found.set(relative, { relative, sourceText: fs.readFileSync(absolute, "utf8") });
        }
      }
    }
  }
  for (const required of REQUIRED_ENTRY_POINTS) {
    if (!found.has(required)) failures.push(`missing required production entry point ${required}`);
  }
  return { sources: [...found.values()].sort((a, b) => a.relative.localeCompare(b.relative)), failures };
}

function initializerName(node: ts.VariableDeclaration | ts.PropertyAssignment | ts.PropertyDeclaration): string | null {
  if (ts.isIdentifier(node.name)) return node.name.text;
  if ((ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node)) && ts.isStringLiteral(node.name)) return node.name.text;
  return null;
}

function numericInitializerText(node: ts.Expression, source: ts.SourceFile): string | null {
  if (ts.isNumericLiteral(node)) return node.text;
  if (ts.isPrefixUnaryExpression(node) && ts.isNumericLiteral(node.operand)) return node.getText(source);
  return null;
}

function sourceViolations(fixtures: SourceFixture[]): string[] {
  const failures = new Set<string>();
  for (const { relative, sourceText } of fixtures) {
    const source = ts.createSourceFile(relative, sourceText, ts.ScriptTarget.Latest, true, relative.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const report = (message: string) => failures.add(`${relative}: ${message}`);
    const visit = (node: ts.Node) => {
      if (ts.isIdentifier(node) && FORBIDDEN_NAMES.has(node.text)) report(`forbidden identifier ${node.text}`);
      if (ts.isVariableDeclaration(node) || ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node)) {
        const name = initializerName(node);
        const initializer = node.initializer;
        if (name && initializer && PRICE_BEARING_NAME.test(name)) {
          const value = numericInitializerText(initializer, source);
          if (value && FORBIDDEN_VALUES.has(value)) report(`price-bearing initializer ${name}=${value}`);
        }
      }
      if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && PRICE_TEXT.test(node.text)) report(`literal price copy ${JSON.stringify(node.text)}`);
      if (ts.isTemplateExpression(node) && JSX_PRICE_TEXT.test(node.getText(source))) report(`template price copy ${JSON.stringify(node.getText(source))}`);
      if ((ts.isJsxElement(node) || ts.isJsxFragment(node)) && JSX_PRICE_TEXT.test(node.getText(source))) report("JSX literal price copy");
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return [...failures].sort();
}

describe("production pricing authority", () => {
  it("auto-discovers the bounded Tasks 14-16 roots and contains no client-owned prices", () => {
    const root = path.resolve(__dirname, "../../..");
    const discovered = discoverProductionSources(root);
    expect([...discovered.failures, ...sourceViolations(discovered.sources)]).toEqual([]);
  });

  it.each([
    ["new file", "src/components/brand-voice/new-price.ts", "export const payableCredits = 30000;"],
    ["renamed file", "src/components/workbench/renamed-pricing.tsx", "export const SCRIPT_GENERATE_CREDITS = 1;"],
    ["property initializer", "src/lib/api/new-contract.ts", "export const quote = { unit_credits: 80 };"],
    ["Chinese JSX price", "src/components/billing/new-card.tsx", "export const Card = () => <p>本次冻结 30000 积分</p>;"],
    ["Chinese template price", "src/components/billing/new-copy.ts", "export const copy = `本次应付 30 积分`;"],
  ])("rejects the %s mutation fixture", (_label, relative, sourceText) => {
    expect(sourceViolations([{ relative, sourceText }])).not.toEqual([]);
  });

  it("fails closed when a required root or entry point disappears", () => {
    const missingRoot = discoverProductionSources(path.join(__dirname, "definitely-missing-root"));
    expect(missingRoot.failures).toContain(`missing required production root ${TASK_PRODUCTION_ROOTS[0]}`);
    expect(missingRoot.failures).toContain(`missing required production entry point ${REQUIRED_ENTRY_POINTS[0]}`);
  });
});
