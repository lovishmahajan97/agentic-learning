---
id: rollout-criteria-and-rollback-triggers
module: deployment
name: Rollout criteria & rollback triggers
kind: practice
difficulty: core
est_minutes: 16
related:
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - llmops-platform/evaluation-gates-for-production-releases
  - evals/offline-vs-online-evaluation
  - reliability/slas-slos
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - deployment/deployment-readiness-reviews
  - deployment/runbooks
---

## TL;DR
Rollout criteria say what evidence lets a release move from one exposure stage to the next (internal,
a few percent, a quarter, everyone); rollback triggers say what observed signal sends it back. Both are
written before the rollout starts, as thresholds with minimum sample sizes, because during a live
incident people rationalise bad numbers. For AI releases the hard part is that quality signals are
noisy and lag behind error and latency signals, so criteria mix automated hard triggers with sampled
quality checks and a deliberate bake time.

## Why it exists
A staged rollout only reduces risk if someone decides, at each stage, whether to continue. Without
written criteria that decision is made by whoever is watching the dashboard, at the moment the numbers
are ambiguous, with a launch date behind them. The predictable failure is "it's probably noise, let's
keep going", followed by an incident at 100% that was visible at 5%. The opposite failure also happens:
an engineer rolls back on a spike that was pure noise, and the team loses confidence in releasing at all.

AI releases make this harder in three ways. First, the thing being released is often not code: a new
prompt, a different model version, a re-embedded index or a changed retrieval parameter. These can
degrade quality with no rise in errors or latency at all. Second, quality signals are slow and noisy:
thumbs-down rates, escalations to humans and LLM-judge scores on sampled traffic need hundreds of
requests and hours or days before a difference is distinguishable from noise. Third, some AI actions are
irreversible. An agent that sent emails or updated records during a bad rollout cannot be rolled back by
redeploying the old version.

The naive alternative, "watch the dashboards and use judgement", fails because judgement under pressure
is biased toward continuing. Writing the criteria in advance moves the hard thinking to a calm moment and
makes the in-the-moment decision mechanical.

## How it works
### 1. Define the stages and what each must prove
A typical ladder is internal users, then a small percentage or one friendly tenant, then a larger
fraction, then everyone. Each stage has a **minimum duration** and a **minimum sample** (requests,
sessions or completed tasks). Duration matters *because* traffic varies by hour and weekday; a stage that
only saw Sunday-night traffic proved little about Monday morning. Sample size matters *because* rare
failures cannot be observed in small samples: at a 1% baseline error rate, 100 requests produce about one
error, so a doubling is invisible; at a few thousand requests it is clearly visible (illustrative orders
of magnitude, from simple counting statistics).

