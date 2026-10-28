# Weather sources for S2: terms and limits

Read on 2026-10-27, before any download (PLAN §6.2, W3; KAPS decision 34 carried). Both sources give the same NWS station reports (METAR and SPECI). SPARQ uses only their GET interfaces, from the analytics node.

## Iowa Environmental Mesonet (Iowa State University): the archive

- **What:** the archived routine and special station reports, through `cgi-bin/request/asos.py` (address in `sparq_core.config`).
- **Terms**, from the site's disclaimer page: *"The materials found on this website are in the public domain and may be used freely by anyone for any lawful purpose."* It also says *"Attributing the Iowa Environmental Mesonet of Iowa State University would be appreciated."* No restriction on commercial or automated use is stated.
- **Attribution:** SPARQ's weather data comes from the Iowa Environmental Mesonet of Iowa State University.
- **Limits:**
  - *"a 1 second per-IP throttle is now in place"*;
  - 429 when the rate is exceeded, 503 under heavy load;
  - at most 1,000 station-years in one request.

  The downloader paces itself at 1.1 s, backs off on 429 and 5xx errors, and makes two requests per station.
- **Quality:** *"This archive simply provides the as-is collection of historical observations, very little quality control is done."* That's one reason W2 scores on Kalshi's results, not on the reports.
- **Not used:** the five-minute station data (`report_type=1`). It isn't freely available live, so a history built on it would assume information the live run can't have.

## NOAA Aviation Weather Center: live reports

- **What:** the Data API's METAR endpoint (address in `sparq_core.config`), with JSON fields `obsTime`, `reportTime`, `receiptTime` and `rawOb`.
- **Terms:** a US Government service; its page sets no separate API terms beyond the limits below.
- **Limits:**
  - *"All requests are rate limited to 100 requests per minute."*
  - *"Exceeding request limits will result in access being blocked."*
  - At most 400 entries per answer.
  - It keeps 30 days of data.
  - It asks callers to *"Set a custom user agent to prevent automated filtering inadvertently blocking valid traffic."*

  SPARQ sends `SPARQ research (+https://github.com/swalsh76/SPARQ)` and makes one request a minute for all stations.

## What the markets settle on (from their rules, for reference)

- **Daily highs:** The Weather Company's maximum for a named NWS climate site, from 2026-08-15 on. Before that, the NWS Climatological Report (Daily).
- **Daily rain (`KXRAIN`):** The Weather Company's precipitation at a climate site; a trace counts as no rain.
- **SPARQ never reads The Weather Company's data.** The tests score against Kalshi's settled results (W2).
