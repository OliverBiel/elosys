"""Vote kind/meaning classifier against real Câmara/Senado descriptions (copied verbatim, 2019-2026)."""

from __future__ import annotations

import pytest

from elosys.camara import vote_kind
from elosys.camara.vote_kind import (
    CLASSIFIER_VERSION,
    KINDS,
    MEANING_CODES,
    classify_camara,
    classify_senate,
    coverage_report,
)

# (vote id, descricao, ultimaAberturaVotacao_descricao, expected kind, expected meaning_code)
CAMARA_CASES = [
    # DVS: "Mantido/Suprimido o texto" -> Sim = keep the highlighted passage
    ("2183943-68", "Mantido o texto. Sim: 330; não: 91; abstenção: 3; total: 424.",
     "Votação do art. 22, inserido no art. 6º da MPV 852/18, com vista a sua supressão, objeto do destaque para "
     "votação em separado do Bloco Parlamentar PDT, SOLIDARIEDADE, PODE, PCdoB, PROS, PPS, AVANTE, PATRI, PV, DC - "
     "DTQ 12.",
     "highlight", "keep_highlighted_text"),
    ("1672576-177", "Suprimido o texto. Sim: 3; não: 414; abstenção: 1; total: 418.",
     "Votação da expressão \"...estabelecidos na legislação e regulamentação exclusivamente federal, cujos termos "
     "prevalecerão sobre quaisquer regras estaduais e ou municipais sobre o mesmo tema\" constante do art. 27, §7º "
     "da Lei 11771/08 inserido no art 4º da Subemenda, objeto do destaque para votação em separado da bancada do PPS "
     "- DTQ 33.",
     "highlight", "keep_highlighted_text"),
    ("2197160-57", "Mantido o texto, objeto do DTQ 1. Sim: 310; não: 119; total: 429.",
     "DTQ 1: PSOL: Destaque para votação em separado do §1º do art 167 da Constituição Federal, alterado pelo art. "
     "1º do Substitutivo à PEC nº 34/2019 (161, I).",
     "highlight", "keep_highlighted_text"),
    # PEC quorum (308): Sim won the plurality but the text still fell
    ("2196833-395", "Suprimido o texto. Sim: 307; não: 166; abstenção: 5; total: 478.",
     "Votação do DTQ 27: PL (2 turno): Destaque para Votação em Separado do Artigo 19 do Substitutivo do Relator, "
     "apresentado a PEC 45/2019 (art. 161, I)",
     "highlight", "keep_highlighted_text"),
    ("46249-305", "Mantido o texto. Sim: 271; não: 128; abstenção: 1; total: 400.",
     "Votação do DTQ 1: PT: Destaque do § 1º do Art. 3° do Substitutivo oferecido ao PL 6.299/2002. (art. 161, I)",
     "highlight", "keep_highlighted_text"),
    ("2557414-41", "Mantido o texto. Sim: 401; Não: 34; Total: 435.",
     "Votação do DTQ 4 (NOVO): Destaque para Votação em Separado do Art. 7º-D da Lei 14237/2021, com redação dada "
     "pelo art. 3º do PLV, apresentado a MPV 1313/2025 (161, I).",
     "highlight", "keep_highlighted_text"),
    # "Mantido o texto aprovado pela Câmara. Rejeitada a Emenda." = vote on a Senate amendment
    ("2187087-179", "Mantido o texto aprovado pela Câmara. Rejeitada a Emenda. Sim: 136; não: 174; total: 310.",
     "Votação do DTQ 1: NOVO: votação em separado da emenda supressiva de autoria do Senado Federal, apresentada ao "
     "PL 5029/19, que suprimiu o art. 44 da Lei 9096/95 alterada pelo art. 1º do PL 5029/2019 aprovado pela Câmara "
     "dos Deputados. (art. 161, II)",
     "amendment", "approve_senate_changes"),
    ("2187087-174", "Rejeitadas as emendas. Mantidos os dispositivos aprovados pela Câmara. Sim: 150; não: 252; "
     "total: 402.",
     "Votação, em globo, das emendas supressivas do Senado Federal ao Projeto de Lei nº 5.029, de 2019, com parecer "
     "pela rejeição.",
     "amendment", "approve_senate_changes"),
    # DVS where Sim is not "keep" (substitution/inclusion, count contradiction)
    ("2229308-71", "Mantido o texto. Sim: 124; não: 317; total: 441.",
     "Votação do DTQ 107: NOVO: destaque para a votação em separado do art. 7º da da Medida Provisória n.º "
     "905/2019, para fins de substituição do artigo 7º da emenda aglutinativa substitutiva global aprovada na "
     "Medida Provisória n.º 905/2019.",
     "highlight", None),
    ("2270894-132", "Mantido o texto. Sim: 54; não: 353; total: 407.",
     "Votação do DTQ 13: NOVO: destaque para a votação em separado da expressão “ficam extintos os benefícios "
     "tributários de que goza a ECT que não sejam extensíveis às demais empresas que explorem os serviços postais” "
     "constante do parágrafo único do artigo 23 do PL 591/21, com vistas à sua inclusão no Substitutivo, onde "
     "couber. (art. 161, IV).",
     "highlight", None),
    ("2261121-177", "Mantido o texto do Senado Federal. Sim: 286; não: 163; total: 449.",
     "Votação do DTQ 2: NOVO: Destaque para a votação em separado da supressão, no substitutivo do Senado Federal, "
     "da alínea f, do inciso I, do § 3º, do art. 7º do texto da Câmara dos Deputados do PL 3472/20 para o seu "
     "restabelecimento. (art. 161, I)",
     "highlight", None),
    # Quoted law text ("incorporados") is not the destaque's aim; reject-and-restore still suppresses
    ("2454495-154", "Mantido o texto. Sim: 318; Não: 144; Abstenção: 2; Total: 464.",
     "Votação do DTQ 5 (PL, Federação Brasil da Esperança - Fe Brasil, UNIÃO, PP, PSD, REPUBLICANOS, MDB, PDT, "
     "Federação PSDB CIDADANIA, PSB, PODE): Destaque para Votação em Separado da expressão \"considerados os créditos "
     "suplementares e especiais incorporados ao limite de despesa de 2025, e\", do §18-A, art. 165, da CF/88, "
     "constante no artigo 1º do substitutivo, apresentado a PEC 66/2023. (Fase 1 - CD) (161, I)",
     "highlight", "keep_highlighted_text"),
    ("2242140-138", "Mantido o texto. Sim: 282; não: 153; total: 435.",
     "Votação do DTQ 2: PT: destaque do art. 6º-B da Lei nº 10.260/2001, na redação dada pelo art. 1º do "
     "Substitutivo do Senado Federal, com vistas à sua rejeição e ao consequente restabelecimento do seu equivalente "
     "no texto aprovado pela Câmara dos Deputados. (art. 161, I)", "highlight", "keep_highlighted_text"),
    # Requerimentos -> procedural, Sim = approve the request
    ("2179189-45", "Rejeitado o Requerimento. Sim: 21; Não: 238; Abstenção: 1. Total: 260.", "",
     "procedural", "approve_request"),
    ("2557414-24", "Rejeitado o Requerimento. Sim: 22; Não: 362; Total: 384.", "",
     "procedural", "approve_request"),
    ("2300880-88", "Rejeitado o Requerimento da Bancada do PSol, que solicita o adiamento da votação por cinco "
     "sessões. Sim: 14; não: 384; total: 398.", "Votação em primeiro turno.",
     "procedural", "approve_request"),
    ("2192459-612", "Rejeitados os Requerimentos de votação parcelada. Sim: 43; não: 299; abstenção: 2; total: 344.",
     "Votação da Consulta formulada pelo Presidente, sobre realização de votação parcelada da matéria, em face do "
     "encaminhamento, à Mesa, de mais de cinco requerimentos nesse sentido.",
     "procedural", "approve_request"),
    ("2229308-53", "Rejeitada a votação parcelada. Sim: 13; não: 247; abstenção: 1; total: 261.",
     "Votação em turno único.", "procedural", "approve_request"),
    ("2192300-10", "Aprovado o Requerimento de Urgência (Art. 155 do RICD). Sim: 367; não: 57; abstenção: 3; "
     "total: 427.", "", "procedural", "approve_urgency"),
    ("2558628-8", "Aprovado, por unanimidade, o Requerimento de Urgência (Art. 155 do RICD). Sim: 310; Não: 115; "
     "Total: 425.", "", "procedural", "approve_urgency"),
    ("2601223-8", "Aprovado o Requerimento de Urgência (Art. 155 do RICD). Sim: 337; Não: 19; Total: 356.", "",
     "procedural", "approve_urgency"),
    # Recurso -> procedural; must win over the "emenda" mentioned inside it
    ("2192459-591", "Rejeitado o Recurso. Sim: 80; não: 199; total: 279.",
     "Votação do Recurso contra o indeferimento da admissibilidade dos Requerimentos de discussão por partes.",
     "procedural", "approve_appeal"),
    ("2601794-5", "Rejeitado o Recurso nº 2, de 2026, contra parecer terminativo da Comissão de Constituição e "
     "Justiça e de Cidadania à Emenda de Plenário nº 3 oferecida ao Projeto de Lei nº 1.743, de 2024. Sim: 151; "
     "Não: 244; Abstenção: 3; Total: 398.", "", "procedural", "approve_appeal"),
    # Preference / admissibility of simple destaques -> procedural
    ("2213191-60", "Rejeitada a preferência. Sim: 103; não: 303; total: 406.",
     "Votação do DTQ 19: PSOL: Destaque para votação da Emenda 116 apresentada à MPV 890/2019, com preferência "
     "sobre o PLV 25/2019.", "procedural", "approve_preference"),
    ("2267745-99", "Rejeitado o destaque de preferência. Sim: 138; não: 307; total: 445.",
     "Votação do DTQ 11: PT - destaque para que a Emenda de Plenário n. 11, apresentada ao PL 5.595/2020, seja "
     "votada com preferência em relação ao texto com precedência regimental", "procedural", "approve_preference"),
    ("2351506-102", "Aprovada a preferência. Sim: 467; Não: 4; Abstenção: 1; Total: 472.",
     "Votação do DTQ 2 (UNIÃO, PP, PSD, REPUBLICANOS, MDB, Federação PSDB CIDADANIA, PODE): Destaque de Preferência "
     "para a Emenda Aglutinativa de Plenário nº 3.", "procedural", "approve_preference"),
    ("1672576-133", "Rejeitada a admissibilidade dos destaques simples. Sim: 36; não: 342; total: 378.",
     "Votação, em globo, da admissibilidade dos destaques simples.", "procedural", "admit_highlights"),
    # "Rejeitado o destaque" -> Sim = support the change asked by the destaque
    ("2213191-67", "Rejeitado o destaque. Sim: 103; não: 288; abstenção: 1; total: 392.",
     "Votação do DTQ 14: PCdoB: Destaque do art. 1º da Emenda 50 para substituir o art. 1º do PLV apresentado à MPV "
     "890/2019 e por consequência a alteração devida ao art. 6º do PLV.", "highlight", "approve_highlight"),
    ("2199763-91", "Rejeitado o DTQ 5. Sim: 4; não: 439; total: 443.",
     "Votação do DTQ 5: PSC: ARTIGO 18 DO PROJETO DE LEI DE CONVERSÃO À MP 881/19, QUE INCLUI ARTIGO 82-A À LEI "
     "11.101/2005, COM FIM DE REESTABELECÊ-LO NA EMENDA AGLUTINATIVA - ART 161, I.", "highlight", "approve_highlight"),
    # Amendments
    ("2216799-67", "Rejeitada a Emenda de Plenário nº 1. Sim: 103; não: 320; total: 423.",
     "Votação da Emenda de Plenário nº 1, com parecer pela rejeição, resalvados os destaques.",
     "amendment", "approve_amendment"),
    ("2600838-46", "Rejeitada a Emenda de Plenário nº 68. Sim: 105; Não: 232; Total: 337.",
     "Votação do DTQ 10 (Fdr PSOL-REDE): Destaque da Emenda de Plenário n. 68 (161, II).",
     "amendment", "approve_amendment"),
    ("2192459-649", "Aprovada a Emenda Aglutinativa nº 5. Sim: 344; não: 132; abstenção: 15; total: 491.",
     "Votação da EMA 5: DEM: Emenda Aglutinativa nº 5.", "amendment", "approve_amendment"),
    ("2193540-135", "Rejeitada a Emenda do Senado Federal nº 1. Sim: 68; não: 281: abstenção: 4; total: 353.",
     "Votação da Emenda do Senado Federal nº 1, objeto do destaque para votação em separado da bancada do PT - "
     "DTQ 3.", "amendment", "approve_senate_changes"),
    ("257161-454", "Aprovadas.\r\n Sim: 267; Não: 116; Total: 383.",
     "Votação das Emendas do Senado Federal ao Projeto de Lei nº 3.729, de 2004, com parecer pela aprovação, com "
     "exceção do inciso IV do art. 8º da Emenda nº 9, com o restabelecimento dos incisos IX e XIII aprovados na "
     "Câmara dos Deputados, e da expressão \"em até 24 (vinte e quatro) horas\" da Emenda nº 32, ressalvados os "
     "destaques.", "amendment", "approve_senate_changes"),
    # Main text (merit)
    ("2265123-52", "Aprovado o Projeto de Lei nº 3.877, de 2020. Sim: 425; não: 10; total: 435.",
     "Votação em turno único.", "merito", "approve_text"),
    ("2309053-94", "Aprovada a Subemenda Substitutiva Global ao Projeto de Lei nº 4.188, de 2021, adotada pelo "
     "Relator da Comissão de Constituição e Justiça e de Cidadania, ressalvados os destaques. Sim: 260; não: 111; "
     "total: 371.", "Votação em turno único.", "merito", "approve_text"),
    ("2260971-89", "Aprovada a Subemenda Substitutiva oferecida ao Projeto de Lei de Conversão, ressalvados os "
     "destaques. Sim: 367; não: 7; abstenção: 1; total: 375.", "Votação, quanto ao mérito, em turno único.",
     "merito", "approve_text"),
    ("2242888-71", "Aprovada a Medida Provisória nº 945, de 2020, na forma do Projeto de Lei de Conversão, "
     "ressalvados os destaques. Sim: 408; não: 67; total: 475.", "Votação, quanto ao mérito, em turno único.",
     "merito", "approve_text"),
    ("2270902-97", "Rejeitada a Medida Provisória nº 1.033, de 2021. Sim: 116; não: 343; total: 459.",
     "Votação em turno único.", "merito", "approve_text"),
    # Rejecting the committee substitute is not rejecting the bill (2397890-55: original 407x6)
    ("2397890-54", "Rejeitado o Substitutivo ao Projeto de Lei Complementar nº 223, de 2023, da Comissão de "
     "Indústria, Comércio e Serviços. Sim: 34; Não: 356; Abstenção: 1; Total: 391.", "Votação em turno único.",
     "merito", "approve_committee_substitute"),
    ("2193564-57", "Aprovado o Substitutivo ao Projeto de Lei Complementar nº 55, de 2019, adotado pelo Relator da "
     "Comissão de Finanças e Tributação. Sim: 382; não: 6; abstenção: 1; total: 389.", "Votação em turno único.",
     "merito", "approve_committee_substitute"),
    ("2229308-64", "Aprovada a Emenda Aglutinativa Global nº 1. Sim: 322; não: 153; abstenção: 2; total: 477.",
     "Votação da EMA 1: Bloco PP: Emenda Aglutinativa Global nº 1.", "merito", "approve_text"),
    ("2293798-23", "Aprovado o Projeto de Resolução nº 57, de 2021, oferecido pelo Conselho de Ética e Decoro "
     "Parlamentar. Sim: 437; Não: 7; Abstenção: 12; Total: 456.", "Votação em turno único.", "merito", "approve_text"),
    ("2261121-172", "Aprovado o Substitutivo do Senado Federal ao Projeto de Lei nº 4.372, de 2020, ressalvados os "
     "Destaques. Sim: 470; não: 15; abstenção; 1; total: 486.", "Votação, quanto ao mérito, em turno único.",
     "merito", "approve_senate_changes"),
    ("2430143-142", "Rejeitados os dispositivos do Substitutivo do Senado Federal ao Projeto de Lei Complementar nº "
     "68, de 2024, com parecer pela rejeição. Sim: 18; Não: 328; Abstenção: 7; Total: 353.",
     "Votação dos dispositivos do Substitutivo do Senado Federal ao Projeto de Lei Complementar nº 68, de 2024, com "
     "parecer pela rejeição.", "merito", "approve_senate_changes"),
    # "art." inside the object must not stop the match
    ("2237876-177", "Rejeitados os §§ 6º e 7º do art. 6º do Substitutivo do Senado Federal ao Projeto de Lei "
     "Complementar nº 11, de 2020, ressalvados os destaques. Sim: 128; não: 290; abstenção: 1; total: 419.",
     "Votação dos §§ 6º e 7º do art. 6º do Substitutivo do Senado Federal ao Projeto de Lei Complementar nº 11, de "
     "2020, com parecer pela rejeição, ressalvados os destaques.", "merito", "approve_senate_changes"),
    ("2238434-104", "Rejeitado o Art. 15 da Emenda de Plenário nº 4. Sim: 38; não: 376; abstenção: 1; total: 415.",
     "Votação do DTQ 9: Bloco UNIÃO (SD): Art. 15 da Emenda de Plenário nº 4. (art. 161, II)",
     "amendment", "approve_amendment"),
    # Senate amendment named only in the opening; "n°" is a degree sign, not "º"
    ("2267686-123", "Aprovada a Emenda n° 1. Sim: 309; não: 108; abstenção:1; total: 418.",
     "Votação do DTQ 1: PT: Emenda do Senado Federal n. 1 (art. 161, II)", "amendment", "approve_senate_changes"),
    ("2242887-152", "Rejeitada a Emenda de nº 2. Sim: 157; não: 288; abstenção: 1; total: 446.",
     "Votação da Emenda do Senado Federal nº 2 ao Projeto de Lei de Conversão nº 20, de 2020, com parecer pela "
     "rejeição, ressalvados os destaques.", "amendment", "approve_senate_changes"),
    ("2306513-116", "Aprovada a parte da Emenda nº 3. Sim: 285; Não: 116; Abstenção: 3; Total: 404.",
     "Votação do DTQ 11 (NOVO): Destaque de parte da Emenda nº 3 do Senado Federal, na parte em que inclui os §§ 10 "
     "e 11 ao art. 148-A do Código de Trânsito Brasileiro, apresentado ao PL 3965/2021 (161, II).",
     "amendment", "approve_senate_changes"),
    # Two-turn non-PEC (treaty with constitutional status) is NOT a PEC turn
    ("2164132-54", "Aprovado, em primeiro turno, nos termos do § 3º do art. 5º da Constituição Federal, o Projeto de "
     "Decreto Legislativo nº 861, de 2017. Sim: 414; não: 39; abstenção: 4; total: 457.",
     "Votação, em primeiro turno, nos termos do § 3º do art. 5º da Constituição Federal, do Projeto de Decreto "
     "Legislativo nº 861, de 2017.", "merito", "approve_text"),
    # PEC turns
    ("2158716-112", "Aprovada, em primeiro turno, a Proposta de Emenda à Constituição nº 372 de 2017. Sim: 402; "
     "não: 8; total: 410.", "Votação do DTQ 1: PL: Preferência para votação da PEC 372/2017 em relação ao "
     "substitutivo da Comissão Especial à PEC 308/2004 (art. 161, IV).", "pec_turn", "pec_first_turn"),
    ("2197160-52", "Aprovado, em primeiro turno, o Substitutivo da Comissão Especial à Proposta de Emenda à "
     "Constituição nº 34, de 2019, ressalvados os destaques. Sim: 364; não: 2; total: 366.",
     "Votação em primeiro turno.", "pec_turn", "pec_first_turn"),
    # PEC substitute whose turn is only in the opening
    ("2275537-128", "Rejeitado o Substitutivo à PEC nº 5, de 2021, adotado pelo relator da Comissão Especial, "
     "ressalvados os destaques. Sim: 297; não: 182; abstenção: 4; total: 483.", "Votação em primeiro turno.",
     "pec_turn", "pec_first_turn"),
    ("2220292-229", "Rejeitada, em primeiro turno, a Proposta de Emenda à Constituição nº 135, de 2019. Sim: 229; "
     "não: 218; abstenção: 1; total: 448.", "Votação em primeiro turno.", "pec_turn", "pec_first_turn"),
    ("2197160-67", "Aprovada, em segundo turno, a Proposta de Emenda à Constituição n° 34, de 2019. Sim: 378; "
     "Não: 4; Total: 382.", "Votação em segundo turno.", "pec_turn", "pec_second_turn"),
    # Final wording
    ("2267686-131", "Aprovada a Redação Final assinada pelo Relator, Dep. Danilo Forte (PSDB-CE). Sim: 350; não: 83; "
     "total: 433.", "Votação da Redação Final.", "final_wording", "approve_final_wording"),
    ("516111-261", "Aprovada a Emenda de Redação nº 1. Sim: 339. Total: 339.", "Votação da Emenda de Redação nº 1.",
     "final_wording", "approve_wording_amendment"),
    # MP preliminary appreciation
    ("2188301-45", "Aprovado, em apreciação preliminar, o Parecer da Comissão Mista, na parte em que manifesta "
     "opinião favorável quanto ao atendimento dos pressupostos constitucionais de relevância e urgência e de sua "
     "adequação financeira e orçamentária, nos termos do artigo 8º da Resolução nº 01, de 2002-CN. Sim: 321; não: 5; "
     "abstenção: 1; total: 327.", "Votação preliminar em turno único.", "procedural", "mp_meets_requirements"),
    # "pelo não atendimento" was approved yet MP 905/2019 went on to merit votes: wording unreliable
    ("2229308-56", "Aprovado, em apreciação preliminar, o Parecer da Comissão Mista, na parte em que manifesta "
     "opinião pelo não atendimento dos pressupostos constitucionais de relevância e urgência e pela inadequação "
     "financeira e orçamentária, nos termos do artigo 8º da Resolução nº 01, de 2002-CN. Sim: 346; não: 23; "
     "abstenção: 2; total: 371.", "Votação preliminar em turno único.", "procedural", None),
    # Committee opinions: approve_opinion only when the description states the conclusion
    ("2236326-19", "Aprovado o parecer da Comissão de Constituição e Justiça e de Cidadania à Comunicação de Medida "
     "Cautelar nº 1/2020. Sim: 233; não: 170; abstenção: 7; total: 410.",
     "Votação do Parecer da Comissão de Constituição e Justiça e de Cidadania à Comunicação de Medida Cautelar nº "
     "1/2020 - Ofício nº 7.007/2019, do Supremo Tribunal Federal.", "other", None),
    ("2270800-34", "Aprovado o Parecer adotado pelo Relator da Comissão de Constituição e Justiça e de Cidadania pela "
     "admissibilidade da Proposta de Emenda à Constituição nº 3, de 2021. Sim: 304; não: 154; abstenção: 2; total: "
     "460.", "Votação, em turno único, do Parecer de admissibilidade da Proposta de Emenda à Constituição nº 3, de "
     "2021.", "other", "approve_opinion"),
    ("2525122-81", "Rejeitado o Parecer da Comissão de Constituição e Justiça e de Cidadania à Representação nº 2, "
     "de 2025, que conclui pela procedência da representação e pela perda do mandato da Dep. Carla Zambelli. Sim: "
     "227; Não: 170; Abstenção: 10; Total: 407.", "Votação do Parecer da Comissão de Constituição e Justiça e de "
     "Cidadania à Representação nº 2, de 2025, que conclui pela procedência da representação e pela perda do "
     "mandato da Dep. Carla Zambelli.", "other", "approve_opinion"),
    ("2494565-52", "Aprovado o parecer da Comissão de Constituição e Justiça e de Cidadania à Sustação de Andamento "
     "de Ação Penal nº 1, de 2025, pela sustação do andamento da Ação Penal. . Sim: 315; Não: 143; Abstenção: 4; "
     "Total: 462.", "Votação do parecer da Comissão de Constituição e Justiça e de Cidadania à Sustação de Andamento "
     "de Ação Penal nº 1, de 2025, pela sustação do andamento da Ação Penal contida na Petição nº 12.100, em curso no "
     "Supremo Tribunal Federal, em relação a todos os crimes imputados.", "other", "approve_opinion"),
    # Nominations / secret elections
    ("2645346-18", "Aprovado o Projeto de Decreto Legislativo nº 995, de 2026 (Aprova a indicação do Sr. Rodrigo "
     "Otávio Soares Pacheco para o cargo de Ministro do Tribunal de Contas da União). Sim: 404; Não: 61; Abstenção: "
     "1; Total: 466.", "Votação secreta em turno único.", "nomination", "approve_nomination"),
    # Same nomination with "(Aprova a indicação...)" after "nº 1.118" and after the counts
    ("2312515-18", "Aprovado o Projeto de Decreto Legislativo nº 1.118 de 2021. Sim: 322; não: 18; total: 348 "
     "(Aprova a indicação do Sr. Antonio Augusto Junho Anastasia para o cargo de Ministro do Tribunal de Contas da "
     "União).", "Votação secreta em turno único.", "nomination", "approve_nomination"),
    ("2576389-4", "Votos: \r\nSra. Daiane Nogueira de Lira: \r\nSim: 388;\r\nNão: 22;\r\nAbstenção: 11;\r\nTotal: "
     "421.", "Votação secreta em turno único.", "nomination", None),
    # Unknown / uninformative -> ('other', None)
    ("2210523-38", "Rejeitada. Sim: 38; não: 285; abstenção: 1; total: 324.",
     "Votação da consulta ao plenário sobre o indeferimento do Requerimento de discussão artigo por artigo.",
     "other", None),
    ("2613731-65", "Resultado. Sim: 182; Não: 182; Abstenção: 2; Total: 366.",
     "Votação do DTQ 2 (PL): Destaque de Emenda de Plenário n. 2. (161, II).", "other", None),
    ("2236326-12", "Fica estabelecida a necessidade de quórum de maioria absoluta para o referendo da medida "
     "cautelar nº 01, de 2020. Sim: 407; não: 5; abstenção: 1; total: 413.",
     "Votação preliminar sobre o quórum de maioria absoluta necessário à manutenção da Medida Cautelar nº 01, de "
     "2020.", "other", None),
    ("2318217-81", "Rejeitada a inclusão. Sim: 119; Não: 310; Abstenção: 1; Total: 430.",
     "Votação do DTQ 3 (Fdr PSOL-REDE): Destaque de Preferência do art. 15, do texto original oriundo do Senado "
     "Federal, para que seja incluído no Substitutivo, apresentado ao PLP 235/2019 (161, IV).", "other", None),
    ("2242344-15", "Aprovada a Minuta de Proposição Legislativa nº 1, de 2020, para dar cumprimento ao quórum de "
     "iniciativa previsto no art. 60 da Constituição Federal à Proposta de Emenda à Constituição que \"Institui "
     "Regime Extraordinário Fiscal, Financeiro e de Contratações para enfrentamento da Calamidade Pública Nacional "
     "decorrente de Pandemia Internacional e dá outras providências\". Sim: 321; não: 14; total: 335.",
     "Votação da Minuta de Proposição Legislativa nº 1, de 2020.", "other", None),
    ("2249946-35", "Aprovado os artigos 1º, 2º, 3º, 5º, 6º, 7º, 8º e seus incisos I e II, 9º, 10, 11 e 12, com "
     "parecer pela aprovação, ressalvados os destaques. Sim: 481; não: 4; total: 485.",
     "Votação, em primeiro turno, dos artigos 1º, 2º, 3º, 5º, 6º, 7º, 8º e seus incisos I e II, 9º, 10, 11 e 12, "
     "com parecer pela aprovação, ressalvados os destaques.", "other", None),
]


