"""Word and phrase lists for the take-review heuristics.

Pure data, no logic. Kept apart from :mod:`video_generator.domain.takes` so the
analysis code stays readable and the lists are easy to tune in one place.
Everything here is compared after :func:`video_generator.domain.takes._normalize`
(lowercase, accent-free), so the entries are written without accents.
"""

from __future__ import annotations

# pt-BR closed-class words plus a few high-frequency verbs/adverbs that carry no
# topical content. Used to reduce a segment to its "content-word bag" before
# measuring how much two segments say the same thing.
STOPWORDS = frozenset(
    """
    a o e as os um uma uns umas de do da dos das em no na nos nas por para pra
    pro com sem sob sobre ate entre desde apos contra que se ao aos
    eu tu ele ela nos vos eles elas voce voces ce ces me te lhe
    meu minha meus minhas teu tua seu sua seus suas nosso nossa dele dela
    este esta esse essa isso isto aquilo aquele aquela ai aqui ali la
    eh sao ser sou estar estao estava tem tinha ter haver havia
    vai vou vamos ir foi era nao sim ja agora entao assim tambem so mais menos
    muito pouco bem mal como quando onde porque pois mas porem logo
    qual quais quem cujo cada todo toda todos todas algum alguma nenhum
    tipo cara mano vei velho neh ne enfim digamos
    coisa coisas negocio negocios lance dessa desse disso desta deste
    ficar fica ficou fazer faz fez faca dizer diz disse falar fala falou
    """.split()
)

# Discourse filler: tokens that are almost always throat-clearing in speech.
FILLER_WORDS = frozenset(
    {
        "ne",
        "tipo",
        "mano",
        "cara",
        "vei",
        "velho",
        "enfim",
        "hum",
        "hmm",
        "ã",
        "ãã",
        "ããã",
        "aham",
        "ahn",
        "aham",
        "sabe",
        "assim",
        "beleza",
    }
)

# Two-word filler openers ("sei la", "ou seja", "quer dizer" as hedges).
FILLER_BIGRAMS = (
    ("sei", "la"),
    ("ou", "seja"),
    ("tipo", "assim"),
    ("meio", "que"),
    ("cara", "assim"),
)

# The presenter narrating their own performance rather than the subject. Matched
# as a normalized substring anywhere in the segment. Curated to avoid delivered,
# on-brand lines the editor wants kept.
SELF_COMMENTARY_PHRASES = (
    "esta uma bagunca",
    "ta uma bagunca",
    "que bagunca",
    "desculpa a bagunca",
    "eu me enrolo",
    "me enrolei",
    "acho que me enrolei",
    "ja to entrando em detalhe",
    "ja estou entrando em detalhe",
    "entrando em detalhe que eu nao ia",
    "nao ia entrar nesse video",
    "nao ia entrar nesse assunto",
    "nao vou entrar muito nisso",
    "vou tentar ser claro",
    "vou tentar nao me enrolar",
    "deixa eu tentar de novo",
    "deixa eu refazer",
    "acho que deu pra entender",
    "se e que deu pra entender",
    "esse e o primeiro video",
    "esse aqui e o primeiro video",
    "primeiro modelo",
    "ficou basicamente isso aqui",
    "acho que eu consigo explicar",
    "espero que tenha ficado claro",
)

# Markers that announce a widening of scope past what the video set out to do.
# A run of low-topic segments that also contains one of these is a tangent.
SCOPE_WIDENING_MARKERS = (
    "a gente consegue deixar mais complex",
    "da pra deixar mais complex",
    "isso nao e linear",
    "voce nao precisa entender isso agora",
    "voce nao precisa saber isso agora",
    "nao precisa entender isso agora",
    "indo um pouco alem",
    "fugindo um pouco",
    "so um adendo",
    "abrindo um parentese",
    "mudando de assunto",
    "so pra constar",
    "aprofundando um pouco",
    "entrando mais a fundo",
)

# Restart / failed-take openers (sentence-initial after normalization).
RESTART_MARKERS = (
    "na verdade",
    "deixa eu",
    "deixem eu",
    "quer dizer",
    "ou melhor",
    "melhor dizendo",
    "vou refazer",
    "deixa eu refazer",
    "recomecando",
    "de novo",
    "espera",
    "espere",
    "perai",
    "pera",
    "calma",
)

# Scaffolds that introduce an example.
EXAMPLE_MARKERS = (
    "por exemplo",
    "tipo assim",
    "imagina que",
    "imagine que",
    "digamos que",
    "vamos supor",
    "supondo que",
    "por exemplo assim",
)
