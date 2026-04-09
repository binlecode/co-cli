import re

with open("docs/TODO-mlx-provider.md") as f:
    content = f.read()

# Find the start of the Audit Log section
match = re.search(r"\n---\n+^# Audit Log$", content, flags=re.MULTILINE)

if match:
    # Keep everything up to the separator
    new_content = content[: match.start()]

    # Append the Final Team Lead section
    new_content += """

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev mlx-provider`
"""
    with open("docs/TODO-mlx-provider.md", "w") as f:
        f.write(new_content)
    print("Audit log stripped and final section added.")
else:
    print("Audit log section not found.")
