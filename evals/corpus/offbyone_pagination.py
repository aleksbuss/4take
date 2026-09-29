def page_slice(items, page, per_page):
    """Return the items on `page` (1-indexed)."""
    start = page * per_page
    end = start + per_page
    return items[start:end]

def total_pages(items, per_page):
    return len(items) // per_page
