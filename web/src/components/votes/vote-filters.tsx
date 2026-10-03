import Link from "next/link";
import { HOUSE_LABEL, type House } from "@/lib/vote-meaning";
import type { ThemeItem } from "@/lib/votes-queries";
import { SELECT_CLASS } from "./party-ui";

export type VoteFilterState = {
  house: House;
  year?: number;
  theme?: number;
  q: string;
  procedural: boolean;
};

/** Plain GET form: works without JS. Switching Casa drops ano/tema (their lists differ). */
export function VoteFilters({
  state, years, themes, houseHref,
}: {
  state: VoteFilterState;
  years: number[];
  themes: ThemeItem[];
  houseHref: (house: House) => string;
}) {
  return (
    <div className="mt-5 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="casa legislativa">
        {(["camara", "senado"] as const).map((h) => (
          <Link key={h} href={houseHref(h)} className={`btn${state.house === h ? " btn--primary" : ""}`}>
            {HOUSE_LABEL[h]}
          </Link>
        ))}
      </div>

      <form className="flex flex-wrap items-center gap-2" action="/votacoes">
        <input type="hidden" name="casa" value={state.house} />
        <select name="ano" defaultValue={state.year ?? ""} className={SELECT_CLASS} aria-label="ano">
          <option value="">todos os anos</option>
          {years.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        {state.house === "camara" && themes.length > 0 ? (
          <select name="tema" defaultValue={state.theme ?? ""} className={SELECT_CLASS} aria-label="tema">
            <option value="">todos os temas</option>
            {themes.map((t) => (
              <option key={t.code} value={t.code}>{t.name}</option>
            ))}
          </select>
        ) : null}
        <div className="input" style={{ width: 280 }}>
          <input name="q" defaultValue={state.q} placeholder="buscar na ementa, descrição ou nº…" />
        </div>
        <label className="flex items-center gap-1.5 text-[12px]" style={{ color: "var(--muted)" }}>
          <input type="checkbox" name="proc" value="1" defaultChecked={state.procedural} />
          mostrar procedimentais
        </label>
        <button type="submit" className="btn">filtrar</button>
      </form>
      {state.house === "senado" ? (
        <p className="text-[12px]" style={{ color: "var(--muted-2)" }}>
          O Senado não publica temas por proposição; o filtro de tema existe só para a Câmara.
        </p>
      ) : null}
    </div>
  );
}
