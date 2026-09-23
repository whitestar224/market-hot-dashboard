"""Shared typed question for JEV rapid judgement."""

RAPID_PRIORITY_QUESTIONS = {
    "research_priority": {
        "type": "choice",
        "instructions": "Using the supplied V4.9 hotspot opportunity, market-mainline relation, identity, person-catalyst, Meta-family, survival, thesis-memory and four-lifeline facts, decide only how urgently this new token deserves deeper evidence research. Non-official hotspot assets may be P0/P1 when mapping and real market acceptance are strong; officiality is not a veto. Do not predict price and do not treat price appreciation alone as quality.",
        "criteria": {
            "deep-research": "Identity or catalyst is traceable, liquidity is usable, buyers and activity show real continuation, at least two independent lifelines align, and this token plausibly captures the mechanism or narrative rather than merely copying it.",
            "watch": "The launch is early or signals conflict: there is a potentially useful source, Meta role, buyer response or survival clue, but identity, persistence, exit depth, lifeline confirmation or token value capture still needs verification.",
            "reject": "Only a name, clone, rank or price move is visible; identity mapping is unsupported, liquidity or exits are unusable, activity is decaying, the thesis is broken, or dormant/reactivating claims lack two independent lifelines.",
        },
    },
}
