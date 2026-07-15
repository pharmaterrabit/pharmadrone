# Phase 4B — MHRA integration

Status: implementation candidate.

## Official source

PharmaTune uses the GOV.UK Search API filtered to the Medicines and Healthcare
products Regulatory Agency, `medical_safety_alert` documents and the
`medicines-recall-notification` alert type. The live index exposed 584 medicine
recall/notification records during implementation on 15 July 2026.

## Production slice

- the full bounded medicine-recall index is fetched newest first;
- GOV.UK path is the stable source identity;
- title, official description, alert class, company, product, MHRA reference,
  publication date and official URL are retained;
- daily scheduled ingestion uses a publication watermark, bounded lookback and
  content checksum with append-only change history;
- explicit MHRA defect descriptions can enter the existing governed recall
  discovery path; ambiguous records do not become direct problem evidence;
- device field safety notices and other alert types are excluded from medicine
  opportunity generation;
- live MHRA coverage is shown inside Data Sources.

An MHRA recall confirms the published regulatory event and stated reason. It does
not prove current buying intent, unresolved status, commercial urgency or that a
specific external technology will solve the issue.

## Completion gate

1. parser, scheduler, persistence and evidence-boundary tests pass;
2. the complete existing regression suite remains green;
3. GitHub main deploys the connector;
4. a live `mhra_medicine_recalls` refresh succeeds against Neon;
5. Data Sources reports retained MHRA records and latest update.
