import { Bell, Search, Settings } from "lucide-react";

import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Glass } from "@/components/ui/glass";
import { Input } from "@/components/ui/input";
import { Logo } from "@/components/ui/logo";

export function TopBar() {
  return (
    <Glass className="col-span-full flex items-center gap-[18px] rounded-card px-6 py-[15px]">
      <Logo />

      <div className="relative mx-2 hidden max-w-[440px] flex-1 sm:block">
        <Search
          className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint"
          size={18}
          strokeWidth={1.8}
        />
        <Input
          aria-label="搜索"
          placeholder="搜索项目、模板、任务…"
          className="rounded-[15px] py-3 pl-11 text-[13.5px]"
        />
      </div>

      <div className="ml-auto flex items-center gap-3">
        <Button variant="icon" size="icon" aria-label="通知">
          <Bell size={18} strokeWidth={1.8} />
        </Button>
        <Button variant="icon" size="icon" aria-label="设置">
          <Settings size={18} strokeWidth={1.8} />
        </Button>
        <Avatar>华</Avatar>
      </div>
    </Glass>
  );
}
