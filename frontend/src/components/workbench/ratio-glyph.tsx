// 真比例矩形 glyph（VIDEO-GEN-PARAMS-UI-0001 Code Review 抽取：原逐字重复于 aspect-ratio-select（图片 8+auto）
// 与 video-aspect-ratio-select（视频 6+adaptive）两处，单处改缩放/圆角另一处会静默漂移）。装饰性 aria-hidden；
// border-current 继承文字色，选中/高亮态自然变色。sentinel（"auto"/"adaptive"）= 虚线方框。纯函数无状态。
export function RatioGlyph({ ratio, sentinel }: { ratio: string; sentinel?: string }) {
  if (sentinel !== undefined && ratio === sentinel) {
    return (
      <span aria-hidden className="inline-flex h-4 w-4 flex-none items-center justify-center">
        <span className="h-4 w-4 rounded-[3px] border border-dashed border-current" />
      </span>
    );
  }
  const [w, h] = ratio.split(":").map(Number);
  const max = 16;
  const width = w >= h ? max : Math.round((max * w) / h);
  const height = h >= w ? max : Math.round((max * h) / w);
  return (
    <span aria-hidden className="inline-flex h-4 w-4 flex-none items-center justify-center">
      <span style={{ width, height }} className="rounded-[2px] border border-current" />
    </span>
  );
}
