# Failure information

{test_info}

# SBFL candidates

The following JSON contains every allowed candidate. `line_ranges` uses original RTL line numbers. Return exactly one assessment for every candidate ID, without duplicates or omissions.

```json
{candidates_json}
```

# Buggy RTL context

The RTL below is the source used by the failing SBFL run. Blank lines have been removed, but the original source line numbers are preserved. Non-contiguous omitted code is explicitly marked.

{source_sections}

# Decision requirements

For each candidate:

1. Determine whether its code creates a wrong value or only propagates one.
2. Relate the code to the failure information when concrete failure details are available.
3. Assign a calibrated root-cause likelihood score from 0.0 to 1.0.
4. Give a concise technical reason for the score, grounded only in the supplied failure information and RTL.

Return only the candidate ID, score, and reason fields required by the supplied JSON schema. Do not include causal roles, source-line lists, or other fields.

The response must satisfy the supplied JSON schema. Do not return prose outside the structured response.
