# AD — Votações nominais (Câmara e Senado, 2019–2026)

**Status:** Aceita — coletores `camara-deputies`, `camara-votes` e `senado-votes`
implementados; consultas e textos da interface web (métricas, regra de ausência, rótulo do
Governo) em `web/src/lib/votes-queries.ts` e `vote-meaning.ts`.
**Relacionado:** [identidade.md](identidade.md), [dados_derivados.md](dados_derivados.md),
[confiabilidade.md](confiabilidade.md), [imutabilidade.md](imutabilidade.md)

> Nomes de tabela/coluna em inglês (ver [banco.md](banco.md)); texto em português.

## Contexto

Queremos comparar partidos e parlamentares pelo que votaram: como cada partido
orientou, quanto cada deputado seguiu o partido, quem votou junto de quem, quem
faltou. O público é leigo, então o dado precisa ser **correto** e
**inteligível**: "votou Sim" não diz nada se a pessoa não sabe o que o Sim
aprovava.

Nenhuma das duas casas publica isso pronto:

- não existe campo estruturado "o que o Sim significa" — só a descrição livre
  do secretário ("Mantido o texto.", "Rejeitado o Requerimento.");
- a orientação da Câmara vem por **bancada**, e a bancada muitas vezes é um
  bloco com rótulo truncado ("Bl PlFdrPtUniPp...") cuja composição a API não data;
- ausência não é dado direto na Câmara: é preciso cruzar mandato em exercício,
  lista de presença e voto registrado.

Período: legislaturas 56 e 57 (2019–2026). Só votações **nominais do
Plenário** — ~96% das votações da Câmara são simbólicas e não têm voto por
deputado.

## Decisão

### 1. Fontes e um coletor por fonte

| Coletor | Fonte | Por quê |
|---|---|---|
| `camara-votes` | Arquivos anuais da Câmara (`votacoes`, `votacoesVotos`, `votacoesOrientacoes`, `votacoesProposicoes`, `eventosPresencaDeputados`, `proposicoesTemas`) + API `/blocos` | Arquivo em lote é reprodutível (ver [confiabilidade.md](confiabilidade.md)); a API só entra para a composição de blocos/federações |
| `camara-deputies` | API `/deputados/{id}` e `/deputados/{id}/historico` | O CPF só existe na API (a coluna `cpf` de `deputados.csv` vem vazia); o histórico dá os períodos em exercício |
| `senado-votes` | API `/dadosabertos/votacao` (janelas mensais), `/senador/{cod}.json` e o painel `/plenario/votacao/orientacaoBancada` | Não existe arquivo em lote de votações do Senado |

Um coletor por fonte porque `reset_source` apaga as tabelas de uma fonte só e
`source.name` é único. Um coletor único obrigaria a rebaixar todos os arquivos
a cada chamada lenta de API. Não há FK entre as tabelas de votação de coletores
diferentes (ex.: `camara_vote_cast` → `camara_deputy`): a FK impediria o
`reset_source` de um sem o outro; a ligação é pela chave natural da fonte
(`camara_deputy_id`, `senator_code`). Fora a proveniência (`provenance_id` →
`parse`, sempre do próprio coletor), a única FK para fora é `person_id` →
`people`, que nenhum coletor apaga.

Os arquivos anuais da Câmara são **regerados todo dia**: o SHA-256 muda a cada
coleta mesmo sem dado novo. `elosys verify` vai acusar `CHANGED` neles; a
conferência dessa fonte é pelas contagens do `camara_votes_report.json`. As
respostas de API que descrevem o estado atual (mês corrente do Senado,
`/deputados/{id}`, `/blocos/{id}/partidos`) também podem acusar `CHANGED` quando a
fonte muda.

**CLDF: indisponível.** A Câmara Legislativa do DF não publica votação em dado
estruturado, só no Diário em PDF. Fica fora até existir fonte estruturada.

Filtro de Plenário:

- Câmara: `siglaOrgao = 'PLEN'` **e** a votação tem linhas em `votacoesVotos`.
- Senado: `casaSessao = 'SF'` e `siglaTipoSessao` em `DOR`/`DEX`/`SDR` (lista
  oficial de tipos de sessão). `informeLegislativo.siglaColegiado` é o histórico
  de comissões da matéria, **não** onde a votação aconteceu — não serve de filtro.

O número baixo de votações nominais do Senado por ano (133 em 2019, 95 em 2024)
é real: a maior parte é decidida em votação simbólica. Conferido contra
`/plenario/lista/votacao` e `/dados/ListaVotacoes{ano}.json`.

