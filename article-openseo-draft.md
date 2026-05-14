# OpenSEO Has 1.7k GitHub Stars. I Built the Same Thing for $0.

I saw OpenSEO trending and did what every developer does.

I starred it before reading the pricing.

Then I read the pricing.

---

## The appeal is real

The pitch is clean: open source, self-hostable, pay-as-you-go. No Semrush subscription. No bloat. Fork it and add your own features. For developers tired of paying $200/month for tools that do 10x more than they need, it lands perfectly.

1.7k stars. 196 forks. Active releases. The community is real.

I get it. I would have starred it too.

---

## Then I opened the pricing section

> *"OpenSEO itself remains free. It works by using DataForSEO's APIs, which is a paid third-party service."*

So it's free the same way a printer is free.

Here's what DataForSEO actually costs:

- Minimum top-up: **$50**
- Backlinks API: **$100/month commitment**
- 100 keyword research requests: **$3.50–$7.00**
- 100 domain overviews: **$4.01**
- Rank tracking at scale: climbs fast depending on keywords and devices

The stars came from developers who love the idea. The cost reality hits after setup.

---

## What I built instead

Part of what I do is build [real-browser SEO automation tools](https://dannwaneri.com/seo-automation/) — agents that visit pages the way Google does, not the way a scraper does.

My SEO agent does a full site audit — titles, meta descriptions, H1s, canonical tags, broken links, GSC quick wins, internal link clusters — in a real Chromium browser.

Not an API. An actual browser visiting each page.

Here's what it costs to run:

- **Browser visits:** $0. Playwright is free.
- **GSC data:** $0. Google already collected it for you.
- **Claude API calls:** fractions of a cent per page on Haiku.
- **Total for a full audit:** under $0.01 for most sites.

I wrote about the exact cost breakdown [here](https://dev.to/dannwaneri/i-was-paying-0006-per-url-for-seo-audits-until-i-realized-most-needed-0-132j). The short version: I was paying $0.006 per URL until I realized most URLs needed $0.

---

## The technical difference that matters

OpenSEO pulls data from DataForSEO's index. That index is updated periodically. It tells you what DataForSEO's crawlers saw, when they saw it.

My agent visits the page right now, in a real browser, and extracts what's actually there — rendered JavaScript, actual title tags, real canonical values, live broken links.

If a page has a client-side rendering issue that hides the H1 from crawlers, a scraper-based tool misses it. A real browser catches it.

This is the same principle behind the [Cloudflare-based automations](https://dannwaneri.com/cloudflare-automation/) I build for clients — edge-deployed, real output, not cached assumptions.

That's not a criticism of OpenSEO. It's a different architectural choice with a real tradeoff.

---

## What OpenSEO has that I don't

I'll be honest:

- Rank tracking over time — I don't have this
- Keyword research at scale — not built
- Backlink analysis — not in my agent
- A polished UI — mine outputs JSON

If you need those features and you're comfortable with the DataForSEO cost model, OpenSEO is a reasonable choice.

But if your core need is: *does this page have what Google needs to rank it* — a real browser costs less and sees more.

---

## One more thing

OpenSEO's contributor list includes **@claude**.

So does mine.

We're both [building production tools with the Claude API](https://dannwaneri.com/ai-agents/). The difference is what you're optimizing for — features, or cost per insight.

I chose cost per insight. My sites are proof it works.

---

## The agent is open source

[github.com/dannwaneri/seo-agent](https://github.com/dannwaneri/seo-agent)

Run it on your own site. It's resumable, so if it crashes at URL 47 it picks up at URL 48. No DataForSEO account needed.

If you want a version that runs on [Cloudflare Workers at the edge](https://dannwaneri.com/cloudflare-automation/), that's something I build for clients too.

---

*I build AI agents and SEO automation tools at [dannwaneri.com](https://dannwaneri.com). Everything I ship, I've run on my own domains first.*
