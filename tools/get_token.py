from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json", ["https://www.googleapis.com/auth/youtube"])
creds = flow.run_local_server(port=0)
open("token.json", "w").write(creds.to_json())
print("token.json created")
