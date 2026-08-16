from __future__ import annotations

import asyncio
import re

from app.services.legal.article_numbers import extract_requested_article_numbers
from app.services.legal.models import LegalClassification


# ---------------------------------------------------------------------------
# Mapeamento determinístico: padrão regex → campos de classificação seguros
# ---------------------------------------------------------------------------

DIPLOMA_PATTERNS: list[tuple[str, dict]] = [
    # Pedido genérico sem factos jurídicos suficientes — deve pedir clarificação
    (
        r"^(tenho|estou\s+com|tenho\s+um|tenho\s+uma).{0,40}(problema|caso).{0,30}(justi[çc]a|tribunal|lei|jur[ií]dico).{0,20}(o\s+que\s+fa[çc]o|como\s+proceder|me\s+ajuda|ajuda)?[?!.]?$",
        {
            "main_branch": "indeterminado",
            "topic_route": "geral",
            "request_type": "passos_praticos",
            "specificity": "geral",
            "audience": "leigo",
            "needs_clarification": True,
            "clarifying_questions": [
                "Qual é a área do problema: penal, laboral, família, civil, administrativo ou outra?",
                "O que aconteceu concretamente, em que data e contra quem?",
                "Pretende apresentar queixa, defender-se, pedir indemnização ou apenas entender os seus direitos?",
            ],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Detenção/prisão por polícia em reunião, manifestação ou actividade política.
    (
        r"(pol[ií]cia|agente[s]?\s+policiais|autoridade).*(prendeu|det[eé]m|deteve|detido|deten[çc][aã]o|esquadra)"
        r"|"
        r"(prendeu|det[eé]m|deteve|detido|deten[çc][aã]o|esquadra).*(pol[ií]cia|agente[s]?\s+policiais|autoridade)"
        r"|"
        r"(actividade|atividade|reuni[aã]o|manifesta[çc][aã]o|partido|pol[ií]tico|democr[aá]tico).*(pol[ií]cia|prendeu|detido|deten[çc][aã]o)",
        {
            "main_branch": "misto",
            "topic_route": "cpp",
            "requested_diplomas": [
                "Código do Processo Penal",
                "Código Penal",
                "Constituição da República de Angola",
            ],
            "branch_candidates": ["penal", "constitucional", "administrativo"],
            "request_type": "passos_praticos",
            "specificity": "factual",
            "audience": "leigo",
            "needs_multi_branch_handling": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Furto/burla/posse de bens em relação afectiva ou ex-casal.
    # Ex.: "a minha ex-namorada roubou-me dinheiro e diz que era do casal".
    (
        r"(ex[\s-]*(namorad[ao]|companheir[ao]|marid[ao]|espos[ao])|namorad[ao]|companheir[ao]|casal|rela[çc][aã]o)"
        r".*(roub(ou|aram|ar)|furt(ou|aram|ar)|tirou|levou|subtraiu|ficou\s+com|apropriou)"
        r"|"
        r"(roub(ou|aram|ar)|furt(ou|aram|ar)|tirou|levou|subtraiu|ficou\s+com|apropriou)"
        r".*(ex[\s-]*(namorad[ao]|companheir[ao]|marid[ao]|espos[ao])|namorad[ao]|companheir[ao]|casal|rela[çc][aã]o)",
        {
            "main_branch": "misto",
            "topic_route": "penal_substantivo",
            "requested_diplomas": ["Código Penal", "Código Civil", "Código da Família"],
            "branch_candidates": ["penal", "civil", "familia"],
            "request_type": "passos_praticos",
            "specificity": "factual",
            "audience": "leigo",
            "needs_multi_branch_handling": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Sucessões / Herança
    (
        r"heran[çc]a|herdeiro|testamento|partilha\s+(de\s+)?bens|sucess[aã]o\s+leg[ií]tima|invent[aá]rio\s+obrigat[oó]rio|falec(ido|eu|imento)|deixou\s+(uma|um)\s+(casa|terreno|bem)",
        {
            "main_branch": "familia",
            "topic_route": "sucessoes",
            "requested_diplomas": ["Código Civil"],
            "branch_candidates": ["familia", "civil"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Sociedades Comerciais (antes de "comercial" genérico)
    (
        r"sociedade[s]?\s+(comercia|an.nima|por\s+quota|unipessoal)|lei\s+das\s+sociedades\s+comerciais|lsc\b",
        {
            "main_branch": "comercial",
            "topic_route": "sociedades",
            "requested_diplomas": ["Lei das Sociedades Comerciais"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Família / Divórcio / Separação / Bens do casal
    (
        r"c[oó]digo\s+(da\s+)?fam[ií].lia|div[oó]rcio|divorciar|separa[çc][aã].o\s+(de\s+)?bens|casamento|guarda\s+(de\s+)?(filhos|menor)|filhos?\s+menores|crian[çc]as?\s+(ficam|ficar)|pens[aã].o\s+de\s+alimentos|filia[çc][aã]o|regime\s+de\s+bens|separei",
        {
            "main_branch": "familia",
            "topic_route": "familia",
            "requested_diplomas": ["Código da Família"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Laboral técnico / jurisprudência laboral
    (
        r"extra\s+vel\s+ultra\s+petit|ultra\s+petit|extra\s+petit|processo\s+laboral|mat[eé]ria\s+disciplinar|recurso\s+em\s+mat[eé]ria\s+disciplinar|c[aâ]mara\s+do\s+trabalho",
        {
            "main_branch": "laboral",
            "topic_route": "laboral",
            "requested_diplomas": ["Lei Geral do Trabalho"],
            "branch_candidates": ["laboral"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Laboral
    (
        r"despedimento|despedid[oa]|contrato\s+de\s+trabalho|lei\s+geral\s+do\s+trabalho|reintegra[çc][aã]o|subsidio\s+de\s+férias|sal[aá]rio\s+m[ií]nimo|horas\s+extra|trabalh(ador|adora|o\s+ileg|o\s+formal)|rescis[aã]o\s+de\s+contrato|justa\s+causa|indemniza[çc][aã]o\s+(por|de)\s+despedimento",
        {
            "main_branch": "laboral",
            "topic_route": "laboral",
            "requested_diplomas": ["Lei Geral do Trabalho"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Tributário (IVA primeiro)
    (
        r"\biva\b|imposto\s+sobre\s+o\s+valor\s+acrescentado|factura[çc][aã]o\s+electr[oó]nica",
        {
            "main_branch": "tributario",
            "topic_route": "iva",
            "requested_diplomas": ["Código Geral Tributário"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Tributário (geral)
    (
        r"c[oó]digo\s+geral\s+tribut[aá]rio|cgt\b|obriga[çc][aã]o\s+tribut[aá]ria|infracc?[aã]o\s+fiscal|administra[çc][aã]o\s+tribut[aá]ria|imposto\s+(sobre|industrial|predial|de\s+rendimento)",
        {
            "main_branch": "tributario",
            "topic_route": "tributario",
            "requested_diplomas": ["Código Geral Tributário"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Jornalismo investigativo / documentos vazados / corrupção pública / dados pessoais
    (
        r"(jornalista|imprensa|not[íi]cia|noticia|comunica[çc][aã]o\s+social|den[uú]ncia|publica[çc][aã]o|documentos?\s+vazad[oa]s?|vazamento|fonte\s+jornal[ií]stica)"
        r".*(corrup[çc][aã]o|funcion[aá]rios?\s+p[uú]blicos?|agentes?\s+p[uú]blicos?|dados\s+pessoais|protec[çc][aã]o\s+de\s+dados|prote[çc][aã]o\s+de\s+dados|segredo|documentos?)"
        r"|corrup[çc][aã]o.*(jornalista|imprensa|documentos?\s+vazad[oa]s?|dados\s+pessoais|protec[çc][aã]o\s+de\s+dados|prote[çc][aã]o\s+de\s+dados)"
        r"|penal.*constitucional.*administrativ[oa].*(protec[çc][aã]o|prote[çc][aã]o)\s+de\s+dados",
        {
            "main_branch": "misto",
            "topic_route": "geral",
            "requested_diplomas": [
                "Código Penal",
                "Constituição da República de Angola",
            ],
            "branch_candidates": ["penal", "constitucional", "administrativo"],
            "request_type": "analise_tecnica",
            "specificity": "comparacao_multi_ramo",
            "audience": "tecnico",
            "needs_multi_branch_handling": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Uso indevido de bens do Estado com múltiplas frentes de responsabilidade
    (
        r"((funcion[aá]rio|agente|servidor)\s+p[uú]blico|titular\s+de\s+cargo\s+p[uú]blico).*(carro|viatura|ve[ií]culo|bem|dinheiro|combust[ií]vel|patrim[oó]nio).*(estado|p[uú]blico)"
        r"|((carro|viatura|ve[ií]culo|bem|patrim[oó]nio)\s+do\s+estado)"
        r"|((disciplinar).*(civil).*(penal).*(administrativ).*(funcion[aá]rio|agente)\s+p[uú]blico)",
        {
            "main_branch": "misto",
            "topic_route": "geral",
            "requested_diplomas": [
                "Código Penal",
                "Constituição da República de Angola",
            ],
            "branch_candidates": ["penal", "administrativo", "civil", "constitucional"],
            "request_type": "analise_tecnica",
            "specificity": "comparacao_multi_ramo",
            "needs_multi_branch_handling": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Peculato / abuso de poder / uso indevido de bem público
    (
        r"peculato(\s+de\s+uso)?|abuso\s+de\s+poder|"
        r"((funcion[aá]rio|agente|servidor)\s+p[uú]blico|titular\s+de\s+cargo\s+p[uú]blico).*(carro|viatura|ve[ií]culo|bem|dinheiro|combust[ií]vel|patrim[oó]nio).*(estado|p[uú]blico)"
        r"|((carro|viatura|ve[ií]culo|bem|patrim[oó]nio)\s+do\s+estado)",
        {
            "main_branch": "penal",
            "topic_route": "penal_substantivo",
            "requested_diplomas": ["Código Penal"],
            "branch_candidates": ["penal", "administrativo", "civil"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Filmagem de polícia + alegação de desacato/desobediência/resistência:
    # classifica o ramo e deixa o retrieval resolver os artigos dinamicamente
    # no corpus, evitando prender o caso a uma lista fechada de artigos.
    (
        r"desacato|desobedi[êe]ncia|resist[êe]ncia|resist(iu|ir|indo)|ordem\s+(do|da|de)\s+(agente|pol[ií]cia|autoridade)|agente.*(ordenou|ordem).*film|interromp(er|eu|ia).*(opera[çc][aã]o|acto)\s+policial|perturba[çc][aã]o\s+do\s+acto\s+policial|viol[êe]ncia.*(agente|pol[ií]cia|funcion[aá]rio|autoridade|acto\s+policial)|amea[çc]a.*(agente|pol[ií]cia|funcion[aá]rio|autoridade|acto\s+policial)|impedir\s+o\s+acto\s+policial",
        {
            "main_branch": "penal",
            "topic_route": "penal_substantivo",
            "requested_diplomas": ["Código Penal"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Protecção da imagem / gravações ilícitas — antes de CPP, porque o contexto Pro
    # pode incluir "Código do Processo Penal" como termo auxiliar de recuperação.
    (
        r"film(ar|agem)|fotograf(ar|ia)|grava(r|[çc][aã]o)|imagem\s+(de|do|da)|pol[ií]c(ia|ial).*(film|foto|grav)|film.*pol[ií]c(ia|ial)",
        {
            "main_branch": "penal",
            "topic_route": "penal_substantivo",
            "requested_diplomas": ["Código Penal"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Processo Penal
    (
        r"pris[aã]o\s+preventiva|mandado\s+de\s+(busca|deten[çc][aã]o)|coacc?[aã]o\s+processual|recurso\s+penal|liberdade\s+provis[oó]ria|c[oó]digo\s+(do|de)?\s*processo\s+penal|\bcpp\b|\bccp\b",
        {
            "main_branch": "penal",
            "topic_route": "cpp",
            "requested_diplomas": ["Código do Processo Penal"],
            "requires_strict_corpus_match": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Penal substantivo
    (
        r"c[oó]digo\s+penal|crime\s+de|tipicidade|dolo|culpa\s+penal|pena\s+de\s+pris[aã]o|burla|furto|homic[ií]dio|corrup[çc][aã]o|v[ií]olencia\s+dom[eé]stica",
        {
            "main_branch": "penal",
            "topic_route": "penal_substantivo",
            "requested_diplomas": ["Código Penal"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Tribunal Constitucional / Lei Orgânica
    (
        r"tribunal\s+constitucional|lei\s+(n[.º°]\s*)?3\s*/\s*08|lei\s+org[aâ]nica\s+do\s+tribunal|recurso\s+de\s+constitucionalidade|lei\s+(n[.º°]\s*)?18\s*/\s*21|lei\s+de\s+revis[aã]o\s+constitucional",
        {
            "main_branch": "constitucional",
            "topic_route": "constitucional",
            "requested_diplomas": ["Lei n.º 3/08"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Terras / Propriedade
    (
        r"lei\s+de\s+terras|concess[aã]o\s+de\s+terra|terreno\s+(r[uú]stico|urbano)|terreno\b|posse\s+de\s+terra|registo\s+(pred|predial|de\s+terra)|conservat[oó]ria\s+(do\s+)?registo\s+predial|propriedade\s+(de\s+terreno|r[uú]stica)|declara[çc][aã]o\s+(do\s+soba|de\s+bairro|da\s+comiss[aã]o\s+de\s+moradores)|soba.*terreno|comiss[aã]o\s+de\s+moradores.*terreno|administra[çc][aã]o.*terreno|despejad[oa]\b|usucapi[aã]o|legalizar\s+terreno",
        {
            "main_branch": "propriedade",
            "topic_route": "terras",
            "requested_diplomas": ["Lei de Terras"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Bilhete de Identidade
    (
        r"bilhete\s+de\s+identidade|\bbia?\b|segunda\s+via\s+(do\s+)?bi|conservat[oó]ria\s+(do\s+)?registo\s+civil|conservat[oó]ria.*(bilhete|identidade|nascimento)",
        {
            "main_branch": "administrativo",
            "topic_route": "identificacao_civil",
            "requested_diplomas": ["Lei do Bilhete de Identidade"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Contencioso Administrativo / impugnação de actos administrativos
    (
        r"contencioso\s+administrativo|impugna[çc][aã]o\s+(administrativa|contenciosa)|recurso\s+contencioso|acto\s+administrativo|ato\s+administrativo|decis[aã]o\s+administrativa|[oó]rg[aã]o\s+p[uú]blico|reclama[çc][aã]o.*recurso\s+hier[aá]rquico|recurso\s+hier[aá]rquico",
        {
            "main_branch": "administrativo",
            "topic_route": "contencioso_admin",
            "requested_diplomas": [
                "Código de Processo do Contencioso Administrativo",
                "Lei n.º 2/94",
            ],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Constitucional
    (
        r"constitui[çc][aã]o\s+(da\s+rep[uú]blica|angolana)|direito[s]?\s+fundamental|garantia\s+constitucional|fiscaliza[çc][aã]o\s+constitucional|direito[s]?\s+constitucionais|constitucionais?\b|direitos\s+garantidos\s+pela\s+constitui[cç]",
        {
            "main_branch": "constitucional",
            "topic_route": "constitucional",
            "requested_diplomas": ["Constituição da República de Angola"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Responsabilidade civil do Estado por actos dos seus agentes
    (
        r"(agente|funcion[aá]rio|servidor|[oó]rg[aã]o).{0,60}(estado|p[uú]blico).{0,80}(dano|preju[ií]zo|indemniza[çc][aã]o|responsabilidade\s+civil)|responsabilidade\s+civil\s+do\s+estado",
        {
            "main_branch": "misto",
            "topic_route": "geral",
            "requested_diplomas": [
                "Constituição da República de Angola",
                "Código Civil",
            ],
            "branch_candidates": ["constitucional", "civil", "administrativo"],
            "request_type": "analise_tecnica",
            "specificity": "comparacao_multi_ramo",
            "needs_multi_branch_handling": True,
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Civil / dívidas, empréstimos e prova documental
    (
        r"emprest(ei|ou|ado|ar|imo)|empr[eé]stimo|m[uú]tuo|d[ií]vida|devedor|credor|cobrar\s+(uma\s+)?d[ií]vida|transfer[eê]ncias?|comprovativo|whatsapp|mensagens?|sem\s+contrato\s+escrito|contrato\s+verbal|reconhece(u|r)?\s+(a\s+)?d[ií]vida",
        {
            "main_branch": "civil",
            "topic_route": "civil_obrigacoes",
            "requested_diplomas": ["Código Civil"],
            "branch_candidates": ["civil"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
    # Civil / Obrigações (genérico — deve vir por último entre os civis)
    (
        r"c[oó]digo\s+civil|obriga[çc][oõ]es\s+civis|responsabilidade\s+civil|m[uú]tuo\s+(banc[aá]rio)?|hipoteca|penhor|arrendamento\s+civil",
        {
            "main_branch": "civil",
            "topic_route": "civil_obrigacoes",
            "requested_diplomas": ["Código Civil"],
            "force_main_branch": True,
            "force_topic_route": True,
        },
    ),
]

# Padrões de audiência — técnico vs leigo
_TECNICO_PATTERNS = re.compile(
    r"nos\s+termos\s+d[ao]|no\s+[âa]mbito\s+d[ao]|requisitos\s+(legais|formais)|"
    r"enquadramento\s+jur[ií]dico|fundamenta[çc][aã]o\s+legal|artigo\s+\d|"
    r"interpreta[çc][aã]o\s+(restritiva|extensiva)|nulidade|anulab|invalidade|"
    r"prescri[çc][aã]o|cadu[çc]|(advogad|jurista|magistrad|juiz|tribunal)|"
    r"pressupost|elementos?\s+essenciais|moldura\s+penal|nexo\s+de\s+causalidade|"
    r"causalidade\s+adequada|ilicitude|impugna[çc][aã]o|recurso|jurisprud[êe]ncia|"
    r"validade\s+formal|qualifica[çc][aã]o\s+jur[ií]dica",
    re.IGNORECASE,
)
_LEIGO_PATTERNS = re.compile(
    r"o\s+que\s+fa[çc]o|me\s+ajud[ae]|preciso\s+saber|tenho\s+direito|posso\s+fazer|"
    r"como\s+(fa[çc]o|funciona|posso)|n[aã]o\s+entendo|[eé]\s+crime|podem\s+me",
    re.IGNORECASE,
)

_EXPLICIT_DIPLOMA_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:c[oó]digo\s+(?:do\s+)?processo\s+penal|cpp|ccp)\b", re.I), "Código do Processo Penal"),
    (re.compile(r"\b(?:c[oó]digo\s+penal)\b", re.I), "Código Penal"),
    (re.compile(r"\b(?:lei\s+geral\s+do\s+trabalho|lgt)\b", re.I), "Lei Geral do Trabalho"),
    (re.compile(r"\b(?:constitui[çc][aã]o\s+(?:da\s+rep[uú]blica\s+de\s+angola|angolana)|cra)\b", re.I), "Constituição da República de Angola"),
    (re.compile(r"\b(?:c[oó]digo\s+civil)\b", re.I), "Código Civil"),
    (re.compile(r"\b(?:c[oó]digo\s+(?:da\s+)?fam[ií]lia)\b", re.I), "Código da Família"),
    (re.compile(r"\b(?:lei\s+de\s+terras)\b", re.I), "Lei de Terras"),
    (re.compile(r"\b(?:lei\s+das\s+sociedades\s+comerciais|lsc)\b", re.I), "Lei das Sociedades Comerciais"),
    (re.compile(r"\b(?:c[oó]digo\s+geral\s+tribut[aá]rio|cgt)\b", re.I), "Código Geral Tributário"),
)


def _explicit_requested_diplomas(question: str) -> list[str]:
    requested = [
        diploma
        for pattern, diploma in _EXPLICIT_DIPLOMA_ALIASES
        if pattern.search(question)
    ]
    law_reference = re.compile(
        r"(?:Lei|Decreto(?:[\s-]Lei)?)\s+(?:n\S*\s+)?\d+\s*[/\-]\s*\d{2,4}",
        re.IGNORECASE,
    )
    requested.extend(
        re.sub(r"\s+", " ", match.group(0)).strip()
        for match in law_reference.finditer(question)
    )
    return list(dict.fromkeys(requested))


TRANSFORMATION_PATTERNS: list[tuple[str, dict]] = [
    (
        r"resum[aei]|em\s+termos\s+simples|explica\s+como\s+se\s+eu\s+fosse|simplific|fala?\s+como\s+leigo|traduz\s+para\s+leigo|para\s+leigo|simpl[ei]s|n[aã]o\s+(percebi|entendi|compreendi)",
        {
            "is_transformation": True,
            "transformation_type": "simplify",
        },
    ),
    (
        r"fale\s+mais|mais\s+detalhes|detalh[aei]|expliq?u[ei]\s+melhor|continua|continue|aprofund",
        {
            "is_transformation": True,
            "transformation_type": "summarize",
        },
    ),
    (
        r"ent[aã]o\s+?.*resum|faz\s+um\s+resumo|com\s+poucas\s+palavras|d[ií]z\s+s[oó]\s+o\s+essencial|breve\s+resumo",
        {
            "is_transformation": True,
            "transformation_type": "summarize",
        },
    ),
]

_BRANCH_KEYWORDS = [
    (r"tribunal\s+constitucional|\bconstitucional\b|recurso\s+de\s+constitucionalidade|lei\s+org[aâ]nica", "constitucional"),
    (r"c[oó]digo\s+penal|\bpenal\b|crime\s+de|homic[ií]dio|burla|furto\b|roubo\b|cpp\b|processo\s+penal", "penal"),
    (r"c[oó]digo\s+civil|\bcivil\b|responsabilidade\s+civil|obriga[cç][aã]o|contrato\b|usucapi[aã]o", "civil"),
    (r"trabalhador|despedimento|\blaboral\b|sal[aá]rio|f[eé]rias|lei\s+geral\s+do\s+trabalho|lgt\b|extra\s+vel\s+ultra\s+petit|mat[eé]ria\s+disciplinar|c[aâ]mara\s+do\s+trabalho", "laboral"),
    (r"casamento|div[oó]rcio|fam[ií]lia|pens[aã]o\s+de\s+alimentos|regime\s+de\s+bens", "familia"),
    (r"sociedade\s+comercial|empresa\b|administrador\b|s[oó]cio\b|\bcomercial\b", "comercial"),
    (r"imposto|tribut[aá]rio|contribuinte|fiscal\b|c[oó]digo\s+geral\s+tribut[aá]rio|\btribut[aá]rio\b", "tributario"),
    (r"contencioso\s+administrativo|acto\s+administrativo|recurso\s+hier[aá]rquico|\badministrativ[oa]\b|disciplinar|funcion[aá]rio\s+p[uú]blico|agente\s+p[uú]blico|concurso\s+p[uú]blico|patrim[oó]nio\s+p[uú]blico|bem\s+p[uú]blico|auditoria|inspec[çc][aã]o", "administrativo"),
]

_COMPARISON_MARKERS = re.compile(
    r"diferen[çc]a|distinguir|distin[çc][aã]o|compara[çc][aã]o|comparar|entre\s+.*\s+e\s+.*|"
    r"distin[çc][aã]o|versus|\bvs\b|quais?\s+ramos|sob\s+quais?\s+ramos|"
    r"multi[-\s]?disciplinar|responsabilidade\s+(penal|civil|administrativa|disciplinar)|"
    r"protec[çc][aã]o\s+de\s+dados|prote[çc][aã]o\s+de\s+dados",
    re.IGNORECASE,
)

_PRACTICAL_PATTERNS = re.compile(
    r"o\s+que\s+fa[çc]o|como\s+proceder|passos?\s+pr[aá]ticos|"
    r"quais?\s+documentos?|documentos?\s+(necess[aá]rios|preciso)|"
    r"o\s+que\s+devo\s+fazer|como\s+posso\s+fazer",
    re.IGNORECASE,
)


def pre_classify(question: str) -> dict:
    """
    Pré-classificador determinístico por regex.

    Retorna um dicionário com overrides de classificação quando detectar
    padrões claros. Dicionário vazio = deixar o LLM classificar livremente.

    Esta função é ZERO-COST (sem chamadas LLM) e não falha.
    """
    q = question.strip()
    overrides: dict = {}
    matched_legal_pattern = False
    article_numbers = extract_requested_article_numbers(q)
    if article_numbers:
        overrides["requested_article_numbers"] = article_numbers

    # 0. Detectar transformações (resumir, simplificar, fale mais)
    for pattern, fields in TRANSFORMATION_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            overrides.update(fields)
            break

    # 1. Detectar diploma/ramo por padrão de regex
    for pattern, fields in DIPLOMA_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            overrides.update(fields)
            matched_legal_pattern = True
            break  # Primeiro match ganha (ordenados por especificidade)

    # 2. Detectar audiência
    if re.search(_TECNICO_PATTERNS, q):
        overrides["audience"] = "tecnico"
    elif re.search(_LEIGO_PATTERNS, q):
        overrides["audience"] = "leigo"

    if _PRACTICAL_PATTERNS.search(q):
        overrides.setdefault("request_type", "passos_praticos")
    elif re.search(_TECNICO_PATTERNS, q):
        overrides.setdefault("request_type", "analise_tecnica")

    # 3. Se a pergunta contiver um diploma explícito com "artigo X", marcar como técnica
    if article_numbers:
        overrides.setdefault("audience", "tecnico")
        overrides["requires_strict_corpus_match"] = True

    # 4. Generic diploma detection — extract ANY law reference format
    #    Matches: "Lei n.º 3/08", "Lei 38/20", "Decreto n.º 42/22", "Decreto-Lei 5/19"
    _LAW_REF = re.compile(
        r"(?:Lei|Decreto(?:[\s-]Lei)?)\s+"
        r"(?:n\S*\s+)?"
        r"\d+\s*[/\-]\s*\d{2,4}",
        re.IGNORECASE,
    )
    _law_names = _LAW_REF.findall(q)
    if _law_names and not overrides.get("requested_diplomas"):
        overrides["requested_diplomas"] = [
            re.sub(r"\s+", " ", name).strip() for name in _law_names
        ]
    # Infer branch from generic keywords in the question
    if not overrides.get("main_branch") or overrides.get("main_branch") == "indeterminado":
        _q_lower = q.lower()
        for _kw, _branch in _BRANCH_KEYWORDS:
            if re.search(_kw, _q_lower):
                overrides["main_branch"] = _branch
                break

    # Do not force a single branch if the question clearly touches multiple branches.
    _q_lower = q.lower()
    branch_hits = {
        _branch for _kw, _branch in _BRANCH_KEYWORDS if re.search(_kw, _q_lower)
    }
    explicit_branch_names = {
        branch
        for branch in branch_hits
        if re.search(rf"\b{re.escape(branch)}\b", _q_lower)
    }
    if len(branch_hits) > 1:
        if (
            _COMPARISON_MARKERS.search(q)
            or len(explicit_branch_names) > 1
            or overrides.get("needs_multi_branch_handling")
            or overrides.get("main_branch") == "misto"
        ):
            overrides["main_branch"] = "misto"
            overrides["topic_route"] = "geral"
            overrides["request_type"] = "comparacao"
            overrides["specificity"] = "comparacao_multi_ramo"
            overrides["needs_multi_branch_handling"] = True
            overrides["branch_candidates"] = sorted(branch_hits)
            overrides["force_main_branch"] = True
            overrides["force_topic_route"] = True
        elif not matched_legal_pattern:
            overrides.pop("force_main_branch", None)
            overrides.pop("force_topic_route", None)

    return overrides


def apply_pre_classification(classification_data: dict, question: str) -> dict:
    """
    Aplica os overrides determinísticos sobre os dados do LLM.

    Regra: o pré-classificador prevalece para main_branch, topic_route
    e requested_diplomas quando o LLM retornou 'indeterminado' ou lista vazia.
    Para audiência, o regex prevalece sempre (é mais confiável que o LLM aqui).
    """
    overrides = pre_classify(question)
    if not overrides:
        return classification_data

    merged = dict(classification_data)

    # main_branch: override se o LLM falhou, ou se o padrão é forte o suficiente
    if "main_branch" in overrides:
        if overrides.get("force_main_branch") or merged.get("main_branch") in (
            "indeterminado",
            None,
            "",
        ):
            merged["main_branch"] = overrides["main_branch"]

    # topic_route: override se o LLM retornou 'geral', ou em padrões fortes
    if "topic_route" in overrides:
        if overrides.get("force_topic_route") or merged.get("topic_route") in (
            "geral",
            None,
            "",
        ):
            merged["topic_route"] = overrides["topic_route"]

    # requested_diplomas: enriquecer se estiver vazio, ou corrigir se o padrão é forte
    if "requested_diplomas" in overrides:
        if overrides.get("force_main_branch") or not merged.get("requested_diplomas"):
            merged["requested_diplomas"] = overrides["requested_diplomas"]

    # A classificação pode inferir o ramo, mas nunca deve fingir que o utilizador
    # pediu um diploma. O filtro estrito e os boosts documentais só são seguros
    # quando o nome ou uma abreviatura inequívoca aparece na própria pergunta.
    merged["requested_diplomas"] = _explicit_requested_diplomas(question)

    if "requested_article_numbers" in overrides:
        existing_articles = merged.get("requested_article_numbers") or []
        merged["requested_article_numbers"] = list(
            dict.fromkeys([*existing_articles, *overrides["requested_article_numbers"]])
        )

    if "branch_candidates" in overrides:
        merged["branch_candidates"] = overrides["branch_candidates"]

    # audience: o regex prevalece sempre (mais confiável)
    if "audience" in overrides:
        merged["audience"] = overrides["audience"]

    # requires_strict_corpus_match: só activa, nunca desactiva
    if overrides.get("requires_strict_corpus_match"):
        merged["requires_strict_corpus_match"] = True

    # is_transformation / transformation_type: override always (deterministic)
    if overrides.get("is_transformation"):
        merged["is_transformation"] = True
        merged["transformation_type"] = overrides.get(
            "transformation_type", "summarize"
        )

    if "request_type" in overrides:
        merged["request_type"] = overrides["request_type"]
    if "specificity" in overrides:
        merged["specificity"] = overrides["specificity"]
    if overrides.get("needs_multi_branch_handling"):
        merged["needs_multi_branch_handling"] = True
    if overrides.get("needs_clarification"):
        merged["needs_clarification"] = True
        merged["clarifying_questions"] = overrides.get("clarifying_questions") or []

    return merged
