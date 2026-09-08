import { render as renderBase, renderHook as renderHookBase, type RenderOptions } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query/query-provider";

// Billing consumers share the same required QueryProvider as the app. Tests may
// override the wrapper to inspect their own client or a mounted quota consumer.
export const render = (ui: ReactNode, options?: RenderOptions) =>
  renderBase(ui, { wrapper: QueryProvider, ...options });
export const renderHook: typeof renderHookBase = (callback, options) =>
  renderHookBase(callback, { wrapper: QueryProvider, ...options });
