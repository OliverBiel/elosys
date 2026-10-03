import { Skeleton } from "@/components/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-8">
      <section>
        <Skeleton className="h-7 w-96" />
        <Skeleton className="mt-4 h-4 w-full max-w-2xl" />
        <Skeleton className="mt-1.5 h-4 w-2/3 max-w-2xl" />
        <div className="mt-5 flex flex-wrap items-center gap-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-40" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-32" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
      </section>

      <section className="table-wrap">
        <div className="p-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="mb-4 flex items-center gap-3 last:mb-0">
              <Skeleton className="h-6 w-6 flex-none" style={{ borderRadius: "999px" }} />
              <Skeleton className="h-3.5 flex-1" />
              <Skeleton className="h-3.5 w-16 flex-none" />
              <Skeleton className="h-3.5 w-16 flex-none" />
              <Skeleton className="h-3.5 w-16 flex-none" />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
