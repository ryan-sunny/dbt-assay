"""Typed judgments from Jev, cached, accounted, and read back.

Jev answers a map of typed questions about one `state` and cannot return anything outside the
options given, which is what separates it from asking a chat model for JSON and hoping.

*** THIS IS A CACHE, NOT A CLIENT WRAPPER, AND THAT IS THE POINT. ***
Every answer lands in `model_decisions`, one row per ANSWER rather than per call, keyed on
(decision_key, question, prompt_version, model_version) with a hash of the state checked on read.
A hit whose state moved is a MISS: the subject changed under a key that did not. Change a question's
criteria, bump `prompt_version`, and only what changed re-runs. Touch nothing and it costs nothing
forever.

*** THE FULL DISTRIBUTION IS STORED, NOT THE WINNER. ***
A calibration check needs it, a gate needs to see the runner-up sitting at 0.19, and a decision
re-read in six months with only its winner cannot say whether it was close. It is a few hundred
bytes.

*** BATCH QUESTIONS, NEVER SUBJECTS. ***
Independent questions about one state go in ONE request and are evaluated in parallel; adding a
question costs only its own tokens. Several SUBJECTS stuffed into one state collapses confidence.
`ask()` makes the first easy and gives you nowhere to do the second.

*** A CHOICE'S CONFIDENCE IS DISTRIBUTION CONCENTRATION, NOT PERMISSION TO ACT. ***
Nothing here multiplies a confidence by anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

# $0.042 per million INPUT tokens; output is free. A 300-model sweep at ~1k tokens of state each is
# about 1.2 cents, and nothing at all once the cache is warm.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000

# *** WHAT assay ASKS THE ROUTER NOT TO DO WITH YOUR SQL. ***
# A judged call sends a digest of compiled SQL and the prose your project wrote about itself. Who
# may serve that, and whether they may keep it, is a decision somebody should be able to make and
# see -- and until now assay made it by default and said nothing.
#
# `data_collection: deny` restricts routing to endpoints that do not collect prompts.
# `zdr: true` restricts it further, to zero-retention endpoints.
# `require_parameters: true` keeps the request away from a provider that would silently drop the
# parameters it was sent, which for a typed-decision call would change the answer rather than
# fail.
# `allow_fallbacks: false` makes a request FAIL rather than route somewhere you did not choose --
# a 503 is then the policy working, not an outage.
PROVIDER_POLICY = {
    "data_collection": "deny",
    "require_parameters": True,
    "allow_fallbacks": False,
}

PROVIDERS = {
    "typesafe": {
        "url": "https://api.typesafe.ai/v1/systemone",
        "env": "TYPESAFE_API_KEY",
        "model": "jev-latest",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/alpha/decisions",
        "env": "OPENROUTER_API_KEY",
        "model": "typesafe/jev-1.13",
        # The router in front of many providers, so routing policy is a thing to state here.
        "takes_provider": True,
        # Real headroom, which pairs with the ledger: what a key has left, from the key itself.
        "key_url": "https://openrouter.ai/api/v1/key",
    },
}

DDL = """
create table if not exists model_decisions (
    decision_key   varchar,
    question       varchar,
    kind           varchar,      -- choice | noul | score
    answer         varchar,      -- the chosen option, the 0-1 truth, or the score, as text
    confidence     double,       -- choice/score only; a noul has NO separate confidence
    probabilities  varchar,      -- the full distribution as given
    state_hash     varchar,
    prompt_version varchar,
    model_version  varchar,      -- what ANSWERED, never what was asked for
    call_id        varchar,
    caller         varchar,
    -- *** WHAT THE JUDGMENT WAS ABOUT, IN WORDS. ***
    -- A decision key is a cache key, and for a batched family it is a hash of the whole batch, so
    -- the store could not say WHICH pair `align__4` referred to. A verdict nobody can reach in
    -- five seconds does not get given, so every answer carries a line a person can read.
    context        varchar,
    -- *** THIS IS THE CALL'S TOKEN COUNT, WRITTEN ON EVERY ANSWER THE CALL PRODUCED. ***
    -- A batch of eight questions about one state is ONE call and eight rows, and each row carries
    -- the whole call's number. Summing this column over decisions therefore counts a batched call
    -- once per answer: on the field store that reads 101,163,351 tokens for 31,426,560 spent, and
    -- it is where the $4.24 in an earlier spec came from against a real $1.32.
    --
    -- It stays, because it is a true fact about the row's call and something may want it without
    -- a join. Every TOTAL goes through `model_calls`, which has one row per call and cannot
    -- double count. `file_checksum` below is the same discipline pointed at the code.
    input_tokens   integer,
    -- *** WHAT THE ANSWER WAS COMPUTED FROM, AS dbt HASHED IT. ***
    -- The sha256 dbt already records for the model's source file, copied in at decide time. A
    -- judged answer whose model's checksum has since moved is about SQL that no longer exists,
    -- and comparing is a dict lookup rather than a call. NULL when the decision key does not name
    -- a model (`pair::`, `bank`) or the project was not registered -- which reports as "cannot be
    -- checked", never as current.
    file_checksum  varchar,
    -- *** HOW TO BUILD THIS STATE AGAIN. ***
    -- The registered builder's name, and the identifiers it was built from -- a uid, a list of
    -- claim ids, a pair of relations. Never derived content: content recorded here is content a
    -- rebuild could not notice changing, which is the one thing the rebuild is for.
    --
    -- Before this, `state_hash` was a one-way number. It could say two answers came from the same
    -- state and could never say whether that state is still what the code says, because nothing
    -- could produce it a second time -- measured at 0 of 871 subjects on the field warehouse.
    state_builder  varchar,
    state_inputs   varchar,      -- json
    decided_at     timestamp,
    primary key (decision_key, question, prompt_version, model_version)
);
-- *** ONE ROW PER CALL, BECAUSE THE CALL IS THE THING WITH A PRICE. ***
-- `model_decisions` is one row per ANSWER and carries the call's tokens on each of them, so every
-- total taken from it was wrong the moment a question got batched. Three separate numbers in one
-- spec came from that. The unit that costs money has its own table now and a sum over it is right
-- by construction rather than by everybody remembering to deduplicate.
--
-- NEVER_PRUNED: it is the record of what was spent.
create table if not exists model_calls (
    call_id       varchar primary key,
    -- provider | minted | reconstructed. The provider returned no id on 9,762 of 19,707 decisions
    -- on the field store, so half of it could not say what a call even was. assay mints its own
    -- now, and history was reconstructed from the decision rows -- which is a weaker fact than a
    -- provider id and says so here rather than passing as one.
    id_source     varchar,
    caller        varchar,
    model_name    varchar,      -- what ANSWERED, as the provider named it
    input_tokens  integer,      -- NULL when the provider returned no usage. Never a zero.
    -- *** SHOWN, NEVER PRICED. ***
    -- Jev does not bill output. The count is worth seeing and multiplying it by anything would be
    -- inventing a rate, so there is no output column in any dollar figure assay prints.
    output_tokens integer,
    usd           double,       -- input only, at the rate below. NULL when usage was absent.
    -- *** THE RATE THAT WAS IN FORCE, ON THE ROW. ***
    -- Derived at read time, a price change silently rewrites every historical total. Stored here,
    -- what was spent stays what was spent. Same argument `prompt_version` already makes.
    usd_per_input_token double,
    called_at     timestamp
);
"""


class BudgetExceeded(RuntimeError):
    pass


class NoProvider(RuntimeError):
    pass


def choice(instructions, criteria: dict) -> dict:
    """One of a defined set. ALWAYS include a no-match option where nothing may fit: a real bug
    surfaced only because the model was allowed to say the answer was not on the list."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions, true_means=None, false_means=None) -> dict:
    """Probability that a condition holds. Near 0.5 means similar probability for yes and no --
    NOT medium intensity, and not a weak yes."""
    q = {"type": "noul", "instructions": instructions}
    if true_means or false_means:
        q["criteria"] = {"true": true_means or "", "false": false_means or ""}
    return q


