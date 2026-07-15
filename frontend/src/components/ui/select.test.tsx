import { render, screen } from "@testing-library/react";
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
