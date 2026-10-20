---
id: knowledge-graphs
module: rag-retrieval
name: Knowledge graphs
kind: concept
difficulty: core
est_minutes: 15
related:
  - rag-retrieval/graphrag
  - rag-retrieval/multi-hop-retrieval
  - rag-retrieval/metadata-filtering
  - rag-retrieval/hybrid-search
  - rag-retrieval/agentic-rag
  - prompting/structured-outputs-json-mode
  - scoping/domain-modeling
  - enterprise-integration/data-mapping-and-canonical-schemas
sources:
  - Hogan et al., 2021 — Knowledge Graphs
  - Official docs: Neo4j Cypher manual
  - Official docs: W3C RDF and SPARQL
---

## TL;DR
A knowledge graph stores facts as typed entities connected by typed relationships, with a schema that
says which connections are valid. In RAG it complements vector search: embeddings find text that sounds
like the question, while a graph answers questions that are joins ("who reports to the owner of system
X?"), aggregations and exact constraints. The hard parts are not the database but building it: extraction,
entity resolution, schema design and keeping it in sync with sources.

## Why it exists
Vector retrieval treats knowledge as a pile of passages ranked by similarity. That is a good fit for
"explain our refund policy" and a poor fit for questions whose answer is a *path* or a *count*:
"Which customers use a product affected by CVE-123?", "How many open contracts does each subsidiary of
Acme hold?", "Who approved the change that broke service Y?". The facts needed are scattered across
documents that share an entity, not wording. Top-k similarity returns passages about the CVE but cannot
follow product to deployment to customer, and it cannot count reliably at all.

The naive alternatives each fail somewhere. Bigger k or long-context stuffing hopes the model performs the
join in its head, which it does unreliably and unverifiably. Iterative agentic retrieval can follow
links, but each hop is a fuzzy search that can drift, and every query re-derives the same structure.
Relational tables can represent the facts but make variable-length paths ("any number of reporting
levels") awkward to express, though recursive CTEs handle many cases.

A knowledge graph makes relationships first-class data. Entities get identities, so mentions in different
documents resolve to the same node; relationships get types and direction, so a query can follow exactly
the edges it means; and the schema constrains what is allowed, so wrong facts can be rejected at write
time. The question becomes a precise traversal instead of a similarity guess.

## How it works
### Data model
Two families dominate.
- **Property graphs** (Neo4j, Amazon Neptune, Memgraph and others): nodes with labels and key-value
  properties, directed relationships with a type and their own properties. Queried with Cypher or its
  standardised relative GQL, or Gremlin. Pragmatic and popular in application engineering.
- **RDF triple stores:** every fact is a (subject, predicate, object) triple with URIs as identifiers,
  queried with SPARQL. Ontology languages (RDFS, OWL) add class hierarchies and inference rules. Strong for
  data integration across organisations and standard vocabularies, heavier to adopt.
Either way the unit is a fact like `(Service:payments)-[:DEPENDS_ON]->(Database:pg-main)`.

### Schema (ontology)
Decide entity types, relationship types, their direction and cardinality, and the properties each carries.
The schema is the leverage point: a good one makes common questions short traversals; a vague one
("RELATED_TO" everywhere) degrades the graph back into a co-occurrence network that cannot answer precise
questions. Start from the questions the graph must answer and model only what they need.

### Construction
1. **From structured sources** (CMDB, HR system, CRM, ticketing): deterministic mapping, high precision.
   Do this first, *because* it is cheap and trustworthy.
2. **From text:** an LLM or NER model extracts entities and relationships into the schema, using
   structured outputs so types are constrained. Recall and precision are both imperfect, so store
   provenance (source document, span, extraction run) on every edge.
3. **Entity resolution:** merge "Acme Corp", "ACME" and "Acme Corporation Ltd" into one node using
   identifiers where available, then normalised names, attributes and, for hard cases, an LLM or human
   review. Skipping this splits facts across duplicate nodes and silently breaks traversals.

### Using it for retrieval
- **Text-to-query:** an LLM translates the question into Cypher or SPARQL, given the schema; the result
  rows go into the prompt. Validate the generated query (read-only, allowed labels, row limits) *because*
  the model will occasionally invent labels or write unbounded traversals.
- **Entity-anchored expansion:** link entities in the question to nodes, pull their k-hop neighbourhood,
  and serialise those facts plus provenance-linked chunks into context.
- **Graph as filter or booster:** use traversal results (for example, all documents attached to systems
  owned by team T) as a metadata filter or ranking signal for vector search.
- **Tools for agents:** expose a few parametrised graph queries as tools rather than free-form query
  generation when reliability matters more than flexibility.

### Keeping it true
The graph is derived data. Sync it from systems of record with change feeds, re-extract changed documents,
and delete edges whose provenance disappears; otherwise it confidently answers with stale facts.

## Example
A miniature in-memory graph showing why a join beats similarity. In production the same logic is one Cypher
query, shown in the comment.

```python
from collections import defaultdict

# (subject, relation, object, source_doc) -- provenance on every edge
facts = [
    ("CVE-123",   "AFFECTS",     "libfoo 2.1",  "sec-advisory-17"),
    ("svc-billing","USES",       "libfoo 2.1",  "sbom-billing"),
    ("svc-search","USES",        "libfoo 3.0",  "sbom-search"),
    ("Acme",      "SUBSCRIBES",  "svc-billing", "crm-export"),
    ("Globex",    "SUBSCRIBES",  "svc-billing", "crm-export"),
    ("Initech",   "SUBSCRIBES",  "svc-search",  "crm-export"),
]
out_edges, in_edges = defaultdict(list), defaultdict(list)
for s, r, o, src in facts:
    out_edges[(s, r)].append((o, src))
    in_edges[(o, r)].append((s, src))

def customers_exposed(cve):
    hits = []
    for lib, src1 in out_edges[(cve, "AFFECTS")]:
        for svc, src2 in in_edges[(lib, "USES")]:
            for cust, src3 in in_edges[(svc, "SUBSCRIBES")]:
                hits.append((cust, svc, [src1, src2, src3]))
    return hits

print(customers_exposed("CVE-123"))
# [('Acme', 'svc-billing', [...]), ('Globex', 'svc-billing', [...])]
# Cypher: MATCH (:CVE {id:'CVE-123'})-[:AFFECTS]->(l)<-[:USES]-(s)<-[:SUBSCRIBES]-(c)
#         RETURN c.name, s.name
```

The three facts live in three different documents (advisory, SBOM, CRM). A vector search for the question
would likely return the advisory and maybe an SBOM, but nothing connects library version to customer. The
traversal returns exactly Acme and Globex, excludes Initech because its service uses a different library
version, and carries a source for every hop, which becomes the citation trail in the answer.

## Trade-offs
- Exact joins, counts and multi-hop paths become reliable, but you pay for schema design, extraction and
  entity resolution; when questions are mostly "explain this topic", vector RAG gives more for less.
- Structured-source graphs are precise and cheap to maintain, while text-extracted graphs reach more
  knowledge at lower precision; mix them and keep provenance so you know which edges to trust.
- Text-to-Cypher handles open-ended questions, but fails on schema drift and complex queries; when the
  question set is narrow, a handful of parametrised query tools is more reliable.
- RDF and OWL give standard vocabularies and inference, but steeper tooling and skills; property graphs
  are quicker to ship unless cross-organisation integration or formal reasoning is a requirement.
- A dedicated graph database suits deep variable-length traversal, but adds an operational system; for
  shallow, fixed joins, relational tables with recursive CTEs are often enough.

## Pitfalls
- Modelling everything as a generic `RELATED_TO` edge; traversals return everything or nothing and the graph
  cannot answer any question more precisely than vector search.
- Skipping entity resolution; the symptom is answers that list "Acme" and "ACME Corp" as different customers
  or miss half of an entity's relationships.
- Letting the LLM generate unvalidated queries; you see hallucinated labels returning empty results, or an
  unbounded path query that times out the database.
- Building the graph once from a document dump; it drifts from source systems and answers with
  reorganised teams or retired services weeks later.
- Dropping provenance; graph answers cannot be cited, and when an extracted fact is wrong nobody can find
  which document or extraction run produced it.

## When to use
Use a knowledge graph when important questions are relational (dependencies, ownership, supply chains,
org structure, fraud rings), require exact counts or constraints, or span many documents joined by shared
entities, and when much of the data already exists in structured systems. Avoid it when the corpus is prose
and questions are explanatory. Measure on a question set split into lookup, relational and aggregate types,
comparing graph-backed answers with hybrid retrieval. In interviews, expect to explain when a graph beats
embeddings, how you would build one from messy text, and how you keep it consistent with its sources.

## Role lens
- **Engineer:** Writes the extraction pipeline with constrained output types, the entity resolution step,
  and validated Cypher or parametrised graph tools the model can call.
- **Architect:** Owns the schema, the boundary between system-of-record data and extracted facts, and the
  sync strategy that keeps the graph from becoming a stale shadow copy.
- **FDE:** Maps the customer's CMDB, CRM or HR data into a graph during discovery, often uncovering that
  their key questions are joins that their current search tool could never answer.

## Check yourself
**Q:** A team built a knowledge graph by having an LLM extract triples from 10,000 documents. Answers to
"which projects does Priya lead?" are incomplete and sometimes include another person's projects. Diagnose.
**A:** Two likely causes. Entity resolution: "Priya", "Priya S." and "P. Sharma" became separate nodes, so
traversal from one node misses edges attached to the others, which explains the incompleteness; worse,
two different Priyas may have merged by name alone, which explains wrong projects. Fix with identifiers from
the HR system and attribute-based matching. Second, schema looseness: if extraction produced varied relation
names (LEADS, MANAGES, WORKS_ON), the query misses synonyms or includes non-lead roles. Constrain relation
types in the extraction schema, and better still, load ownership from the project system directly.

## Quiz
### Q: Which question is a knowledge graph best placed to answer that top-k vector search handles poorly?
- [ ] Summarise the company travel policy
- [x] List all customers subscribed to a service that depends on a library affected by a given vulnerability
- [ ] Find the paragraph that explains how to reset a password
- [ ] Translate a policy document into French
> The vulnerability question is a three-hop join across documents that share entities, not wording. A graph
> traverses typed edges exactly; similarity search finds passages about the CVE but cannot follow the chain.

### Q: Why is entity resolution critical when building a graph from extracted text?
- [ ] It reduces the size of the embedding index
- [ ] It is required by the Cypher query language
- [x] Unmerged duplicates split one entity's facts across nodes, so traversals return incomplete answers
- [ ] It removes the need for a schema
> Text names the same thing many ways. Without merging them, each node holds part of the facts, and wrong
> merges are equally harmful because they attach one entity's relationships to another.

### Q: When are a few parametrised graph query tools better than free-form text-to-Cypher?
- [ ] When the schema changes every day
- [x] When the important question types are known and narrow, and reliability matters more than flexibility
- [ ] When the graph has no provenance
- [ ] When users only ask open-ended exploratory questions
> Fixed, tested queries cannot hallucinate labels or write unbounded traversals. Free-form generation buys
> flexibility for open questions, at the cost of validation work and occasional broken or slow queries.
