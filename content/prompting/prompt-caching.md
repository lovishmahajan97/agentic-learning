---
id: prompt-caching
module: prompting
name: Prompt caching
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - prompting/system-prompt
  - prompting/prompt-templates
  - prompting/prompt-versioning
  - prompting/context-engineering
  - model-internals/kv-cache
  - serving-inference/prefix-caching
  - serving-inference/ttft
  - observability/token-cost-tracking
sources:
  - Official docs: Anthropic prompt caching
  - Kwon et al., 2023 — Efficient Memory Management for Large Language Model Serving with PagedAttention
  - Zheng et al., 2024 — SGLang: Efficient Execution of Structured Language Model Programs
---

## TL;DR
Every request re-runs prefill over the whole prompt, yet in most applications most of the prompt
(system instructions, tool definitions, few-shot examples, a long reference document) is identical from
one request to the next. Prompt caching stores the model's internal state for a prefix and reuses it
when a later request starts with the same bytes, cutting input cost and time-to-first-token. It only
works on exact prefixes, so the layout of your prompt decides whether you get the discount.

## Why it exists
Prefill is compute-bound and grows at least linearly with prompt length (the attention part
quadratically). Take a support assistant with a 20,000-token
system prompt (policies, tone rules, worked examples) plus 2,000 tokens that vary per request. At 1,000
requests an hour, that is 20 million tokens an hour of prefill whose result is identical every time:
the same tokens, through the same weights, produce the same keys and values in every layer. You pay for
the computation, and the user waits for it before the first output token appears.

The naive fixes all cost something you want to keep. Shortening the prompt throws away instructions
that earned their place. Fine-tuning the instructions into the weights makes iteration slow and expensive
and still leaves the reference document to be re-sent. Running your own server with prefix caching is
only an option if you self-host.

The observation that makes caching possible is the same one behind the KV cache inside a single request:
in a causal transformer, the key and value vectors at position *i* depend only on tokens 0..*i*. Two
requests that share their first *n* tokens have identical K and V for those *n* positions in every layer,
so the second request can start from the stored state instead of recomputing it. Prompt caching is the
KV cache applied across requests rather than across decode steps.

## How it works
### What is stored and why the match must be exact
The cache holds the per-layer K and V tensors for a prefix, keyed by a hash of the exact token sequence
up to a breakpoint. Because position *i*'s state depends on every earlier token, a single different
token at position *n* makes everything from *n* onward different; the cache can serve positions before
*n* and must recompute the rest. Lookup is therefore "longest matching prefix", which is why the hosted
APIs describe caching as a prefix match and why one stray timestamp at the top of the system prompt
defeats it entirely.

### Render order and breakpoints
On Anthropic's API a request is rendered in a fixed order: tool definitions, then the system prompt,
then the messages (other providers have their own fixed order). There you mark where a cacheable
prefix ends with `cache_control: {"type": "ephemeral"}` on a content block (up to four breakpoints per request); a breakpoint caches everything from the start
of the request up to and including that block. A top-level `cache_control` on the request instead places
the breakpoint automatically on the last cacheable block, which suits growing conversations. Each
breakpoint looks back a bounded number of blocks for an earlier entry to extend, so very long turns can
benefit from an intermediate breakpoint. Some other providers cache automatically on any repeated prefix
without markers; the prefix rule is the same, only the control differs.

### Lifetime
Entries expire after a short time-to-live (five minutes by default on Anthropic's API, with a one-hour
option at a higher write price) and a read refreshes the timer, so requests that share a prefix and
arrive more often than the TTL keep the entry warm indefinitely. Entries are scoped to the model and
are not shared between customers. The timer starts when the writing request starts, so a long generation
eats into the window.

### Economics
A cache write costs more than ordinary input (about 1.25× for the five-minute TTL and 2× for the
one-hour TTL on Anthropic's API) and a cache read costs a fraction of ordinary input (about 0.1×, lower
on some newer models). With the five-minute TTL two requests already break even (1.25 + 0.1 = 1.35
versus 2.0 uncached); with the one-hour TTL you need three. There is also a minimum cacheable prefix,
which varies by model from a few hundred to a few thousand tokens; shorter prefixes are silently not
cached, with no error.

