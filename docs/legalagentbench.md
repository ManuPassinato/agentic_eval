# LegalAgentBench investigation

Reference notes for adapting or comparing [LegalAgentBench](https://arxiv.org/abs/2412.17259) (Li et al., Dec 2024) against this repo.

- Paper: https://arxiv.org/abs/2412.17259
- Code/data: https://github.com/CSHaitao/LegalAgentBench
- Domain: Chinese legal practice (companies, courts, firms, cases, statutes)

The environment is **14 entity tables** plus **3 knowledge bases**. Agents solve 300 tasks by calling 37 tools. A run stops on `finish` or after 10 steps.

## Mental model

```
question
   │
   ├─ entity lookups (tabular DBs) ── companies, cases, courts, firms, addresses
   ├─ knowledge retrieval (docs) ──── books, statutes, guiding cases
   └─ math / finish
         │
         ▼
      answer (keyword-scored)
```

Hop tasks mostly walk entity tables (and sometimes sum/rank numbers). Writing tasks also pull from the three knowledge bases in parallel, then draft a formatted defense.

## Tasks (300)

Built from a planning tree over tool/corpus dependencies, then rewritten by GPT-4 to sound like real legal questions. Gold answers are extracted programmatically from the known toolchain, then human-verified.

| Type | Count | Role |
|---|---|---|
| 1-hop | 80 | One entity lookup |
| 2-hop | 80 | Two chained lookups |
| 3-hop | 60 | Three hops |
| 4-hop | 40 | Four hops |
| 5-hop | 20 | Five hops |
| Writing | 20 | Parallel lookups + retrieve KB + draft a defense statement |

- Serial hops (1–5): path like `entity1 → entity2 → entity3`. Longer path = harder.
- Writing: given a complaint and a defense template, fetch defendant/company fields, law-firm contacts, plus legal knowledge / articles / cases, then write a formatted defense. Queries average ~1,060 tokens vs ~90–120 for hop tasks.

**3-hop example:** given unified social credit code `91320115773957541H`, resolve the company name, list high-consumption restriction cases, `get_sum` the amounts → `3546224 CNY`.

**Writing example:** PersonA sues CompanyX; Law Firm A represents CompanyX; draft a defense from a required template.

### Scoring

Keyword overlap, not free-form judging.

- `key_answer`: keywords in the final answer → **success rate**
- `key_middle`: keywords from successful tool observations → combined with `key_answer` for **progress rate**

## Environment: 17 corpora

### Entity tables (14)

Structured lookup DBs. Tools take an identifier plus optional `columns`.

| # | Corpus | Rows | Entity | Holds |
|---|---|---|---|---|
| 1 | CompanyInfo | 695 | listed company | profile: name, status, credit code, legal rep, capital, address, industry, scope |
| 2 | CompanyRegister | 10,125 | company | registration record |
| 3 | SubCompanyInfo | 9,433 | parent / subsidiary | investment links |
| 4 | LegalDoc | 24,372 | case (listed cos.) | judgment document |
| 5 | LegalAbstract | 1,200 | case | short summary |
| 6 | CourtInfo | 3,413 | court | name, contacts, location |
| 7 | CourtCode | 3,348 | court | level and admin-division codes |
| 8 | LawfirmInfo | 4,768 | law firm | head, capital, address, phone |
| 9 | LawfirmLog | 101 | law firm | service records |
| 10 | AddrInfo | 19,533 | address | province / city / district |
| 11 | RestrictionCase | 46 | case | high-consumption restriction (限高) |
| 12 | FinalizedCase | 119 | case | closed after final enforcement (终本) |
| 13 | DishonestyCase | 13 | case | dishonest judgment debtor (失信) |
| 14 | AdministrativeCase | 443 | case | administrative penalty |

Table schemas have 3–28 fields. Data is public Chinese legal/company material (paper: MIT license; government copyright still applies).

### Knowledge bases (3)

Document collections. Default retriever is Embedding-3 (`https://bigmodel.cn/`). Parameter: `Number` = how many docs to return.

| # | Corpus | Docs | Holds | Tool |
|---|---|---|---|---|
| 15 | LegalKnowledge (paper: LegalKonwledge) | 26,951 | passages from legal books | `legal_knowledge_retriever` |
| 16 | LegalArticle | 55,347 | enacted statutes | `legal_article_retriever` |
| 17 | LegalCases | 2,370 | official guiding cases | `legal_case_retriever` |

## Tools (37)

### Database / entity (28)

| Area | Tools |
|---|---|
| Company | `get_company_info`, `get_company_register`, `get_company_register_name`, `get_sub_company_info`, `get_sub_company_info_list` |
| Legal docs | `get_legal_document`, `get_legal_abstract`, `get_legal_document_company_list`, `get_legal_document_lawfirm_list` |
| Court | `get_court_info`, `get_court_info_list`, `get_court_code` |
| Law firm | `get_lawfirm_info`, `get_lawfirm_info_list`, `get_lawfirm_log` |
| Address | `get_address_info` |
| Restriction | `get_restriction_case`, `get_restriction_case_company_list`, `get_restriction_case_court_list` |
| Finalized | `get_finalized_case`, `get_finalized_case_company_list`, `get_finalized_case_court_list` |
| Dishonesty | `get_dishonesty_case`, `get_dishonesty_case_company_list`, `get_dishonesty_case_court_list` |
| Admin | `get_administrative_case`, `get_administrative_case_company_list`, `get_administrative_case_court_list` |

Typical signatures: identifier (name, case number, credit code, or region) + optional `columns`. List variants return all matching rows for a company or court.

### Knowledge retrievers (3)

- `legal_knowledge_retriever(query, Number?)`
- `legal_article_retriever(query, Number?)`
- `legal_case_retriever(query, Number?)`

### Math (5)

`get_sum`, `get_subtraction`, `get_multiplication`, `get_division`, `get_rank`

### System (1)

`finish` — emit the final answer.

## How this differs from agentic-eval

| | LegalAgentBench | this repo |
|---|---|---|
| Domain | Chinese legal entities + closed KBs | Brazilian electrical regulation (ANEEL), open web |
| Environment | 14 tables + 3 retrievers | live websearch / webfetch |
| Tools | 37 typed APIs | OpenCode tools (websearch, webfetch; others denied) |
| Tasks | 300 multi-hop + writing | JSONL questions + source profile |
| Scoring | keyword success + progress | traces + answers only (no scoring in v1) |
| Isolation | tool loop, max 10 steps | isolated OpenCode workers, ledger, artifacts |

Useful if we later add a closed-corpus / tool-API harness, multi-hop case construction, or keyword progress metrics.

## ANEEL vertical slice

Dataset: [`cemig-ceia/biblioteca-aneel-categorizado-4`](https://huggingface.co/datasets/cemig-ceia/biblioteca-aneel-categorizado-4), pinned at revision
`6d32fdfc85d7f9b007085a50afe059fbb90e8b49`.

### What the raw data actually provides

- 149,004 rows in one train split and three Parquet shards (366,901,644 bytes).
- About 999.5 million characters / 415 million GPT-2 tokens of PDF-derived text.
- Fields: `bucket_name`, `blob_path`, `format`, `text`, `idx`, `origin`,
  `categories`, `category_paths`, `family`, `prefix`, `role`, and `issuer`.
- Origins reconcile to 148,866 `Legislação ANEEL` and 138 `Acervo ANEEL`.
- Useful entity metadata includes 29 document families, 74 prefixes, 12 roles,
  and five populated issuers.
- The official taxonomy is sparse: only 294 rows are categorized; 148,710
  (99.8%) are `Outros`. It cannot serve as the primary retrieval partition.
- There is no normalized title, source URL, publication date, act number, or
  content hash per row. The builder derives title and act number/year and uses
  the source blob path as provenance.

The dataset is public and ungated, but its card declares no license, citation,
privacy policy, or intended-use terms. Public access is not permission to
redistribute. Documents can contain identifiable people, process numbers, and
signatures; downstream use needs an LGPD and rights review.

### Built representation

The first implementation deliberately uses one entity corpus and one knowledge
base rather than pretending the raw data contains LegalAgentBench's 14 entity
tables:

1. `documents`: normalized document identity, source index/path, title, origin,
   categories, family, prefix, role, issuer, act number/year, length, and text.
2. `documents_fts`: an external-content SQLite FTS5 index over title and full
   text using Unicode tokenization and diacritic folding.

The complete build contains 149,004 documents and produces a 1,675,722,752-byte
SQLite database. The build manifest pins and hashes all three input shards and
the resulting database.

### ANEEL tools

Each of the 29 document families is treated as a separate corpus and receives
three generated tools:

- `search_<family>`: BM25 retrieval within that family with optional issuer and
  year filters; returns bounded snippets and document IDs.
- `get_<family>`: structured number/year lookup with optional role and issuer;
  returns the first bounded content chunk, or candidates when ambiguous.
- `list_<family>`: metadata listing within that family with optional issuer,
  role, and year filters.

Examples include `search_despacho`, `get_resolucao_normativa`,
`list_portaria`, and `search_manual`.

Three shared tools complete the surface:

- `get_aneel_document`: continue reading a document from `next_offset`.
- `get_aneel_corpus_stats`: revision, row counts, and distributions.
- `finish`: final answer plus evidence document IDs.

This yields **87 family tools + 3 shared tools = 90 tools**. It mirrors
LegalAgentBench's corpus-specific APIs and makes family routing observable, but
the schema volume consumes context and can reduce tool-selection accuracy,
especially for small models.

OpenCode starts these tools through a local MCP server. Closed-corpus configs
deny web/search, shell, repository reads, and edits; only `aneel_*` tools are
allowed. Runs permit at most ten distinct tool calls.

### Smoke benchmark and scoring

`examples/aneel_closed_smoke.jsonl` contains manually verified one-hop
tasks based on corpus statistics, REN 1.000/2021, REN 1.059/2023, and CNPE
Resolution 3/2010. Each task records `key_answer`, `key_middle`, task type, and
the gold tool chain in metadata.

Exports calculate LegalAgentBench-style keyword success and progress rates
after casefolding, whitespace normalization, and accent removal. The final
answer is used for success; the complete trace plus final answer is used for
progress.
