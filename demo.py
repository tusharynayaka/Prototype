# # import requests

# # # 1. Configuration
# # API_URL = "https://bmtcmobileapi.karnataka.gov.in/WebAPI/SearchByRouteDetails_v4"

# # headers = {
# #     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
# #     "Content-Type": "application/json",
# #     "Origin": "https://nammabmtcapp.karnataka.gov.in",
# #     "Referer": "https://nammabmtcapp.karnataka.gov.in/",
# # }

# # # 2. Payload with the specific routeid
# # payload = {"routeid": 1781, "servicetypeid": 0}

# # # 3. Execution
# # try:
# #     response = requests.post(API_URL, json=payload, headers=headers, timeout=10)
# #     response.raise_for_status()

# #     route_data = response.json()
# #     print("Success! Retrieved JSON Data:")
# #     print(route_data)

# # except requests.exceptions.RequestException as e:
# #     print(f"API Request Failed: {e}")
# import requests

# session = requests.Session()
# session.headers.update(
#     {
#         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#         "Content-Type": "application/json",
#         "Origin": "https://nammabmtcapp.karnataka.gov.in",
#         "Referer": "https://nammabmtcapp.karnataka.gov.in/",
#     }
# )

# # Step 1: Search route number to get actual routeid
# # Replace URL and payload with the exact search endpoint details from DevTools
# search_url = (
#     "https://bmtcmobileapi.karnataka.gov.in/WebAPI/GetRouteList"  # Example URL
# )
# search_payload = {"routeno": "501-BH"}

# search_res = session.post(search_url, json=search_payload).json()
# # Extract routeid from response array (e.g., target_id = search_res[0]['routeid'])

# # Step 2: Query SearchByRouteDetails_v4 with the valid routeid
# details_url = (
#     "https://bmtcmobileapi.karnataka.gov.in/WebAPI/SearchByRouteDetails_v4"
# )
# details_payload = {
#     "routeid": 1781,  # Replace with target_id
#     "servicetypeid": 0,
# }

# response = session.post(details_url, json=details_payload)
# print(response.json())


from playwright.sync_api import sync_playwright


def get_live_route_data(route_name):
    with sync_playwright() as p:
        # Launch browser (set headless=False if you want to watch it execute)
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        captured_data = None

        # Intercept the target network response
        def handle_response(response):
            nonlocal captured_data
            if "SearchByRouteDetails_v4" in response.url and response.status == 200:
                try:
                    res_json = response.json()
                    # Verify response actually contains route data
                    if res_json.get("issuccess") is True:
                        captured_data = res_json
                except Exception:
                    pass

        page.on("response", handle_response)

        # Navigate to the portal
        page.goto("https://nammabmtcapp.karnataka.gov.in/commuter/search%20by%20route")

        # Fill in the route input field
        input_selector = 'input[placeholder*="Route"]'
        page.wait_for_selector(input_selector)
        page.fill(input_selector, route_name)

        # Select option from dynamic dropdown if applicable, or click search button
        page.click("button:has-text('search')")

        # Wait for network idle/response to be captured
        page.wait_for_timeout(4000)
        browser.close()

        return captured_data


# Run for target route
data = get_live_route_data("501-BH")
print(data)