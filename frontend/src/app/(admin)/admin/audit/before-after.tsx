import type { AdminAuditRow } from "@/lib/api/admin-console";
import { copy } from "@/lib/copy";

import { flattenAuditDiff, truncateAuditValue } from "./format";

/**
 * 单个值渲染：① 破折号（—）读屏读「无」而非 em-dash 噪音；② UUID 形长值中截——可视显截断串 + `title`
 * 供鼠标悬浮，读屏则取 **sr-only 全值**（title 对读屏不可靠，审计追溯要求全值可得）；③ `label`（变更前/后）
 * 可选：变化键带前/后语义（不只靠颜色/箭头），未变键为单值不加。
 */
function AuditValue({ label, text, className }: { label?: string; text: string; className: string }) {
  const shown = truncateAuditValue(text);
  return (
    <span className={className}>
      {label ? <span className="sr-only">{label} </span> : null}
      {text === copy.admin.auditEmpty ? (
        <>
          <span aria-hidden="true">{copy.admin.auditEmpty}</span>
          <span className="sr-only">{copy.admin.auditSrNone}</span>
        </>
      ) : shown !== text ? (
        <>
          <span aria-hidden="true" title={text}>
            {shown}
          </span>
          <span className="sr-only">{text}</span>
        </>
      ) : (
        text
      )}
    </span>
  );
}

/**
 * before/after 快照 → 分组渲染（ADMIN-AUDIT-DIFF-NOISE-0001）。变化键（含单边键）默认展示，保持
 * #169 的 `前 → 后`／sr-only／千分位／—／是-否 语义；未变键收进原生 `<details>` 折叠区（信息**只折叠不删**，
 * 事后可逐键复核「改套餐时余额确实没动」）。原生 details 自带 aria-expanded／键盘切换／读屏折叠态播报。
 * 空态：全未变 → 折叠标题「本次无字段变化」仍可展开；全变化 → 不出现折叠区；两侧皆空 → —。
 */
export function BeforeAfter({ row }: { row: AdminAuditRow }) {
  const leaves = flattenAuditDiff(row.before, row.after);
  if (leaves.length === 0)
    return (
      <span className="text-ink-faint">
        <span aria-hidden="true">{copy.admin.auditEmpty}</span>
        <span className="sr-only">{copy.admin.auditSrNone}</span>
      </span>
    );
  const changed = leaves.filter((l) => l.changed);
  const unchanged = leaves.filter((l) => !l.changed);
  return (
    <div className="flex flex-col gap-0.5 tabular-nums">
      {changed.map(({ key, before, after }) => (
        <span key={key}>
          <span className="text-ink-faint">{key}：</span>
          {before !== undefined && <AuditValue label={copy.admin.auditSrBefore} text={before} className="text-ink-soft" />}
          {before !== undefined && after !== undefined && (
            <span aria-hidden="true" className="text-ink-faint">
              {copy.admin.beforeAfterArrow}
            </span>
          )}
          {after !== undefined && <AuditValue label={copy.admin.auditSrAfter} text={after} className="font-medium text-ink" />}
        </span>
      ))}
      {unchanged.length > 0 && (
        <details className="text-ink-faint">
          <summary className="cursor-pointer select-none text-[12px] text-ink-faint marker:text-ink-faint">
            {changed.length === 0 ? copy.admin.auditAllUnchanged : copy.admin.auditUnchangedFold(unchanged.length)}
          </summary>
          <div className="mt-0.5 flex flex-col gap-0.5 pl-3">
            {unchanged.map(({ key, before, after }) => (
              <span key={key}>
                <span className="text-ink-faint">{key}：</span>
                <AuditValue text={(before ?? after) as string} className="text-ink-soft" />
              </span>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
