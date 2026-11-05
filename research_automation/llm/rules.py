"""Narrow, auditable surface-form proofs; unmatched language always falls back to review.

These rules establish what a sentence says, not whether its economic claim is true.
"""
from __future__ import annotations

import re


def words(text):
    tokens = re.findall(r"[-+−]?\d+(?:\.\d+)?|[%$€₩]|[a-z]+(?:-[a-z]+)*|[가-힣]+", text.casefold())
    forms = {
        "costs": "cost", "prices": "price", "rates": "rate", "margins": "margin",
        "expenditures": "expenditure", "sales": "sale", "farms": "farm", "plants": "plant",
        "falling": "fall", "fell": "fall", "fallen": "fall",
    }
    return [forms.get(token, token) for token in tokens if token not in {"a", "an", "the", "its", "s"}]


def scope_matches(term, label):
    """Allow inserted modifiers, preserving English token boundaries (government != nongovernment)."""
    label_words = set(words(label))
    label_folded = " ".join(label.casefold().split())
    return all(
        token in label_folded if re.search(r"[가-힣]", token) else token in label_words
        for token in words(term) if token not in {"in", "of", "at", "for"}
    )


def complete_literal_level(label, source, effect_label=None):
    """A literal level label is not a missing delta, unless an adjacent delta qualifies it."""
    if re.search(r"[가-힣]", label):
        return False  # This optional level/delta repair is limited to English token boundaries.
    target, tokens = words(label), words(source)
    if effect_label is not None and any(
        words(relation["cause"]) == target and words(relation["effect"]) == words(effect_label)
        for relation in direct_relations(source)
    ):
        return True  # "Record sales increased revenue": increased is the effect predicate.
    for start in range(len(tokens) - len(target) + 1):
        if not target or tokens[start:start + len(target)] != target:
            continue
        before, after = tokens[:start], tokens[start + len(target):]
        modifiers = {"higher", "lower", "rising", "falling", "increasing", "decreasing", "declining", "growing"}
        changes = {"increase", "decrease", "fall", "rise", "decline", "reduction", "growth"}
        if before and before[-1] in modifiers:
            continue
        if len(before) >= 2 and before[-1] in {"in", "of"} and before[-2] in changes:
            continue
        if after and after[0] in {"fall", "declined", "increased", "decreased", "rose", "up", "down"}:
            continue
        if len(after) >= 2 and after[0] in {"is", "was", "were"} and after[1] in modifiers | {"up", "down"}:
            continue
        return True
    return False


def period_keys(text):
    text = text.casefold()
    keys = set()
    for index, name in enumerate(("first", "second", "third", "fourth"), 1):
        if re.search(rf"\b(?:{name}[ -]quarter|q{index}|{index}(?:st|nd|rd|th)[ -]quarter)\b|{index}\s*분기", text):
            keys.add(f"q{index}")
    patterns = {
        "previous_quarter": r"previous[ -]quarter|prior[ -]quarter|전\s*분기|지난\s*분기",
        "next_quarter": r"next[ -]quarter|다음\s*분기",
        "year_over_year": r"year[ -]over[ -]year|전년\s*동기|전년\s*대비",
    }
    keys.update(key for key, pattern in patterns.items() if re.search(pattern, text))
    return keys


def qualifier_check(source, claim, source_modality, proof=None):
    """Check recognized conditions; decline unparsed conditions instead of inventing equivalence."""
    if proof is not None:
        return "supported", "complete_clause"
    node_text = claim.cause_variable + " " + claim.effect_variable
    numbers = lambda text: set(re.findall(r"[-+−]?\d+(?:\.\d+)?", text))
    if not numbers(node_text) <= numbers(source):
        return "contradicted", "candidate_number_missing_from_source"
    if (re.search(r"\b(?:than|relative|compared)\b|대비|year-over-year", source, re.IGNORECASE)
            and not period_keys(source) <= period_keys(node_text)):
        return "contradicted", "comparison_period_missing_from_nodes"
    if source_modality == "conditional":
        match = re.search(r"\bif\s+([^,.;]+)", source, re.IGNORECASE)
        if match is None or not set(words(match[1])) <= set(words(claim.cause_variable)):
            return "uncertain", "unresolved_explicit_condition"
    if re.search(r"\b(?:unless|provided that|only when|only if)\b", source, re.IGNORECASE):
        return "uncertain", "unparsed_condition"
    if claim.horizon != "unknown" and (claim.horizon != "short" or "next_quarter" not in period_keys(source)):
        return "uncertain", "horizon_not_explicitly_supported"
    return "supported", "recognized_qualifiers_preserved"


