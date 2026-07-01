import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None

FINTECH_ICP_PROMPT = """You are a B2B sales researcher. Given a list of company names and domains,
identify which ones match the ideal customer profile (ICP) for Gradient Labs — a company that builds
AI agents to automate customer operations (support, disputes, KYC/KYB, lending workflows, collections)
for consumer and SMB-facing financial services companies.

The key ICP question is: does this company have end customers generating support tickets, disputes,
onboarding friction, or lending/collections workflows? If yes, it's a fit.

TIER 1 — Best fit. Consumer-facing fintechs with high support volumes and clear CX operations needs:
- Neobanks / challenger banks (e.g. Monzo, Revolut, Current, Pockit)
- Consumer payments apps (wallets, remittance, card issuers e.g. Wise, Nala)
- Consumer lending / credit (consumer loans, P2P lending, BNPL, credit cards e.g. Bondora, Yonder)
- Savings / investment / wealth management apps (retail-facing e.g. Plum, Stash)
- Insurtech with consumer or SMB policyholders (e.g. Zego)
- Earned wage access / salary advance platforms (e.g. SteadyPay, Rain)
- Pension / retirement savings platforms (e.g. Penfold)
- Crypto exchanges and consumer crypto wallets with retail customers

TIER 2 — Good fit. SMB-facing or slightly less obvious CX operations fit:
- SMB / business banking platforms (e.g. Rho, Brex, Tide)
- Embedded finance providers that own the end-customer relationship
- Merchant-facing payment gateways/processors with active support operations
- RegTech platforms that are customer-facing (e.g. identity verification for end users)

EXCLUDE entirely — these do NOT have the customer operations Gradient Labs automates:
- Traditional large banks and legacy financial institutions (JPMorgan, Wells Fargo, HSBC, Barclays, etc.)
- Pure B2B financial data / API companies with no end consumers (e.g. Plaid, MX, Codat)
- Core banking / banking infrastructure vendors (e.g. Finastra, Temenos, Mambu)
- B2B RegTech / compliance vendors that sell tools to fintechs (e.g. Onfido, ComplyAdvantage, Chainalysis)
- Pure crypto infrastructure (L1/L2 protocols, dev tooling, node operators)
- Consulting firms, law firms, marketing agencies, hardware vendors

SIZE: Prefer companies with approximately 30–2,000 employees. Exclude tiny pre-product startups
and massive global institutions. If size is uncertain, include it.

Return a JSON array of objects with "name", "domain", and "tier" (integer 1 or 2) for companies
that match the ICP. Only include companies you are confident are a fit.
When in doubt between Tier 1 and Tier 2, use Tier 2. When in doubt whether to include, include it.

Companies to evaluate:
{companies}
"""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def filter_fintech_icp(companies: list[dict], batch_size: int = 30) -> list[dict]:
    """
    Filters a list of {name, domain} dicts to only fintech ICP matches.
    Batches requests to stay within token limits.
    """
    results = []
    for i in range(0, len(companies), batch_size):
        batch = companies[i : i + batch_size]
        results.extend(_filter_batch(batch))
    return results


def _filter_batch(companies: list[dict]) -> list[dict]:
    client = _get_client()
    companies_text = "\n".join(f"- {c['name']} ({c['domain']})" for c in companies)
    prompt = FINTECH_ICP_PROMPT.format(companies=companies_text)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    content = message.content[0].text.strip()

    # Extract JSON array from response
    start = content.find("[")
    end = content.rfind("]") + 1
    if start == -1 or end == 0:
        return []

    try:
        return json.loads(content[start:end])
    except json.JSONDecodeError:
        return []