@pytest.mark.parametrize(("vote_id", "desc", "opening", "kind", "meaning"), CAMARA_CASES,
                         ids=[c[0] for c in CAMARA_CASES])
def test_camara_real_descriptions(vote_id, desc, opening, kind, meaning):
    got = classify_camara(desc, opening)
    assert (got.kind, got.meaning_code) == (kind, meaning)
    assert got.basis  # always says which rule decided


# (codigoSessaoVotacao, descricaoVotacao, expected kind, expected meaning_code)
SENATE_CASES = [
    (7007, "Votação nominal do PLP nº 192/2023, nos termos dos pareceres, com adequações redacionais do Relator.",
     "merito", "approve_text"),
    (6079, "Votação do PL 1066/2020", "merito", "approve_text"),
    (6704, "Votação nominal do PLV nº 12/2023 e Pressupostos Constitucionais da MPV nº 1.154/2023, nos termos do "
     "Parecer.", "merito", "approve_text"),
    (6679, "Votação nominal da Emenda nº 48 (Substitutivo) ao Projeto de Lei Complementar nº 245, de 2019, nos "
     "termos do Parecer.", "merito", "approve_text"),
    (6905, "Votação nominal do Substitutivo da Câmara dos Deputados ao Projeto de Lei Complementar nº 121/2024, nos "
     "termos do parecer.", "merito", "approve_chamber_changes"),
    (6291, "Votação nominal do Substitutivo da Câmara dos Deputados", "merito", "approve_chamber_changes"),
    (5960, "PEC nº 57, de 2016, com as Emendas nºs 1 a 6 - CCJ, nos termos do Parecer (1º Turno)",
     "pec_turn", "pec_first_turn"),
    (5985, "Proposta de Emenda à Constituição nº 17, de 2019 (2º Turno)", "pec_turn", "pec_second_turn"),
    (6289, "Votação da Proposta, em segundo turno, nos termos da Emenda nº 194 (Substitutivo). (Dispõe sobre medidas "
     "permanentes e emergenciais de controle do crescimento das despesas obrigatórias e de reequilíbrio fiscal no "
     "âmbito dos Orçamentos Fiscal e da Seguridade Social da União, e dá outras providências.)",
     "pec_turn", "pec_second_turn"),
    (7009, "Votação nominal da Proposta de Emenda à Constituição nº 66, de 2023 (2º turno).",
     "pec_turn", "pec_second_turn"),
    (7008, "Votação nominal da Expressão do §19 do art. 165 da Constituição, constante do art. 1º da PEC nº "
     "66/2023, destacada.", "highlight", "keep_highlighted_text"),
    # A passage of an amendment that is not in the approved text: "Rejeitado o §11 do art. 37 ...,
    # constante da Emenda nº 1, destacada ... Sim - 22, Não - 48" (Sim = add it, not keep it).
    (6915, "Votação nominal do §11 do art. 37 da CF, constante da Emenda nº1 à Proposta de Emenda à Constituição "
     "54/2024, destacado.", "amendment", "approve_amendment"),
    # Destaques of amendments in the plural, in parts, and of a subemenda: Sim = approve them.
    # 6166 informe: "Aprovadas, em globo, as Emendas nºs 11 e 15 – PLEN, destacadas ... Sim - 51, Não - 14".
    (6166, "Votação das Emendas nºs 11 e 15 - PLEN ao Projeto de Lei nº 2.824, de 2020, destacadas.",
     "amendment", "approve_amendment"),
    # 6335 informe: "Aprovados, em conjunto, ... a supressão do parágrafo único do art. 2º ... proposta pela
    # Emenda nº 91-PLEN ... Sim 50, Não 18": here Sim removed a passage, "Sim = manter" was inverted.
    (6335, "Votação nominal conjunta de parte da Emenda nº 91 e da primeira parte da Emenda nº 96, destacadas.",
     "amendment", "approve_amendment"),
    # 6881 informe: "Aprovada a subemenda ao Substitutivo do Relator, ... que reestabelece o art. 11 ... Sim - 64".
    (6881, "Votação nominal da Subemenda ao Substitutivo do Relator ao PLP nº 175/2024, destacada.",
     "amendment", "approve_amendment"),
    # Passages of the substitute (approved "ressalvados os destaques") stay "Sim = keep".
    (6663, "Votação nominal da expressão “de 2024` constante do art. 3º e par. único da Emenda 55(Subst.) à PEC "
     "32/2022, destacada.", "highlight", "keep_highlighted_text"),
    (6664, "Votação nominal do art. 121, caput e par. único do ADCT, na redação dada pelo art. 2º da Emenda "
     "55(Subst.), destacado.", "highlight", "keep_highlighted_text"),
    (6161, "Votação do Art. 12º do Projeto de Lei de Conversão nº 30, de 2020, destacado.",
     "highlight", "keep_highlighted_text"),
    # Substitutes written "(Subst.)" or "Emenda 15 (Substitutivo)": the main text, not an amendment.
    # 6147 informe: "Aprovado o Substitutivo – Emenda nº 153-PLEN ... (Sim 44, Não 32 ...)".
    (6147, "Votação da Emenda 153 (Subst.) ao PL 2630/2020, com os ajustes propostos pelo Relator, ressalvados os "
     "destaques.", "merito", "approve_text"),
    (6099, "Votação da Emenda nº 46 - PLEN (Subst.) ao PLP nº 39, de 2020, ressalvados os destaques, com adendo do "
     "Relator.", "merito", "approve_text"),
    (6730, "Votação nominal da Emenda 15 (Substitutivo) ao PLP 41/2019, com as Emendas 16 e 22 a 29, nos termos do "
     "Parecer de Plenário.", "merito", "approve_text"),
    (5957, "Emenda nº 13-Plen ao Projeto de Lei nº 1321, de 2019", "amendment", "approve_amendment"),
    (6283, "Votação nominal da Emenda nº 175, destacada.", "amendment", "approve_amendment"),
    (6817, "Votação nominal da Emenda nº 48 à Medida Provisória nº 1.185, de 2023, destacada.",
     "amendment", "approve_amendment"),
    (6293, "Votação nominal da Emenda 4, destacada.", "amendment", "approve_amendment"),
    (6281, "Rejeitado o RQS 717/2021, de destaque do art. 3º do Substitutivo.", "procedural", "approve_request"),
    (6796, "Votação nominal do Requerimento nº 1.103, de 2023 - Solicita o encerramento da discussão do PL nº "
     "3.626/2023.", "procedural", "approve_request"),
    (7081, "Votação nominal da Mensagem nº 7, de 2026 - Jorge Rodrigo Araújo Messias (STF).",
     "nomination", "approve_nomination"),
    (5987, "Ofício \"S\" nº 30, de 2019 - Emmanoel Pereira (CNJ)", "nomination", "approve_nomination"),
    (6884, "Votação nominal Mensagem nº 33, de 2024 - Daniella Xavier Cesar (Senegal e Gâmbia).",
     "nomination", "approve_nomination"),
    (6022, "Escolha do Senhor Sérgio Costa Ravagnani para exercer o cargo de Conselheiro do Conselho Administrativo "
     "de Defesa Econômica - CADE, do Ministério da Justiça e Segurança Pública.", "nomination", "approve_nomination"),
    (6038, "Indicação do Desembargador LUIZ FERNANDO TOMASI KEPPEN, para compor o Conselho Nacional de Justiça - "
     "CNJ, em vaga destinada ao Supremo Tribunal Federal.", "nomination", "approve_nomination"),
    (6049, "Submete à apreciação do Senado Federal a escolha do Senhor Fábio Kanczuk, para exercer o cargo de "
     "Diretor do Banco Central do Brasil.", "nomination", "approve_nomination"),
    # Topic-only / ementa-only descriptions: cannot tell what Sim means -> None
    (6032, "Idade mínima para mulheres.", "other", None),
    (6025, "Supressão da expressão \"no âmbito da União\" do § 1º - B do art. 149 da Constituição Federal, com a "
     "redação do art. 1º da Proposta.", "other", None),
    (5962, "Estabelece a organização básica dos órgãos da Presidência da República e dos Ministérios.", "other", None),
    (6295, "Votação nominal.", "other", None),
    # 2019 nomination wordings
    (6009, "Submetendo à apreciação do Senado a indicação do Senhor Oswaldo D'Albuquerque Lima Neto para compor o "
     "Conselho Nacional do Ministério Público - CNMP, no Biênio 2019/2021, na vaga destinada ao Ministério Público "
     "dos Estados.", "nomination", "approve_nomination"),
    (5944, "Submete à consideração do Senado Federal, nos termos do o art. 84, inciso XIV, combinado com o art. 52, "
     "inciso III, alínea \"d\", da Constituição, o nome do Senhor BRUNO SERRA FERNANDES, para exercer o cargo de "
     "Diretor do Banco Central do Brasil.", "nomination", "approve_nomination"),
    (6182, "Votação da Mensagem nº 86, de 2019 - Nestor José Forster Junior (Estados Unidos da América).",
     "nomination", "approve_nomination"),
    # Requests without "nominal" and in the plural
    (6176, "Votação do Requerimento de adiamento do Projeto de Lei nº 3.267, de 2019.",
     "procedural", "approve_request"),
    (6264, "Votação do Requerimento de Retirada de Pauta do PL nº 2.963, de 2019.", "procedural", "approve_request"),
    (6171, "Votação nominal dos Requerimento nºs 2102 e 2104, de 2020, de retirada de pauta.",
     "procedural", "approve_request"),
    # "1°" typed with a degree sign
    (6608, "Votação nominal da Proposta de Emenda à Constituição nº 47, de 2021, nos termos do Parecer (1° Turno).",
     "pec_turn", "pec_first_turn"),
    # Substitute of a PEC in turn: not an ordinary amendment
    (6374, "Votação nominal da Emenda nº 1 (substitutivo) à PEC 18/2021, em primeiro turno.",
     "pec_turn", "pec_first_turn"),
    # PEC turn on something other than the PEC itself
    (6092, "Votação da Redação proposta pelo Relator, em substituição às Emendas nºs 34 e 45 à PEC nº 10/2020 (1º "
     "Turno)", "pec_turn", None),
    # One roll call for three PDLs was a secret election among candidates: no yes/no meaning
    (6486, "Votação nominal dos Projetos de Decreto Legislativo nºs 1.118, 1.119 e 1.120, de 2021.", "other", None),
]


