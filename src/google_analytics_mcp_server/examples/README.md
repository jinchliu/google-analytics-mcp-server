# Schema examples

`admin.json` and `data.json` map schema short names to examples returned by
`ga_describe_schema`. Each entry records the intended versions, a short title and
the JSON value for that schema. For a method, examples come from its request schema.

IDs, domains, addresses, dates and project names are placeholders. Replace them with
resources and fields available to the caller. Examples do not establish service
eligibility or execute operations. Writes still require the applicable tool policy.

These examples were authored against the bundled discovery snapshots. The offline
suite recursively validates each example against every declared API version in
[`tests/test_examples.py`](../../../tests/test_examples.py). Live API behavior and
eligibility are verified separately for selected workflows; see the
[test guide](../../../tests/README.md).
