"""
PubMed E-utilities klijent za DoseCheck.

Koristi besplatni NCBI E-utilities API (esearch + efetch). Bez API ključa
limit je 3 zahteva/sekundi; ako postoji NCBI_API_KEY u okruženju, diže se na 10/s.

Filter kvaliteta ("Examine metodologija" reprodukovana legalno):
samo meta-analize, sistematski pregledi i RCT, na ljudima, sa apstraktom,
od 2010. naovamo.
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
API_KEY = os.environ.get("NCBI_API_KEY")
_MIN_INTERVAL = 0.11 if API_KEY else 0.34  # ~10/s sa ključem, ~3/s bez
_last_call = 0.0


@dataclass
class Study:
    pmid: str
    title: str
    abstract: str = ""
    journal: str = ""
    year: Optional[int] = None
    study_type: str = "other"           # meta_analysis | systematic_review | rct | other
    sample_size: Optional[int] = None
    pub_types: list[str] = field(default_factory=list)


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _get(endpoint: str, params: dict) -> bytes:
    params = {**params, "tool": "dosecheck", "email": "supp5science@gmail.com"}
    if API_KEY:
        params["api_key"] = API_KEY
    url = f"{BASE}/{endpoint}?{urlencode(params)}"
    _throttle()
    req = Request(url, headers={"User-Agent": "DoseCheck/0.1"})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def build_query(term: str, since_year: int = 2010) -> str:
    """Query šablon sa filterima kvaliteta."""
    return (
        f'("{term}"[Title/Abstract]) '
        f'AND (meta-analysis[pt] OR systematic review[pt] OR randomized controlled trial[pt]) '
        f'AND humans[MeSH] '
        f'AND ("{since_year}"[dp] : "3000"[dp]) '
        f'AND hasabstract'
    )


def esearch(term: str, retmax: int = 200, since_year: int = 2010) -> list[str]:
    """Vrati listu PMID-ova za dati pojam, uz filtere kvaliteta."""
    data = _get("esearch.fcgi", {
        "db": "pubmed",
        "term": build_query(term, since_year),
        "retmax": retmax,
        "retmode": "json",
    })
    import json
    ids = json.loads(data)["esearchresult"].get("idlist", [])
    return ids


def _classify_pub_types(pub_types: list[str]) -> str:
    low = [p.lower() for p in pub_types]
    if any("meta-analysis" in p for p in low):
        return "meta_analysis"
    if any("systematic review" in p for p in low):
        return "systematic_review"
    if any("randomized controlled trial" in p for p in low):
        return "rct"
    return "other"


def _extract_sample_size(abstract: str) -> Optional[int]:
    """Grubo izvlačenje veličine uzorka iz apstrakta (regex n = ...)."""
    import re
    candidates = re.findall(r'\bn\s*=\s*([\d,]{1,7})', abstract, flags=re.IGNORECASE)
    sizes = []
    for c in candidates:
        try:
            sizes.append(int(c.replace(",", "")))
        except ValueError:
            pass
    return max(sizes) if sizes else None


def efetch(pmids: list[str]) -> list[Study]:
    """Povuci pune zapise (naslov, apstrakt, časopis, godina, tip) za PMID-ove."""
    if not pmids:
        return []
    studies: list[Study] = []
    # efetch u batch-evima od 100 (URL limit)
    for i in range(0, len(pmids), 100):
        chunk = pmids[i:i + 100]
        xml = _get("efetch.fcgi", {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
        })
        root = ET.fromstring(xml)
        for art in root.findall(".//PubmedArticle"):
            studies.append(_parse_article(art))
    return studies


def _parse_article(art: ET.Element) -> Study:
    pmid = art.findtext(".//PMID", default="").strip()
    title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""

    # Apstrakt može imati više <AbstractText> sekcija (Background/Methods/Results...)
    parts = []
    for at in art.findall(".//Abstract/AbstractText"):
        label = at.get("Label")
        text = "".join(at.itertext())
        parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(parts).strip()

    journal = art.findtext(".//Journal/Title", default="").strip()
    year_txt = (art.findtext(".//JournalIssue/PubDate/Year")
                or art.findtext(".//JournalIssue/PubDate/MedlineDate", default="")[:4])
    try:
        year = int(year_txt)
    except (ValueError, TypeError):
        year = None

    pub_types = [pt.text for pt in art.findall(".//PublicationType") if pt.text]

    return Study(
        pmid=pmid,
        title=title.strip(),
        abstract=abstract,
        journal=journal,
        year=year,
        study_type=_classify_pub_types(pub_types),
        sample_size=_extract_sample_size(abstract),
        pub_types=pub_types,
    )


def fetch_studies(term: str, retmax: int = 200, since_year: int = 2010) -> list[Study]:
    """Kompletan tok: esearch -> efetch."""
    ids = esearch(term, retmax=retmax, since_year=since_year)
    return efetch(ids)