@pytest.mark.parametrize(("code", "desc", "kind", "meaning"), SENATE_CASES, ids=[str(c[0]) for c in SENATE_CASES])
def test_senate_real_descriptions(code, desc, kind, meaning):
    got = classify_senate(desc)
    assert (got.kind, got.meaning_code) == (kind, meaning)
    assert got.basis


# (codigoSessaoVotacao, identificacao, descricaoVotacao, expected kind, expected meaning_code)
SENATE_IDENTIFIED_CASES = [
    (6328, "PEC 6/2018", "Votação nominal, em 1º Turno", "pec_turn", "pec_first_turn"),
    (6773, "PEC 45/2019", "Votação nominal da Emenda nº 803 (Substitutivo) e das Emendas e Subemendas de Plenário, "
     "em primeiro turno, nos termos dos pareceres, ressalvados os destaques.", "pec_turn", "pec_first_turn"),
    # PEC substitute without its turn: it is the PEC, but which turn is unknown
    (6282, "PEC 186/2019", "Votação nominal da Emenda nº 194 (Substitutivo)", "pec_turn", None),
    (6679, "PLP 245/2019", "Votação nominal da Emenda nº 48 (Substitutivo) ao Projeto de Lei Complementar nº 245, de "
     "2019, nos termos do Parecer.", "merito", "approve_text"),
]


