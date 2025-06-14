import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

def search_audiobookbay(query, page=1):
    query = query.lower()
    encoded_query = quote_plus(query)
    base_url = "https://audiobookbay.lu"  # Try changing this if needed
    search_url = f"{base_url}/page/{page}/?s={encoded_query}&cat=undefined%2Cundefined"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9"
    }

    print(f"[🔍] Fetching: {search_url}")
    response = requests.get(search_url, headers=headers)
    print(f"[🌐] Status Code: {response.status_code}")

    if response.status_code != 200:
        print("[❌] Failed to fetch page.")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    posts = soup.select("div.post")

    print(f"[📄] Found {len(posts)} posts")

    results = []
    for post in posts:
        title_tag = post.select_one("div.postTitle h2 a")
        if not title_tag:
            continue

        title = title_tag.text.strip()
        link = base_url + title_tag.get("href")

        img_tag = post.select_one("div.postContent img")
        img = img_tag.get("src") if img_tag else None

        size_tag = post.select_one("div.postContent p[style*='text-align:center;']")
        size = size_tag.text.strip().replace("\n", " ") if size_tag else "Unknown size"

        results.append({
            "title": title,
            "link": link,
            "image": img,
            "details": size
        })

    return results
