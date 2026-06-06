import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="min-h-screen bg-background px-6 py-10 text-foreground">
      <section className="mx-auto flex max-w-5xl flex-col gap-6">
        <p className="text-sm font-medium text-muted-foreground">Huading AI</p>
        <div className="flex flex-col gap-4">
          <h1 className="text-4xl font-semibold tracking-normal">华鼎项目骨架已就绪</h1>
          <p className="max-w-2xl text-base leading-7 text-muted-foreground">
            Next.js、TypeScript、Tailwind CSS 与 shadcn/ui 组件目录已经初始化，等待后续业务模块接入。
          </p>
        </div>
        <div>
          <Button>开始构建</Button>
        </div>
      </section>
    </main>
  );
}