def score(instructions, levels: list) -> dict:
    """Probability-weighted position on ORDERED levels. Each level must describe a concrete
    situation and stand on its own; 'medium' describes nothing."""
    if len(levels) < 2:
        raise ValueError("a score needs at least two levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


_KEY_NAMES = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY")
_DOTENV_PATH: str | None = None
_FROM_ENV: set | None = None


def state_hash(state) -> str:
    """Stable across dict ordering, because a re-serialised state is not a changed one."""
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:32]


def key_source(env_name: str) -> str:
    """Where the key came from, so `assay config` can say rather than imply."""
    if env_name in _FROM_ENV:
        return "environment"
    return f"{_DOTENV_PATH}" if _DOTENV_PATH else "environment"


def load_env() -> None:
    """*** A KEY IN A .env IS A KEY, AND assay CALLED IT ABSENT. ***

    Ported from a repo whose client did read `.env`; the loader was dropped on the way across, so
    every judged command printed "no API key found" while the key sat one directory up. That is the
    worst shape a capability check can take: the tier was off, the tool said so plainly, and it was
    wrong.

    `override=False`, so an exported variable always beats a file. The shell is the more deliberate
    of the two statements and a stale file must not win over what someone just typed.
    """
    global _DOTENV_PATH, _FROM_ENV
    if _FROM_ENV is not None:
        return
    _FROM_ENV = {n for n in _KEY_NAMES if os.environ.get(n)}
    found = find_dotenv(usecwd=True)
    if found:
        _DOTENV_PATH = found
        load_dotenv(found, override=False)


