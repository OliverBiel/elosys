import type { AgreementBasis } from "@/lib/votes-queries";
import { SELECT_CLASS } from "./party-ui";

type Option = { value: string; label: string };

/** Plain GET form (works without JS); casa travels as a hidden field, switched by HouseTabs. */
export function PartyFilters({
  action, hidden, periods, period, basis, procedural, children,
}: {
  action: string;
  hidden: Record<string, string | string[] | undefined>;
  periods: Option[];
  period: string;
  /** Omitted = no basis toggle on this page. */
  basis?: AgreementBasis;
  procedural: boolean;
  children?: React.ReactNode;
}) {
  const hiddenInputs = Object.entries(hidden).flatMap(([k, v]) =>
    (v == null ? [] : Array.isArray(v) ? v : [v]).map((x, i) => (
      <input key={`${k}-${i}`} type="hidden" name={k} value={x} />
    ))
  );
  return (
    <form className="flex flex-wrap items-center gap-2" action={action}>
      {hiddenInputs}
      <select name="periodo" defaultValue={period} className={SELECT_CLASS} aria-label="período">
        {periods.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {basis ? (
        <select
          name="base"
          defaultValue={basis === "orientation" ? "orientacao" : "maioria"}
          className={SELECT_CLASS}
          aria-label="posição do partido"
        >
          <option value="orientacao">posição = orientação oficial do partido</option>
          <option value="maioria">posição = voto da maioria da bancada</option>
        </select>
      ) : null}
      {children}
      <label className="flex items-center gap-1.5 text-[12px]" style={{ color: "var(--muted)" }}>
        <input type="checkbox" name="proc" value="1" defaultChecked={procedural} />
        incluir procedimentais (requerimentos, urgência…)
      </label>
      <button type="submit" className="btn">filtrar</button>
    </form>
  );
}
