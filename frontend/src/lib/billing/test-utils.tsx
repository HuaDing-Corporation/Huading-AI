import { render as renderBase, renderHook as renderHookBase, type RenderOptions } from "@testing-library/react";
import { transferableAbortController } from "node:util";
import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query/query-provider";

// Only billing test consumers need this transport alignment (Vitest isolates files).
// Their Request/fetch are Node-native; jsdom's DOM-realm signal is rejected before
// MSW sees a GET. Use real Node controllers, never drop signal or stub cancellation.
const nativeController = transferableAbortController();
globalThis.AbortController = nativeController.constructor as typeof AbortController;
globalThis.AbortSignal = nativeController.signal.constructor as typeof AbortSignal;

// Billing consumers share the same required QueryProvider as the app. Tests may
// override the wrapper to inspect their own client or a mounted quota consumer.
export const render = (ui: ReactNode, options?: RenderOptions) =>
  renderBase(ui, { wrapper: QueryProvider, ...options });
export const renderHook: typeof renderHookBase = (callback, options) =>
  renderHookBase(callback, { wrapper: QueryProvider, ...options });
