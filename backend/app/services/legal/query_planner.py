from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.legal.models import LegalClassification


TOKEN_RE = re.compile(r"[\wÀ-ÿ-]+", re.UNICODE)
CLAUSE_RE = re.compile(r"(?:[?;]|\b(?:e ainda|bem como|além disso|alem disso)\b)", re.IGNORECASE)
STOPWORDS = {
    "a", "ao", "aos", "as", "como", "com", "da", "das", "de", "do", "dos",
    "e", "ela", "ele", "em", "entre", "essa", "esse", "esta", "este", "eu",
    "me", "meu", "minha", "na", "nas", "no", "nos", "o", "os", "ou", "para",
    "pela", "pelo", "por", "qual", "quais", "que", "se", "sem", "ser", "sobre",
    "tem", "ter", "um", "uma",
}


@dataclass(slots=True)
class LegalSearchTask:
    query: str
    branch: str | None = None
    purpose: str = "issue"


@dataclass(slots=True)
class LegalQueryPlan:
    original_query: str
    issues: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    tasks: list[LegalSearchTask] = field(default_factory=list)


class LegalQueryPlanner:
    def plan(
        self, question: str, classification: LegalClassification
    ) -> LegalQueryPlan:
        normalized = re.sub(r"\s+", " ", question).strip()
        clauses = [part.strip(" .,:;?-") for part in CLAUSE_RE.split(normalized)]
        issues = [part for part in clauses if len(part.split()) >= 3] or [normalized]
        tokens = [
            token.casefold()
            for token in TOKEN_RE.findall(normalized)
            if len(token) >= 3 and token.casefold() not in STOPWORDS
        ]
        concepts = list(dict.fromkeys(tokens))[:14]
        entities = list(
            dict.fromkeys(
                match.group(0).strip()
                for match in re.finditer(
                    r"\b(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ-]+(?:\s+|$)){2,5}", question
                )
            )
        )[:8]

        branches = [
            branch
            for branch in (
                classification.branch_candidates
                if classification.needs_multi_branch_handling
                else [classification.main_branch]
            )
            if branch not in {"misto", "indeterminado"}
        ]
        tasks = [LegalSearchTask(query=normalized, purpose="original")]
        for issue in issues[:5]:
            tasks.append(LegalSearchTask(query=issue, purpose="decomposed_issue"))
        for branch in branches[:4]:
            for issue in issues[:3]:
                tasks.append(
                    LegalSearchTask(
                        query=f"{issue}. Ramo jurídico: {branch}",
                        branch=branch,
                        purpose="branch_issue",
                    )
                )

        unique: dict[tuple[str, str | None], LegalSearchTask] = {}
        for task in tasks:
            unique.setdefault((task.query.casefold(), task.branch), task)
        return LegalQueryPlan(
            original_query=normalized,
            issues=issues[:5],
            concepts=concepts,
            entities=entities,
            tasks=list(unique.values()),
        )


legal_query_planner = LegalQueryPlanner()
