import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get } from "./api";

/** GET with caching + optional auto-refresh. Data always comes from the backend. */
export function useGet<T = any>(path: string | null, query?: Record<string, any>, refetchMs?: number) {
  return useQuery<T>({
    queryKey: [path, query],
    queryFn: () => get<T>(path as string, query),
    enabled: !!path,
    refetchInterval: refetchMs,
  });
}

export function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}