def resolve_provider(name: str = "auto") -> tuple[str, dict, str]:
    """Whichever key is actually present. TypeSafe direct is waitlisted; OpenRouter is not."""
    load_env()
    order = ["typesafe", "openrouter"] if name == "auto" else [name]
    for p in order:
        spec = PROVIDERS.get(p)
        if not spec:
            raise NoProvider(f"unknown provider {p!r}. Use one of {sorted(PROVIDERS)}.")
        key = os.environ.get(spec["env"])
        if key:
            return p, spec, key
    raise NoProvider(
        "no API key found. Set TYPESAFE_API_KEY or OPENROUTER_API_KEY, in your environment or in "
        "a .env in this directory or above it. `assay config` shows what assay resolved. The "
        "structural checks need neither: run `assay check`.")


@dataclass
class Client:
    provider: str = "auto"
    model: str | None = None
    max_spend_usd: float = 1.0
    timeout: int = 120
    retries: int = 3
    spent_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    # None means the shipped policy; {} means send none. `jev.provider` in audit.yml sets it.
    provider_policy: dict | None = None
    # Whether the endpoint ever rejected the policy -- measured, not assumed.
    provider_rejected: str = ""
    _resolved: tuple | None = field(default=None, repr=False)

    def _conn(self) -> tuple[str, dict, str]:
        if self._resolved is None:
            self._resolved = resolve_provider(self.provider)
        return self._resolved

    @property
    def available(self) -> bool:
        try:
            self._conn()
            return True
        except NoProvider:
            return False

    def ask(self, state, questions: dict, *, caller: str = "assay") -> dict:
        """One batch of questions about one state. Uncached; `decide()` is the one with the table."""
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError(
                "the judgment tier needs httpx: `uv add dbt-assay[jev]` or `pip install httpx`"
            ) from e

        _name, spec, key = self._conn()
        model = self.model or spec["model"]
        # *** A WAREHOUSE HOLDS `inf` AND `NaN`, AND JSON HAS NO WAY TO SAY EITHER. ***
        # Reported from the field (25.17): `feeds` sampled a source holding an `inf`, the request
        # body could not be built, and the command died with "jev failed after 3 attempts" --
        # which sends a person to their key, their network and the provider's status page for a
        # float in their own data. Coerced to null HERE, the one place every family sends
        # through, and counted so the command says it happened.
        clean, n_bad = finite(state)
        if n_bad:
            NONFINITE.append((caller, n_bad))
        body = {"model": model, "state": clean, "questions": questions}
        # *** SENT, AND NOT CLAIMED. ***
        # OpenRouter documents the `provider` object for CHAT COMPLETIONS. assay posts to
        # `/api/alpha/decisions`, a different endpoint that proxies TypeSafe's wire format, and
        # nothing says the object is honoured there. So it is sent -- it costs nothing and helps
        # if it is read -- and `assay config` reports it as UNCONFIRMED rather than as a
        # protection assay has. A guard that cannot see is worse than no guard, and a privacy
        # claim nobody verified is that guard.
        policy = self.provider_policy if self.provider_policy is not None else PROVIDER_POLICY
        if policy and spec.get("takes_provider"):
            body["provider"] = dict(policy)

        # Estimated BEFORE the call, because a cap that only fires after the spend is not a cap.
        est = len(json.dumps(body, default=str)) / 4 * USD_PER_INPUT_TOKEN
        if self.spent_usd + est > self.max_spend_usd:
            raise BudgetExceeded(
                f"would exceed the ${self.max_spend_usd:.2f} cap "
                f"(spent ${self.spent_usd:.4f}). Raise jev.max_spend_usd in audit.yml.")

        # Encoded ONCE, outside the retries. An encoding failure is assay's own bug, it happens
        # before anything reaches a provider, and retrying it only waits nine seconds to blame
        # the wrong component.
        try:
            payload = json.dumps(body, allow_nan=False, default=str)
        except ValueError as e:
            raise RuntimeError(
                f"assay could not encode the request for {caller} ({e}). Nothing was sent to the "
                f"provider; this is a bug in assay, not in your key or your network.") from e
        last = None
        for attempt in range(self.retries):
            try:
                r = httpx.post(spec["url"], content=payload, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {key}",
                                        "Content-Type": "application/json"})
                out = r.json()
                if "answers" not in out:
                    # *** A REJECTED POLICY IS DROPPED ONCE, LOUDLY, NOT SILENTLY FOREVER. ***
                    # The `provider` object is documented for chat completions and this is a
                    # different endpoint. If it turns out to be rejected, the request must still
                    # work -- but the fact that the routing policy was NOT applied has to survive,
                    # or assay would go on believing it asked for something it never sent.
                    if "provider" in body and r.status_code in (400, 422):
                        self.provider_rejected = (
                            f"HTTP {r.status_code}: this endpoint rejected the `provider` routing "
                            f"policy, so it was NOT applied. {json.dumps(out)[:160]}")
                        body.pop("provider")
                        continue
                    # A 4xx here is a malformed QUESTION, not a transient fault, and the message
                    # names the field. Surfacing it beats retrying what cannot succeed.
                    raise RuntimeError(f"HTTP {r.status_code}: {json.dumps(out)[:300]}")
                used = int((out.get("usage") or {}).get("input_tokens") or 0)
                self.calls += 1
                self.input_tokens += used
                self.spent_usd += used * USD_PER_INPUT_TOKEN if used else est
                return out
            except BudgetExceeded:
                raise
            except Exception as e:
                last = e
                if "HTTP 4" in str(e):
                    raise                       # a bad question will not fix itself
                if attempt < self.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"jev failed after {self.retries} attempts: {last}")


