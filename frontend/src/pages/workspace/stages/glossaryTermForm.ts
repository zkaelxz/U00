// The category and policy selects use '' for "None", but the API accepts only a value or null:
// an empty string is rejected as an unknown category/policy.
export const noneAsNull = (v: string): string | null => (v === '' ? null : v)