@pytest.mark.parametrize(("code", "ident", "desc", "kind", "meaning"), SENATE_IDENTIFIED_CASES,
                         ids=[str(c[0]) for c in SENATE_IDENTIFIED_CASES])
def test_senate_identification_detects_pec(code, ident, desc, kind, meaning):
    got = classify_senate(desc, identification=ident)
    assert (got.kind, got.meaning_code) == (kind, meaning)


def test_senate_without_identification_keeps_v1_behavior():
    assert classify_senate("Votação nominal, em 1º Turno") == ("other", None, "no_rule")
    assert classify_senate("Votação nominal da Emenda nº 194 (Substitutivo)").meaning_code == "approve_text"


@pytest.mark.parametrize("desc", ["Mensagem nº 7, de 2021- Vinicius Clay Araujo Gonçalves (ANCINE)",
                                  "Votação nominal."])
@pytest.mark.parametrize("ident", ["MSF 7/2021", "OFS 30/2019"])
def test_secret_msf_ofs_vote_is_an_authority_confirmation(desc, ident):
    got = classify_senate(desc, ident, is_secret=True)
    assert tuple(got) == ("nomination", "approve_nomination", "senate_secret_msf_ofs")
    # The same open vote keeps the description-based result
    assert tuple(classify_senate(desc, ident)) == ("other", None, "no_rule")


