import type { VoteResult } from "@/lib/votes-queries";

/** "2019-07-10" -> "10/07/2019"; anything else is returned as is. */
export function formatVoteDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

export function formatCount(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("pt-BR");
}

export function truncate(text: string | null | undefined, max: number): string {
  if (!text) return "";
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
}

export function VoteResultBadge({ result }: { result: VoteResult }) {
  const tone = result.approved === true ? " badge--green" : result.approved === false ? " badge--red" : "";
  return (
    <span className={`badge${tone}`} title={result.raw ? `valor na fonte: ${result.raw}` : undefined}>
      {result.label}
    </span>
  );
}

/** Sim × Não, with the secret-vote caveat inline. */
export function VoteScore({ yes, no, isSecret }: { yes: number | null; no: number | null; isSecret: boolean }) {
  return (
    <span className="mono whitespace-nowrap" style={{ fontSize: 13 }}>
      <span style={{ color: "var(--green)" }}>{formatCount(yes)}</span>
      <span style={{ color: "var(--muted-2)" }}> × </span>
      <span style={{ color: "var(--red)" }}>{formatCount(no)}</span>
      {isSecret ? (
        <span className="block text-[10px]" style={{ color: "var(--muted-2)" }}>
          voto secreto
        </span>
      ) : null}
    </span>
  );
}
