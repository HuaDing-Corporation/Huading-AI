import { redirect } from "next/navigation";

// /admin 落地即租户管理（后台首页 = 最高频页面）。
export default function AdminIndexPage() {
  redirect("/admin/tenants");
}