def test_secret_flag_needs_msf_ofs_and_never_overrides_a_classified_vote():
    assert classify_senate("Votação nominal.", "PL 1/2021", is_secret=True) == ("other", None, "no_rule")
    got = classify_senate("Votação nominal do PL 1066/2020", "MSF 1/2020", is_secret=True)
    assert (got.kind, got.meaning_code) == ("merito", "approve_text")


# Real destaques on a Câmara substitute (senate_vote, identificacao + descricaoVotacao). 6174 informe:
# "Rejeitado o art. 17-E ... Sim - 52; Não - 22 ... Mantido o texto apresentado pelo relator".
SENATE_ON_CHAMBER_SUBSTITUTE = [
    (6113, "PL 1179/2020 (Substitutivo-CD)", "Votação do art. 18 do Substitutivo da Câmara dos Deputados ao Projeto "
     "de Lei nº 1.179, de 2020, destacado."),
    (6174, "PL 550/2019 (Substitutivo-CD)", "Votação do Art. 17 - E da Lei nº 12.334, de 2010, previsto no art. 4º "
     "do Substitutivo, destacado."),
    (6677, "PL 1825/2022 (Substitutivo-CD)", "Votação nominal das expressões contidas no PL 1.825/2022 (Substitutivo "
     "da CD ao PLS 68/2017), destacadas."),
    (6862, "SCD 6/2016", "Votação nominal do § 5º do art. 29 do Substitutivo da Câmara dos Deputados nº 6, de 2016, "
     "ao PLS nº 135/2010, destacado."),
    (6898, "PL 576/2021 (Substitutivo-CD)", "Votação nominal do Art. 21 do texto consolidado pelo parecer ao "
     "Substitutivo da CD ao PL nº 576/2021, destacado."),
]