# (caller, how many values) for every request that carried a non-finite float, coerced to null.
# Read and printed by the command, the way `states.VOCAB_DROPS` is.
NONFINITE: list = []


def finite(obj) -> tuple:
    """(obj with every non-finite float replaced by None, how many were replaced)."""
    import math
    if isinstance(obj, float):
        return (None, 1) if not math.isfinite(obj) else (obj, 0)
    if isinstance(obj, dict):
        out, n = {}, 0
        for k, v in obj.items():
            out[k], m = finite(v)
            n += m
        return out, n
    if isinstance(obj, list | tuple):
        out_l, n = [], 0
        for v in obj:
            c, m = finite(v)
            out_l.append(c)
            n += m
        return out_l, n
    return obj, 0


def key_headroom(provider: str = "auto") -> dict:
    """What the key itself says is left, from the provider that knows.

    *** THE LEDGER SAYS WHAT WAS SPENT. THIS SAYS WHAT IS LEFT. ***
    `assay cost` reads the calls assay made, which cannot see a key shared with anything else, a
    credit limit set on it, or a balance nearing the floor where a router starts adding billing
    checks and expiring caches. One request, before a run, turns "will this finish" from a guess
    into a number.

    Unlike the routing policy, every figure here is the provider's own answer about the key, so
    it is reported as fact rather than as something assay asked for.
    """
    try:
        import httpx
    except ImportError:
        return {"note": "the judgment tier needs httpx: `uv add dbt-assay[jev]`"}
    try:
        name, spec, key = resolve_provider(provider)
    except NoProvider as e:
        return {"note": str(e)}
    url = spec.get("key_url")
    if not url:
        return {"provider": name,
                "note": f"{name} does not publish a key endpoint, so assay cannot say what this "
                        f"key has left. That is not the same as it having plenty."}
    try:
        r = httpx.get(url, timeout=15, headers={"Authorization": f"Bearer {key}"})
        d = (r.json() or {}).get("data") or {}
    except Exception as e:                                       # noqa: BLE001
        return {"provider": name, "note": f"could not read the key's limits: {e}"}
    return {"provider": name, "label": d.get("label"), "limit": d.get("limit"),
            "limit_remaining": d.get("limit_remaining"), "limit_reset": d.get("limit_reset"),
            "usage": d.get("usage"), "usage_daily": d.get("usage_daily"),
            "is_free_tier": d.get("is_free_tier")}


