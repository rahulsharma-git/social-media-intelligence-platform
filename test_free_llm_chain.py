"""Safe diagnostic for Build 5.20 semantic-analysis fallback chain.

Run locally or in GitHub Actions with provider secrets set. It prints only the
provider name and validated result; it never prints API keys.
"""
import os
from scheduled_media_reports import semantic_article_analysis

sample_title = "IN-SPACe seeks wider use of indigenous chips in space"
sample_body = (
    "IN-SPACe is encouraging greater adoption of indigenous semiconductor chips "
    "and electronics in Indian space systems to strengthen domestic capability "
    "and reduce dependence on imported components."
)

print("Configured providers:")
print("- Groq:", "yes" if os.getenv("GROQ_API_KEY") else "no")
print("- OpenRouter:", "yes" if os.getenv("OPENROUTER_API_KEY") else "no")
print("- Cloudflare Workers AI:", "yes" if os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID") else "no")

result, provider = semantic_article_analysis(
    sample_title,
    sample_body,
    "Diagnostic sample",
    ["IN-SPACe"],
)
print("Selected provider:", provider)
print("Validated result:", result if result else "Remote analysis unavailable; scheduled report will use local rules.")
