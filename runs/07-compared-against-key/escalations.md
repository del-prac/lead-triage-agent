# Escalations

5 lead(s) need a person. Each card says what to decide and what each answer leads to;
outcomes are computed by re-running the same rules with that answer applied.

## L-004

**Why it is here:** Escalate: strong fit but no contact email; a human should try to recover contact details.

**Decide:** Can we find a contact email for Priya Natarajan at verdantlabs.com?

- If *Contact recovered* → **QUALIFY sell_now P1**
- If *No contact found* → **REJECT: nobody to follow up with**

**First action:** Check the website and LinkedIn for the named person; do not guess an address and email it blind.  
**Urgency:** same day (strong fit)

**Submitted (untrusted text, shown verbatim):**

```
lead_id: L-004
submitted_at: 2026-06-02T08:05:00Z
name: Priya Natarajan
email: 
company: Verdant Labs
website: https://verdantlabs.com
monthly_budget_usd: 12000
message: We need help with content strategy for a developer audience. Budget approved for Q3.
source: webinar
```

**Scores:** value 70 ($12,000/mo budget +45, services: content +5, industry saas_software +10, timeline within_quarter +5, source webinar +5); credibility 100 (none)

**Model evidence (quotes from the lead):**

- request_type: "We need help with content strategy for a developer audience."
- buying_stage: "Budget approved for Q3."

## L-008

**Why it is here:** Escalate: high value but low credibility: value 90, credibility 40 (identity withheld -25, freemail email -15, missing company -10).

**Decide:** Is this person who they say they are, at the company they describe?

- If *Identity and company verified* → **QUALIFY sell_now P1**
- If *Cannot verify* → **REJECT: high-value claims we cannot stand behind**

**First action:** Reply asking for the company name and a work email before any pricing or call.  
**Urgency:** same day (high value if real)

**Submitted (untrusted text, shown verbatim):**

```
lead_id: L-008
submitted_at: 2026-06-03T14:11:00Z
name: Rick Alvarez
email: rickalvarez88@gmail.com
company: 
website: 
monthly_budget_usd: 40000
message: VP of Growth at a Fortune 500 retailer. Can't share the company name yet. Ready to sign this week if the price is right.
source: contact_form
```

**Scores:** value 90 ($40,000/mo budget +55, enterprise scale +20, industry cpg_retail +5, timeline now +10); credibility 40 (freemail email -15, missing company -10, missing website -10, identity withheld -25)

**Model evidence (quotes from the lead):**

- request_type: "Ready to sign this week if the price is right."
- buying_stage: "Ready to sign this week if the price is right."
- company_scale: "Fortune 500 retailer"
- identity_withheld: "Can't share the company name yet."

## L-011

**Why it is here:** Would be NURTURE (value 55, credibility 100) if a genuine buyer; held because another agency is asking for pricing and deliverables, possibly for a client, so a human should decide what to share.

**Decide:** Is this agency buying on behalf of a real client, or researching us?

- If *Buying for a confirmed client* → **NURTURE readiness**
- If *Researching us* → **REJECT; consider adding the domain to known_actors.deny**

**First action:** Reply with public information only (pricing is tailored) and ask who the end client is.  
**Urgency:** normal

**Submitted (untrusted text, shown verbatim):**

```
lead_id: L-011
submitted_at: 2026-06-04T15:37:00Z
name: Sam Whitfield
email: sam@apexdigitalpartners.com
company: Apex Digital Partners
website: https://apexdigitalpartners.com
monthly_budget_usd: 30000
message: Curious how you structure retainers and what deliverables you include at each tier. Putting together a comparison for a client of ours.
source: contact_form
```

**Scores:** value 55 ($30,000/mo budget +55); credibility 100 (none)

**Model evidence (quotes from the lead):**

- request_type: "Curious how you structure retainers and what deliverables you include at each tier. Putting together a comparison for a client of ours."
- buying_stage: "Curious how you structure retainers"

## L-018

**Why it is here:** Escalate: company and budget look real but the message is empty, so need and readiness are unknown.

**Decide:** What do they need, and are they ready to buy?

- If *Specific need, ready now* → **QUALIFY sell_now P2**
- If *Just exploring* → **NURTURE readiness**

**First action:** Send a short note asking what prompted the inquiry; the company and budget look real.  
**Urgency:** within 1 business day

**Submitted (untrusted text, shown verbatim):**

```
lead_id: L-018
submitted_at: 2026-06-06T14:33:00Z
name: Ben Cutler
email: ben.cutler@quorumdata.ai
company: Quorum Data
website: https://quorumdata.ai
monthly_budget_usd: 22000
message: 
source: contact_form
```

**Scores:** value 45 ($22,000/mo budget +45); credibility 90 (missing message -10)

## L-019

**Why it is here:** Privacy or legal request, which needs whoever owns data and legal requests (often on a deadline), not sales triage.

**Decide:** This is a privacy or legal request, not a sales lead. Who owns it, and has the clock started?

- If *Routed to the privacy/legal owner* → **handled outside sales; no sales follow-up**

**First action:** Forward to the data-protection owner today and log the request date.  
**Urgency:** high: statutory deadline (GDPR Art. 12: one month to respond)

**Submitted (untrusted text, shown verbatim):**

```
lead_id: L-019
submitted_at: 2026-06-07T08:19:00Z
name: Hannah Vogel
email: h.vogel@example.de
company: 
website: 
monthly_budget_usd: 0
message: Please delete all personal data you hold about me under GDPR Article 17. I submitted a form on your site last year.
source: contact_form
```

**Scores:** value 0 (none); credibility 20 (placeholder email -60, missing company -10, missing website -10)

**Model evidence (quotes from the lead):**

- request_type: "Please delete all personal data you hold about me under GDPR Article 17."
- needs_compliance_judgement: "Please delete all personal data you hold about me under GDPR Article 17."

# Bad-faith review list

Auto-rejected for bad faith. Skim in bulk; overturn any that look genuine.

| Lead | Decision | Why |
|---|---|---|
| L-006 | REJECT | Text in the lead tries to instruct an automated agent (bad faith); otherwise credible buyer below the $3,000 floor today; nurture toward it. |
| L-010 | REJECT | Test or junk submission with no real request. |
| L-013 | REJECT | Disposable email address plus an up-front request for methodology or client results, a fishing pattern rather than a buyer. |
| L-020 | REJECT | Suspected phishing (payment pressure and/or a link to an executable); not a lead, flagged for security review and the link was not opened. |