def unpack(ans: dict) -> tuple:
    """(kind, answer, confidence, probabilities) from any of the three primitives."""
    kind = ans.get("type")
    if kind == "noul":
        # A noul has NO separate confidence. Storing its own value there would invite a gate to
        # read "0.91 confident" off a question that answered "91% likely yes".
        return "noul", str(ans.get("noul")), None, json.dumps({"true": ans.get("noul")})
    if kind == "score":
        return ("score", str(ans.get("score")), ans.get("confidence"),
                json.dumps(ans.get("probabilities") or {}))
    return ("choice", ans.get("choice"), ans.get("confidence"),
            json.dumps(ans.get("probabilities") or {}))


def _checksum_for(store, decision_key: str) -> str | None:
    """The sha256 dbt recorded for the model this decision is about, or None.

    *** NONE IS A THIRD ANSWER AND NOT A QUIET PASS. ***
    Three ways to get here with nothing: the key does not name a model at all (`pair::`, `bank` --
    190 of 19,707 on the field store), the project was never registered on this store, or dbt
    recorded no checksum. All three mean assay cannot say whether the SQL moved, and `assay stale`
    counts them in their own column rather than adding them to "current". An absent measurement
    never reads as a pass, which is the rule the whole tool is built on.
    """
    have = getattr(store, "checksums", None)
    if not have:
        return None
    return have.get(str(decision_key).split("::")[0]) or None


