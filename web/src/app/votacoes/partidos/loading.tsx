import { Skeleton } from "@/components/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-8">
      <section>
        <Skeleton className="h-7 w-[28rem] max-w-full" />
        <Skeleton className="mt-4 h-4 w-full max-w-2xl" />
        <Skeleton className="mt-1.5 h-4 w-2/3 max-w-2xl" />
        <div className="mt-5 flex flex-wrap items-center gap-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-40" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-48" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
      </section>

      <section className="table-wrap">
        <div className="p-4">
          {Array.from({ length: 12 }).map((_, r) => (
            <div key={r} className="flex items-center gap-1 py-1">
              <Skeleton className="h-3.5 w-20" />
              {Array.from({ length: 12 }).map((_, c) => (
                <Skeleton key={c} className="h-9 flex-1" style={{ borderRadius: 4 }} />
              ))}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
