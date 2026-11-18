# Data quality report

Scraped at: 2026-11-18T11:57:31+00:00

## Counts

| source | raw rows | clean rows |
|---|---|---|
| darglobal (projects) | 43 | 42 |
| darglobal (company pages) | 22 | 22 |
| darglobal (news, Jan-Sep 2026) | 23 | 23 |
| wasalt (listings) | 300 | 300 |

## DarGlobal company pages

22 pages, 116,406 chars, 31 FAQ pairs. Pages: about, partners, get-in-touch, faq, why-invest, hospitality, development-management, luxury-golf-communities, investor, commercial, partners/aston-martin, partners/elie-saab, partners/fendi-casa, partners/lamborghini, partners/marriott-residences-aida-oman, partners/missoni, partners/mouawad, partners/pagani, partners/paramount, partners/the-trump-organization, partners/w-hotels, exclusive-plots

## DarGlobal news (Q1-Q3 2026)

23 articles (press 11, blog 12), 2026-01-12 to 2026-09-30. By month: 2026-01 2, 2026-02 2, 2026-03 1, 2026-04 2, 2026-06 3, 2026-07 3, 2026-08 5, 2026-09 5

## DarGlobal projects

| field | non-null | null % |
|---|---|---|
| country | 42/42 | 0% |
| city | 41/42 | 2% |
| brand_partners | 25/42 | 40% |
| property_types | 40/42 | 5% |
| starting_price | 0/42 | 100% |
| status | 18/42 | 57% |
| summary | 36/42 | 14% |
| sections | 42/42 | 0% |

By country: Oman 15, United Arab Emirates 10, Saudi Arabia 10, Spain 3, United Kingdom 2, Qatar 2

Text per project: min 600, median 2616, max 5212 chars

## Wasalt listings


Area status: from_url 129, estimated 98, None 40, ambiguous 33

| field | non-null | null % |
|---|---|---|
| property_type | 300/300 | 0% |
| purpose | 300/300 | 0% |
| bedrooms | 222/300 | 26% |
| area_sqm | 227/300 | 24% |
| price | 0/300 | 100% |
| city | 0/300 | 100% |
| district | 0/300 | 100% |
| description | 0/300 | 100% |

By purpose: rent 105, sale 195

By type: apartment 183, villa 57, floor 28, land 23, building 6, rest house 3

Detail pages fetched: 0/300

## Random rows (eyeball check)

- **D-Villas At Jumeirah Golf Estates** | Dubai, United Arab Emirates | brands [] | types ['apartment', 'villa'] | 2377 chars | https://darglobal.co.uk/d-villas-at-jge
- **Sunrise Haven** | Muscat, Oman | brands [] | types ['villa'] | 3048 chars | https://darglobal.co.uk/Sunrise-Haven-Luxury-Villas
- **Marea-Interiors by Missoni** | Casares, Spain | brands ['Missoni'] | types ['apartment'] | 3353 chars | https://darglobal.co.uk/marea
- **Villa, for rent, 8 bedrooms, about 358.87 sqm** | price None | city None | https://wasalt.sa/en/property/rent/villa-35887-sqm-facing-north-with-8-bedrooms-5916492
- **Villa, for rent, 4 bedrooms, 250 sqm** | price None | city None | https://wasalt.sa/en/property/rent/villa-250-sqm-facing-north-with-4-bedrooms-5919778