def decide(store, client: Client, recipe, questions: dict, *,
           prompt_version: str | dict, caller: str = "assay",
           contexts: dict | None = None) -> dict:
    """Cached judgments. Returns {question: {kind, answer, confidence, probabilities, cached}}.

    *** IT TAKES A RECIPE, NOT A STATE, AND THAT IS THE WHOLE POINT. ***
    Eighteen callers used to assemble their own dict here. Two wrote it out inline, two merged the
    vocabulary in at the call site, and the result was a `state_hash` nothing could ever reproduce:
    rebuilding all 871 model and edge subjects of a real warehouse matched ZERO stored hashes.
    A `states.Recipe` carries the builder's name and the identifiers it was built from, so the
    same function can build it again months later and the comparison becomes possible. A bare dict
    is refused rather than accepted and quietly made unreproducible.
    """
    from .contracts import check_question_ids
    from .states import Recipe
    if not isinstance(recipe, Recipe):
        raise TypeError(
            "decide() takes a states.Recipe, not a raw state. Build it with `states.make(<builder>"
            ", ctx, key=..., inputs=...)`, so that `assay stale --exact` can build it again. "
            f"Got {type(recipe).__name__}.")
    state, decision_key = recipe.state, recipe.key
    check_question_ids(questions)
    store.con.execute(DDL)
    sh = state_hash(state)
    model_name = client.model or client._conn()[1]["model"]

    hits, ask_these = cache_split(store, recipe, questions, prompt_version, sh)

    if ask_these:
        resp = client.ask(state, ask_these, caller=caller)
        served = resp.get("model") or model_name
        # *** A CALL assay CANNOT NAME IS A CALL assay CANNOT COUNT. ***
        # This was `resp.get("id") or ""`, and the provider returned no id for a whole day's work:
        # 9,762 of 19,707 decisions on the field store carry an empty one, so nothing could say
        # which rows shared a call and every total over them was a guess. The id is assay's to
        # mint when the provider declines, and `id_source` says which happened rather than
        # letting a minted id pass as the provider's.
        call_id = resp.get("id") or ""
        id_source = "provider" if call_id else "minted"
        if not call_id:
            call_id = f"assay-{uuid.uuid4().hex}"
        usage = resp.get("usage") or {}
        # *** ABSENT USAGE IS NULL, NOT ZERO. ***
        # A zero here is a measurement saying the call was free. NULL is assay saying it does not
        # know, which is the only honest reading and the one `assay cost` reports a count of.
        used = usage.get("input_tokens")
        used = int(used) if used is not None else None
        out_used = usage.get("output_tokens")
        out_used = int(out_used) if out_used is not None else None
        usd = used * USD_PER_INPUT_TOKEN if used is not None else None
        checksum = _checksum_for(store, decision_key)
        inputs_json = json.dumps(recipe.inputs, sort_keys=True, default=str)
        rows = []
        answers_map = resp.get("answers") or {}
        for q, ans in answers_map.items():
            kind, answer, conf, probs = unpack(ans)
            hits[q] = {"kind": kind, "answer": answer, "confidence": conf,
                       "probabilities": json.loads(probs), "cached": False}
            rows.append([decision_key, q, kind, answer, conf, probs, sh,
                         version_of(prompt_version, q), served, call_id, caller,
                         (contexts or {}).get(q, ""), used, checksum,
                         recipe.builder, inputs_json])
        # *** THE STATE ITSELF, KEYED BY ITS HASH. ***
        # Written before the answers, so a decision can never point at a state that is not there.
        # `insert or ignore`: the same state under the same hash is the same state, and a cache
        # hit re-uses one by definition.
        store.con.execute(
            "insert or ignore into states (state_hash, state, first_seen) "
            "values (?, ?, current_timestamp)",
            [sh, json.dumps(state, default=str, sort_keys=True)])
        # Written before the answers, for the same reason the state is: a decision must never
        # point at a call that is not there.
        store.con.execute(
            """insert or replace into model_calls
               (call_id, id_source, caller, model_name, input_tokens, output_tokens, usd,
                usd_per_input_token, called_at)
               values (?,?,?,?,?,?,?,?, current_timestamp)""",
            [call_id, id_source, caller, served, used, out_used, usd, USD_PER_INPUT_TOKEN])
        store.con.executemany(
            """insert or replace into model_decisions
               (decision_key, question, kind, answer, confidence, probabilities, state_hash,
                prompt_version, model_version, call_id, caller, context, input_tokens,
                file_checksum, state_builder, state_inputs, decided_at)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, current_timestamp)""", rows)
        # keep the lookup index current: this is the one writer of model_decisions in a command
        idx = getattr(store, "_latest_decisions", None)
        if idx is not None:
            for r in rows:
                idx[(r[0], r[1], r[7])] = (r[2], r[3], r[4], r[5], r[6])
    if ON_DECIDE is not None:
        ON_DECIDE(bool(ask_these), client)
    return hits