### The invalidation hierarchy
Because of render order, a change invalidates its own tier and everything after it. Changing tool
definitions (adding, removing, reordering) invalidates tools, system and messages. Changing the system
prompt keeps the tools entry but invalidates system and messages. Changing a message invalidates from
that message onward. Changing the model is a full miss, since caches are model-scoped. Changing
generation configuration that is rendered into the prompt, such as thinking or effort settings, also
invalidates the messages tier (and on some models the earlier tiers too), so pin those per route rather
than varying them per request.

### Observing it
The response `usage` block reports three input counts: `input_tokens` (uncached remainder),
`cache_creation_input_tokens` (written this request, at the write premium) and
`cache_read_input_tokens` (served from cache). The prompt size is their sum. A healthy multi-turn loop
shows reads growing turn over turn and small writes; reads stuck at zero mean something volatile sits
before the breakpoint.

### Concurrency
An entry becomes readable only once the first response has started. N identical requests fired in
parallel all miss and all pay the write premium. For fan-out, send one request, wait for its first token,
then send the rest.

### Self-hosted equivalents
vLLM's automatic prefix caching and SGLang's RadixAttention do the same thing inside the serving engine,
with no markers: any common token prefix is reused. vLLM hashes each fixed-size KV block together with
everything before it and shares blocks whose hashes match (so reuse is at block granularity); SGLang
keeps the cached prefixes in a radix tree and matches the longest one. The serving module covers the memory side.

## Example
Two calls with the same 20k-token policy document and tool list, different questions. Stable content is
rendered first with one breakpoint at the end of the system block; the only volatile part is the user
turn, which comes after it. The `usage` fields show the write on the first call and the read on the second.

```python
import anthropic, json

client = anthropic.Anthropic()
MODEL = "claude-opus-5-5"

SYSTEM = open("policies.md").read()                       # ~20k tokens, byte-identical every request
TOOLS = sorted(json.load(open("tools.json")), key=lambda t: t["name"])   # deterministic order

def ask(question: str):
    r = client.messages.create(
        model=MODEL, max_tokens=1024,
        tools=TOOLS,                                              # rendered first; must not vary
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],        # breakpoint: caches tools + system
        messages=[{"role": "user", "content": question}],        # volatile tail, after the breakpoint
    )
    u = r.usage
    print(f"uncached={u.input_tokens} written={u.cache_creation_input_tokens} read={u.cache_read_input_tokens}")
    return r

ask("Can a Basic-plan customer get a refund after 45 days?")
# illustrative: uncached=~20  written=~20000  read=0        -> paid the 1.25x write once
ask("Does the 14-day trial convert to a paid plan automatically?")
# illustrative: uncached=~20  written=0       read=~20000   -> prefix at the read rate (0.1x on most models)
```

Arithmetic for 1,000 requests an hour with a 20k stable prefix and 2k variable tail, at an illustrative
$3 per million input tokens and the common 0.1× read rate: uncached, each request bills 22k tokens, about $0.066, so $66 an hour.
Cached, each request bills 2k at full price plus 20k at 0.1×, equivalent to 4k tokens, about $0.012, so
$12 an hour, with the occasional write after an idle gap adding a few cents. Roughly an 80% cut in input
cost, and time-to-first-token drops because 20k tokens of prefill are skipped.

## Trade-offs
- You trade a constraint on prompt layout (stable content first, volatile last) for the discount; it
  flips to a surcharge when the prompt is mostly unique per request, because the write premium is paid
  and never read back.
- The write premium means a prefix used once within the TTL costs more than not caching at all; the
  break-even is two uses, so cache the parts you know repeat and leave one-off documents uncached.
- The one-hour TTL doubles the write price to survive idle gaps; it pays only when requests sharing a
  prefix arrive 5 to 60 minutes apart, and buys nothing under continuous traffic.
- Putting per-user content (name, plan, profile) in the system prompt personalises the model but creates
  one prefix per user; moving it after the shared breakpoint keeps cross-user sharing, and a second
  per-user breakpoint is worth adding only when that user makes many requests within the TTL.
- A prompt release invalidates every cached prefix at once, so a deploy costs a cost and latency blip; a
  gradual alias move spreads the writes, at the price of running two prompt versions for a while.

## Pitfalls
- A timestamp, request id or UUID interpolated into the system prompt; the symptom is
  `cache_creation_input_tokens` near the full prompt on every request and reads stuck at zero.
- Non-deterministic serialization: `json.dumps` without `sort_keys`, iterating a set, or a tool list
  built per user; the symptom is an intermittent hit rate that nobody can explain from the prompt text.
