# Release process

## Cadence

Services release on Tuesdays and Thursdays. No releases on Fridays or the day before a public
holiday unless the release fixes a SEV1.

## Steps

1. Open a release PR from `main` into `release`; the changelog is generated from merged PR titles.
2. CI must be green, including the smoke suite against staging.
3. One approval from a code owner and one from the on-call primary.
4. Deploy to the canary (5% of traffic) for at least 30 minutes; error rate must stay under 0.5%.
5. Promote to 100%. The release manager posts the summary in the releases channel.

## Rollback

`deploy rollback <service>` restores the previous image. Rollback first, investigate second.
