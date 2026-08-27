"""Run this directly (not through the pipeline) to see exactly what the
Michigan listing page returns, so we can see why 0 links matched.

    python debug_michigan.py

It will:
  1. fetch the listing page and show status code + page length
  2. print every <a href> found on the page (first 40)
  3. print how many of those match the .pdf/.xlsx/.csv filter
"""

import re
import requests
from bs4 import BeautifulSoup

from salt_tracker.discovery.michigan import MichiganSource, FILE_EXT_RE

url = MichiganSource.listing_urls[0]
headers = {"User-Agent": "Mozilla/5.0 (compatible; RoadSaltTracker/1.0; +research use)"}

resp = requests.get(url, headers=headers, timeout=30)
print(f"status: {resp.status_code}")
print(f"content length: {len(resp.text)}")
print()

soup = BeautifulSoup(resp.text, "lxml")
all_links = soup.select("a[href]")
print(f"total <a href> tags found: {len(all_links)}")
print()

print("first 40 links (href -> text):")
for a in all_links[:40]:
    print(f"  {a['href']!r}  ->  {a.get_text(' ', strip=True)!r}")

print()
file_links = [a for a in all_links if FILE_EXT_RE.search(a["href"])]
print(f"links matching pdf/xlsx/csv filter: {len(file_links)}")
for a in file_links:
    print(f"  {a['href']}")

# Save the raw HTML so you can grep/inspect it directly if needed
with open("debug_michigan_page.html", "w") as f:
    f.write(resp.text)
print()
print("full HTML saved to debug_michigan_page.html")