UP = {"increase", "increased", "increases", "raise", "raised", "raises"}
DOWN = {"decrease", "decreased", "decreases", "lower", "lowered", "lowers", "reduce", "reduced", "reduces"}
VERBS = "|".join(sorted(UP | DOWN | {"improve", "improves", "improved"}, key=len, reverse=True))
PREDICATE = re.compile(r"\b(?:(?P<modal>may|might|could|will)\s+)?(?P<verb>" + VERBS + r")\b", re.IGNORECASE)
CONDITIONAL = re.compile(
    r"^if\s+(?P<cause>[^,]+),\s+.*?\bexpects?\s+(?:its\s+|the\s+)?"
    r"(?P<effect>.+?)\s+to\s+(?P<verb>improve|increase|decrease|fall|rise)\s*[.!]?\s*$", re.IGNORECASE,
)
KOREAN_DIRECT = re.compile(
    r"^(?:(?P<topic>.+?)(?:은|는)\s+)?(?P<cause>.+?)(?:으로|로)\s+"
    r"(?P<effect>.+?)(?:이|가)\s+(?P<verb>상승|하락|증가|감소)"
    r"(?:했다|하였다|했으나|하였으나|했지만|하였지만)[.!]?\s*$"
)
KOREAN_FORECAST = re.compile(
    r"^(?:(?:회사\s*측은|보고서는)\s+)?(?P<cause>.+?)(?:이|가|으로|로)\s+"
    r"(?P<effect>.+?)(?:을|를|이|가)\s+(?P<verb>낮출|낮아질|높일|높아질|증가할|감소할)\s+"
    r"것으로\s+(?:전망했다|예상했다)[.!]?\s*$"
)
KOREAN_CHANNEL = re.compile(
    r"^(?P<cause>.+?)(?:으로|로)\s+(?P<effect>.+?)(?:이|가)?\s+"
    r"(?P<verb>낮아짐|낮아질|낮춤|하락함|하락|높아짐|높아질|높임|상승함|상승)"
    r"(?:\s+전망)?[.!]?\s*$"
)


def clauses(text):
    for part in re.split(r";|,\s*(?:but|while)\s+", text, flags=re.IGNORECASE):
        part = part.strip().rstrip(".! ")
        if part:
            yield part


def direct_relations(text):
    for part in clauses(text):
        if re.match(r"if\b", part, re.IGNORECASE) or "?" in part or "？" in part:
            continue
        for match in PREDICATE.finditer(part):
            cause, effect = part[:match.start()].strip(), part[match.end():].strip()
            # Avoid treating a noun such as "tax increase" as the sentence's predicate.
            if not cause or not effect or PREDICATE.match(effect):
                continue
            modal = (match["modal"] or "").casefold()
            verb = match["verb"].casefold()
            direction = "increase" if verb in UP else "decrease" if verb in DOWN else None
            if verb.startswith("improv") and "margin" in words(effect):
                direction = "increase"
            if not direction:
                continue
            yield {
                "cause": cause, "effect": effect,
                "direction": direction,
                "modality": "possible" if modal in {"may", "might", "could"} else "forecast" if modal == "will" else "observed",
                "horizon": "unknown",
            }


