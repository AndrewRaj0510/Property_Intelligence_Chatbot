# Data quality report

Scraped at: 2026-11-28T09:30:41+00:00

## Counts

| source | raw rows | clean rows |
|---|---|---|
| darglobal (projects) | 42 | 41 |
| darglobal (company pages) | 24 | 24 |
| darglobal (news, Jan-Sep 2026) | 23 | 23 |
| wasalt (listings) | 300 | 300 |

## DarGlobal company pages

24 pages, 120,508 chars, 31 FAQ pairs. Pages: about, partners, get-in-touch, faq, why-invest, hospitality, development-management, luxury-golf-communities, investor, commercial, partners/aston-martin, partners/elie-saab, partners/fendi-casa, partners/lamborghini, partners/marriott-residences-aida-oman, partners/missoni, partners/mouawad, partners/pagani, partners/paramount, partners/the-trump-organization, partners/w-hotels, Invest-crypto, exclusive-plots, invest-in-aida

## DarGlobal news (Q1-Q3 2026)

23 articles (press 11, blog 12), 2026-01-12 to 2026-09-30. By month: 2026-01 2, 2026-02 2, 2026-03 1, 2026-04 2, 2026-06 3, 2026-07 3, 2026-08 5, 2026-09 5

## DarGlobal projects

| field | non-null | null % |
|---|---|---|
| country | 41/41 | 0% |
| city | 41/41 | 0% |
| brand_partners | 26/41 | 37% |
| property_types | 40/41 | 2% |
| starting_price | 0/41 | 100% |
| status | 19/41 | 54% |
| summary | 37/41 | 10% |
| sections | 41/41 | 0% |

By country: Oman 13, United Arab Emirates 11, Saudi Arabia 10, Spain 3, United Kingdom 2, Qatar 2

Text per project: min 1335, median 2616, max 5212 chars

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

- **Da Vinci Tower interiors by Pagani** | Dubai, United Arab Emirates | brands ['Pagani'] | types ['apartment'] | 4363 chars | https://darglobal.co.uk/davinci-tower-by-pagani
- **Trump International Hotel & Tower Dubai** | Dubai, United Arab Emirates | brands ['Trump'] | types ['apartment', 'penthouse'] | 2616 chars | https://darglobal.co.uk/trump-international-hotel-and-tower-dubai
- **Marriott Residences Aida, Oman** | Muscat, Oman | brands ['Marriott'] | types ['apartment'] | 3777 chars | https://darglobal.co.uk/marriott-residences-aida-oman
- **Villa, for rent, 8 bedrooms, about 358.87 sqm** | price None | city None | https://wasalt.sa/en/property/rent/villa-35887-sqm-facing-north-with-8-bedrooms-5916492
- **Villa, for rent, 4 bedrooms, 250 sqm** | price None | city None | https://wasalt.sa/en/property/rent/villa-250-sqm-facing-north-with-4-bedrooms-5919778
