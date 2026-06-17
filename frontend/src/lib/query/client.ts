import { QueryClient } from "@tanstack/react-query";

// One client per browser session. Conservative defaults: short staleness, one
// retry, no refetch-on-focus (the console drives freshness via SSE + invalidate).
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false }
    }
  });
}
