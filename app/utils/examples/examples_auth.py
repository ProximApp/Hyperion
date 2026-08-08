example_AuthorizeValidation = {
    "client_id": "5507cc3a-fd29-11ec-b939-0242ac120002",
    "redirect_uri": "http://127.0.0.1:8000/docs",
    "response_type": "code",
    "scope": "API openid",
    "state": "azerty",
    "code_challenge": "c2cf464b7901205c037cd821bc493b191943bdb5244a665e9fcab6478bf79415",  # hashlib.sha256("AntoineMonBelAntoine".encode()).hexdigest()
    "code_challenge_method": "S256",
    "auth_access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiYWRtaW4iOnRydWUsImlhdCI6MTUxNjIzOTAyMn0.KMUFsIDTnFmyG3nMiGM6H9FNFUROf3wh7SmqJp-QV30",
}

example_TokenReq_access_token = {
    "grant_type": "authorization_code",
    "code": "123456789",
    "redirect_uri": "http://127.0.0.1:8000/docs",
    "client_id": "5507cc3a-fd29-11ec-b939-0242ac120002",
    "code_verifier": "AntoineMonBelAntoine",
}

example_TokenReq_refresh_token = {
    "grant_type": "refresh_token",
    "refresh_token": "refresh_token",
    "client_id": "5507cc3a-fd29-11ec-b939-0242ac120002",
}