### 2. Compare against a baseline, not an absolute
Where possible run the old and new version side by side on comparable traffic, so seasonal or upstream
effects hit both. Criteria are expressed as relative regressions ("canary p95 latency no more than 20%
above control") plus absolute floors so tiny baselines do not produce absurd ratios.

### 3. Split triggers into hard and soft
- **Hard triggers** are automated and immediate: error rate, timeouts, p95 or p99 latency, guardrail
  block rate, cost per request, provider 429s, crash loops. They are fast to measure and unambiguous.
- **Soft triggers** need a human decision within a set time: online quality score from an LLM judge on
  sampled traffic, user feedback rate, escalation-to-human rate, task completion rate. They are slower and
  noisier, so they set a *hold* rather than an automatic rollback, and a named person decides.

### 4. Make the outcome one of three states
At each evaluation the release is **promote** (all criteria met, duration and sample reached),
**hold** (not enough evidence yet, or a soft trigger needs review) or **roll back** (any hard trigger
tripped after the minimum sample). The explicit hold state prevents both premature promotion and panic
rollbacks.

### 5. Make rollback cheap and rehearsed
Version every artefact that changes behaviour: prompt, model identifier (pinned, never a floating alias),
retrieval config, index. Ship behind a flag so rollback is a config change, not a redeploy. For index
changes keep the old index serving until the new one has baked, because re-embedding a corpus can take
hours and is not a rollback you can do mid-incident. For agents with side effects, gate write actions
behind approval or dry-run during early stages, because no trigger can un-send an email.

### 6. Decide roll forward versus roll back
Rolling back is the default because it restores a known state. Rolling forward with a fix is right only
when rollback is impossible (a schema migration already applied, an external contract already changed)
or when the old version is itself broken by an external change, such as a deprecated provider model.

## Example
A stage gate evaluated every 15 minutes during a canary of a new prompt and model version. The numbers
are illustrative thresholds a team might agree, not recommendations.

```python
def evaluate(canary, control, minutes_in_stage, min_minutes=120, min_requests=3000):
    # Hard triggers: checked as soon as there is enough data to trust them.
    if canary["requests"] >= min_requests:
        err_limit = max(2 * control["error_rate"], 0.01)          # relative, with an absolute floor
        if canary["error_rate"] > err_limit:
            return "ROLLBACK", "error rate"
        if canary["p95_ms"] > 1.2 * control["p95_ms"]:
            return "ROLLBACK", "p95 latency"
        if canary["cost_per_req"] > 1.3 * control["cost_per_req"]:
            return "ROLLBACK", "cost per request"
        if canary["guardrail_block_rate"] > control["guardrail_block_rate"] + 0.02:
            return "ROLLBACK", "guardrail blocks"
    # Not enough evidence yet: neither promote nor roll back.
    if canary["requests"] < min_requests or minutes_in_stage < min_minutes:
        return "HOLD", "collecting evidence"
    # Soft trigger: sampled LLM-judge quality needs a human if it drops.
    if canary["judge_pass"] < control["judge_pass"] - 0.03:
        return "HOLD", "quality drop: page release owner"
    return "PROMOTE", "all criteria met"

control = {"error_rate": 0.004, "p95_ms": 4100, "cost_per_req": 0.011,
           "guardrail_block_rate": 0.006, "judge_pass": 0.91}
canary  = {"requests": 4200, "error_rate": 0.005, "p95_ms": 4500, "cost_per_req": 0.012,
           "guardrail_block_rate": 0.007, "judge_pass": 0.86}
print(evaluate(canary, control, minutes_in_stage=150))
# ('HOLD', 'quality drop: page release owner')
```

Errors, latency (4500 is under 1.2 × 4100 = 4920) and cost are within limits, so no hard trigger fires,
but the sampled judge pass rate fell five points. The gate holds and pages a human, who checks the judge
sample and decides whether the drop is real before any promotion.

## Trade-offs
- Long bake times and large minimum samples make decisions trustworthy, but slow down every release;
  the balance flips toward speed for low-risk changes behind flags and toward caution for model swaps.
- Automated rollback reacts in minutes, but can thrash on noisy metrics; reserve automation for hard,
  low-noise signals and route noisy quality signals to a human hold.
- Relative thresholds against a live control absorb traffic shifts, but require running two versions
  at once, which doubles some costs and is awkward for stateful or per-tenant releases.
- Rolling out by tenant gives clean blast-radius control and clearer attribution, but small tenants
  produce too little traffic to judge, so per-tenant stages need longer durations.

## Pitfalls
- Writing criteria after the rollout starts; the symptom is thresholds that conveniently sit just
  above whatever the canary is currently showing.
- Judging a stage before its minimum sample; the symptom is releases promoted on a quiet hour and
  incidents that start when peak traffic arrives.
- Monitoring only errors and latency for a prompt or model change; the symptom is a green rollout
  followed by a week of user complaints about worse answers.
- Pointing at a floating model alias, so the provider changes the model underneath you; the symptom is
  a quality shift that no release on your side explains and that rollback does not fix.
- Assuming rollback undoes everything; the symptom is a clean revert while records written or messages
  sent by the bad version remain in customer systems.

## When to use
Use explicit criteria and triggers for any change that can alter behaviour in production: code, prompt,
model, retrieval configuration, index, guardrail policy, tool set. Scale the ladder to risk: a copy change
behind a flag may need only a short internal stage; a model swap for an agent with write tools needs
staged tenants, a long bake and human-reviewed quality gates. Measure what the customer feels (errors,
latency, cost, task success) and what predicts it (judge score on sampled traffic, escalations). In
interviews, strong answers name the three-state decision, minimum samples, relative-plus-absolute
thresholds, hard versus soft triggers, and the irreversibility of agent side effects.

## Role lens
- **Engineer:** Implements the gate logic, version pinning and flags, and wires metrics so the canary
  and control are comparable on the same dashboard and alert rules.
- **Architect:** Decides which artefacts are versioned and independently rollbackable, which signals are
  hard triggers, and how irreversible actions are fenced during early rollout stages.
- **FDE:** Agrees the stages and thresholds with the customer before go-live, including who on their side
  can call a rollback and how affected users are told.

## Check yourself
**Q:** A new model version is at 10% of traffic. Error rate and latency are fine, but the thumbs-down
rate is 4.1% versus 3.2% on control after 900 rated responses. Roll back, hold or promote?
**A:** Hold, not promote, and probably not an automatic rollback yet. Thumbs-down is a noisy soft
signal: 900 ratings at roughly 3 to 4% means about 30 to 37 negative ratings, and a difference of that
size can arise from chance or from which users happen to rate. I would extend the stage until the sample
is large enough, read a sample of the negative responses to see whether they share a cause, and run the
LLM judge on matched traffic for a less biased signal. If the failures cluster on a real regression,
roll back; if they are spread and the gap closes with more data, continue.

## Quiz
### Q: Why should rollback triggers be written before the rollout begins?
- [ ] So they can be loaded into the deployment tool's configuration file
- [x] Because decisions made while watching ambiguous live numbers are biased toward continuing the release
- [ ] Because thresholds cannot be changed once traffic is flowing
- [ ] Because auditors require a document for every deployment
> Pre-committed thresholds move the reasoning to a calm moment. In the moment, sunk cost and schedule
> pressure make "probably noise" the default reading of a bad number, which is how 5% problems reach 100%.

### Q: Which signal is the best candidate for an automated, immediate rollback trigger?
- [ ] Average user satisfaction rating from in-app surveys
- [ ] LLM-judge quality score on 50 sampled responses
- [x] Error rate exceeding twice the control rate after the minimum request count
- [ ] Number of support tickets opened this week
> Error rate is fast, low-noise and unambiguous once a minimum sample exists. Survey ratings, small judge
> samples and tickets are slow or noisy, so they should trigger a human-reviewed hold rather than automation.

### Q: A rollout re-embedded the whole corpus with a new embedding model. What makes rollback safe?
- [ ] Re-running the old embedding model over the corpus if quality drops
- [ ] Pinning the new embedding model version
- [ ] Rolling back the application code only
- [x] Keeping the old index serving in parallel until the new one has passed its bake period
> Re-embedding takes hours, so it is not a mid-incident rollback. Keeping the old index live lets
> rollback be a configuration switch, while code-only rollback would query vectors from an incompatible model.
