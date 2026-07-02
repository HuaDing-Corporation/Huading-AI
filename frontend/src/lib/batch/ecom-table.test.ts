import * as XLSX from "xlsx";
import { describe, expect, it } from "vitest";

import { parseEcomWorkbook, toEcomTableRow, validateEcomRow } from "./ecom-table";

function makeBuf(rows: (string | number)[][]): Uint8Array {
  const ws = XLSX.utils.aoa_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
  return XLSX.write(wb, { type: "array", bookType: "xlsx" }) as Uint8Array;
}

describe("ecom-table 解析 + 校验", () => {
  it("SheetJS 解析中文表头 → 草稿行（空图→undefined）", () => {
    const rows = parseEcomWorkbook(
      makeBuf([
        ["商品名", "卖点", "商品图"],
        ["保温杯", "316 不锈钢", "http://x/1.png"],
        ["雨伞", "自动折叠", ""]
      ])
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ product_name: "保温杯", selling_points: "316 不锈钢", image_url: "http://x/1.png" });
    expect(rows[1].image_url).toBeUndefined();
  });

  it("英文表头兼容", () => {
    const rows = parseEcomWorkbook(makeBuf([["product_name", "selling_points", "image_url"], ["Cup", "steel", "http://x/c.png"]]));
    expect(rows[0]).toMatchObject({ product_name: "Cup", selling_points: "steel", image_url: "http://x/c.png" });
  });

  it("validateEcomRow：必填 商品名/卖点 + 图恰好二选一(XOR，对齐后端)", () => {
    expect(validateEcomRow({ product_name: "", selling_points: "x", image_url: "u" })).toEqual(["product_name"]);
    expect(validateEcomRow({ product_name: "a", selling_points: "", image_url: "u" })).toEqual(["selling_points"]);
    expect(validateEcomRow({ product_name: "a", selling_points: "b" })).toEqual(["image"]); // 都不给
    expect(validateEcomRow({ product_name: "a", selling_points: "b", image_url: "u", image_asset_id: "id" })).toEqual(["image"]); // 都给 → XOR 违反
    expect(validateEcomRow({ product_name: "a", selling_points: "b", image_url: "u" })).toEqual([]);
    expect(validateEcomRow({ product_name: "a", selling_points: "b", image_asset_id: "id" })).toEqual([]);
  });

  it("toEcomTableRow：image_asset_id 优先于 image_url", () => {
    expect(toEcomTableRow({ product_name: " a ", selling_points: " b ", image_url: "u", image_asset_id: "id" })).toEqual({
      product_name: "a",
      selling_points: "b",
      image_asset_id: "id"
    });
    expect(toEcomTableRow({ product_name: "a", selling_points: "b", image_url: "u" })).toEqual({
      product_name: "a",
      selling_points: "b",
      image_url: "u"
    });
  });
});
