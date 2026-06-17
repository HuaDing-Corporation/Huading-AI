"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { makeQueryClient } from "@/lib/query/client";

export function QueryProvider({ children }: { children: ReactNode }) {
  // useState so the client is created once per mount (not per render) and never
  // shared across requests on the server.
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