@pytest.mark.parametrize(("code", "ident", "desc"), SENATE_ON_CHAMBER_SUBSTITUTE,
                         ids=[str(c[0]) for c in SENATE_ON_CHAMBER_SUBSTITUTE])
def test_senate_destaque_on_chamber_substitute_has_no_meaning(code, ident, desc):
    got = classify_senate(desc, identification=ident)
    assert tuple(got) == ("highlight", None, "senate_highlight_on_chamber_substitute")


def test_senate_destaque_on_plc_substitute_keeps_its_meaning():
    # 6837: a Senado substitute to a Câmara bill (PLC), not a Câmara substitute.
    got = classify_senate("Votação Nominal do Capítulo XI do Tìtulo I do Substitutivo ao Projeto de Lei da Câmara "
                          "nº 29, de 2017, destacado.", identification="PLC 29/2017")
    assert tuple(got) == ("highlight", "keep_highlighted_text", "senate_highlighted_text")


def test_senate_chamber_substitute_itself_keeps_its_meaning():
    # The vote on the whole Câmara substitute (not a destaque) is unaffected (6291).
    got = classify_senate("Votação nominal do Substitutivo da Câmara dos Deputados", "PL 1/2021 (Substitutivo-CD)")
    assert got.meaning_code == "approve_chamber_changes"


