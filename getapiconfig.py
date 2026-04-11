import requests


def debug_provider_request():
    # Adding a common Clash User-Agent is the "magic" fix for many APIs
    headers = {'User-Agent': 'Clash-Verge/1.0 (clash-verge-rev; mihomo)'}

    url = input("Paste your provider URL: ").strip()

    try:
        print("\nSending request with Clash User-Agent...")
        response = requests.get(url, headers=headers, allow_redirects=True)

        # DEBUG: See what the server thinks we are
        print(f"Status Code: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")

        content = response.text.strip()

        # Save whatever we got
        with open("raw_debug_output.yaml", "w", encoding="utf-8") as f:
            f.write(content)

        print("\n--- FIRST 20 LINES OF RESPONSE ---")
        print("\n".join(content.splitlines()[:20]))
        print("\n--- Full response saved to 'raw_debug_output.yaml' ---")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    debug_provider_request()