def version_of(prompt_version: str | dict, question: str) -> str:
    """The version one question is filed under.

    *** TWO FAMILIES IN ONE CALL, EACH UNDER ITS OWN VERSION. ***
    A call carried one version, so the `read` locator had to borrow `finding_is_correct`'s -- and
    two questions under one version cannot have their verdicts told apart. A dict maps each
    question's prefix to its version; a string still means every question in the call.
    """
    if isinstance(prompt_version, dict):
        return prompt_version[question.split("__")[0]]
    return prompt_version


def _latest_index(store) -> dict:
    """{(decision_key, question, prompt_version): the latest (kind, answer, confidence,
    probabilities, state_hash)}, read ONCE per store connection.

    *** A DAY WITH NOTHING TO ASK TOOK 3.5 MINUTES TO SAY SO. *** (sunny-data, 0.52.4) Each
    subject's cache lookup was its own query, two per subject, and each scanned every answer the
    store has ever held: 13,718 queries a run, slower every day the store grows. One read, then
    dictionary lookups; `decide` adds each answer it writes, and it is the only writer of
    `model_decisions` while a command runs."""
    idx = getattr(store, "_latest_decisions", None)
    if idx is None:
        idx = {}
        try:
            for k, q, pv, kind, ans, conf, probs, sh in store.con.execute("""
                    select decision_key, question, prompt_version, kind, answer, confidence,
                           probabilities, state_hash
                    from (select *, row_number() over (partition by decision_key, question,
                                 prompt_version order by decided_at desc) rn
                          from model_decisions) where rn = 1""").fetchall():
                idx[(k, q, pv)] = (kind, ans, conf, probs, sh)
        except Exception:                                        # noqa: BLE001
            idx = {}
        store._latest_decisions = idx
    return idx


def cache_split(store, recipe, questions: dict, prompt_version: str | dict,
                sh: str | None = None) -> tuple[dict, dict]:
    """(answers the store already holds for this exact state, questions it does not).

    The ONE definition of a cache hit, used by `decide` to answer and by `plan` to count -- so a
    plan line can never promise a hit the run then pays for.
    """
    sh = sh or state_hash(recipe.state)
    hits, ask_these = {}, {}
    idx = _latest_index(store)
    for q, qdef in questions.items():
        row = idx.get((recipe.key, q, version_of(prompt_version, q)))
        # A hit whose state moved is a MISS. The subject changed under a key that did not, and
        # serving the old answer is how a cache starts lying about the present.
        if row and row[4] == sh:
            hits[q] = {"kind": row[0], "answer": row[1], "confidence": row[2],
                       "probabilities": json.loads(row[3] or "{}"), "cached": True}
        else:
            ask_these[q] = qdef
    return hits, ask_these


# Called after every `decide` with (whether it sent a request, the client). A command's progress
# line hangs off this, so no call site has to remember to tick.
ON_DECIDE = None


@dataclass
class Plan:
    """What a judged command is about to do, net of what the store already answers."""
    calls: int                   # requests that will be sent
    cached: int                  # requests the store answers in full
    usd: float                   # priced from the states that will be SENT, not a sample
    seconds: float | None        # at this project's observed rate; None when never measured
    rate_basis: str = ""         # what the rate was measured on, said beside it
    price_basis: str = ""        # what the tokens-per-state ratio was measured on, or ""

    def line(self) -> str:
        """"48 call(s) to make, 492 already answered · ~$0.0054 · about 4 min at the rate
        measured on 502 calls of assay.semantics"."""
        head = f"{self.calls:,} call(s) to make"
        if self.cached:
            head += f", {self.cached:,} already answered"
        tail = f" · ~${self.usd:.4f}" + ("" if self.price_basis else
                                          " (no calls recorded here yet: estimated from another project's ledger)")
        if not self.calls:
            return head + " · nothing to send"
        if self.seconds is None:
            return head + tail + " · no rate measured on this store yet"
        return head + tail + f" · about {_duration(self.seconds)} at the rate measured on " \
                             f"{self.rate_basis}"


def _duration(sec: float) -> str:
    if sec < 90:
        return f"{max(1, round(sec))}s"
    if sec < 5400:
        return f"{round(sec / 60)} min"
    return f"{sec / 3600:.1f} h"


