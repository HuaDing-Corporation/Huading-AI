import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it } from "vitest";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./select";

// SELECT-ARIA-LABEL-FIX-0001 组件级承重（护栏，因为类型系统救不了我们）：
// 连字符属性（aria-*）在 JSX 里逃逸 TS excess-property 检查——tsc 永远不报 SelectTrigger 吞掉 aria-label，
// 唯一护栏是测试。Radix Select 触发器 role = combobox（本仓 e2e a11y 快照已核实：`combobox [ref]`）。
// 变异哨兵：把 select.tsx 里 `{...props}` 透传删掉 → 下面两条必红（可及名回落到 SelectValue 当前值文本）。
function renderSelectWithLabel(label: string) {
  return render(
    <Select defaultValue="a">
      <SelectTrigger aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="a">选项 A</SelectItem>
      </SelectContent>
    </Select>
  );
}

describe("SelectTrigger · aria-label 透传（确定值可及名）", () => {
  it("aria-label='动作' → combobox 可及名精确为「动作」（而非当前值「选项 A」）", () => {
    renderSelectWithLabel("动作");
    expect(screen.getByRole("combobox", { name: "动作" })).toBeInTheDocument();
    // 反证：可及名不是当前值文本（aria-label 覆盖内容名）。
    expect(screen.queryByRole("combobox", { name: "选项 A" })).not.toBeInTheDocument();
  });

  it("aria-label='被操作租户' → combobox 可及名精确为「被操作租户」", () => {
    renderSelectWithLabel("被操作租户");
    expect(screen.getByRole("combobox", { name: "被操作租户" })).toBeInTheDocument();
  });

  it("aria-labelledby 仍透传（回归保护，别把既有能力改坏）：外部 label 元素 → 可及名取其文本", () => {
    render(
      <>
        <span id="lbl-x">画面比例</span>
        <Select defaultValue="a">
          <SelectTrigger aria-labelledby="lbl-x">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="a">1:1</SelectItem>
          </SelectContent>
        </Select>
      </>
    );
    expect(screen.getByRole("combobox", { name: "画面比例" })).toBeInTheDocument();
  });
});

// FIX1（PR #171）：rest 透传把 asChild 变成合法 API，但本组件固定追加箭头（第二个子节点）→ asChild 必崩。
// 类型 + 运行时双双排除。asChild 不含连字符、是合法标识符 → TS 这次抓得到，类型层可当护栏。
describe("SelectTrigger · 排除 asChild（固定两个子节点，不支持）", () => {
  it("🔴 类型层护栏：传 asChild 是类型错误（去掉 select.tsx 的 Omit → 此处 @ts-expect-error 未触发 → tsc 变红）", () => {
    // @ts-expect-error asChild 不受支持：本组件固定渲染 trigger + 箭头两个子节点
    const el = <SelectTrigger asChild>{<button />}</SelectTrigger>;
    expect(el).toBeTruthy(); // 仅使变量被用；真正的护栏是 tsc 对上面 @ts-expect-error 的校验
  });

  it("运行时护栏：as-cast 绕过类型塞 asChild → 不崩、不透传给 Radix（渲染正常、可及名在、rest 仍透传）", () => {
    // 模拟调用方用 as-any/spread 绕过类型层塞入 asChild（双 cast，不引入 no-explicit-any）。
    const sneaky = { "aria-label": "动作", asChild: true };
    render(
      <Select defaultValue="a">
        <SelectTrigger {...(sneaky as unknown as ComponentProps<typeof SelectTrigger>)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">选项 A</SelectItem>
        </SelectContent>
      </Select>
    );
    // 未崩（能渲染到此——asChild 被丢弃，没到 Radix 触发 failed-to-slot）；rest 仍透传 → 可及名在。
    expect(screen.getByRole("combobox", { name: "动作" })).toBeInTheDocument();
  });
});