def test_senate_destacado_passage_naming_substitute_and_amendment_is_unsure():
    # Synthetic: the passage could belong to the amendment or to the substitute -> never guess.
    got = classify_senate("Votação do § 2º do art. 5º da Emenda nº 3 ao Substitutivo, destacado.")
    assert (got.kind, got.meaning_code) == ("highlight", None)
    got = classify_senate("Votação do art. 2º da Emenda nº 3 e da Emenda 55(Subst.), destacados.")
    assert (got.kind, got.meaning_code) == ("highlight", None)


def test_senate_amendment_voted_in_pec_turn_is_not_the_pec():
    # Synthetic order guard: the amendment rule runs before the PEC-turn rule.
    got = classify_senate("Votação nominal da Emenda nº 3 - PLEN à PEC nº 10/2020 (1º Turno)")
    assert (got.kind, got.meaning_code) == ("amendment", "approve_amendment")


def test_camara_pec_turn_on_other_object_drops_meaning():
    # Synthetic: no 2019-2026 description votes a plenary amendment "em primeiro turno".
    got = classify_camara("Rejeitada, em primeiro turno, a Emenda de Plenário nº 1 à Proposta de Emenda à "
                          "Constituição nº 45, de 2019. Sim: 100; não: 300.", "Votação em primeiro turno.")
    assert (got.kind, got.meaning_code, got.basis) == ("pec_turn", None, "pec_turn_other_object")


def test_proposition_type_marks_substitute_as_pec_but_never_overrides_projeto():
    desc = ("Aprovado o Substitutivo adotado pelo Relator da Comissão Especial, ressalvados os destaques. Sim: 364; "
            "não: 2; total: 366.")
    assert classify_camara(desc, "Votação em segundo turno.", proposition_type="PEC").meaning_code == "pec_second_turn"
    assert classify_camara(desc, "Votação em turno único.", proposition_type="PEC")[:2] == ("pec_turn", None)
    assert classify_camara(desc, "Votação em segundo turno.").meaning_code == "approve_committee_substitute"
    # 2265123-52 under a PEC id must still read as the bill's text
    got = classify_camara("Aprovado o Projeto de Lei nº 3.877, de 2020. Sim: 425; não: 10; total: 435.",
                          "Votação em turno único.", 425, 10, proposition_type="PEC")
    assert got.meaning_code == "approve_text"


def test_aglutinativa_substitutiva_of_a_pec_is_the_pec_not_an_amendment():
    # 2293449-176 (PEC 23/2021, 312 x 144): it replaces the whole PEC text. Neither text names the
    # turn, so the meaning stays unknown rather than "aprovar a emenda".
    desc = "Aprovada a Emenda Aglutinativa Substitutiva nº 1, ressalvados os destaques. Sim: 312; não: 144; total: 456."
    opening = "Votação da Emenda Aglutinativa Substitutiva nº 1."
    got = classify_camara(desc, opening, 312, 144, proposition_type="PEC")
    assert tuple(got) == ("pec_turn", None, "pec_turn_unknown")
    got = classify_camara(desc, "Votação em segundo turno.", 312, 144, proposition_type="PEC")
    assert (got.kind, got.meaning_code) == ("pec_turn", "pec_second_turn")
    # Without the PEC type it is still an ordinary amendment
    assert classify_camara(desc, opening, 312, 144)[:2] == ("amendment", "approve_amendment")


