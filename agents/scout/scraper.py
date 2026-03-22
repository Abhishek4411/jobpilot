"""Multi-portal job scraper using python-jobspy.

Search strategy: India locations are always searched first (top keywords × all India cities).
Then international locations are searched for top keywords only.
This ensures India opportunities are never missed.
"""

from typing import Any

from core.config_loader import load_config
from core.logger import get_logger

log = get_logger(__name__)

# Indian city suffixes/keywords for location classification
_INDIA_MARKERS = {"bengaluru", "bangalore", "mumbai", "pune", "hyderabad",
                  "delhi", "ncr", "noida", "gurugram", "gurgaon", "chennai",
                  "kolkata", "ahmedabad", "kochi", "india", "jaipur", "coimbatore"}


def _safe(val: object, default: str = "") -> str:
    """Convert a pandas value to string, treating NaN/None/nat as empty."""
    s = str(val) if val is not None else default
    return default if s.lower() in ("nan", "none", "nat", "") else s


def _build_india_first_params(keywords: list[str], india_locs: list[str],
                               intl_locs: list[str]) -> list[dict]:
    """Build search combos: India locations × top keywords first, then international.

    Args:
        keywords: Ordered list of search terms (most relevant first).
        india_locs: India city list (priority).
        intl_locs: International location list.

    Returns:
        Ordered list of {search_term, location} dicts.
    """
    params: list[dict] = []

    # Phase 1: Top 5 keywords × top 4 India cities (highest priority searches)
    for kw in keywords[:5]:
        for loc in india_locs[:4]:
            params.append({"search_term": kw, "location": loc, "country": "India"})

    # Phase 2: Remaining keywords × top 2 India cities
    for kw in keywords[5:8]:
        for loc in india_locs[:2]:
            params.append({"search_term": kw, "location": loc, "country": "India"})

    # Phase 3: Top 3 keywords × international locations (only if open_to_international)
    for kw in keywords[:3]:
        for loc in intl_locs[:2]:
            params.append({"search_term": kw, "location": loc, "country": None})

    return params


def scrape_jobs() -> list[dict[str, Any]]:
    """Scrape jobs from LinkedIn, Indeed, and Glassdoor using python-jobspy.

    India locations are always searched first and more thoroughly.

    Returns:
        List of job dicts with keys: title, company, location, salary_min,
        salary_max, url, description, source.
    """
    try:
        from jobspy import scrape_jobs as jobspy_scrape
    except ImportError:
        log.error("python-jobspy not installed. Run: pip install python-jobspy")
        return []

    cfg = load_config()
    prefs = cfg.get("job_preferences", {})
    keyword_groups = prefs.get("search_keywords", {}).get("groups", [])
    loc_cfg = prefs.get("locations", {})
    india_locs = loc_cfg.get("india_priority", ["Bengaluru", "Mumbai", "Hyderabad", "Pune", "Delhi NCR"])
    intl_locs = loc_cfg.get("international_preferred", ["Remote"])
    open_intl = loc_cfg.get("open_to_international", True)
    portals = prefs.get("portals", {})

    site_names = []
    if portals.get("linkedin"):
        site_names.append("linkedin")
    if portals.get("indeed"):
        site_names.append("indeed")
    if portals.get("glassdoor"):
        site_names.append("glassdoor")
    if not site_names:
        site_names = ["linkedin", "indeed"]

    flat_keywords = [g[0] for g in keyword_groups if g]
    search_params = _build_india_first_params(
        flat_keywords, india_locs, intl_locs if open_intl else []
    )

    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for params in search_params:
        try:
            kwargs: dict[str, Any] = {
                "site_name": site_names,
                "search_term": params["search_term"],
                "location": params["location"],
                "results_wanted": 15,  # 15 per combo — enough, less CPU
                "hours_old": 336,      # 14 days — recent data only
            }
            if params.get("country"):
                kwargs["country_indeed"] = params["country"]

            df = jobspy_scrape(**kwargs)
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                url = _safe(row.get("job_url", ""), "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                desc = _safe(row.get("description", ""), "")
                sal_min = row.get("min_amount")
                sal_max = row.get("max_amount")

                all_jobs.append({
                    "title": _safe(row.get("title", "")),
                    "company": _safe(row.get("company", "")),
                    "location": _safe(row.get("location", "")),
                    "salary_min": sal_min if _safe(str(sal_min)) not in ("nan", "none") else None,
                    "salary_max": sal_max if _safe(str(sal_max)) not in ("nan", "none") else None,
                    "url": url,
                    "description": desc[:3000],
                    "source": _safe(row.get("site", "")),
                })

        except Exception as e:
            log.error("jobspy error for '%s' @ '%s': %s", params["search_term"], params["location"], e)

    india_count = sum(
        1 for j in all_jobs
        if any(m in j.get("location", "").lower() for m in _INDIA_MARKERS)
    )
    log.info("Scout scraped %d jobs total (%d India, %d international)",
             len(all_jobs), india_count, len(all_jobs) - india_count)
    return all_jobs