- Letting the automatic breakpoint land after a unique per-request tail; the symptom is a write on
  every request while reads never cover the full shared prefix.
- A prefix below the model's minimum cacheable size; the symptom is zero writes and zero reads with no
  error, usually on a short prompt that someone assumed was cached.
- Rewriting earlier history (injecting then deleting a reminder, truncating the middle of a conversation);
  the symptom is a write the size of the whole conversation on every turn of an agent loop.
- Fan-out of N identical requests at once; the symptom is N writes and no reads at the start of each batch.
- Switching models or toggling thinking and effort per request; the symptom is full misses on
  byte-identical text, which looks impossible until you remember caches are model- and config-scoped.

## When to use
Reach for caching whenever a prefix larger than the model's minimum repeats within the TTL: shared system
prompts and tool sets, few-shot examples, a long document interrogated with many questions, and above all
agent loops, where every turn re-sends the growing history. Skip it for one-off requests with unique
content and for batch jobs where only a few hundred tokens of instructions are shared. Measure three
things: hit ratio (reads over total prompt tokens), time-to-first-token before and after, and cost per
request from the usage fields. Add an integration test that asserts a second identical request shows
reads, because the common failure is a later change that silently breaks caching for months. In
interviews, the expected depth is: prefix match, render order, the write-versus-read economics, and where
to put the breakpoint.

## Role lens
- **Engineer:** Structures the request builder as stable, session and turn layers, places breakpoints at
  those boundaries, and asserts in CI that the second identical request reports `cache_read_input_tokens`.
- **Architect:** Decides what is shared across users versus per tenant, sets TTL policy per route, and
  puts the expected hit ratio into the cost model instead of pricing every request at full input cost.
- **FDE:** Finds the customer-injected context (user profile, current date) sitting at the top of the
  prompt, moves it after the shared breakpoint, and shows the before-and-after usage fields as proof.

## Check yourself
**Q:** An agent runs 30 turns per task; tools plus system prompt are 40k tokens and each turn adds about
3k tokens of history. Where do you put breakpoints, and roughly how much input cost does caching save?
**A:** One explicit breakpoint at the end of the system block guarantees a read point for the expensive
shared part, and top-level automatic caching covers the growing conversation tail. Uncached, turn *t*
sends 40k + 3k·*t* tokens, so 30 turns send about 1.2M + 1.4M ≈ 2.6M tokens. Cached, turn 1 writes
43k at 1.25×; each later turn reads everything so far at about 0.1× and writes only the new 3k at 1.25×.
Reads total roughly 2.5M tokens, equivalent to about 250k at full price, plus about 160k-equivalent of
writes, so around 410k-equivalent versus 2.6M: an 80-85% reduction, with time-to-first-token falling on
every turn. The conditions: turns start less than the TTL apart, history is append-only, the tool list is
deterministic, and the model and thinking settings do not change mid-task.

## Quiz
### Q: Why must a cached prefix match the new request byte for byte?
- [ ] The hash function cannot tolerate partial matches
- [x] Each position's keys and values depend on every earlier token, so one changed token changes all state after it
- [ ] Tokenizers are non-deterministic, so only exact bytes are comparable
- [ ] The cache stores text rather than tensors and is compared as a string
> Attention state at position *i* is a function of tokens 0..*i*. A change at position *n* leaves earlier
> state valid but makes everything from *n* on different, so only the longest exact prefix can be reused.

### Q: Usage shows `cache_creation_input_tokens` near the full prompt on every request and reads near zero. Most likely cause?
- [ ] The TTL is too short for the request rate
- [ ] The model does not support caching, so writes are wasted
- [x] Something volatile, such as a timestamp or request id, sits in the prefix before the breakpoint
- [ ] More breakpoints are needed to cover the whole prompt
> A write on every request means the prefix is never seen twice. A TTL problem would show gaps, not
> constant misses, and an unsupported model would show zero writes. Diff two rendered prompts to find the
> changing bytes.

### Q: When does the one-hour TTL pay off over the default five-minute TTL?
- [ ] Always, because a longer lifetime means more hits
- [ ] Under continuous traffic, where requests arrive every few seconds
- [x] When requests sharing the prefix arrive 5 to 60 minutes apart
- [ ] When the prefix is below the minimum cacheable size
> Under continuous traffic every read refreshes the five-minute entry, so the doubled write price of the
> one-hour TTL buys nothing. It pays only across idle gaps longer than five minutes and shorter than an hour.