def test_opening_alone_classifies_when_description_is_missing():
    assert tuple(classify_camara(None, "Votação da Redação Final.")) == (
        "final_wording", "approve_final_wording", "opening_final_wording")
    got = classify_camara(None, "Votação das Emendas do Senado Federal ao Projeto de Lei nº 3.729, de 2004, com "
                                "parecer pela aprovação, ressalvados os destaques.")
    assert tuple(got) == ("amendment", "approve_senate_changes", "opening_senate_amendments")


@pytest.mark.parametrize("classify", [lambda d: classify_camara(d, None), classify_senate])
@pytest.mark.parametrize("desc", [None, "", "   ", "Texto completamente desconhecido sem padrão."])
def test_unknown_or_empty_is_other_without_meaning(classify, desc):
    assert tuple(classify(desc)) == ("other", None, "no_rule")


def test_accents_and_case_do_not_matter():
    a = classify_camara("MANTIDO O TEXTO. Sim: 330; Nao: 91.", "")
    b = classify_camara("Mantido o texto. Sim: 330; não: 91.", "")
    assert a == b
    assert a.meaning_code == "keep_highlighted_text"


def test_mantido_with_sim_losing_contradicts_keep_meaning():
    # If Sim meant "keep", the text could not be kept with 124 x 317.
    got = classify_camara("Mantido o texto. Sim: 124; não: 317; total: 441.", "")
    assert (got.kind, got.meaning_code) == ("highlight", None)


def test_suprimido_with_sim_above_pec_quorum_contradicts_keep_meaning():
    got = classify_camara("Suprimido o texto. Sim: 320; não: 100; total: 420.", "")
    assert got.meaning_code is None


def test_dvs_count_boundaries():
    # Tie: the text cannot be "Mantido" if Sim meant keep and did not win.
    assert classify_camara("Mantido o texto. Sim: 200; não: 200; total: 400.", "").meaning_code is None
    # PEC quorum boundary: 307 Sim can lose the passage, 308 cannot.
    assert classify_camara("Suprimido o texto. Sim: 307; não: 166.", "").meaning_code == "keep_highlighted_text"
    assert classify_camara("Suprimido o texto. Sim: 308; não: 166.", "").meaning_code is None


def test_description_counts_disagreeing_with_vote_counts_drop_meaning():
    # 2271124-70: description says 276 x 155, votosSim/votosNao say 122 x 271.
    desc = "Mantido o texto. Sim: 276; não: 155; abstenção: 1; total: 432."
    opening = ("Votação do DTQ 3: PT: destaque, com vistas à rejeição, do inciso II do art. 3º da Lei nº "
               "7.689/1988, constante do PLV à MPV 1034/2021. (art. 161, I)")
    assert classify_camara(desc, opening).meaning_code == "keep_highlighted_text"
    assert classify_camara(desc, opening, yes_count=276, no_count=155).meaning_code == "keep_highlighted_text"
    got = classify_camara(desc, opening, yes_count=122, no_count=271)
    assert (got.kind, got.meaning_code, got.basis) == ("highlight", None, "description_count_mismatch")


def test_description_rejecting_what_the_counts_approve_drops_meaning():
    # 2399598-70: votosSim/votosNao 315 x 87, yet the description says rejected 35 x 375.
    desc = "Rejeitada a Emenda de Plenário nº 40. Sim: 35; não: 375; total: 410."
    opening = "Votação do DTQ 5: Bloco Fdr PSOL-REDE (Psol): Emenda de plenário n° 40 (art. 161, II)."
    assert classify_camara(desc, opening, yes_count=315, no_count=87).meaning_code is None


def test_count_mismatch_tolerance_boundary():
    desc = "Mantido o texto. Sim: 300; não: 100."
    assert classify_camara(desc, "", yes_count=305, no_count=100).meaning_code == "keep_highlighted_text"
    got = classify_camara(desc, "", yes_count=306, no_count=100)
    assert (got.meaning_code, got.basis) == (None, "description_count_mismatch")


def test_count_guard_needs_both_counts_and_quoted_counts():
    desc = "Mantido o texto. Sim: 300; não: 100."
    assert classify_camara(desc, "", yes_count=None, no_count=10).meaning_code == "keep_highlighted_text"
    assert classify_camara(desc, "", 10, None).meaning_code == "keep_highlighted_text"
    got = classify_camara("Aprovado o Requerimento.", "", yes_count=10, no_count=400)
    assert got.meaning_code == "approve_request"


def test_oral_votes_added_to_the_description_are_tolerated():
    # 2242282-7: the clerk adds the oral vote of a deputy to the panel count (331 vs 330).
    desc = ("Aprovado o Requerimento de Urgência (Art. 155 do RICD). Sim: 331; não: 25; total: 356. Computado o "
            "voto verbal")
    got = classify_camara(desc, "", yes_count=330, no_count=25)
    assert got.meaning_code == "approve_urgency"


def test_every_meaning_code_is_produced_and_every_kind_is_known():
    produced = {classify_camara(c[1], c[2]) for c in CAMARA_CASES} | {classify_senate(c[1]) for c in SENATE_CASES}
    for r in produced:
        assert r.kind in KINDS
    # Every documented code has a real example above, and no undocumented code is produced.
    assert {r.meaning_code for r in produced} - {None} == MEANING_CODES
    assert CLASSIFIER_VERSION == "1.4"


def test_every_meaning_code_has_a_sentence_in_the_module_doc():
    for code in MEANING_CODES:
        assert f"    {code}" in vote_kind.__doc__


def test_coverage_report_counts_kinds_and_meaning_share():
    rows = [
        classify_camara("Rejeitado o Requerimento. Sim: 21; Não: 238.", ""),
        classify_camara("Mantido o texto. Sim: 330; não: 91.", ""),
        classify_camara("Rejeitada.", ""),
        {"kind": "other", "meaning_code": None, "basis": "no_rule", "description": "Rejeitada."},
    ]
    rep = coverage_report(rows)
    assert rep["total"] == 4
    assert rep["by_kind"] == {"procedural": 1, "highlight": 1, "other": 2}
    assert rep["with_meaning"] == 2
    assert rep["with_meaning_pct"] == 50.0
    assert rep["by_basis"]["no_rule"] == 2


def test_coverage_report_empty():
    rep = coverage_report([])
    assert rep["total"] == 0
    assert rep["with_meaning_pct"] == 0.0
