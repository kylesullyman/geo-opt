**The idea**  
Local businesses (dentists, contractors, restaurants, salons) are increasingly getting found — or not found — through AI answers (ChatGPT, Perplexity, Google AI Overviews) instead of a list of search results. Most of them have zero idea whether they show up in those answers, let alone how to change it. The service: audit a business's AI visibility, fix what's blocking it, and keep monitoring — sold as a productized offering (GEO/AI SEO) to local businesses around Orange County.

**How the underlying tech actually works**  
AI engines answer a question through a retrieval pipeline, not a ranking algorithm: crawl the web → break pages into small chunks → convert chunks to embeddings → index them → at query time, retrieve and rerank the best-matching chunks → generate an answer from those chunks → cite the sources it pulled from. Visibility comes down to controllable levers at each stage — is the business's site even crawlable by AI bots, is content chunked into clean self-contained answers, does structured schema markup make the business's identity unambiguous, is the business's info consistent and current enough to win the reranking step.

**How the service works (the loop)**

1. **Detect** — check crawlability, schema, and whether the business actually gets mentioned for real customer queries.
2. **Generate the fix** — corrected robots.txt, generated schema markup, drafted FAQ content.
3. **Apply** — with you (or the client's webmaster) in the loop, since most fixes land in someone else's CMS and AI-drafted content always gets reviewed before it goes live.
4. **Monitor** — rerun the same test queries on a schedule, track whether visibility is trending up.  
    Sold as three tiers: one-time audit, one-time implementation, monthly monitoring retainer.

**How the tool is architected**  
Split between what's reusable and what's strategic. The mechanical stuff — crawl check, schema check, the citation-test runner, report generation — is one tool you build once and point at any client. The judgment calls — which test queries actually matter for this business, what's already winning those queries, how to implement a fix in this client's specific CMS — stay manual, per client. That split is what keeps the business scalable without producing a generic report that misses what actually matters for each client.

**How it actually gets built**  
A Claude Code session acting as the general contractor: it writes the shared data contracts first (single-threaded, since everything else depends on it), then dispatches independent subagents in parallel to build the modules that don't depend on each other (crawl checker, schema checker, citation tester, report generator), builds the riskiest piece (the LLM-drafted content generator) sequentially with a manual read-through before merging, and keeps final integration in the main session since wiring everything together is exactly the kind of tightly-coupled work that shouldn't get split across agents.