# Phase 4A — EMA integration

Status: implementation candidate.

## Official source

PharmaTune consumes the European Medicines Agency medicines JSON file published
at `medicines-output-medicines_json-report_en.json`. EMA states that its website
JSON files update twice daily at 06:00 and 18:00 Amsterdam time and embed a feed
timestamp.

## First production slice

- human and veterinary medicine catalogue records are normalised from the official feed;
- EMA product number is the stable source identity;
- medicine, active substance, authorisation holder, status, category, indication,
  key regulatory dates, last-update date and official EPAR URL are retained;
- the existing scheduler checks the feed daily and uses a record watermark,
  bounded lookback and content checksum for repeat-safe updates;
- official medicine facts project into Pharmaceutical Memory as authorisation-holder
  and active-substance relationships;
- Data Sources displays live EMA coverage inside PharmaTune.

EMA catalogue data is regulatory context. It is never converted into a product
problem, confirmed root cause, customer need, buying intent or solution-fit claim.

## Next EMA source families

Post-authorisation procedures, referrals, PIPs, orphan designations, PSUSAs,
DHPCs, shortages, herbal medicines and grouped documents remain separate bounded
source jobs. They must be added one family at a time with their own evidence role
and regression fixture.

## Completion gate

1. automated parser, scheduler, persistence and memory tests pass;
2. full existing regression suite remains green;
3. GitHub main deploys the new source;
4. a live `ema_medicines` refresh succeeds against Neon;
5. Data Sources reports retained EMA medicines and categories.