def observed_rate(store, caller: str) -> tuple[float | None, str]:
    """(seconds per call, what it was measured on), from the ledger's own timestamps.

    *** A PLAN THAT QUOTES CALLS AND NOT TIME CANNOT BE DECIDED ON. ***
    Reported from the field (25.10): "301 calls" was true, and the only way to learn it meant 43
    minutes was to spend them. The ledger timestamps every call, so the rate is a measurement.
    The median gap between consecutive calls, ignoring gaps over two minutes -- those are the
    pauses between runs, not the speed of one. This caller's history first, else every caller's.
    """
    for where, args, label in _callers(caller):
        try:
            row = store.con.execute(f"""
                with t as (
                    select epoch(called_at) - epoch(lag(called_at) over (order by called_at)) g
                    from model_calls where {where} and called_at is not null)
                select median(g), count(*) from t where g > 0 and g < 120""", args).fetchone()
        except Exception:                                        # noqa: BLE001
            return None, ""
        if row and row[0] is not None and row[1] >= 5:
            return float(row[0]), f"{int(row[1]):,} calls of {label}"
    return None, ""


def _callers(caller: str) -> tuple:
    """This caller, then its family (`assay.ask.x` -> `assay.ask.%`), then every caller."""
    fam = caller.rsplit(".", 1)[0] + ".%" if caller.count(".") >= 2 else None
    return tuple(x for x in (("caller = ?", [caller], caller),
                             ("caller like ?", [fam], fam) if fam else None,
                             ("true", [], "every command")) if x)


# Billed input tokens per (state characters / 4) with no ledger to measure. The 75th percentile over
# 8,393 calls on the field store (median 1.63): a quote nobody can check yet should err high,
# because the failure that prompted this was a quote that came in at half the bill.
UNMEASURED_TOKEN_RATIO = 2.25


def token_ratio(store, caller: str) -> tuple[float | None, str]:
    """(billed input tokens per state-character/4, what it was measured on).

    *** `read --dry-run` QUOTED $0.0276 AND THE RUN COST $0.0522. ***
    Reported from the field (25.2). Two causes: it priced the subject's state rather than the one
    the builder sends, and a state's own size is not what is billed -- the question's text and the
    provider's framing ride along. Measured on the field ledger the ratio runs 1.4 (`read`) to 6.6
    (`columns`), so no constant is right. The ledger holds the real one for every caller.
    """
    for where, args, label in _callers(caller):
        try:
            row = store.con.execute(f"""
                with calls as (
                    select any_value(state_hash) sh, any_value(input_tokens) tok
                    from model_decisions
                    where {where} and input_tokens is not null group by call_id)
                select median(tok / (length(s.state) / 4.0)), count(*)
                from calls join states s on s.state_hash = calls.sh
                where length(s.state) > 0""", args).fetchone()
        except Exception:                                        # noqa: BLE001
            return None, ""
        if row and row[0] is not None and row[1] >= 5:
            return float(row[0]), f"{int(row[1]):,} calls of {label}"
    return None, ""


def plan(store, asks: list, caller: str) -> Plan:
    """`asks` is [(recipe, questions, prompt_version)]. Nothing is sent and nothing is written.

    Priced from each state that WILL be sent, times this store's measured tokens per state; with
    no history, times `UNMEASURED_TOKEN_RATIO`, and the line says so.
    """
    store.con.execute(DDL)
    calls = cached = 0
    state_chars = 0
    for recipe, questions, pv in asks:
        _hits, ask_these = cache_split(store, recipe, questions, pv)
        if not ask_these:
            cached += 1
            continue
        calls += 1
        state_chars += len(json.dumps(recipe.state, default=str, sort_keys=True))
    ratio, price_basis = token_ratio(store, caller)
    tokens = state_chars / 4 * (ratio if ratio is not None else UNMEASURED_TOKEN_RATIO)
    rate, basis = observed_rate(store, caller)
    return Plan(calls=calls, cached=cached, usd=tokens * USD_PER_INPUT_TOKEN,
                seconds=rate * calls if rate is not None else None, rate_basis=basis,
                price_basis=price_basis)
