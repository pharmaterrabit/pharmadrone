# Checkpoint 8.3 — Account Intelligence

Status: implementation complete; production projection required after merge.

## A — Organisation identity and aliases

PharmaTune now builds canonical profiles for every source-supported organisation,
including commercial companies, universities, hospitals, research institutes and
public-sector bodies. Source names remain as evidence-linked aliases. The model is
separate from customer-tenant organisations and does not silently merge unrelated
entities.

## B — MAH, manufacturer, product and signal relationships

Official EMA, FDA, MHRA, ClinicalTrials.gov and other stored records create
auditable relationships between an organisation and its products, programmes or
signals. Every relationship keeps its source ID, official evidence link and last
observation. Opportunity signals are linked without being presented as proof of
commercial need.

## C — Contact evidence and responsible-function routing

Named contacts are stored only when a public source provides both a person's name
and an official evidence URL. Their status is “listed in an official public
source”, never “100% current”. ClinicalTrials.gov public central/location contacts
are preserved. When no named person is supported, PharmaTune shows the evidence-
derived responsible function—such as Quality / CMC or Supply Chain / Procurement—
and explicitly says that this is not a verified person.

## D — Weekly monitoring and history

The GitHub Actions scheduler contains a weekly `account_intelligence` job. It
reprojects all active stored evidence, records append-only organisation/contact
observations, updates next-review dates, and flags unseen named contacts when their
seven-day verification window expires. The Companies workspace reads this stored
projection, so normal page navigation does not perform network enrichment.

## Completion gate

- migration 9 installs the account-intelligence schema;
- organisation profiles expose aliases, relationships, contacts and routes;
- named-contact evidence rules and ClinicalTrials.gov contact parsing pass tests;
- weekly cadence, change history and expired-contact behavior pass tests;
- the complete automated suite and Python compilation pass;
- GitHub main contains the checkpoint and the production workflow runs the first
  account projection after the regulatory bootstrap.