def source_proof(sentences, claim):
    for sentence in sentences:
        # A rule may not silently replace the candidate's evidence with a different sentence.
        if " ".join(claim.evidence_text.casefold().split()) not in " ".join(sentence["text"].casefold().split()):
            continue
        relations = list(direct_relations(sentence["text"]))
        driven = re.fullmatch(r"(?P<effect>.+?),\s*driven by\s+(?P<cause>.+?)[.!]?\s*", sentence["text"].strip(), re.IGNORECASE)
        if driven:
            effect = re.split(r"\s+as\s+", driven["effect"], flags=re.IGNORECASE)[-1]
            if not re.search(r"\b(?:not|no|never|if)\b|[?？]", effect, re.IGNORECASE):
                up = re.search(r"\b(?:grows?|grew|growth|increases?|increased|rises?|rose|higher)\b", effect, re.IGNORECASE)
                down = re.search(r"\b(?:falls?|fell|declines?|declined|decreases?|decreased|lower)\b", effect, re.IGNORECASE)
                if bool(up) != bool(down):
                    relations.append({
                        "cause": driven["cause"], "effect": effect,
                        "direction": "increase" if up else "decrease",
                        "modality": "forecast" if re.search(r"\bwill\b", effect, re.IGNORECASE) else "possible" if re.search(r"\b(?:may|might|could)\b", effect, re.IGNORECASE) else "observed",
                        "horizon": "unknown", "connector": "driven by",
                    })
        for part in re.split(r",\s*|;", sentence["text"].strip()):
            forecast = KOREAN_FORECAST.fullmatch(part.strip())
            if forecast:
                relations.append({
                    "cause": forecast["cause"], "effect": forecast["effect"],
                    "direction": "increase" if forecast["verb"] in {"높일", "높아질", "증가할"} else "decrease",
                    "modality": "forecast",
                    "horizon": "short" if "next_quarter" in period_keys(part) else "unknown",
                })
            korean = KOREAN_DIRECT.fullmatch(part.strip())
            if korean and re.search(r"증가|감소|상승|하락|개선|악화|인상|인하|급등|급락|확대|축소", korean["cause"]):
                topic = korean["topic"]
                effect = (topic + "의 " if topic else "") + korean["effect"]
                relations.append({
                    "cause": korean["cause"], "effect": effect,
                    "direction": "increase" if korean["verb"] in {"상승", "증가"} else "decrease",
                    "modality": "observed", "horizon": "unknown",
                })
        condition = CONDITIONAL.fullmatch(sentence["text"].strip())
        if condition:
            verb = condition["verb"].casefold()
            direction = {"increase": "increase", "rise": "increase", "decrease": "decrease", "fall": "decrease"}.get(verb)
            if verb == "improve" and "margin" in words(condition["effect"]):
                direction = "increase"
            if direction:
                relations.append({
                    "cause": condition["cause"], "effect": condition["effect"],
                    "direction": direction, "modality": "conditional",
                    "horizon": "short" if re.search(r"\bnext quarter\b", condition["cause"], re.IGNORECASE) else "unknown",
                })
        for relation in relations:
            if words(relation["cause"]) != words(claim.cause_variable):
                continue
            if words(relation["effect"]) != words(claim.effect_variable):
                continue
            if claim.horizon not in {"unknown", relation["horizon"]}:
                continue
            return {**relation, "sentence_ids": [sentence["id"]], "route": "exact_clause"}
    return None


def direct_channel_relation(claim):
    excluded = {
        "increase", "increases", "increased", "rising", "rise", "fall", "lower", "higher",
        "decrease", "decreases", "decreased", "improvement", "improved", "reduction",
        "conditional", "next", "previous", "quarter", "third", "second", "first",
        "relative", "compared", "to", "in", "of", "at", "for",
    }

    def entity(label):
        return [token for token in words(label) if token not in excluded]

    relations = list(direct_relations(claim.transmission_channel))
    korean = KOREAN_CHANNEL.fullmatch(claim.transmission_channel.strip())
    if korean and period_keys(korean["cause"]) <= period_keys(claim.cause_variable):
        def compact(text, cause=False):
            text = text.casefold()
            if cause:
                text = re.sub(r"^(?:다음|지난|전|이번)\s*분기\s*", "", text)
            text = re.sub(r"의(?=\s)", "", text)
            return re.sub(r"\s+", "", text)

        if (compact(korean["cause"], True) == compact(claim.cause_variable, True)
                and compact(korean["effect"]) == compact(claim.effect_variable)):
            return {
                "cause": korean["cause"], "effect": korean["effect"],
                "direction": "increase" if korean["verb"] in {"높아짐", "높아질", "높임", "상승함", "상승"} else "decrease",
            }
    for relation in relations:
        if (entity(relation["cause"]) == entity(claim.cause_variable)
                and words(relation["effect"]) == words(claim.effect_variable)):
            return relation
    return None


def linked_verbatim_channel(sentences, linked_ids, claim):
    """Only a nearby literal mechanism sharing BOTH edge endpoints can bypass paraphrase review."""
    channel = " ".join(claim.transmission_channel.casefold().split())
    cause_words, effect_words = set(words(claim.cause_variable)), set(words(claim.effect_variable))
    mechanism_words = set(words(claim.transmission_channel))
    if not (cause_words & mechanism_words) or len(effect_words & mechanism_words) < min(2, len(effect_words)):
        return []
    for sentence in sentences:
        if not any(abs(sentence["id"] - index) <= 1 for index in linked_ids):
            continue
        text = " ".join(sentence["text"].casefold().split())
        if re.search(r"\b(?:not|no|never|without)\b|않|아닌", text):
            continue
        if channel in text:
            return [sentence["id"]]
    return []
