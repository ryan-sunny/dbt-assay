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
        body = {"model": model, "state": state, "questions": questions}

        # Estimated BEFORE the call, because a cap that only fires after the spend is not a cap.
        est = len(json.dumps(body, default=str)) / 4 * USD_PER_INPUT_TOKEN
        if self.spent_usd + est > self.max_spend_usd:
            raise BudgetExceeded(
                f"would exceed the ${self.max_spend_usd:.2f} cap "
                f"(spent ${self.spent_usd:.4f}). Raise jev.max_spend_usd in audit.yml.")

        last = None
        for attempt in range(self.retries):
            try:
                r = httpx.post(spec["url"], json=body, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {key}",
                                        "Content-Type": "application/json"})
                out = r.json()
                if "answers" not in out:
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


def decide(store, client: Client, state, questions: dict, *, decision_key: str,
           prompt_version: str, caller: str = "assay", contexts: dict | None = None) -> dict:
    """Cached judgments. Returns {question: {kind, answer, confidence, probabilities, cached}}."""
    from .contracts import check_question_ids
    check_question_ids(questions)
    store.con.execute(DDL)
    sh = state_hash(state)
    model_name = client.model or client._conn()[1]["model"]

    hits, ask_these = {}, {}
    for q, qdef in questions.items():
        row = store.con.execute(
            """select kind, answer, confidence, probabilities, state_hash
               from model_decisions
               where decision_key = ? and question = ? and prompt_version = ?
               order by decided_at desc limit 1""",
            [decision_key, q, prompt_version]).fetchone()
        # A hit whose state moved is a MISS. The subject changed under a key that did not, and
        # serving the old answer is how a cache starts lying about the present.
        if row and row[4] == sh:
            hits[q] = {"kind": row[0], "answer": row[1], "confidence": row[2],
                       "probabilities": json.loads(row[3] or "{}"), "cached": True}
        else:
            ask_these[q] = qdef

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
        rows = []
        answers_map = resp.get("answers") or {}
        for q, ans in answers_map.items():
            kind, answer, conf, probs = unpack(ans)
            hits[q] = {"kind": kind, "answer": answer, "confidence": conf,
                       "probabilities": json.loads(probs), "cached": False}
            rows.append([decision_key, q, kind, answer, conf, probs, sh,
                         prompt_version, served, call_id, caller,
                         (contexts or {}).get(q, ""), used, checksum])
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
                file_checksum, decided_at)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?, current_timestamp)""", rows)
    return hits
