"use client";

import type { ReactNode } from "react";

import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

// 管理后台通用密集表格（ADMIN-CONSOLE-UI-0001 · UI UX Pro Max 后台规范）：
// 紧凑行高（py-2 / 12.5px）、数字列右对齐 tabular-nums、容器 overflow-x-auto（375 下页面不横滚）、
// 加载骨架 / 错误重试 / 空态三态齐备、th scope=col。表头 sticky 仅对容器内滚动生效（页面纵向滚动时
// 不粘——横滚容器限制；20 行/页的密度下可接受，真需要页面级粘性需重构滚动容器）。
export interface AdminColumn<T> {
  key: string;
  label: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}

export const adminCell = "px-3 py-2 text-[12.5px] align-top";

export function AdminTable<T>({
  columns,
  rows,
  rowKey,
  loading,
  error,
  onRetry,
  minWidth = 760
}: {
  columns: AdminColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  minWidth?: number;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-9 animate-pulse rounded-field bg-glass-soft" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="text-[13px] text-error-fg">
        {copy.admin.error}{" "}
        {onRetry && (
          <button type="button" onClick={onRetry} className="underline">
            {copy.admin.retry}
          </button>
        )}
      </div>
    );
  }
  if (rows.length === 0) {
    return <p className="text-[13px] text-ink-soft">{copy.admin.empty}</p>;
  }
  return (
    <div className="min-w-0 overflow-x-auto rounded-field border border-line-gold">
      <table className="w-full border-collapse text-left" style={{ minWidth }}>
        <thead>
          <tr className="border-b border-line-gold text-[11.5px]">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(adminCell, "sticky top-0 bg-glass-soft font-medium text-ink-soft", c.align === "right" && "text-right")}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-line-gold last:border-none">
              {columns.map((c) => (
                <td key={c.key} className={cn(adminCell, c.align === "right" && "text-right tabular-nums")}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 分页条（page/page_size —— 对齐 BE 真契约分页语义；「第 x–y / 共 n」）。 */
export function AdminPager({
  page,
  pageSize,
  total,
  onPage
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (next: number) => void;
}) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div className="flex items-center justify-between text-[12px] text-ink-soft">
      <span className="tabular-nums">{copy.admin.pageRange(from, to, total)}</span>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="rounded-field border border-line-gold bg-glass-fill px-3 py-1 hover:bg-glass-hover disabled:opacity-40"
        >
          {copy.admin.prevPage}
        </button>
        <button
          type="button"
          onClick={() => onPage(page + 1)}
          disabled={page * pageSize >= total}
          className="rounded-field border border-line-gold bg-glass-fill px-3 py-1 hover:bg-glass-hover disabled:opacity-40"
        >
          {copy.admin.nextPage}
        </button>
      </div>
    </div>
  );
}
