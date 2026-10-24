import requests

G = "https://graph.facebook.com"


def get(path, **params):
    r = requests.get(f"{G}/{path}", params=params, timeout=60)
    if r.status_code != 200:
        print("ERROR:", r.text)
        input("Press Enter to close")
        raise SystemExit(1)
    return r.json()


app_id = input("App ID: ").strip()
app_secret = input("App Secret: ").strip()
short = input("Token from Graph API Explorer: ").strip()

long_user = get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                client_secret=app_secret, fb_exchange_token=short)["access_token"]

pages = get("me/accounts", fields="name,access_token,instagram_business_account",
            access_token=long_user).get("data", [])

found = None
for p in pages:
    ig = p.get("instagram_business_account")
    if ig:
        found = (p["name"], ig["id"], p["access_token"])
        break

if not found:
    print("No Instagram account is linked to your Facebook Pages.")
    print("Pages seen:", [p["name"] for p in pages])
    input("Press Enter to close")
    raise SystemExit(1)

name, ig_id, token = found
info = get("debug_token", input_token=token, access_token=f"{app_id}|{app_secret}")["data"]
print("Page:", name)
print("Token never expires:", info.get("expires_at", 0) == 0)

with open("ig_keys.txt", "w") as f:
    f.write(f"IG_USER_ID={ig_id}\nIG_TOKEN={token}\n")
print("Done. Open ig_keys.txt")
