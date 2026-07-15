# Phase 4C — FDA Orange Book

PharmaTune ingests the official monthly FDA Orange Book archive and joins its
product, patent and exclusivity files by application and product number.

The source is regulatory lifecycle context only. It does not create opportunity
signals, and listed patents are not legal advice or a freedom-to-operate opinion.
The scheduled job is repeat-safe through stable keys and content checksums.

If FDA/Akamai blocks the bulk Orange Book archive, the connector automatically
loads official daily Drugs@FDA product facts. It leaves patents and exclusivity
empty rather than guessing, and returns to the Orange Book archive automatically
when it becomes available again.
