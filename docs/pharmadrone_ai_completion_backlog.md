# PharmaDrone AI completion backlog

The first standalone vertical slice is usable end to end. The following work is deliberately deferred:

1. Stripe checkout, webhook signature validation and subscription lifecycle handling.
2. Email verification, password reset, MFA and workspace invitations.
3. Server-side token revocation and refresh-token rotation.
4. Production rate limiting backed by a shared store.
5. HTML print stylesheet refinements and optional dependency-light PDF generation.
6. Deployment-specific observability, alerting and privacy retention configuration.

None of these items should be represented as complete in customer-facing materials until implemented and tested.