### 2. Identidade: liga a quem já existe, nunca cria pessoa

Mesma filosofia de [identidade.md](identidade.md): na dúvida, não afirma.

- **Deputado** = CPF da API igual a `people.cpf` com `cpf_trusted = 1` **e**
  mais um fator concordando: data de nascimento (`cpf+nascimento`) ou nome civil
  normalizado (`cpf+nome`) contra o `politician_history` da pessoa. Verificação
  só-leitura na base real: 929/929 deputados das legislaturas 56 e 57 casaram por
  `cpf+nascimento`.
- **Senador** = `NomeCompletoParlamentar` normalizado **e** `DataNascimento`
  iguais a uma linha de `politician_history` de cargo SENADOR / 1º SUPLENTE /
  2º SUPLENTE de **exatamente uma** pessoa (`nome+nascimento`). 153/153 casaram.
  Sem CPF no Senado; nome sozinho foi rejeitado (gera homônimo).
- Sem match: `person_id` NULL + `unmatched_reason` — deputado: CPF ausente,
  inválido, não confiável, em mais de uma pessoa ou fora de `people`, ou
  nascimento e nome civil diferentes; senador: sem cadastro, sem nome ou
  nascimento, homônimos, nascimento diferente, histórico sem nascimento, não
  encontrado. **Nenhum dos três
  coletores cria linha em `people`**; o `senado-votes` confere a contagem de
  `people` antes e depois e falha se mudou.

### 3. Exercício do mandato e ausência (Câmara)

`camara_deputy_term` tem uma linha por mudança de situação do `/historico`,
com início e fim em minuto (hora local de Brasília, como a fonte publica).
`in_office = 1` quando a situação é `Exercício`, ou `CONVOCADO` com descrição
"Entrada - Reassunção" (suplente chamado de volta: 2.556 votos registrados
nesse estado). Qualquer outra situação é 0.

Correções que o histórico exige (todas contadas no report):

- linhas com `situacao` NULL são artefatos ("Nome/Partido no início da
  legislatura", repetidos para cada legislatura antiga) → descartadas;
- linhas datadas **antes do início da legislatura** são descartadas: sem isso,
  o deputado 74140 ficava "em exercício" de 2023-02-01 até a posse real em
  2026-06-30 e apareceria ausente em 1.111 votações (e a BA com 40 deputados
  para 39 cadeiras);
- várias linhas no mesmo minuto: vale a última (o estado resultante).

Checagem barata e forte, no report do `camara-deputies` (`seat_check`): no dia 15
de cada mês, ao meio-dia, os deputados em exercício cabem nas 513 cadeiras e,
por UF, nas cadeiras da UF.

Regra de ausência (aplicada na consulta, não persistida):

- **voto registrado sempre vence** — quem votou estava lá, qualquer que seja a
  situação no histórico;
- **"presente e não votou"** = está na lista de presença da sessão
  (`camara_session_presence`) e não tem voto;
- **"ausente"** = não votou **e** estava em exercício na hora da sessão **e**
  não está na lista de presença **e** a situação dele não mudou naquele dia
  (os carimbos de hora do `/historico` não batem com o relógio da sessão; em dia
  de mudança não se afirma nada);
- o termo é sempre procurado dentro da legislatura da data da votação (o último
  termo de uma legislatura encerrada se sobreporia à seguinte).

### 4. Orientação: qual bancada fala por cada partido

**Câmara** (`elosys/camara/benches.py`, versão em `BENCHES_VERSION`), por par
(votação, partido), em ordem, parando no primeiro que casa:

1. `party` — a linha do próprio partido, depois de normalizar alias e truncamento
   ("Solidaried", "Republican", "Podemos" → PODE, caixa e acento; "GOV." é só a
   liderança do Governo);
2. `federation` — federação cujo rótulo completo (sem "...") lista o partido;
3. bloco, só com evidência datada:
   - `bloc_api` — bloco da API `/blocos` **dentro de uma janela citada** em
     `API_BLOC_WINDOWS` e só enquanto `/partidos` for igual à composição
     documentada. A API não tem data: `/partidos` é a composição do dia da
     coleta, então ela sozinha nunca data uma filiação;
   - `bloc_curated` — tabela curada `CURATED_BLOCS`: cada entrada tem janela de
     datas e cita documentos oficiais da Câmara (Ordem do Dia em PDF, seção
     "Lideranças"; notícia oficial; biografia do líder). Um partido só entra na
     janela se os documentos o colocam no bloco nas duas pontas;
   - `bloc_label` — rótulo de bloco sem "..." e feito só de siglas
     ("PpMdbPtb"): o próprio rótulo lista a composição inteira;
4. senão → `bench_label` NULL, `unmatched_reason` (`no_party`, `no_bench_row`,
   `bloc_unresolved`, `ambiguous`) e a interface diz **"sem orientação
   identificada"**. Mais de uma linha candidata → `ambiguous`, nunca escolhe.

Linhas de liderança (Governo, Maioria, Minoria, Oposição) nunca orientam um
partido. Toda base que não é `party` exige `match_evidence` (CHECK no schema):
URL da API, URL oficial citada ou o rótulo transcrito. **Nunca adivinha
composição de bloco truncado.**

"Governo" é rotulado com o governo da data: Bolsonaro até 2022-12-31, Lula a
partir de 2023-01-01. Na Câmara, partidos aparecem com o nome da época; não
fundimos siglas que mudaram (exceção: "PATRI" e "PATRIOTA", as duas grafias do
mesmo partido no arquivo de 2019; no Senado, ver abaixo).

**Senado:** a orientação vem do painel de votação, que publica de forma
**parcial** (ex.: 2019-09, 3 de 17 votações; 2021-06, 20 de 20). O painel não
tem o código da votação; a ligação a `senate_vote` é por `sequencial` (mesmo
`sequencialVotacao`, mesma data ou dia seguinte, mesmo placar Sim/Não) ou, só
para votações sem sequencial, por `placar` (mesma data ou dia seguinte, mesmo
placar, exatamente um candidato e reivindicado por um único voto do painel). O resto fica fora, contado no report com o motivo. Sem orientação
publicada a interface diz **"sem orientação publicada"** e mostra só os votos.
Os rótulos do painel usam as siglas antigas (PRB, PR, PPS) e são mapeados para
as atuais, porque o `/votacao` reescreve votos antigos com o nome atual do
partido.

### 5. O que o "Sim" significa: classificado no parse, por regra versionada

`elosys/camara/vote_kind.py` lê a descrição oficial (Câmara: `descricao`, depois
`ultimaAberturaVotacao_descricao`; Senado: `descricaoVotacao` + `identificacao`)
e grava em cada votação:

- `kind` — `merito`, `pec_turn`, `highlight`, `amendment`, `final_wording`,
  `procedural`, `nomination`, `other`;
- `meaning_code` — uma frase por código ("Sim = manter no texto o trecho
  destacado"); a redação para o público fica no `vote-meaning.ts` do app web, então
  muda sem recoletar;
- `kind_basis` — qual padrão casou (trilha de auditoria).

É uma função determinística de **uma linha** (como `iso_date`), não um sinal
entre linhas — por isso fica no parse e fora de `rule_run`. A versão do
classificador (`CLASSIFIER_VERSION`) entra no `parser_version`.

Princípios:

- **Nunca adivinha.** Padrão ambíguo → `meaning_code` NULL e a interface mostra
  "interpretação indisponível" ao lado da descrição original (que aparece sempre).
- O significado **não depende do resultado**: em "Aprovado/Rejeitado o X", o
  Sim apoia X nos dois casos. Exceção: destaque "Mantido/Suprimido o texto",
  onde Sim = manter o trecho.
- Checagem de direção: nas 3.096 votações nominais da Câmara 2019–2026, 0
  significados invertidos. "Rejeitado" com Sim > Não **não** é contradição: é
  quórum qualificado (PEC exige 308 votos Sim, inclusive para manter trecho
  destacado; PLP e urgência do art. 155 exigem 257; no Senado, PEC exige 49 e PLP
  41). Qualquer checagem futura
  tem que saber o tipo da matéria antes de acusar.
- No Senado, `resultadoVotacao` de destaque diz o destino do destaque, não se
  o Sim venceu — não é usado para classificar.

### 6. Votos que não são Sim/Não

| Caso | Câmara | Senado |
|---|---|---|
| Voto secreto | `voto` vazio → `secret`. A fonte diz **quem** votou, não **como**. Nunca vira Não/Abstenção | `Votou` (só aparece em votação secreta) → `secret` |
| Presidindo | `Artigo 17` (RICD) → `presiding`: não é ausência nem abstenção | `Presidente (art. 51 RISF)` → `presiding` |
| Presente sem voto | lista de presença sem voto (§3) | `P-NRV` → `present_not_voted`. A página oficial mostra `NCom` em ~10% desses casos: a interface diz **"não registrou voto"**, nunca "presente" |
| Ausência justificada / falta | só "ausente", inferido (§3); a Câmara não publica justificativa — licença tira o deputado do exercício | `AP`, `LS`, `LP`, `MIS`... → `absent_justified`; `NCom` → `absent` |
| Código fora da lista | — | `NA` ("Dispositivo não citado", em geral uma licença que a API esconde) e `MERC` → `other`, com o código bruto mostrado |

Todo voto guarda o valor bruto (`vote_raw`) ao lado do normalizado.

Algumas votações do Senado listam **só quem votou**, sem código de ausência
(ex.: abril de 2019). Nelas, senador fora da lista é **"desconhecido"**, não
"ausente" (report `votes_listing_only_voters`).

Placar: no Senado os totais da API só existem em votação secreta; nas abertas
ficam NULL e o placar sai dos votos. Na Câmara a votação secreta também traz
os totais Sim/Não.

### 7. `last_filed_*` não é o item votado

`camara_vote.last_filed_proposition_id`/`last_filed_description` vêm de
`ultimaApresentacaoProposicao_*`: o **último documento apresentado** na
proposição antes da votação (em 2192459-636 é um Requerimento de Informação).
O item votado se lê na descrição + abertura + `camara_vote_proposition`. Uma
votação pode ter proposições de tipos diferentes, então "o tipo da votação"
nem sempre é conhecido.

### 8. Métricas calculadas na consulta, não persistidas

Fidelidade (votos iguais à orientação ÷ votos dados quando a orientação é
Sim/Não/Obstrução; Liberado, sem orientação e Artigo 17 fora), posição
majoritária do partido, concordância partido × partido / × Governo / deputado ×
deputado e presença são calculadas em `web/src/lib/votes-queries.ts`. Nada
agregado entre linhas é gravado — então não há `rule_run` nem `signal` (ver
[dados_derivados.md](dados_derivados.md)). O que é gravado são interpretações
**de uma linha** (voto normalizado, orientação normalizada, `kind`/`meaning_code`,
bancada do partido, deputado → pessoa), sempre ao lado do valor bruto e da base
que as produziu.

### 9. Transação única por coletor

Cada coletor apaga as tabelas dele (esse reset é commitado) e grava tudo numa
**única transação**, commitada no fim, depois das checagens. Qualquer falha
(arquivo de um ano fora do ar, `/blocos` caiu) faz rollback: as tabelas ficam
**vazias**, nunca pela metade e nunca com a versão anterior. Uma coleta parcial
seria pior que nenhuma — votação sem bancada viraria "sem orientação
identificada" em todo lugar; elenco incompleto viraria "nunca em exercício".
Depois de uma falha, rode o coletor de novo.

## Consequências

- Toda tela de votação mostra a descrição original; a frase simples é um
  complemento, nunca substitui.
- Parte das orientações de bloco fica sem dono de propósito (rótulo truncado
  sem documento datado). O report (`bench`) mostra a cobertura total e quantas
  linhas ficaram sem dono por motivo; medida por ano na revisão, 2024/2025 ficou
  em 99,8%.
- Ausência na Câmara depende do `camara-deputies` ter rodado; sem ele, só
  "presente e não votou" é afirmável.
- Mudar regra de significado ou de bancada = subir a versão e recoletar. Mudar
  a redação para o público = só o front.
- `elosys verify` sempre acusa os arquivos da Câmara (regerados diariamente). As
  respostas de API que descrevem o estado atual (mês corrente do Senado,
  `/deputados/{id}`, `/blocos/{id}/partidos`) também podem acusar `CHANGED` quando
  a fonte muda.

## Pontos em aberto

- ⚠️ **Manutenção das janelas de bloco.** `API_BLOC_WINDOWS` e
  `CURATED_BLOCS` terminam em **2026-10-02**. Votação depois disso cujo partido
  só é orientado por bloco de rótulo truncado cai em `bloc_unresolved`. Ao recoletar depois do
  recesso: olhar `votes_after_last_window_end` no `camara_votes_report.json`,
  conferir a composição na Ordem do Dia das sessões novas (seção "Lideranças"),
  estender as janelas com as URLs e subir `BENCHES_VERSION`. Se a API `/partidos`
  mudar, a janela de `bloc_api` se desliga sozinha e a linha passa para a tabela
  curada.
- ⚠️ Blocos da legislatura 56 sem composição oficial encontrada (ex.:
  "NovoCidPv", 2022) seguem sem orientação identificada.
- ⚠️ Votações do Senado que só o painel lista (ex.: 2019-02-12, 2022-04-06) não
  entram: o painel não tem código de senador, só nome.
- ⚠️ CLDF: reavaliar se a Câmara Legislativa passar a publicar votação em dado
  estruturado.
